# -*- coding: utf-8 -*-
"""Platform capability adapter contracts (Commit 06 — declaration only).

This package contains **interfaces and data types only**: no implementations,
no network, no IO, no real platform URLs. Local adapters (wrapping the existing
Windows implementation) and Cloud adapters (synthetic/replay) are implemented in
Commit 07 against these contracts.

See docs/ADAPTER_CONTRACT.md for the full specification and
docs/LOCAL_FUNCTIONAL_PARITY.md for the frozen feature baseline (F01-F28).
"""
from cloud_test.adapters.auth import AuthAdapter
from cloud_test.adapters.base import (
    AdapterContractError,
    AdapterError,
    AuthError,
    Course,
    ExecutionResult,
    HealthStatus,
    NotFoundError,
    OrderRecord,
    PermanentError,
    Product,
    QrSession,
    QrState,
    RateLimitedError,
    SessionUser,
    TaskLogEntry,
    TaskStatus,
    TransientError,
    ValidationError,
    assert_synthetic,
)
from cloud_test.adapters.course import CourseAdapter
from cloud_test.adapters.execution import ExecutionAdapter, ExecutionContext
from cloud_test.adapters.qr import QrAdapter
from cloud_test.adapters.storage import LogSink, StorageAdapter

__all__ = [
    "AdapterContractError",
    "AdapterError",
    "AuthAdapter",
    "AuthError",
    "Course",
    "CourseAdapter",
    "ExecutionContext",
    "ExecutionAdapter",
    "ExecutionResult",
    "HealthStatus",
    "LogSink",
    "NotFoundError",
    "OrderRecord",
    "PermanentError",
    "Product",
    "QrAdapter",
    "QrSession",
    "QrState",
    "RateLimitedError",
    "SessionUser",
    "StorageAdapter",
    "TaskLogEntry",
    "TaskStatus",
    "TransientError",
    "ValidationError",
    "assert_synthetic",
]
