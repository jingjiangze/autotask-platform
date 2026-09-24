# -*- coding: utf-8 -*-
"""Side-effect-free configuration reading for the Cloud Test Runtime.

Rules (Commit 05):

- Only ``CLOUD_TEST_MODE=1`` enables cloud-test mode. Every other value
  ("0", "false", "yes", ...) and unset mean disabled.
- Cloud Test Runtime requires ``CLOUD_TEST_EGRESS_REQUIRED=1``. If cloud-test
  mode is enabled but egress-required is not exactly "1", callers must FAIL
  CLOSED. This module never rewrites the value to hide a config error.
- Runner ID is an optional stable identity from the environment. It is never
  derived from hostname / IP / username, and a missing Runner ID does not
  block runtime creation (a per-instance runtime identity is still built).
- Git SHA is audit metadata only, never a security boundary.
"""
from __future__ import annotations

import os
import subprocess
import uuid

__all__ = [
    "CloudTestConfigError",
    "get_egress_required",
    "get_git_sha",
    "get_runner_id",
    "get_runtime_id",
    "is_cloud_test_enabled",
    "require_cloud_test_enabled",
    "require_egress_required",
]


class CloudTestConfigError(RuntimeError):
    """Raised when cloud-test configuration fails validation (fail closed)."""


def is_cloud_test_enabled() -> bool:
    """True only when CLOUD_TEST_MODE is exactly "1"."""
    return os.environ.get("CLOUD_TEST_MODE", "0") == "1"


def require_cloud_test_enabled() -> None:
    if not is_cloud_test_enabled():
        raise CloudTestConfigError(
            "Cloud Test Runtime rejected: CLOUD_TEST_MODE is not enabled"
        )


def get_egress_required() -> bool:
    """True only when CLOUD_TEST_EGRESS_REQUIRED is exactly "1".

    Unset defaults to required ("1"), matching cloud_test_guard.require_ready.
    Any other value counts as NOT required, which callers must treat as a
    fail-closed configuration error.
    """
    return os.environ.get("CLOUD_TEST_EGRESS_REQUIRED", "1") == "1"


def require_egress_required() -> None:
    if not get_egress_required():
        raise CloudTestConfigError(
            "Cloud Test Runtime rejected: "
            "CLOUD_TEST_EGRESS_REQUIRED is not 1 while CLOUD_TEST_MODE is enabled"
        )


def get_runner_id() -> str:
    """Stable runner identity from CLOUD_TEST_RUNNER_ID, or "" if absent.

    Never derived from hostname, IP or username.
    """
    return os.environ.get("CLOUD_TEST_RUNNER_ID", "").strip()


def get_runtime_id() -> str:
    """Unique id for one runtime instance (uuid4, no persistence).

    Distinct concept from Runner ID: runner_id identifies the machine/runner,
    runtime_id identifies a single run instance.
    """
    return str(uuid.uuid4())


def get_git_sha() -> str:
    """Audit-only git SHA: env override, then `git rev-parse HEAD`, else "unknown".

    No arbitrary shell, no full-stdout logging, never raises.
    """
    sha = os.environ.get("CLOUD_TEST_GIT_SHA", "").strip()
    if sha:
        return sha[:40]
    try:
        p = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except Exception:  # noqa: BLE001 — audit metadata must never raise
        return "unknown"
    out = (p.stdout or "").strip()
    if p.returncode == 0 and len(out) == 40 and all(c in "0123456789abcdef" for c in out):
        return out
    return "unknown"
