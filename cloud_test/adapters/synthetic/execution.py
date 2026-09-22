# -*- coding: utf-8 -*-
"""SyntheticExecutionAdapter — full simulated task lifecycle (F22/F23/F17).

Runs entirely in-process: no subprocess, no network, no threads. Time is
injectable (duration_ms + clock/sleep) so CI can exercise the complete
lifecycle in milliseconds. Progress/heartbeat/log are delivered through
callbacks; persistence is the Runner's business (Commit 17+).

Scenarios (plan §30): SUCCESS / TRANSIENT_FAILURE / PERMANENT_FAILURE /
EXCEPTION / TIMEOUT. Exit-code semantics stay identical to the local adapter.
"""
from __future__ import annotations

import time
from typing import Callable, Dict, List, Optional

from cloud_test.adapters.base import (
    ExecutionResult,
    OrderRecord,
    TaskStatus,
    TransientError,
    ValidationError,
)
from cloud_test.adapters.execution import RISK_CATEGORIES

SCENARIOS = ("SUCCESS", "TRANSIENT_FAILURE", "PERMANENT_FAILURE", "EXCEPTION", "TIMEOUT")

SCENARIO_EXIT_CODES = {
    "SUCCESS": 0,
    "TRANSIENT_FAILURE": -1,
    "PERMANENT_FAILURE": 1,
    "TIMEOUT": -9,
}


class SyntheticExecutionContext:
    """Concrete ExecutionContext driven by callbacks (no persistence)."""

    def __init__(self, task_id: str, attempt: int = 0, runner_id: str = "synthetic-runner",
                 work_ref: str = "", log_ref: str = "",
                 on_heartbeat: Optional[Callable] = None,
                 on_progress: Optional[Callable] = None,
                 on_log: Optional[Callable] = None):
        self.order_id = task_id
        self.task_id = task_id
        self.attempt = attempt
        self.runner_id = runner_id
        self.work_ref = work_ref
        self.log_ref = log_ref
        self.account = ""
        self.on_heartbeat = on_heartbeat
        self.on_progress = on_progress
        self.on_log = on_log
        self.events: List[tuple] = []
        self.heartbeats = 0
        self.progress_state = (0, 0)

    def heartbeat(self) -> None:
        self.heartbeats += 1
        self.events.append(("heartbeat", self.heartbeats))
        if self.on_heartbeat:
            self.on_heartbeat(self)

    def log(self, event: str, message: str = "") -> None:
        self.events.append((event, message))
        if self.on_log:
            self.on_log(event, message)

    def progress(self, done: int, total: int) -> None:
        self.progress_state = (int(done), int(total))
        self.events.append(("progress", self.progress_state))
        if self.on_progress:
            self.on_progress(done, total)


class SyntheticExecutionAdapter:
    name = "synthetic-execution"
    is_synthetic = True

    def __init__(self, scenario: str = "SUCCESS", duration_ms: int = 0, steps: int = 3,
                 clock=time.monotonic, sleep=time.sleep, risk_flags: str = "",
                 exit_code_map=None):
        if scenario not in SCENARIOS:
            raise ValidationError(f"unknown synthetic execution scenario: {scenario!r}")
        self._scenario = scenario
        self._duration_ms = max(0, int(duration_ms))
        self._steps = max(1, int(steps))
        self._clock = clock
        self._sleep = sleep
        self._risk_flags = risk_flags
        #: Optional per-order exit-code override, keyed by order.account.
        #: Lets a caller (e.g. a shared contract harness) drive a specific
        #: outcome per task without changing the adapter's default scenario.
        self._exit_code_map = dict(exit_code_map or {})
        self.runs: List[str] = []

    # -------------------------------------------------------------- interface
    def prepare(self, order: OrderRecord) -> SyntheticExecutionContext:
        ctx = SyntheticExecutionContext(
            task_id=order.id,
            attempt=order.attempt,
            work_ref=f"mem://{order.id}",
            log_ref=f"mem://{order.id}/log",
        )
        ctx.account = order.account
        return ctx

    def execute(self, ctx: SyntheticExecutionContext) -> ExecutionResult:
        self.runs.append(ctx.order_id)
        overridden = self._exit_code_map.get(getattr(ctx, "account", "") or "")
        scenario = self._scenario
        if overridden is not None:
            scenario = {0: "SUCCESS", -9: "TIMEOUT"}.get(overridden, "TRANSIENT_FAILURE"
                                                        if overridden < 0 else "PERMANENT_FAILURE")
        # Coarse step duration: total is spread across steps (0 => no sleeping).
        step_delay = (self._duration_ms / 1000.0 / self._steps) if self._duration_ms else 0.0

        ctx.log("prepare", "synthetic execution prepared")
        ctx.log("start", f"scenario={scenario}")
        for i in range(1, self._steps + 1):
            if step_delay:
                self._sleep(step_delay)
            ctx.heartbeat()
            ctx.progress(i, self._steps)
            ctx.log("step", f"{i}/{self._steps}")

        if scenario == "EXCEPTION":
            ctx.log("error", "synthetic unexpected exception")
            raise TransientError("synthetic execution raised an unexpected exception")

        exit_code = SCENARIO_EXIT_CODES[scenario]
        status = TaskStatus.DONE if exit_code == 0 else TaskStatus.FAILED
        ctx.log("complete" if exit_code == 0 else "failed", f"exit_code={exit_code}")
        return ExecutionResult(exit_code=exit_code, status=status,
                               risk_flags=self._risk_flags, log_ref=ctx.log_ref)

    def cancel(self, ctx: SyntheticExecutionContext) -> None:
        ctx.log("canceled", "synthetic cancel (idempotent)")

    def scan_risk(self, log_text: str) -> str:
        low = (log_text or "").lower()
        keywords = {
            "captcha": ("验证码", "captcha", "滑块", "安全验证"),
            "forbidden": ("403", "forbidden", "无权限", "无权访问"),
            "risk_ctrl": ("风控", "操作频繁", "次数限制", "too many", "封禁"),
            "login_fail": ("登录失败", "用户名或密码", "login failed", "cookie"),
            "network": ("max retries", "connection", "timed out", "超时", "连接失败"),
        }
        hits = [name for name in RISK_CATEGORIES
                if any(k in low for k in keywords.get(name, ()))]
        return ",".join(hits)

    def last_log_ref(self, ctx: SyntheticExecutionContext) -> Optional[str]:
        return ctx.log_ref or None

    # ------------------------------------------------------------------ utils
    @property
    def scenario(self) -> str:
        return self._scenario

    def as_map(self) -> Dict[str, str]:
        return {"name": self.name, "scenario": self._scenario}
