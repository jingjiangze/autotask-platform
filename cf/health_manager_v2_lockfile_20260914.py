# -*- coding: utf-8 -*-
"""轻量看护进程（唯一看护入口，不与 NSSM/计划任务叠加）
职责:
    1. 保证 order_platform.py 存活且 /health 可用（不健康 → 冷却后重启）
    2. 保证 cloudflared.exe 存活（消失 → 拉起）
    3. 单实例：锁文件(PID+心跳)互斥 + 启动时回收本项目游离/僵尸 pythonw 实例
    4. 防重启风暴：10 分钟内最多重启平台 3 次，超限熔断转人工（只记录不再拉起）

幂等:
    - 锁文件带 PID 与心跳时间戳（O_EXCL 原子创建）。锁陈旧（持有者已死或心跳
      TTL 内无更新，如启动早期挂起的僵尸实例）→ 新实例可接管。
    - 启动取得锁后执行 survey_and_clean()：回收本项目其余 health_manager 实例，
      以及不持有 8766 端口的游离 order_platform 实例（/health 正常时保留端口持有者）。
    因此即使计划任务/手动脚本重复触发，最终全域只有 1 个看护 + 1 个平台。

用法:
    python health_manager.py                    # 看护模式（计划任务调用）
    python health_manager.py clean              # 只清理本项目重复/僵尸实例后退出（不启动服务）

说明: 上一版用 ctypes.windll.GetLastError() 判定互斥体已存在，该判定方法本身不可靠
      （ctypes 未用 use_last_error 时不保证 LastError 来自 CreateMutexW），且不处理
      "持有者启动早期挂起" 的僵尸持久占用，已由本版锁文件方案替代。
"""
import os
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
LOCK_FILE = os.path.join(HERE, "cf", "health_manager.lock")
HEALTH_URL = "http://127.0.0.1:8766/health"

CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200

CHECK_INTERVAL = 30
COOLDOWN = 60           # 每次重启后的冷却期
RESTART_WINDOW = 600    # 重启风暴判定窗口
RESTART_LIMIT = 3       # 窗口内重启上限，超限熔断
LOCK_HEARTBEAT_TTL = 100  # 秒：锁心跳超过该时长视为陈旧锁，允许接管


def log(msg):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    try:
        if os.path.exists(LOG) and os.path.getsize(LOG) > 512 * 1024:
            open(LOG, "w", encoding="utf-8").close()
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _pid_alive(pid):
    """仅凭 PID 判断进程是否存在（存在≠属于我们，调用方确认语义）"""
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


def _taskkill(pid):
    try:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                       capture_output=True, creationflags=CREATE_NO_WINDOW, timeout=15)
    except Exception:
        pass


def _project_pids():
    """枚举本项目 python 进程：返回 {pid: 命令行}（仅含命令行含 order_platform.py 或 health_manager.py 者）。
    优先 PowerShell Get-CimInstance（实测可靠且快），wmic 仅作回退；均失败返回空 dict（best-effort）。"""
    txt = ""
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe' or Name='python.exe'\" "
             "| Select-Object ProcessId,CommandLine | ConvertTo-Csv -NoTypeInformation"],
            capture_output=True, text=True, errors="replace",
            creationflags=CREATE_NO_WINDOW, timeout=30)
        txt = r.stdout or ""
        if txt.strip().count("\n") <= 1:      # 只有表头/空 → 尝试 wmic 全量兜底
            txt = ""
    except Exception:
        txt = ""
    if not txt.strip():
        try:
            r = subprocess.run(
                ["wmic", "process", "get", "ProcessId,CommandLine", "/format:csv"],
                capture_output=True, text=True, errors="replace",
                creationflags=CREATE_NO_WINDOW, timeout=20)
            txt = r.stdout or ""
        except Exception:
            txt = ""
    out = {}
    import csv
    import io
    try:
        for row in csv.DictReader(io.StringIO(txt)):
            pid = (row.get("ProcessId") or "").strip()
            cmd = (row.get("CommandLine") or "").strip()
            if not pid.isdigit() or not cmd:
                continue
            if "order_platform.py" in cmd or "health_manager.py" in cmd:
                out[int(pid)] = cmd
    except Exception:
        pass
    return out


def platform_ok():
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=5) as r:
            return r.status == 200
    except Exception:
        return False


def listener_pid(port):
    """找到占用端口的 PID（用于保留健康平台实例），没有则返回 None"""
    try:
        r = subprocess.run(["netstat", "-ano"], capture_output=True, text=True,
                           creationflags=CREATE_NO_WINDOW, timeout=15)
        for ln in (r.stdout or "").splitlines():
            parts = ln.split()
            if len(parts) >= 5 and parts[3] == "LISTENING" and parts[1].endswith(f":{port}"):
                return int(parts[4])
    except Exception:
        pass
    return None


def survey_and_clean(self_pid=None):
    """回收本项目游离/僵尸实例（不含 self_pid；默认=当前进程）：
    1) 其余 health_manager 实例 → 全部终止；
    2) order_platform 实例 → /health 正常则保留 8766 端口持有者、终止其余；
       /health 不可用（平台已死）则终止全部（由调用方重新拉起）。
    返回 (清理平台数, 清理看护数)。"""
    self_pid = self_pid or os.getpid()
    procs = _project_pids()
    killed_plat = killed_hm = 0
    plat_pids = [p for p, c in procs.items() if "order_platform.py" in c]
    hm_pids = [p for p, c in procs.items() if "health_manager.py" in c and p != self_pid]
    if platform_ok():
        keep = listener_pid(8766)
        for pid in plat_pids:
            if pid != keep:
                _taskkill(pid)
                killed_plat += 1
    else:
        for pid in plat_pids:
            _taskkill(pid)
            killed_plat += 1
    for pid in hm_pids:
        _taskkill(pid)
        killed_hm += 1
    return killed_plat, killed_hm


def start_platform():
    # 平台用 pythonw（无控制台，免疫控制台关闭事件）；stdout/stderr 落盘留尸检线索
    out_log = open(os.path.join(HERE, "cf", "platform_stdout.log"), "ab")
    pyw = PYEXE.replace("Scripts\\python.exe", "Scripts\\pythonw.exe")
    exe = pyw if os.path.exists(pyw) else PYEXE
    return subprocess.Popen([exe, PLATFORM], cwd=HERE, env=os.environ.copy(),
                            creationflags=CREATE_NO_WINDOW | DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
                            stdout=out_log, stderr=subprocess.STDOUT)


# ---------------- 锁文件单实例（含陈旧锁接管） ----------------
def _lock_taken_by_other():
    try:
        pid, ts = 0, 0.0
        with open(LOCK_FILE, encoding="utf-8") as f:
            for line in f:
                key, _, val = line.strip().partition("=")
                if key == "pid":
                    pid = int(val)
                elif key == "ts":
                    ts = float(val)
        if pid and _pid_alive(pid) and (time.time() - ts) < LOCK_HEARTBEAT_TTL:
            return True
    except Exception:
        pass
    return False


def acquire_lock():
    """原子尝试创建锁文件。返回 True=取得锁；False=已有其他活跃看护应退出。"""
    for _ in range(6):
        if _lock_taken_by_other():
            return False
        try:
            fd = os.open(LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.write(fd, f"pid={os.getpid()} ts={time.time()}".encode("utf-8"))
            os.close(fd)
            return True
        except FileExistsError:
            # 陈旧/竞态锁：删除后小退避重试
            try:
                os.remove(LOCK_FILE)
            except OSError:
                pass
            time.sleep(1.0)
        except Exception:
            time.sleep(1.0)
    return False


def renew_lock():
    try:
        fd = os.open(LOCK_FILE, os.O_WRONLY | os.O_TRUNC)
        os.write(fd, f"pid={os.getpid()} ts={time.time()}".encode("utf-8"))
        os.close(fd)
    except Exception:
        pass


def release_lock():
    try:
        if os.path.exists(LOCK_FILE):
            os.remove(LOCK_FILE)
    except Exception:
        pass


def cloudflared_running():
    try:
        r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq cloudflared.exe"],
                           capture_output=True, text=True, creationflags=CREATE_NO_WINDOW, timeout=15)
        return "cloudflared.exe" in (r.stdout or "")
    except Exception:
        return False


def start_cloudflared():
    env = os.environ.copy()
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        env.pop(k, None)
    env["NO_PROXY"] = "*"
    lf = open(CFLOG, "ab")
    return subprocess.Popen([CFD, "tunnel", "--config", CFG, "--no-autoupdate", "run", "wk-platform"],
                            stdout=lf, stderr=subprocess.STDOUT, env=env,
                            creationflags=CREATE_NO_WINDOW | DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP)


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "clean":
        # 人工清理模式：只清本项目重复/僵尸实例，不启动任何服务
        import threading
        plat, hm = survey_and_clean()
        print(f"clean 完成: 终止平台实例 {plat} 个、看护实例 {hm} 个（不含当前进程）")
        log(f"clean 完成: 终止平台实例 {plat} 个、看护实例 {hm} 个")
        return

    if not acquire_lock():
        log("已有其他看护实例持有锁，本实例自动让位退出")
        return

    try:
        log("看护进程启动（锁文件单实例）")
        killed_plat, killed_hm = survey_and_clean()
        if killed_plat or killed_hm:
            log(f"启动收敛: 已终止本项目游离/僵尸实例 平台{killed_plat} 个、看护{killed_hm} 个")

        pf_fails = 1  # 首次探测即按"已失败1次"计：开机/看护启动后平台未就绪则直接拉起，不空等 2 轮
        cooldown_until = 0.0
        restarts = []           # 平台重启时间戳列表
        breaker_logged = False
        cf_cooldown = 0.0

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
                            # 重启前清理全部游离平台实例（含占端口、不占端口的僵尸）
                            _plat, _hm = survey_and_clean()
                            if _plat or _hm:
                                log(f"重启收敛: 终止平台{_plat}个、看护{_hm}个")
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

                # ---- cloudflared ----
                if now >= cf_cooldown:
                    if not cloudflared_running():
                        try:
                            start_cloudflared()
                            cf_cooldown = now + COOLDOWN
                            log("cloudflared 不在运行，已重新拉起")
                        except Exception as e:
                            log(f"cloudflared 拉起失败: {e}")
            except Exception as e:
                # 看护自身绝不因单轮异常而死：记录并继续下一轮
                log(f"看护单轮异常(已忽略): {type(e).__name__}: {e}")
                continue
            finally:
                pass
    finally:
        release_lock()


if __name__ == "__main__":
    main()