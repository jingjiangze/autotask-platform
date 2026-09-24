# -*- coding: utf-8 -*-
"""QrAdapter contract — covers F08 QR creation / F09 QR status polling.

Declaration only. Local implementation wraps api_qr_start() (order_platform.py:1335),
qr_thread() (:520), qr_img() (:1490), qr_status() (:1496), qr_janitor() (:841).

Cloud implementation is a synthetic state machine (waiting -> scanned ->
confirmed) with a locally generated PNG. Route names and front-end flow stay
identical: /api/qr_start -> /qr/:oid -> /qr_status/:oid -> /api/courses.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from cloud_test.adapters.base import AdapterSurface, QrSession

__all__ = ["QrAdapter"]


@runtime_checkable
class QrAdapter(AdapterSurface, Protocol):
    """QR login capability."""

    def create_session(self, oid: str) -> QrSession:
        """Start a QR session for an order (order row is created in waiting_qr)."""
        ...

    def qr_png(self, oid: str) -> bytes:
        """PNG bytes for the QR image. Unknown/expired session -> NotFoundError."""
        ...

    def get_status(self, oid: str) -> str:
        """Current QR state (QrState.*). Unknown order returns "unknown"
        — matching the local /qr_status contract exactly."""
        ...

    def wait_confirmed(self, oid: str, timeout_s: float) -> bool:
        """Block until confirmed or timeout (local: background qr_thread)."""
        ...

    def cleanup(self) -> int:
        """Drop terminal/stale sessions (local TTL 600s); returns count removed."""
        ...
