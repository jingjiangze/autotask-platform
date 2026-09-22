# -*- coding: utf-8 -*-
"""service_platform.py — 把「自动任务平台」注册为原生 Windows 服务（SCM 托管）。

设计要点
--------
1. **自托管，零外部包装器**：用 pywin32 的 servicemanager 自己完成 SCM 握手，
   由 `python.exe -u service_platform.py` 直接作为服务映像启动。
   不依赖 pythonservice.exe，也不需要 NSSM / WinSW 等第三方二进制。
2. **只做生命周期托管**：HTTP 逻辑仍在 order_platform.py，本文件不复制业务代码。
   import order_platform 会触发其模块级初始化（建表/迁移/恢复/4 个后台线程），
   与原先 `pythonw order_platform.py` 的运行语义完全一致。
3. **静默但可诊断**：服务进程无控制台，stdout/stderr 重定向到带轮转的日志文件，
   保留 order_platform 的 print 输出用于尸检。
4. **有序停机**：先关 Waitress（停止接收新连接、排空在途请求），
   再树杀仍在运行的任务引擎子进程，最后退出 —— 避免 Stop 时留下孤儿进程。
5. **替代 health_manager 的看护职责**：进程存活由 SCM 的失败恢复策略负责，
   不再需要「看护进程 + 锁文件 + 子进程对」这套自研模型（也正是审计中
   "僵尸成对进程"问题的根源）。

命令行（由 tools/service_manager.py 调用，一般不需要手动执行）
------------------------------------------------------------
    python.exe -u service_platform.py            # 作为服务运行时由 SCM 启动
    python.exe -u service_platform.py console    # 前台调试运行（Ctrl+C 退出）
"""
import os
import sys
import time
import atexit
import threading
import traceback

APP_DIR = os.path.dirname(os.path.abspath(__file__))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

# ----------------------------------------------------------------------------
# 服务身份
# ----------------------------------------------------------------------------
SVC_NAME = "WKAutoTaskPlatform"
SVC_DISPLAY = "WK 自动任务平台 (AutoTask Platform)"
SVC_DESC = ("自动任务平台：Flask + Waitress 订单服务，监听 127.0.0.1:8766；"
            "按单派生隔离子进程调用任务引擎。由服务控制管理器托管，开机自启。")

HOST = "127.0.0.1"
PORT = 8766
THREADS = 16

LOG_PATH = os.path.join(APP_DIR, "cf", "platform_service.log")
LOG_KEEP_BYTES = 2 * 1024 * 1024      # 超过则保留尾部一半


# ----------------------------------------------------------------------------
# 日志重定向（服务无控制台，print 必须落文件）
# ----------------------------------------------------------------------------
class _RotatingWriter:
    """把写入追加到文件；超过上限时保留尾部，避免服务日志无限增长。"""

    def __init__(self, path, keep=LOG_KEEP_BYTES):
        self.path = path
        self.keep = keep
        self._lock = threading.Lock()
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
        except Exception:
            pass

    def write(self, text):
        if not text:
            return 0
        with self._lock:
            try:
                with open(self.path, "a", encoding="utf-8", errors="replace") as f:
                    f.write(text)
                if os.path.getsize(self.path) > self.keep:
                    with open(self.path, "rb") as f:
                        data = f.read()
                    with open(self.path, "wb") as f:
                        f.write(data[-self.keep // 2:])
            except Exception:
                pass
        return len(text)

    def flush(self):
        pass

    def isatty(self):
        return False


def _redirect_output():
    """把 stdout/stderr 接到轮转日志；stdin 接到 devnull。"""
    writer = _RotatingWriter(LOG_PATH)
    sys.stdout = writer
    sys.stderr = writer
    try:
        sys.stdin = open(os.devnull, "r")
    except Exception:
        pass
    return writer


# ----------------------------------------------------------------------------
# 服务实现
# ----------------------------------------------------------------------------
try:
    import servicemanager
    import win32event
    import win32service
    import win32serviceutil
    _PYWIN32_OK = True
except Exception:            # 允许在没装 pywin32 的环境里 import 本文件做静态检查
    _PYWIN32_OK = False

    class _Stub:             # noqa: D401 - 仅用于占位
        pass
    servicemanager = win32event = win32service = win32serviceutil = _Stub()


if _PYWIN32_OK:

    class PlatformService(win32serviceutil.ServiceFramework):
        _svc_name_ = SVC_NAME
        _svc_display_name_ = SVC_DISPLAY
        _svc_description_ = SVC_DESC

        def __init__(self, args):
            win32serviceutil.ServiceFramework.__init__(self, args)
            # 手动复位事件：SvcStop / SvcShutdown 置位，SvcDoRun 等待
            self.hWaitStop = win32event.CreateEvent(None, 0, 0, None)
            self._server = None

        # ---- SCM 回调 -------------------------------------------------------
        def SvcStop(self):
            """收到停止请求：先声明 STOP_PENDING，再放行主线程收尾。"""
            self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING, waitHint=30000)
            self._svc_log("收到停止请求，开始有序停机…")
            win32event.SetEvent(self.hWaitStop)

        def SvcShutdown(self):
            """系统关机时也会走停止流程。"""
            self.SvcStop()

        def SvcDoRun(self):
            servicemanager.LogInfoMsg(f"{SVC_NAME}: 服务启动")
            _redirect_output()
            self._svc_log("=" * 72)
            self._svc_log(f"{SVC_NAME} 启动  pid={os.getpid()}  "
                          f"python={sys.executable}")
            try:
                self._run()
            except Exception:
                tb = traceback.format_exc()
                self._svc_log("服务主循环异常:\n" + tb)
                servicemanager.LogErrorMsg(f"{SVC_NAME}: {tb}")
            self._svc_log(f"{SVC_NAME} 已退出")

        # ---- 内部 -----------------------------------------------------------
        def _svc_log(self, msg):
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            try:
                print(f"[{stamp}] {msg}", flush=True)
            except Exception:
                pass

        def _run(self):
            """import 平台 → 起 Waitress → 阻塞等停止信号 → 收尾。"""
            self._svc_log("正在加载 order_platform（含建表/迁移/启动恢复/后台线程）…")
            import order_platform as P
            self._svc_log("order_platform 加载完成")

            from waitress.server import create_server
            self._server = create_server(P.app, host=HOST, port=PORT, threads=THREADS)
            self._svc_log(f"Waitress 已监听 http://{HOST}:{PORT} (threads={THREADS})")

            t = threading.Thread(target=self._server.run, name="waitress", daemon=True)
            t.start()

            # 阻塞等待停止 / 关机事件
            win32event.WaitForSingleObject(self.hWaitStop, win32event.INFINITE)

            self._svc_log("正在关闭 Waitress（停止接收新连接，排空在途请求）…")
            try:
                self._server.close()
                t.join(timeout=10)
                self._svc_log("Waitress 已关闭")
            except Exception:
                self._svc_log("关闭 Waitress 异常:\n" + traceback.format_exc())

            self._svc_log("正在清理仍在运行的任务引擎子进程…")
            killed = _kill_running_order_children(P)
            self._svc_log(f"子进程清理完成，处理 {killed} 个运行中订单")
            self._svc_log("有序停机结束")


def _kill_running_order_children(P):
    """树杀 DB 中 status='running' 的订单子进程（best-effort）。

    不修改订单状态：下一次启动时 order_platform 的 recover_stale_orders()
    会按 attempt 规则统一收敛，保持单一收敛入口。
    """
    n = 0
    try:
        with P.db() as c:
            rows = c.execute(
                "SELECT id, pid FROM orders "
                "WHERE status='running' AND pid IS NOT NULL AND pid != 0").fetchall()
    except Exception:
        return 0
    for r in rows:
        try:
            if P._pid_alive(r["pid"]):
                P._kill_tree(r["pid"])
                n += 1
        except Exception:
            continue
    return n


def _main():
    argv = sys.argv[1:]

    # 前台调试模式：直接跑主逻辑，不经过 SCM
    if argv and argv[0] in ("console", "debug", "fg"):
        _redirect_output()
        print(f"[console] 前台调试启动 (pid={os.getpid()})", flush=True)
        import order_platform as P
        from waitress.server import create_server
        srv = create_server(P.app, host=HOST, port=PORT, threads=THREADS)
        print(f"[console] http://{HOST}:{PORT}", flush=True)
        try:
            srv.run()
        except KeyboardInterrupt:
            print("[console] 收到中断，退出", flush=True)
            srv.close()
        return

    # 服务模式：自己完成 SCM 握手（等价于 pythonservice.exe 内部做的事）
    if not _PYWIN32_OK:
        sys.stderr.write("缺少 pywin32，无法以服务方式运行。请: pip install pywin32\n")
        return 1
    servicemanager.Initialize()
    servicemanager.PrepareToHostSingle(PlatformService)
    servicemanager.StartServiceCtrlDispatcher()
    return 0


if __name__ == "__main__":
    sys.exit(_main() or 0)
