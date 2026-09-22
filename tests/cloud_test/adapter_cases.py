# -*- coding: utf-8 -*-
"""Unified contract cases driven against BOTH adapter sides (plan §40).

Each case is written once and executed by test_adapter_contract.py against the
Local bundle (fake in-memory backend, never the real platform) and the Synthetic
bundle. Expectation kinds: "ok" (value shape checked) or "err" (exact exception
class + retryability checked).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Callable, Optional

from cloud_test.adapters import (
    AdapterError,
    ExecutionResult,
    OrderRecord,
    QrState,
    build_local_adapters,
    build_synthetic_adapters,
)
from tests.cloud_test.fake_backend import (
    ACCOUNT_AUTH_FAIL,
    ACCOUNT_EMPTY,
    ACCOUNT_OK,
    ACCOUNT_RATE_LIMIT,
    ACCOUNT_TRANSIENT,
    FAKE_PNG,
    ORDER_RC_BUSINESS,
    ORDER_RC_CRASH,
    ORDER_RC_OK,
    ORDER_RC_TIMEOUT,
    FakeBackend,
)

PNG_SIG = FAKE_PNG[:8]

SIDES = ("local", "synthetic")


class StepClock:
    """Deterministic clock/sleep pair for synthetic adapters (fast CI)."""

    def __init__(self, step: float = 0.05):
        self.now = 0.0
        self.step = step

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:  # noqa: ARG002 - deterministic
        self.now += self.step


def make_sides(work_dir, qr_scenario: str = "confirm", execution_scenario: str = "SUCCESS"):
    """Build a Local bundle (fake backend) and a Synthetic bundle of the same shape."""
    fake = FakeBackend(str(work_dir), qr_scenario=qr_scenario)
    clock = StepClock()
    local = build_local_adapters(backend=fake, qr_create_hook=fake.qr_create_session)
    synthetic = build_synthetic_adapters(
        qr_scenario=qr_scenario,
        execution_scenario=execution_scenario,
        # Per-order exit-code sentinels let one shared case drive both sides.
        execution_exit_code_map={
            ORDER_RC_OK: 0,
            ORDER_RC_TIMEOUT: -9,
            ORDER_RC_CRASH: -1,
            ORDER_RC_BUSINESS: 1,
        },
        clock=clock,
        sleep=clock.sleep,
    )
    return {"local": local, "synthetic": synthetic}, fake


def observe(fn: Callable) -> tuple:
    """Normalise an outcome: ("ok", value) | ("err", exc_name, retryable, code)."""
    try:
        return ("ok", fn())
    except AdapterError as exc:
        return ("err", type(exc).__name__, exc.retryable, exc.code)
    except Exception as exc:  # noqa: BLE001 - surfaced as a hard failure by tests
        return ("unexpected", type(exc).__name__, str(exc)[:120])


def issue_session(auth, user_id: int) -> str:
    """Uniform session minting: local uses the cookie-token scheme, synthetic uuid4."""
    fn = getattr(auth, "create_session", None) or auth.session_token
    return fn(user_id)


def advance_qr(bundle, oid: str) -> str:
    adapter = bundle.qr
    if hasattr(adapter, "advance"):
        return adapter.advance(oid)
    return adapter.backend.qr_advance(oid)


def _order(oid: str, account: str, platform: str = "zhs", attempt: int = 0) -> OrderRecord:
    return OrderRecord(id=oid, user_id=1, product="syn_product", platform=platform,
                       account=account, courses="SYN-COURSE-001", status="PENDING",
                       attempt=attempt)


@dataclass(frozen=True)
class Case:
    name: str
    run: Callable
    expect: str            # "ok" | "err"
    error: Optional[str] = None
    retryable: Optional[bool] = None
    check: Optional[Callable] = None


# ---------------------------------------------------------------- course cases
def _c_success(b):
    return b.course.get_courses("zhs", account=ACCOUNT_OK, secret_ref="plain:pw")


CASES = [
    Case("course_success", _c_success, "ok",
         check=lambda v: len(v) >= 1 and all(hasattr(c, "id") and hasattr(c, "name") for c in v)),
    Case("course_invalid_platform", lambda b: b.course.get_courses("bogus"), "err",
         "ValidationError", False),
    Case("course_missing_account",
         lambda b: b.course.get_courses("chaoxing"), "err", "ValidationError", False),
    Case("course_auth_failure",
         lambda b: b.course.get_courses("zhs", account=ACCOUNT_AUTH_FAIL, secret_ref="plain:pw"),
         "err", "AuthError", False),
    Case("course_empty",
         lambda b: b.course.get_courses("zhs", account=ACCOUNT_EMPTY, secret_ref="plain:pw"),
         "ok", check=lambda v: list(v) == []),
    Case("course_rate_limited",
         lambda b: b.course.get_courses("zhs", account=ACCOUNT_RATE_LIMIT, secret_ref="plain:pw"),
         "err", "RateLimitedError", True),
    Case("course_transient",
         lambda b: b.course.get_courses("zhs", account=ACCOUNT_TRANSIENT, secret_ref="plain:pw"),
         "err", "TransientError", True),
]

# ------------------------------------------------------------------ auth cases
def _register(b, username: str):
    return b.auth.register(b.auth.reg_code, username, "case-pass-123")


CASES += [
    Case("auth_register",
         lambda b: _register(b, f"case-{uuid.uuid4().hex[:8]}"), "ok",
         check=lambda u: bool(u.username) and u.is_admin is False),
    Case("auth_duplicate",
         lambda b: (_register(b, "case-dup-user"), _register(b, "case-dup-user"))[1],
         "err", "ValidationError", False),
    Case("auth_wrong_reg_code",
         lambda b: b.auth.register("WRONG-CODE", f"case-{uuid.uuid4().hex[:8]}", "pw"),
         "err", "ValidationError", False),
    Case("auth_login",
         lambda b: _login_after_register(b), "ok", check=lambda u: u.username.startswith("case-")),
    Case("auth_login_invalid",
         lambda b: _login_invalid(b), "err", "AuthError", False),
    Case("auth_session_roundtrip", lambda b: _session_roundtrip(b), "ok",
         check=lambda v: v == (True, True)),
    Case("auth_session_unknown_token",
         lambda b: b.auth.session_user("not-a-token"), "ok", check=lambda v: v is None),
]


def _login_after_register(b):
    name = f"case-{uuid.uuid4().hex[:8]}"
    _register(b, name)
    return b.auth.login(name, "case-pass-123")


def _login_invalid(b):
    name = f"case-{uuid.uuid4().hex[:8]}"
    _register(b, name)
    return b.auth.login(name, "wrong-password")


def _session_roundtrip(b):
    """Session resolves before logout; logout itself is a no-op returning None.

    (Post-logout invalidation is a synthetic-only property: the local platform's
    logout is stateless cookie deletion, so its token keeps verifying — see
    TestDocumentedDifferences in test_adapter_compatibility.py.)
    """
    name = f"case-{uuid.uuid4().hex[:8]}"
    user = _register(b, name)
    token = issue_session(b.auth, user.id)
    seen = b.auth.session_user(token)
    b.auth.logout(token)
    return (seen is not None and seen.username == name, True)


# -------------------------------------------------------------------- qr cases
def _qr_create(b):
    return b.qr.create_session("SYN-QR-0001")


def _qr_poll_states(b):
    oid = "SYN-QR-0002"
    b.qr.create_session(oid)
    states = [b.qr.get_status(oid)]
    for _ in range(4):
        states.append(advance_qr(b, oid))
    return states


CASES += [
    Case("qr_create", _qr_create, "ok",
         check=lambda s: s.oid == "SYN-QR-0001" and s.state == QrState.WAITING),
    Case("qr_png", lambda b: b.qr.qr_png(_qr_created_oid(b)), "ok",
         check=lambda data: isinstance(data, bytes) and data.startswith(PNG_SIG)),
    Case("qr_poll_flow", _qr_poll_states, "ok",
         check=lambda states: states[0] == QrState.WAITING
         and QrState.SCANNED in states and states[-1] == QrState.CONFIRMED),
    Case("qr_status_unknown", lambda b: b.qr.get_status("SYN-QR-MISSING"), "ok",
         check=lambda v: v == QrState.UNKNOWN),
    Case("qr_png_not_found", lambda b: b.qr.qr_png("SYN-QR-MISSING"), "err", "NotFoundError", False),
]


def _qr_created_oid(b) -> str:
    oid = "SYN-QR-0003"
    b.qr.create_session(oid)
    return oid


# ------------------------------------------------------------- execution cases
def _exec(b, account: str, oid: str) -> ExecutionResult:
    ctx = b.execution.prepare(_order(oid, account))
    return b.execution.execute(ctx)


CASES += [
    Case("execution_success", lambda b: _exec(b, ORDER_RC_OK, "SYN-TASK-OK"), "ok",
         check=lambda r: isinstance(r, ExecutionResult) and r.exit_code == 0 and r.status == "DONE"),
    Case("execution_timeout", lambda b: _exec(b, ORDER_RC_TIMEOUT, "SYN-TASK-T"), "ok",
         check=lambda r: r.exit_code == -9 and r.retryable and r.status == "FAILED"),
    Case("execution_crash", lambda b: _exec(b, ORDER_RC_CRASH, "SYN-TASK-C"), "ok",
         check=lambda r: r.exit_code == -1 and r.retryable),
    Case("execution_business_error", lambda b: _exec(b, ORDER_RC_BUSINESS, "SYN-TASK-B"), "ok",
         check=lambda r: r.exit_code == 1 and r.retryable is False
         and isinstance(r.to_error(), AdapterError) and r.to_error().retryable is False),
    Case("execution_risk_scan",
         lambda b: b.execution.scan_risk("检测到 验证码 与 403 forbidden"), "ok",
         check=lambda v: v == "captcha,forbidden"),
    Case("execution_risk_clean", lambda b: b.execution.scan_risk("一切正常 no signal"), "ok",
         check=lambda v: v == ""),
    Case("execution_heartbeat_progress", lambda b: _exec_observability(b), "ok",
         check=lambda v: v[0] >= 1 and "start" in v[2] and v[3]),
    Case("execution_cancel_idempotent", lambda b: _exec_cancel(b), "ok", check=lambda v: v is None),
]


def _exec_observability(b):
    ctx = b.execution.prepare(_order("SYN-TASK-OBS", ORDER_RC_OK))
    r = b.execution.execute(ctx)
    events = [e[0] for e in ctx.events]
    return (ctx.heartbeats, ctx.progress_state, events, r.exit_code == 0)


def _exec_cancel(b):
    ctx = b.execution.prepare(_order("SYN-TASK-CANCEL", ORDER_RC_OK))
    b.execution.cancel(ctx)
    b.execution.cancel(ctx)  # idempotent
    return None


# --------------------------------------------------------------- storage cases
def _storage_create(b):
    return b.storage.create_order(1, "syn_product", "zhs", ACCOUNT_OK, "plain:pw", "")


CASES += [
    Case("storage_products", lambda b: b.storage.get_products(), "ok",
         check=lambda ps: len(ps) == 3 and all(isinstance(p.code, str) for p in ps)),
    Case("storage_create_read", lambda b: _storage_roundtrip(b), "ok",
         check=lambda v: v[0] == v[1] and v[2] == "PENDING"),
    Case("storage_update", lambda b: b.storage.update_order(_storage_create(b).id, status="DONE").status,
         "ok", check=lambda v: v == "DONE"),
    Case("storage_submit", lambda b: _storage_submit(b), "ok",
         check=lambda v: v[0] == "SYN-COURSE-001" and v[1] == "PENDING"),
    Case("storage_submit_empty", lambda b: b.storage.submit_order(_storage_create(b).id, "   "),
         "err", "ValidationError", False),
    Case("storage_not_found", lambda b: b.storage.get_order("SYN-NOPE-0001"), "err",
         "NotFoundError", False),
    Case("storage_list", lambda b: _storage_list(b), "ok", check=lambda v: v >= 2),
    Case("storage_find_by_prefix", lambda b: _storage_prefix(b), "ok",
         check=lambda v: v[0] == v[1] and v[2] is None),
]


def _storage_roundtrip(b):
    rec = _storage_create(b)
    got = b.storage.get_order(rec.id)
    return (rec.id, got.id, got.status)


def _storage_submit(b):
    rec = _storage_create(b)
    out = b.storage.submit_order(rec.id, "SYN-COURSE-001")
    return (out.courses, out.status)


def _storage_list(b):
    _storage_create(b)
    _storage_create(b)
    return len(b.storage.list_orders(user_id=1, limit=10))


def _storage_prefix(b):
    rec = _storage_create(b)
    hit = b.storage.find_order_by_prefix(rec.id[:8])
    miss = b.storage.find_order_by_prefix("SY")  # too short -> None
    return (rec.id, getattr(hit, "id", None), miss)


CASE_NAMES = [c.name for c in CASES]
