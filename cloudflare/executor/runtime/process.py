"""引擎子进程树控制（§72 runtime 层；Windows NtSuspend/NtResume 语义，psutil 跨平台封装）。"""

from __future__ import annotations

from typing import Any


def control_tree(proc: Any, suspend: bool) -> bool:
    """挂起（suspend=True）/恢复引擎子进程及其全部后代。幂等：进程已退出返回 False。"""
    if proc is None or proc.poll() is not None:
        return False
    try:
        import psutil
        parent = psutil.Process(proc.pid)
        targets = [parent] + parent.children(recursive=True)
        for p in targets:
            try:
                p.suspend() if suspend else p.resume()
            except psutil.NoSuchProcess:
                continue
        return True
    except Exception:
        return False


def suspend_tree(proc: Any) -> bool:
    return control_tree(proc, suspend=True)


def resume_tree(proc: Any) -> bool:
    return control_tree(proc, suspend=False)
