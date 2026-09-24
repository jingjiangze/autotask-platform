# -*- coding: utf-8 -*-
"""StorageAdapter + LogSink contracts — covers F01/F05/F06/F10-F16/F27.

Declaration only. Local implementation wraps the SQLite helpers
(db():64, init_db():164, set_order():223, safe_set_order():233,
get_setting():503, set_setting():511) and order_dir()/:486 file layout.
Cloud implementation targets Durable Object SQLite (Commit 21); cloud settings
are whitelisted (F19) and local-only knobs must be reported as unavailable.
"""
from __future__ import annotations

from typing import List, Optional, Protocol, runtime_checkable

from cloud_test.adapters.base import (
    AdapterSurface,
    OrderRecord,
    Product,
    TaskLogEntry,
)

#: Local-only settings that must be shown as "Cloud Test only / Not available"
#: in the cloud admin UI (v3 §38 / F19). Never expose real entries for these.
LOCAL_ONLY_SETTINGS = ("proxy_pool", "spoof", "jitter")

#: Settings that remain meaningful in cloud test mode (whitelist).
CLOUD_SETTINGS = (
    "jobs",
    "min_free_mb",
    "log_keep_kb",
    "log_keep_days",
    "concurrency",
    "order_timeout_min",
    "reg_code",
)

__all__ = ["CLOUD_SETTINGS", "LOCAL_ONLY_SETTINGS", "LogSink", "StorageAdapter"]


@runtime_checkable
class StorageAdapter(AdapterSurface, Protocol):
    """Order / product / settings persistence."""

    def get_products(self) -> List[Product]:
        """Enabled products ordered by sort (F05)."""
        ...

    def create_order(
        self,
        user_id: int,
        product: str,
        platform: str,
        account: str,
        secret_ref: str,
        courses: str = "",
    ) -> OrderRecord:
        """Create an order. ``secret_ref`` is opaque — never a plain password."""
        ...

    def get_order(self, oid: str) -> OrderRecord:
        """Load one order; missing -> NotFoundError."""
        ...

    def list_orders(
        self, user_id: Optional[int] = None, limit: int = 50, offset: int = 0
    ) -> List[OrderRecord]:
        """List orders (user_id=None means all; admin/control-plane only)."""
        ...

    def find_order_by_prefix(self, prefix: str) -> Optional[OrderRecord]:
        """Guest order lookup by full id or first 6-8 chars (F14)."""
        ...

    def update_order(self, oid: str, **fields) -> OrderRecord:
        """Patch an order. Writing a terminal state twice must be harmless
        (local safe_set_order semantics)."""
        ...

    def submit_order(self, oid: str, courses: str) -> OrderRecord:
        """Attach selected courses and move the order to PENDING (F10/F11)."""
        ...

    def get_setting(self, key: str, default: str = "") -> str:
        """Read a setting. Local-only keys must be rejected in cloud mode."""
        ...

    def set_setting(self, key: str, value: str) -> None:
        """Write a setting. Cloud mode accepts CLOUD_SETTINGS only,
        else ValidationError (no silent ignore)."""
        ...


@runtime_checkable
class LogSink(AdapterSurface, Protocol):
    """Task log storage (F16). Local: orders/<oid>/log.txt with RollingLog."""

    def append(self, entry: TaskLogEntry) -> None:
        """Append one structured entry."""
        ...

    def read_tail(self, task_id: str, max_bytes: int = 8000) -> str:
        """Plain-text tail. Default 8000 bytes matches the local order detail
        view (ANSI stripping is the renderer's job, not the sink's)."""
        ...

    def list_entries(self, task_id: str, limit: int = 200) -> List[TaskLogEntry]:
        """Structured read for the cloud UI."""
        ...
