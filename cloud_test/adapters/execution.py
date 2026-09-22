# -*- coding: utf-8 -*-
"""ExecutionAdapter contract — covers F22 execution / F23 heartbeat / F17 risk scan.

Declaration only. Local implementation wraps build_order_env() (order_platform.py:581),
run_chaoxing()/:694, run_zhs()/:711, _spawn()/:651 (20s heartbeat, timeout -> tree
kill -> -9), scan_risk()/:635 with its five frozen risk categories.

Cloud implementation simulates a full run: prepare -> start -> step -> progress
-> heartbeat -> done, over synthetic courses only.
"""
from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable

from cloud_test.adapters.base import (
    AdapterSurface,
    ExecutionResult,
    OrderRecord,
)

#: Frozen risk categories — names must not change (F17).
RISK_CATEGORIES = ("captcha", "forbidden", "risk_ctrl", "login_fail", "network")

__all__ = ["ExecutionContext", "ExecutionAdapter", "RISK_CATEGORIES"]


@runtime_checkable
class ExecutionContext(Protocol):
    """Everything a running task needs. Local: subprocess env + log file +
    order row updates. Cloud: synthetic equivalent."""

    order_id: str
    attempt: int

    def heartbeat(self) -> None:
        """Mark the task alive (local cadence: every 20s)."""
        ...

    def log(self, event: str, message: str = "") -> None:
        """Append a task log entry (F16)."""
        ...

    def progress(self, done: int, total: int) -> None:
        """Report progress for both UI and admin views."""
        ...


@runtime_checkable
class ExecutionAdapter(AdapterSurface, Protocol):
    """Task execution capability.

    exit_code semantics (frozen): 0 ok / -9 timeout (retryable) /
    <0 crash (retryable) / >0 engine business error (NOT retryable).
    """

    def prepare(self, order: OrderRecord) -> ExecutionContext:
        """Build an isolated execution context for one order."""
        ...

    def execute(self, ctx: ExecutionContext) -> ExecutionResult:
        """Run the task to completion. Must never raise for a normal
        non-zero engine exit — return ExecutionResult instead; use
        AdapterError subclasses only for platform-level failures."""
        ...

    def cancel(self, ctx: ExecutionContext) -> None:
        """Stop a running task (local: tree kill; must be idempotent)."""
        ...

    def scan_risk(self, log_text: str) -> str:
        """Return comma-joined RISK_CATEGORIES hits, "" when clean."""
        ...

    def last_log_ref(self, ctx: ExecutionContext) -> Optional[str]:
        """Opaque reference to the produced log for later reading (F16)."""
        ...
