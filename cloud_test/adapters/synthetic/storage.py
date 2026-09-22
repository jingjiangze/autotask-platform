# -*- coding: utf-8 -*-
"""SyntheticStorageAdapter + SyntheticLogSink — in-memory repositories.

Never opens orders/platform.db, never writes under orders/, never creates
cookies.json. All identifiers carry a synthetic marker (SYN-/MOCK-/TEST-).
Each instance owns its own store: no module-level singletons (plan §47).
Settings enforce the cloud whitelist (F19): local-only knobs (proxy_pool,
spoof, jitter) are rejected, not silently ignored.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

from cloud_test.adapters.base import (
    NotFoundError,
    OrderRecord,
    Product,
    TaskLogEntry,
    TaskStatus,
    ValidationError,
)
from cloud_test.adapters.storage import CLOUD_SETTINGS, LOCAL_ONLY_SETTINGS

SYNTHETIC_PRODUCTS = (
    Product(code="SYN-PRODUCT-CX-VIDEO", name="Synthetic 学习通 · 视频（合成）",
            desc="仅用于 Cloud Test 流程验证", price="测试免费", platform="chaoxing", sort=1),
    Product(code="SYN-PRODUCT-ZHS-VIDEO", name="Synthetic 知到 · 视频（合成）",
            desc="仅用于 Cloud Test 流程验证", price="测试免费", platform="zhs", sort=2),
    Product(code="SYN-PRODUCT-ZHS-QR", name="Synthetic 知到 · 扫码（合成）",
            desc="仅用于 Cloud Test 流程验证", price="测试免费", platform="zhsqr", sort=3),
)

SYNTHETIC_SETTINGS = {
    "jobs": "2",
    "min_free_mb": "500",
    "log_keep_kb": "300",
    "log_keep_days": "3",
    "concurrency": "2",
    "order_timeout_min": "10",
    "reg_code": "SYN-REG-CODE",
}

SYNTHETIC_PREFIX = "SYN-"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class SyntheticStorageAdapter:
    name = "synthetic-storage"
    is_synthetic = True

    def __init__(self, products=SYNTHETIC_PRODUCTS, settings=None):
        self._products: List[Product] = list(products)
        self._orders: Dict[str, OrderRecord] = {}
        self._settings: Dict[str, str] = dict(settings or SYNTHETIC_SETTINGS)
        self._seq = 0

    # -------------------------------------------------------------- interface
    def get_products(self) -> List[Product]:
        return [p for p in self._products if p.enabled]

    def create_order(self, user_id: int, product: str, platform: str, account: str,
                     secret_ref: str, courses: str = "") -> OrderRecord:
        self._seq += 1
        oid = f"{SYNTHETIC_PREFIX}ORDER-{self._seq:04d}-{uuid.uuid4().hex[:6]}"
        record = OrderRecord(
            id=oid, user_id=int(user_id), product=product, platform=platform,
            account=account or "", courses=courses or "", status=TaskStatus.PENDING,
            created_at=_now(),
        )
        self._orders[oid] = record
        return record

    def get_order(self, oid: str) -> OrderRecord:
        rec = self._orders.get(oid)
        if rec is None:
            raise NotFoundError("订单不存在")
        return rec

    def list_orders(self, user_id: Optional[int] = None, limit: int = 50,
                    offset: int = 0) -> List[OrderRecord]:
        rows = list(self._orders.values())
        if user_id is not None:
            rows = [r for r in rows if r.user_id == int(user_id)]
        rows.sort(key=lambda r: r.created_at, reverse=True)
        return rows[offset:offset + limit]

    def find_order_by_prefix(self, prefix: str) -> Optional[OrderRecord]:
        q = (prefix or "").strip()
        if len(q) < 6:
            return None
        for oid, rec in self._orders.items():
            if oid.startswith(q):
                return rec
        return None

    def update_order(self, oid: str, **fields) -> OrderRecord:
        rec = self.get_order(oid)
        unknown = [k for k in fields if not hasattr(rec, k)]
        if unknown:
            raise ValidationError(f"unknown order fields: {unknown}")
        if "id" in fields or "user_id" in fields:
            raise ValidationError("id and user_id are immutable")
        updated = OrderRecord(**{**rec.__dict__, **fields})
        self._orders[oid] = updated
        return updated

    def submit_order(self, oid: str, courses: str) -> OrderRecord:
        if not (courses or "").strip():
            raise ValidationError("请至少选择一门课程")
        return self.update_order(oid, courses=courses.strip(), status=TaskStatus.PENDING)

    def get_setting(self, key: str, default: str = "") -> str:
        return self._settings.get(key, default)

    def set_setting(self, key: str, value: str) -> None:
        if key in LOCAL_ONLY_SETTINGS:
            raise ValidationError(
                f"setting {key!r} is local-only and not available in cloud test mode"
            )
        if key not in CLOUD_SETTINGS:
            raise ValidationError(f"setting {key!r} is not in the cloud whitelist")
        self._settings[key] = str(value)

    # ------------------------------------------------------------------ utils
    @property
    def order_count(self) -> int:
        return len(self._orders)


class SyntheticLogSink:
    """In-memory task log (F16) — replaces orders/<oid>/log.txt."""

    name = "synthetic-log"
    is_synthetic = True

    def __init__(self):
        self._entries: Dict[str, List[TaskLogEntry]] = {}

    def append(self, entry: TaskLogEntry) -> None:
        self._entries.setdefault(entry.task_id, []).append(entry)

    def read_tail(self, task_id: str, max_bytes: int = 8000) -> str:
        entries = self._entries.get(task_id, [])
        lines = [f"[{e.timestamp}] {e.runner_id or '-'}/{e.event}: {e.message}" for e in entries]
        text = "\n".join(lines)
        data = text.encode("utf-8")[-int(max_bytes):]
        return data.decode("utf-8", errors="replace")

    def list_entries(self, task_id: str, limit: int = 200) -> List[TaskLogEntry]:
        return list(self._entries.get(task_id, []))[-int(limit):]
