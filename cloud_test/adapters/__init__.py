# -*- coding: utf-8 -*-
"""Platform capability adapters: contracts + implementations.

- ``base.py`` / ``auth.py`` / ``course.py`` / ``qr.py`` / ``execution.py`` /
  ``storage.py`` hold the **contracts** (Commit 06: Protocols, DTOs, error
  taxonomy, status vocabularies — no IO).
- ``local/`` wraps the existing Windows platform (production only).
- ``synthetic/`` provides in-memory Cloud Test equivalents (no network,
  no subprocess, no real files).
- ``factory.py`` builds bundles and verifies synthetic provenance.

See docs/ADAPTER_CONTRACT.md for the specification and
docs/LOCAL_FUNCTIONAL_PARITY.md for the frozen feature baseline (F01-F28).
"""
from cloud_test.adapters.auth import AuthAdapter
from cloud_test.adapters.base import (
    AdapterContractError,
    AdapterError,
    AuthError,
    Course,
    ExecutionResult,
    ExecutionTask,
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
from cloud_test.adapters.factory import (
    AdapterBundle,
    assert_synthetic_bundle,
    build_local_adapters,
    build_synthetic_adapters,
)
from cloud_test.adapters.execution import ExecutionAdapter, ExecutionContext
from cloud_test.adapters.local import (
    LocalAuthAdapter,
    LocalBackendUnavailable,
    LocalCourseAdapter,
    LocalExecutionContext,
    LocalExecutionAdapter,
    LocalLogSink,
    LocalQrAdapter,
    LocalStorageAdapter,
)
from cloud_test.adapters.qr import QrAdapter
from cloud_test.adapters.storage import LogSink, StorageAdapter
from cloud_test.adapters.synthetic import (
    SyntheticAuthAdapter,
    SyntheticCourseAdapter,
    SyntheticExecutionContext,
    SyntheticExecutionAdapter,
    SyntheticLogSink,
    SyntheticQrAdapter,
    SyntheticStorageAdapter,
)

__all__ = [
    "AdapterBundle",
    "AdapterContractError",
    "AdapterError",
    "AuthAdapter",
    "AuthError",
    "Course",
    "CourseAdapter",
    "ExecutionContext",
    "ExecutionAdapter",
    "ExecutionResult",
    "ExecutionTask",
    "HealthStatus",
    "LogSink",
    "LocalAuthAdapter",
    "LocalBackendUnavailable",
    "LocalCourseAdapter",
    "LocalExecutionContext",
    "LocalExecutionAdapter",
    "LocalLogSink",
    "LocalQrAdapter",
    "LocalStorageAdapter",
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
    "SyntheticAuthAdapter",
    "SyntheticCourseAdapter",
    "SyntheticExecutionContext",
    "SyntheticExecutionAdapter",
    "SyntheticLogSink",
    "SyntheticQrAdapter",
    "SyntheticStorageAdapter",
    "TaskLogEntry",
    "TaskStatus",
    "TransientError",
    "ValidationError",
    "assert_synthetic",
    "assert_synthetic_bundle",
    "build_local_adapters",
    "build_synthetic_adapters",
]
