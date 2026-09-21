# -*- coding: utf-8 -*-
"""轻量看护进程（唯一看护入口）
职责:
    1. 保证 order_platform.py 存活且 /health 可用（不健康 → 冷却后重启）
    2. 保证 cloudflared.exe 存活（消失 → 拉起）
    3. 防重启风暴：10 分钟内最多重启平台 3 次，超限熔断转人工（只记录不再拉起）
幂等: 锁文件互斥（O_EXCL 原子创建 + PID/心跳时间戳 + 陈旧锁接管）。
      背景：历史版本曾用命名互斥体 + ctypes.windll.GetLastError() 判定，因 ctypes
      未用 use_last_error 导致判定不可靠、且对"启动早期挂起的僵尸持有者"无接管能力，
      已在生产实测出现双实例/僵尸并存，故改为锁文件方案（纯文件操作，不引外部进程，
      不增加 AV 行为特征）。重复触发时，后到实例读到活跃锁即自动让位退出。
      僵尸回收请用独立脚本 tools/cleanup_stale.py（人工/按需执行，文档见 README_RUN）。
启动: python health_manager.py           （由计划任务 WK_AutoTaskPlatform 静默拉起；也兼容
                                        cf\\启动平台和隧道.bat 手动启动）
     python health_manager.py clean      （回收本项目游离/僵尸 python 实例，保留健康平台与锁持有者）
     python health_manager.py kill-all   （回收本项目全部实例，停机/维护用，慎用）
"""
import msvcrt
import os
import sqlite3
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
PYEXE = r"C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
PLATFORM = os.path.join(HERE, "order_platform.py")
CFD = os.path.join(HERE, "cf", "cloudflared.exe")
CFG = os.path.join(os.path.expanduser("~"), ".cloudflared", "wk_config.yml")
CFLOG = os.path.join(HERE, "cf", "cloudflared.log")
LOG = os.path.join(HERE, "cf", "health_manager.log")
LOCK_FILE = os.path.join(HERE, "cf", "health_manager.lock")          # 人类可读信息文件，永不加锁
OSLOCK_FILE = os.path.join(HERE, "cf", "health_manager.lock.osl")    # OS 排他锁载体，内容无意义
HEALTH_URL = "http://127.0.0.1:8766/health"

CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200

CHECK_INTERVAL = 30
COOLDOWN = 60           # 每次重启后的冷却期
RESTART_WINDOW = 600    # 重启风暴判定窗口
RESTART_LIMIT = 3       # 窗口内重启上限，超限熔断
LOCK_HEARTBEAT_TTL = 100  # 秒：锁心跳超过该时长视为陈旧锁，允许接管
DB_PATH = os.path.join(HERE, "orders", "platform.db")

# ---- cloudflared 隧道健康（只看进程不等于隧道健康，需连接级判定）----
CF_METRICS_URL = "http://127.0.0.1:20241/ready"  # cloudflared --metrics 的 /ready：200=已有边缘连接
CF_RESTART_WINDOW = 1800    # 30 分钟：隧道重启风暴判定窗口
CF_RESTART_LIMIT = 5        # 窗口内最多重启 5 次，超限进入 degraded 停止自动重启
CF_STATUS_EVERY = 20        # 每 20 轮（约 10 分钟）打一次隧道状态摘要
CF_PROBE_EVERY = 20         # 每 20 轮做一次关键 hostname 可达性探测（仅记录，绝不触发重启）


def log(msg):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    try:
        if os.path.exists(LOG) and os.path.getsize(LOG) > 512 * 1024:
            open(LOG, "w", encoding="utf-8").close()
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def platform_ok():
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=5) as r:
            return r.status == 200
    except Exception:
        return False


def listener_pid(port):
    """找到占用端口的 PID（用于重启前清理僵死实例），没有则返回 None"""
    try:
        r = subprocess.run(["netstat", "-ano"], capture_output=True, text=True,
                           errors="replace",
                           creationflags=CREATE_NO_WINDOW, timeout=15)
        for ln in (r.stdout or "").splitlines():
            parts = ln.split()
            if len(parts) >= 5 and parts[3] == "LISTENING" and parts[1].endswith(f":{port}"):
                return int(parts[4])
    except Exception:
        pass
    return None


def _pid_alive(pid):
    if not pid:
        return False
    try:
        import ctypes
        h = ctypes.windll.kernel32.OpenProcess(0x00100000, False, int(pid))  # SYNCHRONIZE
        if not h:
            return False
        ctypes.windll.kernel32.CloseHandle(h)
        return True
    except Exception:
        return False


# ---------------- 单实例锁：改用 OS 级排他锁 ----------------
# 旧实现的两个致命缺陷（2026-09-14 实测导致 4 个看护实例并存）：
#   1) O_EXCL 创建失败时会 remove() 掉持锁者的锁文件 —— 并发启动时"败者删胜者的锁"，
#      双方随后都能创建成功，于是双双进入主循环；
#   2) 依赖 pid+心跳时间的启发式判活，进程若阻塞超过 TTL 就被判定为陈旧锁，可被顶掉。
# 现改为 msvcrt 字节区间排他锁：由 OS 持有，进程退出（含崩溃）自动释放，不存在僵尸持锁；
# 且永不删除锁文件（文件本身不是锁，删它会重新引入竞态）。
_lock_fh = None          # 持有 OS 锁的文件句柄；进程存活期间必须保持打开


def _write_info():
    """把 pid/心跳写入「信息文件」。该文件不加锁，任何工具都能直接读，便于诊断。
    （曾把信息与锁放同一文件，结果 Python 带缓冲的 read 会跨过锁区而报 EACCES。）"""
    try:
        with open(LOCK_FILE, "w", encoding="utf-8") as f:
            f.write(f"pid={os.getpid()}\nts={time.time()}\n"
                    f"start={time.strftime('%Y-%m-%d %H:%M:%S')}\n")
    except Exception:
        pass


def _try_os_lock():
    """尝试取得 OS 级排他锁。True=已取得，False=已被其它看护实例持有"""
    global _lock_fh
    try:
        fh = open(OSLOCK_FILE, "a+b")
    except Exception as e:
        log(f"  [锁] 打开锁载体失败: {type(e).__name__}: {e}")
        return False
    try:
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)   # 非阻塞独占第 1 字节
    except OSError as e:
        try:
            fh.close()
        except Exception:
            pass
        log(f"  [锁] 已被其它实例持有 (errno={e.errno})")
        return False
    except Exception as e:
        try:
            fh.close()
        except Exception:
            pass
        log(f"  [锁] 加锁异常: {type(e).__name__}: {e}")
        return False
    _lock_fh = fh                        # 全程持有，不可关闭
    try:
        fh.write(b"1")
        fh.flush()
    except Exception:
        pass
    _write_info()
    return True


def acquire_lock():
    for i in range(6):
        if _try_os_lock():
            return True
        log(f"  [锁] 第 {i + 1}/6 次尝试未取得，1 秒后重试")
        time.sleep(1.0)
    return False


def renew_lock():
    """刷新信息文件里的心跳（锁本身由 OS 持有，不依赖心跳判活）"""
    _write_info()


def release_lock():
    global _lock_fh
    _lock_fh = _release_handle(_lock_fh)
    # 故意不删除文件：OS 锁已由句柄关闭释放，删文件只会制造新的竞态窗口


def _release_handle(fh):
    try:
        if fh and not fh.closed:
            try:
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            except Exception:
                pass
            fh.close()
    except Exception:
        pass
    return None


def _last_backup_ts():
    """读取 settings.last_backup（与平台 housekeeping 共用，避免每日双份备份）"""
    try:
        with sqlite3.connect(DB_PATH, timeout=10) as c:
            r = c.execute("SELECT value FROM settings WHERE key='last_backup'").fetchone()
        return float(r[0]) if r and r[0] else 0.0
    except Exception:
        return 0.0


def _set_last_backup():
    try:
        with sqlite3.connect(DB_PATH, timeout=15) as c:
            c.execute("INSERT INTO settings(key,value) VALUES('last_backup',(?)) "
                      "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(time.time()),))
    except Exception:
        pass


def start_platform():
    # 平台用 pythonw（无控制台，免疫控制台关闭事件）；stdout/stderr 落盘留尸检线索
    out_log = open(os.path.join(HERE, "cf", "platform_stdout.log"), "ab")
    pyw = PYEXE.replace("Scripts\\python.exe", "Scripts\\pythonw.exe")
    exe = pyw if os.path.exists(pyw) else PYEXE
    return subprocess.Popen([exe, PLATFORM], cwd=HERE, env=os.environ.copy(),
                            creationflags=CREATE_NO_WINDOW | DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
                            stdout=out_log, stderr=subprocess.STDOUT)


def cloudflared_running():
    try:
        r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq cloudflared.exe"],
                           capture_output=True, text=True, errors="replace",
                           creationflags=CREATE_NO_WINDOW, timeout=15)
        return "cloudflared.exe" in (r.stdout or "")
    except Exception:
        return False


def start_cloudflared():
    env = os.environ.copy()
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        env.pop(k, None)
    env["NO_PROXY"] = "*"
    lf = open(CFLOG, "ab")
    # --metrics 暴露本机只读指标口，供 tunnel_healthy() 做连接级判定
    return subprocess.Popen([CFD, "tunnel", "--config", CFG, "--no-autoupdate",
                             "--metrics", "127.0.0.1:20241", "run", "wk-platform"],
                            stdout=lf, stderr=subprocess.STDOUT, env=env,
                            creationflags=CREATE_NO_WINDOW | DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP)


# ---------------- 隧道连接级健康判定（进程存在 != 隧道可用） ----------------
def cf_metrics_ready():
    """cloudflared 指标口 /ready。True/False=拿到明确结论；None=指标口不可用（老进程），需日志兜底"""
    try:
        with urllib.request.urlopen(CF_METRICS_URL, timeout=4) as r:
            return r.status == 200
    except Exception:
        return None


def cf_log_last_connection():
    """从 cloudflared.log 尾部（64KB）解析最近一次成功连接/断开的时间戳"""
    try:
        if not os.path.exists(CFLOG):
            return None, None
        size = os.path.getsize(CFLOG)
        with open(CFLOG, "rb") as f:
            f.seek(max(0, size - 65536))
            data = f.read().decode("utf-8", "replace")
        last_ok = last_bad = None
        for ln in data.splitlines():
            if "Registered tunnel connection" in ln:
                last_ok = ln[:19]
            elif "Connection terminated" in ln or "Failed to dial" in ln:
                last_bad = ln[:19]
        return last_ok, last_bad
    except Exception:
        return None, None


def tunnel_healthy():
    """返回 (healthy: bool, reason: str)。三层判定：进程 -> 指标口 -> 日志兜底"""
    if not cloudflared_running():
        return False, "进程不存在"
    m = cf_metrics_ready()
    if m is True:
        return True, "metrics/ready=200"
    if m is False:
        return False, "metrics/ready 非 200"
    # 指标口不可用（历史进程未带 --metrics）→ 用日志兜底
    ok, bad = cf_log_last_connection()
    if ok and (not bad or ok >= bad):
        return True, f"log 最近连接 {ok}"
    if ok:
        return False, f"log 已断开(ok={ok} bad={bad})"
    return True, "无日志证据，按进程存在处理"


def cf_hostnames():
    """从隧道 ingress 配置里取出对外 hostname（用于可达性探测）"""
    try:
        hs = []
        with open(CFG, encoding="utf-8") as f:
            for ln in f:
                s = ln.strip()
                if s.startswith("- hostname:"):
                    hs.append(s.split(":", 1)[1].strip())
        return hs
    except Exception:
        return []


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def cf_probe_hosts():
    """探测关键 hostname 是否可达。任何 HTTP 状态码（含 302/401/403）都算「可达」。
    仅用于记录，绝不触发重启 —— 本机断网时它也会失败，不应误判为隧道故障。"""
    op = urllib.request.build_opener(_NoRedirect)
    out = []
    for h in cf_hostnames():
        try:
            req = urllib.request.Request(f"https://{h}/", headers={"User-Agent": "wk-health/1.0"})
            with op.open(req, timeout=10) as r:
                out.append(f"{h}=HTTP {r.status}")
        except Exception as e:
            code = getattr(e, "code", None)
            out.append(f"{h}=HTTP {code}" if code else f"{h}=不可达({type(e).__name__})")
    return out


def run_cleanup(kill_all=False):
    """调用 tools/cleanup_stale.py 回收游离/僵尸实例。
    复用独立脚本（保留健康平台与锁持有者），避免在此重复维护枚举/判定逻辑。
    防自杀：把自身的 PID 经环境变量 CS_PROTECT_PID 显式传给子进程丙保护，
    不依赖 Get-CimInstance 的 ParentProcessId（实测在快速启停竞态下会错报）。"""
    script = os.path.join(HERE, "tools", "cleanup_stale.py")
    if not os.path.exists(script):
        print(f"[cleanup] 未找到 {script}，请确认工具脚本存在")
        return
    env = os.environ.copy()
    env["CS_PROTECT_PID"] = str(os.getpid())
    args = [PYEXE, script] + (["--kill-all"] if kill_all else [])
    try:
        subprocess.run(args, env=env, creationflags=CREATE_NO_WINDOW, timeout=120)
    except Exception as e:
        print(f"[cleanup] 执行失败: {e}")


def main():
    if len(sys.argv) > 1 and sys.argv[1] in ("clean", "kill-all"):
        kill_all = sys.argv[1] == "kill-all"
        print("回收游离/僵尸实例..." if not kill_all else "回收本项目全部实例（停机模式）...")
        run_cleanup(kill_all)
        print("完成。若看护不在运行，请执行: Start-ScheduledTask -TaskName WK_AutoTaskPlatform")
        return

    if not acquire_lock():
        log("已有其他看护实例持有锁，本实例自动让位退出")
        return

    try:
        log("看护进程启动（锁文件单实例）")
        pf_fails = 0
        cooldown_until = 0.0
        restarts = []           # 平台重启时间戳列表
        breaker_logged = False
        cf_cooldown = 0.0
        bk_cooldown = 0.0
        # ---- 隧道看护状态 ----
        cf_fails = 0            # 连续失败计数
        cf_restarts = []        # 隧道重启时间戳列表（窗口内）
        cf_degraded = False     # 熔断标志：连续重启仍不恢复则停止自动重启
        cf_last_restart = 0.0   # 最近一次重启时间
        cf_round = 0            # 轮次计数，用于周期性状态摘要与可达性探测

        while True:
            time.sleep(CHECK_INTERVAL)
            renew_lock()
            now = time.time()
            try:
                # ---- 平台 ----
                if platform_ok():
                    pf_fails = 0
                else:
                    pf_fails += 1
                    if pf_fails >= 2 and now >= cooldown_until:
                        restarts = [t for t in restarts if now - t < RESTART_WINDOW]
                        if len(restarts) >= RESTART_LIMIT:
                            if not breaker_logged:
                                log("平台短时间内连续重启失败，熔断停止自动重启，需要人工处理")
                                breaker_logged = True
                        else:
                            pid = listener_pid(8766)
                            if pid:
                                try:
                                    subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                                                   capture_output=True, creationflags=CREATE_NO_WINDOW)
                                    time.sleep(2)
                                except Exception:
                                    pass
                            try:
                                start_platform()
                            except Exception as e:
                                log(f"平台拉起失败: {e}")
                            restarts.append(now)
                            cooldown_until = now + COOLDOWN
                            pf_fails = 0
                            log(f"平台不健康，已重启（10 分钟内第 {len(restarts)} 次）")
                if platform_ok():
                    breaker_logged = False  # 恢复健康后解除熔断标记

                # ---- cloudflared（进程 + 隧道连接双重判定）----
                cf_ok, cf_reason = tunnel_healthy()
                if cf_ok:
                    if cf_fails or cf_degraded:
                        log(f"cloudflared 已恢复正常（{cf_reason}）")
                    cf_fails = 0
                    cf_degraded = False
                else:
                    cf_fails += 1
                    log(f"cloudflared 异常({cf_fails}): {cf_reason}")
                    if cf_degraded:
                        pass  # 已熔断：只记录，不再重启，等人工介入/自然恢复
                    elif now >= cf_cooldown:
                        cf_restarts = [t for t in cf_restarts if now - t < CF_RESTART_WINDOW]
                        if len(cf_restarts) >= CF_RESTART_LIMIT:
                            cf_degraded = True
                            log(f"cloudflared {CF_RESTART_WINDOW // 60} 分钟内已重启 "
                                f"{len(cf_restarts)} 次仍未恢复，进入 degraded 停止自动重启。"
                                f"公网入口不可用（本地业务不受影响），需人工检查。")
                        else:
                            if cf_reason == "进程不存在":
                                try:
                                    start_cloudflared()
                                    log("cloudflared 进程缺失，已拉起")
                                except Exception as e:
                                    log(f"cloudflared 拉起失败: {e}")
                            else:
                                # 进程活着但隧道不通：杀掉重建，避免僵死进程占位
                                subprocess.run(["taskkill", "/F", "/IM", "cloudflared.exe"],
                                               capture_output=True, creationflags=CREATE_NO_WINDOW)
                                time.sleep(2)
                                try:
                                    start_cloudflared()
                                    log("cloudflared 隧道不通，进程已重建")
                                except Exception as e:
                                    log(f"cloudflared 拉起失败: {e}")
                            cf_restarts.append(now)
                            cf_last_restart = now
                            cf_cooldown = now + COOLDOWN
                            log(f"cloudflared 重启完成（{CF_RESTART_WINDOW // 60} 分钟内第 "
                                f"{len(cf_restarts)} 次），冷却 {COOLDOWN}s")

                # ---- 隧道状态摘要 + 关键 hostname 可达性（周期打点，便于事后排查）----
                cf_round += 1
                if cf_round % CF_STATUS_EVERY == 0:
                    last_ok, last_bad = cf_log_last_connection()
                    last_rst = (time.strftime("%H:%M:%S", time.localtime(cf_last_restart))
                                if cf_last_restart else "无")
                    log(f"[隧道状态] healthy={cf_ok}({cf_reason}) 连续失败={cf_fails} "
                        f"窗口内重启={len(cf_restarts)} degraded={cf_degraded} "
                        f"最近重启={last_rst} 最近连接={last_ok or '无'} 最近断开={last_bad or '无'}")
                if cf_round % CF_PROBE_EVERY == 0:
                    try:
                        log("[隧道探测] " + " ".join(cf_probe_hosts()))
                    except Exception as pe:
                        log(f"[隧道探测] 失败: {pe}")

                # ---- 每日数据库补备份（平台停机也备份；与平台 housekeeping 共用 last_backup，避免双份）----
                if now >= bk_cooldown and now - _last_backup_ts() > 86400:
                    try:
                        import backup_manager
                        dst, ok, msg = backup_manager.backup_database()
                        if ok:
                            _set_last_backup()
                            log(f"看护补备份成功: {dst}")
                        else:
                            log(f"看护补备份失败: {msg}")
                    except Exception as be:
                        log(f"看护补备份异常: {be}")
                    bk_cooldown = now + 3600  # 无论成败，1 小时内不重试
            except Exception as e:
                log(f"看护单轮异常(已忽略): {type(e).__name__}: {e}")
                continue
    finally:
        release_lock()


if __name__ == "__main__":
    main()