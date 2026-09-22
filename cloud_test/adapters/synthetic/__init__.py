# -*- coding: utf-8 -*-
"""Synthetic (Cloud Test) adapters: no network, no subprocess, no real files.

Every adapter declares ``is_synthetic = True`` and is verified by
``assert_synthetic`` in the factory. Instances are independent (no module-level
singletons) so tests never leak state into each other.
"""
from cloud_test.adapters.synthetic.auth import (
    DEFAULT_REG_CODE,
    SEED_USERS,
    SyntheticAuthAdapter,
)
from cloud_test.adapters.synthetic.course import (
    ACCOUNT_SCENARIOS,
    SCENARIOS as COURSE_SCENARIOS,
    SYNTHETIC_COURSES,
    SyntheticCourseAdapter,
)
from cloud_test.adapters.synthetic.execution import (
    SCENARIO_EXIT_CODES,
    SCENARIOS as EXECUTION_SCENARIOS,
    SyntheticExecutionContext,
    SyntheticExecutionAdapter,
)
from cloud_test.adapters.synthetic.qr import (
    PNG_SIGNATURE,
    SCENARIOS as QR_SCENARIOS,
    SyntheticQrAdapter,
)
from cloud_test.adapters.synthetic.storage import (
    SYNTHETIC_PRODUCTS,
    SYNTHETIC_SETTINGS,
    SyntheticLogSink,
    SyntheticStorageAdapter,
)

__all__ = [
    "ACCOUNT_SCENARIOS",
    "COURSE_SCENARIOS",
    "DEFAULT_REG_CODE",
    "EXECUTION_SCENARIOS",
    "PNG_SIGNATURE",
    "QR_SCENARIOS",
    "SCENARIO_EXIT_CODES",
    "SEED_USERS",
    "SYNTHETIC_COURSES",
    "SYNTHETIC_PRODUCTS",
    "SYNTHETIC_SETTINGS",
    "SyntheticAuthAdapter",
    "SyntheticCourseAdapter",
    "SyntheticExecutionContext",
    "SyntheticExecutionAdapter",
    "SyntheticLogSink",
    "SyntheticQrAdapter",
    "SyntheticStorageAdapter",
]
