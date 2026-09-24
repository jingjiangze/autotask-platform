# -*- coding: utf-8 -*-
"""Cloud Test Runtime Layer (Commit 05).

Boundary only:

    Windows Production Runtime != Cloud Test Runtime

A Cloud Test Runtime is only valid when ALL of the following hold:

    CLOUD_TEST_MODE=1
  + CLOUD_TEST_EGRESS_REQUIRED=1
  + egress guard verified (cloud_test_guard.require_ready)
  + immutable RuntimeIdentity created

Any failing condition is FAIL CLOSED. This layer never "repairs"
configuration: it validates, asserts and identifies. Runner orchestration,
schedulers and task adapters belong to later commits and must depend only on
``is_cloud_test_runtime`` / ``assert_cloud_test_runtime`` / ``runtime_identity``.
"""
from cloud_test.config import (
    CloudTestConfigError,
    get_egress_required,
    get_git_sha,
    get_runner_id,
    get_runtime_id,
    is_cloud_test_enabled,
    require_cloud_test_enabled,
    require_egress_required,
)
from cloud_test.identity import RuntimeIdentity, build_identity
from cloud_test.runtime import (
    CloudTestRuntimeError,
    assert_cloud_test_runtime,
    is_cloud_test_runtime,
    runtime_identity,
)

__all__ = [
    "CloudTestConfigError",
    "CloudTestRuntimeError",
    "RuntimeIdentity",
    "assert_cloud_test_runtime",
    "build_identity",
    "get_egress_required",
    "get_git_sha",
    "get_runner_id",
    "get_runtime_id",
    "is_cloud_test_enabled",
    "is_cloud_test_runtime",
    "require_cloud_test_enabled",
    "require_egress_required",
    "runtime_identity",
]
