# -*- coding: utf-8 -*-
"""LocalQrAdapter — wraps the existing QR capability (F08/F09).

Wraps (never rewrites, never exposes the raw registry):
  qr_status()  route logic        (order_platform.py:1496-1503) -> get_status
  qr_img()     route logic        (:1488-1494)                  -> qr_png
  qr_janitor() TTL sweeping       (:841-852)                    -> cleanup
  QR_SESSIONS registry            (:518)                        -> read/write via backend

KNOWN GAP (documented, not faked): session *creation* lives inside the Flask
route api_qr_start (:1333-1358) which performs the real network call and spawns
qr_thread. Extracting it would mean modifying order_platform.py, which this
commit forbids. Therefore ``create_session`` requires an injected
``create_hook`` (production wiring) and raises a clear AdapterContractError
instead of pretending to work.
"""
from __future__ import annotations

import time

from cloud_test.adapters.base import (
    AdapterContractError,
    NotFoundError,
    QrSession,
    QrState,
)
from cloud_test.adapters.local.backend import resolve_backend

__all__ = ["LocalQrAdapter"]


class LocalQrAdapter:
    name = "local-qr"
    is_synthetic = False

    def __init__(self, backend=None, create_hook=None, clock=time.monotonic, sleep=time.sleep):
        self._backend = backend
        self._create_hook = create_hook
        self._clock = clock
        self._sleep = sleep

    @property
    def backend(self):
        if self._backend is not None:
            return self._backend
        return resolve_backend()

    def _sessions(self) -> dict:
        return getattr(self.backend, "QR_SESSIONS", {})

    # -------------------------------------------------------------- interface
    def create_session(self, oid: str) -> QrSession:
        # NOTE: the hook is looked up WITHOUT resolving the backend. Resolving it
        # would import the side-effectful order_platform module (DB init + threads)
        # merely to fail — see backend.py.
        hook = self._create_hook
        if hook is None and self._backend is not None:
            hook = getattr(self._backend, "qr_create_session", None)
        if hook is None:
            raise AdapterContractError(
                "local QR creation is embedded in the Flask route api_qr_start; "
                "inject create_hook (production wiring) or extract it in a later commit"
            )
        result = hook(oid)
        if isinstance(result, QrSession):
            return result
        state = dict(result or {}).get("state", QrState.WAITING)
        return QrSession(oid=oid, state=state)

    def qr_png(self, oid: str) -> bytes:
        st = self._sessions().get(oid)
        img = (st or {}).get("img")
        if not img:
            raise NotFoundError("QR 不存在或已过期")
        return bytes(img)

    def get_status(self, oid: str) -> str:
        """QR session state.

        Prefers the live session registry (QR_SESSIONS, the platform's own source
        of truth, kept in sync with orders.qr_state by qr_thread). Falls back to
        the /qr_status route rule (:1496-1503) when no session is registered —
        including its quirk that any non-"waiting_qr" order reads as "confirmed".
        """
        st = self._sessions().get(oid)
        if st and st.get("state"):
            return st["state"]
        with self.backend.db() as c:
            row = c.execute("SELECT qr_state,status FROM orders WHERE id=?", (oid,)).fetchone()
        if not row:
            return QrState.UNKNOWN
        state = row["qr_state"] if row["status"] == "waiting_qr" else QrState.CONFIRMED
        return state or QrState.UNKNOWN

    def wait_confirmed(self, oid: str, timeout_s: float) -> bool:
        deadline = self._clock() + float(timeout_s)
        while True:
            state = self.get_status(oid)
            if state == QrState.CONFIRMED:
                return True
            if state in (QrState.EXPIRED, QrState.CANCELED, QrState.ERROR):
                return False
            if self._clock() >= deadline:
                return False
            self._sleep(0.05)

    def cleanup(self, ttl_s: float = 600.0) -> int:
        sessions = self._sessions()
        now = self._clock()
        dead = [k for k, v in list(sessions.items())
                if now - v.get("ts", now) > ttl_s
                or v.get("state") in (QrState.CONFIRMED, QrState.EXPIRED,
                                      QrState.CANCELED, QrState.ERROR)]
        for k in dead:
            sessions.pop(k, None)
        return len(dead)
