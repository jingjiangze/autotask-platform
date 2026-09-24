# -*- coding: utf-8 -*-
"""Cloud Test Runtime: validate + assert + identify. Never repair.

assert_cloud_test_runtime() is the hard gate:

    configuration check (CLOUD_TEST_MODE=1)
        -> egress-required check (CLOUD_TEST_EGRESS_REQUIRED=1)
        -> egress guard verification (cloud_test_guard.require_ready)
        -> RuntimeIdentity created

Only when all three layers pass does a Cloud Test Runtime exist. Any failure
raises ``CloudTestRuntimeError`` (a RuntimeError); this module never returns
None/False to hide a rejection and never mutates the environment, firewall or
configuration to make a failing check pass.
"""
from __future__ import annotations

from cloud_test import config
from cloud_test.identity import RuntimeIdentity, build_identity

__all__ = [
    "CloudTestRuntimeError",
    "assert_cloud_test_runtime",
    "is_cloud_test_runtime",
    "runtime_identity",
]


class CloudTestRuntimeError(RuntimeError):
    """Cloud Test Runtime rejected the current process. Never auto-recovered."""


def is_cloud_test_runtime() -> bool:
    """Side-effect-free configuration check.

    True only when cloud-test mode is enabled AND egress is required.
    This does NOT verify the kernel egress guard and installs nothing;
    entering task execution must go through assert_cloud_test_runtime().
    """
    return config.is_cloud_test_enabled() and config.get_egress_required()


def assert_cloud_test_runtime() -> RuntimeIdentity:
    """Hard fail-closed gate. Returns an immutable RuntimeIdentity or raises.

    Order matters: configuration first, then the kernel egress guard, and
    only then is an identity created. If the guard cannot be verified the
    runtime must not proceed into any task execution (Commit 04 contract).
    """
    if not config.is_cloud_test_enabled():
        raise CloudTestRuntimeError(
            "Cloud Test Runtime rejected: CLOUD_TEST_MODE is not enabled"
        )
    if not config.get_egress_required():
        raise CloudTestRuntimeError(
            "Cloud Test Runtime rejected: "
            "CLOUD_TEST_EGRESS_REQUIRED is not 1 while CLOUD_TEST_MODE is enabled"
        )

    from cloud_test_guard import require_ready

    try:
        require_ready()
    except Exception as exc:
        # Re-raise as RuntimeError (fail closed). The original message is
        # static guard text — no tokens, cookies or env dumps pass through.
        raise CloudTestRuntimeError(
            f"Cloud Test Runtime rejected: egress guard is not ready ({exc})"
        ) from exc

    return build_identity(
        runner_id=config.get_runner_id(),
        runtime_id=config.get_runtime_id(),
        git_sha=config.get_git_sha(),
    )


def runtime_identity() -> RuntimeIdentity:
    """Validated runtime identity.

    Re-runs the full assert gate each call: an identity is only ever returned
    for a process whose configuration and egress guard verify *right now*
    (a cached identity could outlive a config drift). Failure raises.
    """
    return assert_cloud_test_runtime()
