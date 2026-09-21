# -*- coding: utf-8 -*-
"""按需清理脚本：终止本项目游离/僵尸 python 实例（人工/诊断时执行）
背景：Windows 环境下多源并发触发（计划任务、开机 VBS/bat）偶尔会产生一个
      卡在解释器早期启动期的 pythonw 僵尸（约 1 线程 / 4MB，命令行含
      order_platform.py 或 health_manager.py，不持有 8766 端口、不参与锁文件）。
      此类僵尸在主程序 main() 之前即挂起，看护与锁文件均无法自愈，需按需清扫。
用法:
    python tools\\cleanup_stale.py            # 清理游离实例（保留 8766 健康平台与锁持有看护）
    python tools\\cleanup_stale.py --kill-all # 清理本项目全部实例（停机/维护用，慎用）
说明:
    - 保留规则：/health 正常 → 保留端口持有者的 order_platform，终止其余；
      /health 不可用 → 终止全部平台实例。
    - 看护进程：终止"非锁持有者"的全部 health_manager 实例（锁文件 cf/health_manager.lock 为准）。
    - 本脚本啮合 README_RUN.md「修复 / 诊断」一节；执行后如看护不在运行，
      请用 Start-ScheduledTask -TaskName WK_AutoTaskPlatform 重新拉起。
"""
import os
import subprocess
import sys
import time

CREATE_NO_WINDOW = 0x08000000
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # D:\web
LOCK_FILE = os.path.join(HERE, "cf", "health_manager.lock")
HEALTH_URL = "http://127.0.0.1:8766/health"
PORT = 8766


def list_project_python():
    """返回 [{pid, cmdline}]：命令行含 order_platform.py / health_manager.py 的 python* 进程。
    优先 PowerShell Get-CimInstance，wmic 全量兜底。"""
    txt = ""
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe' or Name='python.exe'\" "
             "| Select-Object ProcessId,CommandLine | ConvertTo-Csv -NoTypeInformation"],
            capture_output=True, text=True, errors="replace",
            creationflags=CREATE_NO_WINDOW, timeout=30)
        txt = r.stdout or ""
    except Exception:
        txt = ""
    if not txt.strip():
        try:
            r = subprocess.run(["wmic", "process", "get", "ProcessId,CommandLine", "/format:csv"],
                               capture_output=True, text=True, errors="replace",
                               creationflags=CREATE_NO_WINDOW, timeout=20)
            txt = r.stdout or ""
        except Exception:
            txt = ""
    import csv
    import io
    out = []
    try:
        for row in csv.DictReader(io.StringIO(txt)):
            pid = (row.get("ProcessId") or "").strip()
            cmd = (row.get("CommandLine") or "").strip()
            if pid.isdigit() and ("order_platform.py" in cmd or "health_manager.py" in cmd):
                out.append({"pid": int(pid), "cmd": cmd})
    except Exception:
        pass
    return out


def platform_ok():
    try:
        import urllib.request
        with urllib.request.urlopen(HEALTH_URL, timeout=5) as r:
            return r.status == 200
    except Exception:
        return False


def listener_pid(port):
    try:
        r = subprocess.run(["netstat", "-ano"], capture_output=True, text=True,
                           errors="replace", creationflags=CREATE_NO_WINDOW, timeout=15)
        for ln in (r.stdout or "").splitlines():
            parts = ln.split()
            if len(parts) >= 5 and parts[3] == "LISTENING" and parts[1].endswith(f":{port}"):
                return int(parts[4])
    except Exception:
        pass
    return None


def lock_owner_pid():
    try:
        with open(LOCK_FILE, encoding="utf-8") as f:
            for line in f:
                if line.startswith("pid="):
                    return int(line.split("=", 1)[1].strip())
    except Exception:
        pass
    return None


def kill(pid, tree=False):
    # 默认单进程终止（无 /T）：实测发现僵尸实例往往是活跃实例的"启动器父进程"
    # （僵尸 pythonw 在解释器早期衍生出真实进程后自身挂起驻留），若默认树杀会连坐
    # 杀掉被它托起的活跃平台/看护实例（2026-09-14 clean 误杀即源于此）。
    # tree=True 仅用于 --kill-all（停机语义：彻底清场）。
    args = ["taskkill", "/F"] + (["/T"] if tree else []) + ["/PID", str(pid)]
    try:
        subprocess.run(args, capture_output=True, creationflags=CREATE_NO_WINDOW, timeout=15)
        print(f"  终止 PID {pid}" + ("(树)" if tree else ""))
    except Exception as e:
        print(f"  终止 PID {pid} 失败: {e}")


def main():
    kill_all = "--kill-all" in sys.argv
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[cleanup_stale] {now} 开始扫描本项目 python 实例")

    procs = list_project_python()
    if not procs:
        print("未发现本项目实例")
        return
    seen = set(p["pid"] for p in procs)
    # 预取"父进程映射"（python.exe/pythonw.exe 互相构成启动器父子对）
    alive_parents = {}
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe' or Name='python.exe'\" "
             "| Select-Object ProcessId,ParentProcessId | ConvertTo-Csv -NoTypeInformation"],
            capture_output=True, text=True, errors="replace",
            creationflags=CREATE_NO_WINDOW, timeout=30)
        import csv, io
        for row in csv.DictReader(io.StringIO(r.stdout or "")):
            pid = (row.get("ProcessId") or "").strip()
            pp = (row.get("ParentProcessId") or "").strip()
            if pid.isdigit() and pp.isdigit():
                alive_parents[int(pid)] = int(pp)
    except Exception:
        alive_parents = {}
    for p in procs:
        print(f"  发现 PID {p['pid']}: ...\\{os.path.basename(p['cmd'].strip('\"'))}")

    platform_hold = None
    if not kill_all:
        if platform_ok():
            platform_hold = listener_pid(PORT)
            print(f"  /health 正常，平台保留端口持有者 PID {platform_hold}")
        else:
            print("  /health 不可用，将终止全部平台实例（由看护重新拉起）")
    hm_hold = lock_owner_pid() if not kill_all else None
    if hm_hold:
        print(f"  看护保留锁持有者 PID {hm_hold}")

    # 待保留集合：端口持有者 + 锁持有者 + 本脚本自身
    keep = set()
    if platform_hold:
        keep.add(platform_hold)
    if hm_hold:
        keep.add(hm_hold)
    keep.add(os.getpid())

    # 触发者保护（防自杀）：health_manager.py clean/kill-all 是通过再起一个
    # cleanup_stale 子进程实现的（run_cleanup）。调用方（health_manager）会把自己的
    # PID 经 CS_PROTECT_PID 显式传入，这里强制纳入保护，避免子进程把触发它的父进程
    # 误判为"游离 health_manager 实例"而杀掉（2026-09-14 实测：Get-CimInstance 的
    # ParentProcessId 在快速启停竞态下会错报，不能依赖进程树推断）。
    try:
        parent_pid = int(os.environ.get("CS_PROTECT_PID") or "0")
    except Exception:
        parent_pid = 0
    if parent_pid:
        keep.add(parent_pid)

    # 二次保护：保留实例的整条父链一并保护。
    # 实测 2026-09-14：本项目 python 以「启动器父(1T) + 活跃子」成对存在，
    # 若杀父（即使不用 /T）活跃子也会随之退出 → 曾误杀健康平台/看护。
    # 因此凡属保留实例祖先的项目内进程一律视为保护对象。
    protect = set(keep)
    changed = True
    while changed:
        changed = False
        for pid in list(protect):
            pp = alive_parents.get(pid)
            if pp and pp in seen and pp not in protect:
                protect.add(pp)
                changed = True
    protected_extra = protect - keep
    if protected_extra:
        print(f"  父链保护（不杀，作为保留实例的启动器）：{sorted(protected_extra)}")

    for p in procs:
        pid = p["pid"]
        if pid in protect:
            print(f"  保留 PID {pid}（保留链）")
            continue
        # 仅"真孤儿"（不属保留链）才回收；单进程杀，不树杀
        kill(pid, tree=kill_all)

    print("完成。若看护不在运行，请执行: Start-ScheduledTask -TaskName WK_AutoTaskPlatform")


if __name__ == "__main__":
    main()