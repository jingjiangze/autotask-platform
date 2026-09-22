# -*- coding: utf-8 -*-
"""LocalExecutionAdapter — wraps the real executors (F22/F23/F17).

Wraps (never rewrites): run_chaoxing (order_platform.py:694), run_zhs (:711),
_spawn (:651, 20s heartbeat + timeout tree-kill -> -9), _kill_tree (:262),
scan_risk (:635) with RISK_PATTERNS (:627), build_order_env (:581),
order_dir (:486).

Only this adapter may reach the real executors; cloud adapters must not reuse
it (enforced by is_synthetic=False + cloud_test.runtime).

Local-only realities kept honest:
  * progress() is in-memory: the platform has no progress column.
  * log() records adapter-level events; the task's own output still goes to
    orders/<oid>/log.txt via RollingLog, which the adapter does not touch.
"""
from __future__ import annotations

from typing import List, Optional

from cloud_test.adapters.base import (
    AdapterContractError,
    ExecutionResult,
    OrderRecord,
    TaskStatus,
)
from cloud_test.adapters.local.backend import resolve_backend
from cloud_test.adapters.execution import RISK_CATEGORIES

__all__ = ["LocalExecutionContext", "LocalExecutionAdapter"]


class LocalExecutionContext:
    """Concrete ExecutionContext for local runs (see module docstring)."""

    def __init__(self, order: OrderRecord, backend=None, env=None, work_dir="",
                 log_ref="", runner_id=""):
        self.order = order
        self.order_id = order.id
        self.attempt = order.attempt
        self.runner_id = runner_id
        self.env = env
        self.work_dir = work_dir
        self.log_ref = log_ref
        self.pid = 0
        self.events: List[tuple] = []
        self.heartbeats = 0
        self.progress_state = (0, 0)
        self._backend = backend

    def heartbeat(self) -> None:
        self.heartbeats += 1
        self.events.append(("heartbeat", self.heartbeats))
        if self._backend is None:
            return
        self._backend.set_order(self.order_id, heartbeat_at=self._backend.now_str())

    def log(self, event: str, message: str = "") -> None:
        self.events.append((event, message))

    def progress(self, done: int, total: int) -> None:
        self.progress_state = (int(done), int(total))


class LocalExecutionAdapter:
    name = "local-execution"
    is_synthetic = False

    def __init__(self, backend=None, prepare_hook=None):
        self._backend = backend
        self._prepare_hook = prepare_hook

    @property
    def backend(self):
        if self._backend is not None:
            return self._backend
        return resolve_backend()

    # -------------------------------------------------------------- interface
    def prepare(self, order: OrderRecord) -> LocalExecutionContext:
        if self._prepare_hook is not None:
            return self._prepare_hook(order)
        b = self.backend
        o = dict(order.__dict__)
        env, work, _profile = b.build_order_env(order.id, o)
        return LocalExecutionContext(
            order=order, backend=b, env=env, work_dir=work,
            log_ref=str(b.order_dir(order.id)) + "\\log.txt",
        )

    def execute(self, ctx: LocalExecutionContext) -> ExecutionResult:
        platform = ctx.order.platform
        b = self.backend
        # Mirrors _spawn (:659 heartbeat_at at start, :674 20s refresh): the
        # adapter surfaces the lifecycle it can observe without owning the child.
        ctx.heartbeat()
        ctx.log("start", f"platform={platform} engine=local")
        if platform == "chaoxing":
            rc = b.run_chaoxing(ctx.order_id, {"platform": platform, "account": ctx.order.account,
                                               "courses": ctx.order.courses, "speed": 0}, ctx.env,
                                ctx.work_dir)
        elif platform == "zhs":
            rc = b.run_zhs(ctx.order_id, {"platform": platform, "account": ctx.order.account,
                                          "courses": ctx.order.courses}, ctx.env, ctx.work_dir)
        else:
            raise AdapterContractError(f"unsupported platform for execution: {platform!r}")
        ctx.heartbeat()
        rc = int(rc)
        ctx.log("complete" if rc == 0 else "failed", f"exit_code={rc}")
        status = TaskStatus.DONE if rc == 0 else TaskStatus.FAILED
        return ExecutionResult(
            exit_code=rc,
            status=status,
            risk_flags=self._scan_oid(ctx.order_id),
            log_ref=ctx.log_ref,
        )

    def cancel(self, ctx: LocalExecutionContext) -> None:
        pid = getattr(ctx, "pid", 0)
        if pid:
            self.backend._kill_tree(pid)  # tree kill + exit verification
            ctx.pid = 0

    def scan_risk(self, log_text: str) -> str:
        """Same five frozen categories as the platform (pattern source: backend)."""
        low = (log_text or "").lower()
        patterns = getattr(self.backend, "RISK_PATTERNS", None)
        if patterns is None:
            raise AdapterContractError("backend does not expose RISK_PATTERNS")
        hits = [name for name, kws in patterns if any(k.lower() in low for k in kws)]
        unknown = [h for h in hits if h not in RISK_CATEGORIES]
        if unknown:
            raise AdapterContractError(f"risk categories drifted from contract: {unknown}")
        return ",".join(hits)

    def last_log_ref(self, ctx: LocalExecutionContext) -> Optional[str]:
        return ctx.log_ref or None

    # ------------------------------------------------------------------ utils
    def _scan_oid(self, oid: str) -> str:
        try:
            return self.backend.scan_risk(oid) or ""
        except Exception:  # noqa: BLE001 - risk scan must never break execution
            return ""
