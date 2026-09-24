"""§72 agent/cleanup —— 任务收尾清理（plan §110/§115）。

隔离目录在任务结束即删（日志已入 R2）；清理失败不掩盖任务结果。
"""

from __future__ import annotations

import shutil
from pathlib import Path


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
