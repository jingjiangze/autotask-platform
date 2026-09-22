# -*- coding: utf-8 -*-
"""Adapter contract primitives: error taxonomy, DTOs, status vocabularies.

Declaration only. Every adapter (Local or Cloud) shares these types so one
contract test suite can run against both sides (docs/ADAPTER_CONTRACT.md §2-§4).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

__all__ = [
    "AdapterContractError",
    "AdapterError",
    "AuthError",
    "Course",
    "ExecutionResult",
    "ExecutionTask",
    "HealthStatus",
    "NotFoundError",
    "OrderRecord",
    "PermanentError",
    "Product",
    "QrSession",
    "QrState",
    "RateLimitedError",
    "SessionUser",
    "TaskLogEntry",
    "TaskStatus",
    "TransientError",
    "ValidationError",
    "assert_synthetic",
]


# --------------------------------------------------------------------------
# Error taxonomy — retryable is decided by TYPE, never by string matching.
# Mirrors the frozen local semantics: timeout(-9) / crash(<0) retryable,
# engine business error (>0) not retryable (F25).
# --------------------------------------------------------------------------
class AdapterError(Exception):
    """Base class for all adapter failures."""

    retryable: bool = False
    code: str = "adapter_error"

    def __init__(self, message: str = ""):
        super().__init__(message)
        self.message = message

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"{type(self).__name__}(retryable={self.retryable}, {self.message!r})"


class ValidationError(AdapterError):
    """Invalid input (missing account, empty courses, unsupported platform)."""

    retryable = False
    code = "validation_error"


class AuthError(AdapterError):
    """Authentication failed / session or cookie expired."""

    retryable = False
    code = "auth_error"


class NotFoundError(AdapterError):
    """Resource does not exist (order / QR session / product)."""

    retryable = False
    code = "not_found"


class RateLimitedError(AdapterError):
    """Throttled or temporarily busy — safe to retry after backoff."""

    retryable = True
    code = "rate_limited"


class TransientError(AdapterError):
    """Timeout / crash / network flake — retryable."""

    retryable = True
    code = "transient_error"


class PermanentError(AdapterError):
    """Business refusal — must NOT be retried (engine non-zero exit code)."""

    retryable = False
    code = "permanent_error"


class AdapterContractError(AdapterError):
    """The contract itself is violated (e.g. a cloud adapter is not synthetic)."""

    retryable = False
    code = "adapter_contract_error"


# --------------------------------------------------------------------------
# Status vocabularies (docs/ADAPTER_CONTRACT.md §3)
# --------------------------------------------------------------------------
class TaskStatus:
    """Unified task states. Local legacy values map onto these."""

    PENDING = "PENDING"
    CLAIMED = "CLAIMED"
    RUNNING = "RUNNING"
    DONE = "DONE"
    FAILED = "FAILED"
    RETRY_WAIT = "RETRY_WAIT"
    CANCELED = "CANCELED"

    ALL = (PENDING, CLAIMED, RUNNING, DONE, FAILED, RETRY_WAIT, CANCELED)

    #: legacy orders.status -> unified state (read-only mapping, F-table §3)
    LEGACY_MAP = {
        "pending": PENDING,
        "waiting_qr": PENDING,
        "running": RUNNING,
        "done": DONE,
        "failed": FAILED,
        "canceled": CANCELED,
    }


class QrState:
    """QR session states — must match local /qr_status semantics exactly (F09)."""

    WAITING = "waiting"
    SCANNED = "scanned"
    CONFIRMED = "confirmed"
    EXPIRED = "expired"
    CANCELED = "canceled"
    ERROR = "error"
    UNKNOWN = "unknown"

    ALL = (WAITING, SCANNED, CONFIRMED, EXPIRED, CANCELED, ERROR, UNKNOWN)
    TERMINAL = (CONFIRMED, EXPIRED, CANCELED, ERROR)


# --------------------------------------------------------------------------
# DTOs — frozen, aligned with real business fields, no secrets.
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Product:
    code: str
    name: str
    desc: str = ""
    price: str = ""
    platform: str = ""
    enabled: bool = True
    sort: int = 0


@dataclass(frozen=True)
class Course:
    id: str
    name: str
    kind: str = ""

    def __post_init__(self):
        if not self.id or not self.name:
            raise ValidationError("Course requires non-empty id and name")


@dataclass(frozen=True)
class SessionUser:
    id: int
    username: str
    is_admin: bool = False


@dataclass(frozen=True)
class QrSession:
    oid: str
    state: str = QrState.WAITING
    created_at: str = ""

    def __post_init__(self):
        if self.state not in QrState.ALL:
            raise ValidationError(f"unknown QR state: {self.state!r}")


@dataclass(frozen=True)
class OrderRecord:
    """Mirrors orders table (F-table §2). ``password`` is deliberately absent:
    local secrets stay in the Local adapter behind ``secret_ref``."""

    id: str
    user_id: int
    product: str = ""
    platform: str = ""
    account: str = ""
    courses: str = ""
    status: str = TaskStatus.PENDING
    note: str = ""
    qr_state: str = ""
    created_at: str = ""
    started_at: str = ""
    finished_at: str = ""
    exit_code: int = 0
    risk_flags: str = ""
    attempt: int = 0
    runner_id: str = ""
    lease_id: str = ""


@dataclass(frozen=True)
class ExecutionResult:
    """``exit_code`` semantics (frozen): 0 ok, -9 timeout, <0 crash, >0 business."""

    exit_code: int
    status: str = TaskStatus.DONE
    risk_flags: str = ""
    log_ref: str = ""

    @property
    def retryable(self) -> bool:
        return self.exit_code == -9 or self.exit_code < 0

    def to_error(self) -> AdapterError:
        """Map a non-zero exit code to the unified error taxonomy."""
        if self.exit_code == 0:
            raise ValueError("exit_code 0 is success, not an error")
        if self.retryable:
            return TransientError(f"execution failed with exit_code={self.exit_code}")
        return PermanentError(f"execution failed with exit_code={self.exit_code}")


@dataclass(frozen=True)
class ExecutionTask:
    """Minimal frozen context record (plan §17 / ADAPTER_CONTRACT §5.5).

    Named ``ExecutionTask`` to avoid colliding with the frozen Commit-06
    ``ExecutionContext`` Protocol, which remains the live interface
    (order_id/attempt/heartbeat/log/progress). Password/cookie never appear here.
    """

    task_id: str
    attempt: int = 0
    runner_id: str = ""
    work_ref: str = ""
    log_ref: str = ""


@dataclass(frozen=True)
class TaskLogEntry:
    timestamp: str
    task_id: str
    event: str
    message: str = ""
    runner_id: str = ""
    attempt: int = 0


@dataclass(frozen=True)
class HealthStatus:
    status: str = "ok"
    database: str = "ok"
    queue: str = "ok"
    pending: int = 0
    running: int = 0
    extra: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------
# Shared adapter surface
# --------------------------------------------------------------------------
@runtime_checkable
class AdapterSurface(Protocol):
    """Every adapter exposes identity + provenance for audit."""

    name: str
    is_synthetic: bool


def assert_synthetic(adapter) -> None:
    """Cloud-side guard: a cloud-test adapter must declare itself synthetic.

    Called by cloud runners before any task execution. Raises
    ``AdapterContractError`` (fail closed) — never returns a falsy sentinel.
    """
    if not getattr(adapter, "is_synthetic", False):
        raise AdapterContractError(
            f"adapter {getattr(adapter, 'name', type(adapter).__name__)!r} "
            "is not synthetic; cloud test mode must not use real adapters"
        )
