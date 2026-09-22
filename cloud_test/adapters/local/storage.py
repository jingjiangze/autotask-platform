# -*- coding: utf-8 -*-
"""LocalStorageAdapter + LocalLogSink — wraps the existing SQLite layer.

Wraps (never rewrites): db()/init_db (order_platform.py:64/164), set_order (:223),
safe_set_order (:233), get_setting (:503), set_setting (:511), now_str (:228),
order_dir (:486), RollingLog (:109) file layout.

LOCAL_PRODUCTION ONLY. Never usable under CLOUD_TEST_MODE (enforced by
local.backend.resolve_backend) and never imported by synthetic tests.
"""
from __future__ import annotations

from typing import List, Optional

from cloud_test.adapters.base import (
    NotFoundError,
    OrderRecord,
    Product,
    TaskLogEntry,
    TaskStatus,
    ValidationError,
)
from cloud_test.adapters.local.backend import resolve_backend

__all__ = ["LocalLogSink", "LocalStorageAdapter"]

_ORDER_FIELDS = ("product", "platform", "account", "courses", "status", "note", "qr_state",
                 "started_at", "finished_at", "exit_code", "risk_flags", "attempt",
                 "worker_running", "pid", "heartbeat_at", "env_profile")

#: unified -> legacy (ADAPTER_CONTRACT §3). Legacy values are accepted as-is.
_UNIFIED_TO_LEGACY = {
    TaskStatus.PENDING: "pending",
    TaskStatus.CLAIMED: "running",
    TaskStatus.RUNNING: "running",
    TaskStatus.DONE: "done",
    TaskStatus.FAILED: "failed",
    TaskStatus.RETRY_WAIT: "pending",
    TaskStatus.CANCELED: "canceled",
}


def _to_unified(status: str) -> str:
    return TaskStatus.LEGACY_MAP.get(status or "", status or "")


def _to_legacy(status: str) -> str:
    return _UNIFIED_TO_LEGACY.get(status, status)


def _row_to_order(row) -> OrderRecord:
    r = dict(row)
    return OrderRecord(
        id=str(r.get("id", "")),
        user_id=int(r.get("user_id") or 0),
        product=r.get("product") or "",
        platform=r.get("platform") or "",
        account=r.get("account") or "",
        courses=r.get("courses") or "",
        status=_to_unified(r.get("status") or ""),
        note=r.get("note") or "",
        qr_state=r.get("qr_state") or "",
        created_at=r.get("created_at") or "",
        started_at=r.get("started_at") or "",
        finished_at=r.get("finished_at") or "",
        exit_code=int(r.get("exit_code") or 0),
        risk_flags=r.get("risk_flags") or "",
        attempt=int(r.get("attempt") or 0),
    )


class LocalStorageAdapter:
    name = "local-storage"
    is_synthetic = False

    def __init__(self, backend=None):
        self._backend = backend

    @property
    def backend(self):
        if self._backend is not None:
            return self._backend
        return resolve_backend()

    # -------------------------------------------------------------- interface
    def get_products(self) -> List[Product]:
        with self.backend.db() as c:
            rows = c.execute("SELECT * FROM products WHERE enabled=1 ORDER BY sort").fetchall()
        return [Product(code=r["code"], name=r["name"], desc=r["desc"] or "",
                        price=r["price"] or "", platform=r["platform"] or "",
                        enabled=bool(r["enabled"]), sort=int(r["sort"] or 0)) for r in rows]

    def create_order(self, user_id: int, product: str, platform: str, account: str,
                     secret_ref: str, courses: str = "") -> OrderRecord:
        b = self.backend
        import uuid as _uuid
        oid = _uuid.uuid4().hex
        stored = self._encode_secret(secret_ref)
        with b.db() as c:
            c.execute("INSERT INTO orders(id,user_id,product,platform,account,password,courses,"
                      "status,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                      (oid, int(user_id), product, platform, account, stored, courses,
                       "pending", b.now_str()))
            row = c.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
        return _row_to_order(row)

    def get_order(self, oid: str) -> OrderRecord:
        with self.backend.db() as c:
            row = c.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
        if not row:
            raise NotFoundError("订单不存在")
        return _row_to_order(row)

    def list_orders(self, user_id: Optional[int] = None, limit: int = 50,
                    offset: int = 0) -> List[OrderRecord]:
        with self.backend.db() as c:
            if user_id is None:
                rows = c.execute("SELECT * FROM orders ORDER BY created_at DESC LIMIT ? OFFSET ?",
                                 (limit, offset)).fetchall()
            else:
                rows = c.execute("SELECT * FROM orders WHERE user_id=? ORDER BY created_at DESC "
                                 "LIMIT ? OFFSET ?", (int(user_id), limit, offset)).fetchall()
        return [_row_to_order(r) for r in rows]

    def find_order_by_prefix(self, prefix: str) -> Optional[OrderRecord]:
        q = (prefix or "").strip().lower()
        if len(q) < 6:
            return None
        with self.backend.db() as c:
            row = c.execute("SELECT * FROM orders WHERE id LIKE ? OR substr(id,1,8)=?",
                            (q + "%", q[:8])).fetchone()
        return _row_to_order(row) if row else None

    def update_order(self, oid: str, **fields) -> OrderRecord:
        unknown = [k for k in fields if k not in _ORDER_FIELDS]
        if unknown:
            raise ValidationError(f"unknown order fields: {unknown}")
        if "status" in fields and fields["status"] is not None:
            fields["status"] = _to_legacy(str(fields["status"]))
        self.backend.safe_set_order(oid, **fields)  # terminal rewrite is harmless
        return self.get_order(oid)

    def submit_order(self, oid: str, courses: str) -> OrderRecord:
        if not (courses or "").strip():
            raise ValidationError("请至少选择一门课程")
        return self.update_order(oid, courses=courses.strip(), status=TaskStatus.PENDING)

    def get_setting(self, key: str, default: str = "") -> str:
        return self.backend.get_setting(key, default)

    def set_setting(self, key: str, value: str) -> None:
        # Local keeps the real knobs (proxy_pool/spoof/jitter are meaningful here).
        self.backend.set_setting(key, value)

    # ------------------------------------------------------------------ utils
    def _encode_secret(self, secret_ref: str) -> str:
        ref = (secret_ref or "").strip()
        if not ref:
            return ""
        if ref.startswith("enc:"):
            return ref.split(":", 1)[1]
        if ref.startswith("plain:"):
            return self.backend.encrypt_secret(ref.split(":", 1)[1])
        raise ValidationError("unresolvable secret_ref for local storage")


class LocalLogSink:
    """Task log over the real orders/<oid>/log.txt layout (F16)."""

    name = "local-log"
    is_synthetic = False

    def __init__(self, backend=None):
        self._backend = backend

    @property
    def backend(self):
        if self._backend is not None:
            return self._backend
        return resolve_backend()

    def _log_path(self, task_id: str) -> str:
        return str(self.backend.order_dir(task_id)) + "\\log.txt"

    def append(self, entry: TaskLogEntry) -> None:
        line = f"[{entry.timestamp}] {entry.event}: {entry.message}\n"
        with open(self._log_path(entry.task_id), "a", encoding="utf-8") as f:
            f.write(line)

    def read_tail(self, task_id: str, max_bytes: int = 8000) -> str:
        import os
        p = self._log_path(task_id)
        if not os.path.exists(p):
            return ""
        with open(p, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - int(max_bytes)))
            return f.read().decode("utf-8", errors="replace")

    def list_entries(self, task_id: str, limit: int = 200) -> List[TaskLogEntry]:
        text = self.read_tail(task_id, max_bytes=1 << 20)
        entries = []
        for line in text.splitlines()[-int(limit):]:
            entries.append(TaskLogEntry(timestamp="", task_id=task_id, event="raw", message=line))
        return entries
