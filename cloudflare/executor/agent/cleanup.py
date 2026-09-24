"""§72 agent/cleanup —— 任务收尾清理（plan §110/§115）。

隔离目录在任务结束即删（日志已入 R2）；清理失败不掩盖任务结果。
stage-cloud-35：§115 启动孤儿扫描 —— 按中央任务状态清理残留隔离目录。
"""

from __future__ import annotations

import os
import shutil
import signal
import time
from pathlib import Path
from typing import Any

TERMINAL = ("succeeded", "failed", "canceled")


def delete_task_dir(task_dir: str | Path) -> bool:
    """删除任务隔离目录；不存在返回 False，其余异常交由调用方吞没。"""
    p = Path(task_dir)
    if not p.exists():
        return False
    shutil.rmtree(p, ignore_errors=True)
    return True


def safe_invoke(cleanup_fn, task_id: str) -> None:
    """调用运行时注册的 cleanup 钩子（§110：任务结束删除隔离目录）。"""
    if cleanup_fn is None:
        return
    try:
        cleanup_fn(task_id)
    except Exception:
        pass


def _kill_pidfile(task_dir: Path) -> bool:
    """杀掉目录内 pid.txt 记录的引擎进程（孤儿清理；进程不存在返回 False）。"""
    pidf = task_dir / "pid.txt"
    try:
        pid = int(pidf.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return False
    try:
        os.kill(pid, signal.SIGTERM)
        return True
    except (OSError, ProcessLookupError):
        return False


def decide_cleanup(state: dict[str, Any] | None, executor_id: str, now: float) -> str:
    """§115 决策：state 为中央返回（None=不可达）。

    返回 delete | kill+delete | keep：
    - 中央不可达            → keep（fail-safe，不盲删）
    - 中央无此任务          → delete（纯残留）
    - 终态                  → delete
    - 在跑且是本人且租约有效 → keep（重启恢复场景，保守保留）
    - 在跑但他人/租约过期   → kill+delete（§115 孤儿进程）
    """
    if state is None:
        return "keep"
    if not state.get("found"):
        return "delete"
    status = state.get("status")
    if status in TERMINAL:
        return "delete"
    ours = state.get("executor_id") in (None, "", executor_id)
    lease_ok = state.get("lease_expires_at") is None or state["lease_expires_at"] > now
    if ours and lease_ok:
        return "keep"
    return "kill+delete"


def scan_orphans(client: Any, task_root: str | Path, executor_id: str) -> dict[str, Any]:
    """§115：启动时扫描残留任务目录，按中央状态清理。返回清理报告（不含敏感内容）。"""
    report: dict[str, Any] = {"checked": 0, "deleted": [], "killed": [], "kept": []}
    root = Path(task_root)
    if not root.is_dir():
        return report
    for d in sorted(root.iterdir()):
        if not d.is_dir():
            continue
        report["checked"] += 1
        verdict = decide_cleanup(client.task_state(d.name), executor_id, time.time())
        if verdict == "delete":
            if delete_task_dir(d):
                report["deleted"].append(d.name)
        elif verdict == "kill+delete":
            if _kill_pidfile(d):
                report["killed"].append(d.name)
            delete_task_dir(d)
            report["deleted"].append(d.name)
        else:
            report["kept"].append(d.name)
    return report
