# -*- coding: utf-8 -*-
"""SyntheticQrAdapter — deterministic QR state machine + stdlib-generated PNG.

No HTTP, no online QR service, no sleeps in the default path. The generated
image is a real PNG (usable by the browser preview) but is not required to be
scannable by a phone — F08's UI contract is "QR appears -> state changes".

State machine (must match the local vocabulary exactly, F09):
    waiting -> scanned -> confirmed
    terminal: confirmed / expired / canceled / error
"""
from __future__ import annotations

import hashlib
import struct
import time
import zlib
from typing import Dict

from cloud_test.adapters.base import NotFoundError, QrSession, QrState, ValidationError

SCENARIOS = ("confirm", "expire", "cancel", "error", "stuck")
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _png_bytes(width: int, height: int, seed: str) -> bytes:
    """Minimal deterministic RGB PNG (8-bit truecolor), stdlib only."""
    digest = hashlib.sha256(seed.encode()).digest()

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    rows = bytearray()
    for y in range(height):
        rows.append(0)  # filter type 0
        for x in range(width):
            idx = (y * width + x) % len(digest)
            bit = (digest[idx] >> (x % 8)) & 1
            # 8x8 blocks + a quiet border makes the pattern visible in the UI
            on = bit and 3 <= x < width - 3 and 3 <= y < height - 3
            v = 0 if on else 255
            rows += bytes((v, v, v))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (PNG_SIGNATURE + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(bytes(rows), 9)) + chunk(b"IEND", b""))


class SyntheticQrAdapter:
    name = "synthetic-qr"
    is_synthetic = True

    def __init__(self, scenario: str = "confirm", size: int = 96,
                 clock=time.monotonic, sleep=time.sleep):
        if scenario not in SCENARIOS:
            raise ValidationError(f"unknown synthetic QR scenario: {scenario!r}")
        self._scenario = scenario
        self._size = int(size)
        self._clock = clock
        self._sleep = sleep
        self._sessions: Dict[str, dict] = {}

    # ------------------------------------------------------------------ utils
    def _sess(self, oid: str) -> dict:
        st = self._sessions.get(oid)
        if st is None:
            raise NotFoundError("QR 不存在或已过期")
        return st

    def _next_state(self, oid: str, polls: int) -> str:
        """Pure transition function — the single definition of the state machine."""
        if self._scenario == "confirm":
            if polls <= 1:
                return QrState.WAITING
            if polls == 2:
                return QrState.SCANNED
            return QrState.CONFIRMED
        if self._scenario == "expire":
            return QrState.SCANNED if polls == 1 else QrState.EXPIRED
        if self._scenario == "cancel":
            return QrState.CANCELED
        if self._scenario == "error":
            return QrState.ERROR
        return QrState.WAITING  # stuck

    # -------------------------------------------------------------- interface
    def create_session(self, oid: str) -> QrSession:
        self._sessions[oid] = {"polls": 0, "state": QrState.WAITING, "ts": self._clock(),
                               "oid": oid}
        return QrSession(oid=oid, state=QrState.WAITING)

    def advance(self, oid: str) -> str:
        """Advance the simulated poll clock by one step (deterministic, no sleep)."""
        st = self._sess(oid)
        if st["state"] in QrState.TERMINAL:
            return st["state"]
        st["polls"] += 1
        st["state"] = self._next_state(oid, st["polls"])
        return st["state"]

    def get_status(self, oid: str) -> str:
        st = self._sessions.get(oid)
        return st["state"] if st else QrState.UNKNOWN

    def qr_png(self, oid: str) -> bytes:
        self._sess(oid)
        return _png_bytes(self._size, self._size, f"syn-qr:{oid}")

    def wait_confirmed(self, oid: str, timeout_s: float) -> bool:
        deadline = self._clock() + float(timeout_s)
        while True:
            state = self.advance(oid)
            if state == QrState.CONFIRMED:
                return True
            if state in QrState.TERMINAL:
                return False
            if self._clock() >= deadline:
                return False
            self._sleep(0.01)

    def cleanup(self, ttl_s: float = 600.0) -> int:
        now = self._clock()
        dead = [k for k, v in list(self._sessions.items())
                if now - v.get("ts", now) > ttl_s or v.get("state") in QrState.TERMINAL]
        for k in dead:
            self._sessions.pop(k, None)
        return len(dead)
