# -*- coding: utf-8 -*-
"""Immutable Cloud Test Runtime identity.

Metadata only — never contains environment variable dumps, PATH, HOME,
cookies, credentials or process arguments.
"""
from __future__ import annotations

import platform as _platform
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

ENVIRONMENT = "cloud-test"

__all__ = ["RuntimeIdentity", "build_identity", "utc_now_iso"]


def utc_now_iso() -> str:
    """Canonical internal timestamps are UTC ISO-8601, e.g. 2026-09-22T04:00:00Z."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class RuntimeIdentity:
    environment: str
    runner_id: str
    runtime_id: str
    git_sha: str
    started_at: str
    python_version: str
    platform: str

    def __post_init__(self):
        # environment is pinned; it cannot drift to production/prod/local.
        if self.environment != ENVIRONMENT:
            raise ValueError(
                "RuntimeIdentity.environment must be "
                f"'{ENVIRONMENT}', got {self.environment!r}"
            )
        if not self.runtime_id:
            raise ValueError("RuntimeIdentity.runtime_id must be non-empty")

    def to_dict(self) -> dict:
        """Structured metadata safe for logging/audit (no secrets, no env dump)."""
        return asdict(self)


def build_identity(
    runner_id: str = "",
    runtime_id: str = "",
    git_sha: str = "",
) -> RuntimeIdentity:
    """Build a RuntimeIdentity from explicit values (tests may inject).

    An empty runtime_id is auto-generated (one unique id per identity build).
    """
    from cloud_test.config import get_runtime_id

    return RuntimeIdentity(
        environment=ENVIRONMENT,
        runner_id=runner_id,
        runtime_id=runtime_id or get_runtime_id(),
        git_sha=git_sha,
        started_at=utc_now_iso(),
        python_version=(
            f"{sys.version_info.major}.{sys.version_info.minor}"
            f".{sys.version_info.micro}"
        ),
        platform=f"{_platform.system()} {_platform.machine()}",
    )
