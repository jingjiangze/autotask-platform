# -*- coding: utf-8 -*-
"""Local adapters: thin wrappers over the existing Windows platform.

Every adapter here is ``is_synthetic = False`` and refuses to resolve the real
backend under CLOUD_TEST_MODE (see backend.py). Tests inject a fake backend so
no test ever imports order_platform, opens orders/platform.db or starts the
platform's background threads.
"""
from cloud_test.adapters.local.auth import LocalAuthAdapter
from cloud_test.adapters.local.backend import LocalBackendUnavailable, resolve_backend
from cloud_test.adapters.local.course import LocalCourseAdapter
from cloud_test.adapters.local.errors import classify_legacy_error
from cloud_test.adapters.local.execution import LocalExecutionContext, LocalExecutionAdapter
from cloud_test.adapters.local.qr import LocalQrAdapter
from cloud_test.adapters.local.storage import LocalLogSink, LocalStorageAdapter

__all__ = [
    "LocalAuthAdapter",
    "LocalBackendUnavailable",
    "LocalCourseAdapter",
    "LocalExecutionContext",
    "LocalExecutionAdapter",
    "LocalLogSink",
    "LocalQrAdapter",
    "LocalStorageAdapter",
    "classify_legacy_error",
    "resolve_backend",
]
