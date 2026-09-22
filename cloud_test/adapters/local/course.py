# -*- coding: utf-8 -*-
"""LocalCourseAdapter — wraps query_courses()/_query_courses() (F07).

Wraps (never rewrites): query_courses (order_platform.py:1082),
_query_courses (:1093) — a subprocess call to tools_query_courses.py whose JSON
contract is {"ok":true,"courses":[{"id","name","kind"}]} | {"ok":false,"error"}.

Contract translation performed here (the whole point of the adapter):
  secret_ref  -> the password argument (see SECRET REF RULES below)
  cookie_ref  -> cookie_path argument (zhs_cookie mode)
  (ok, msg)   -> list[Course]  or a typed AdapterError

SECRET REF RULES (docs/ADAPTER_CONTRACT.md §5.3):
  ""                     -> no password (zhs_cookie mode)
  "plain:<password>"     -> literal password (local wiring only)
  "enc:<ciphertext>"     -> decrypted with backend.decrypt_secret (local only)
  anything else          -> ValidationError (opaque refs are not resolvable locally)
A cloud/synthetic adapter must never perform this resolution.
"""
from __future__ import annotations

from typing import List

from cloud_test.adapters.base import Course, ValidationError
from cloud_test.adapters.course import PLATFORMS
from cloud_test.adapters.local.backend import resolve_backend
from cloud_test.adapters.local.errors import classify_legacy_error

__all__ = ["LocalCourseAdapter"]


class LocalCourseAdapter:
    name = "local-course"
    is_synthetic = False

    def __init__(self, backend=None):
        self._backend = backend

    @property
    def backend(self):
        if self._backend is not None:
            return self._backend
        return resolve_backend()

    # ------------------------------------------------------------ translation
    def _resolve_secret(self, secret_ref: str) -> str:
        ref = (secret_ref or "").strip()
        if not ref:
            return ""
        if ref.startswith("plain:"):
            return ref.split(":", 1)[1]
        if ref.startswith("enc:"):
            return self.backend.decrypt_secret(ref.split(":", 1)[1])
        raise ValidationError(
            "unresolvable secret_ref: local adapter accepts 'plain:' or 'enc:' refs only"
        )

    @staticmethod
    def _to_courses(raw_courses) -> List[Course]:
        out = []
        for item in raw_courses or []:
            out.append(Course(id=str(item.get("id", "")), name=str(item.get("name", "")),
                              kind=str(item.get("kind", ""))))
        return out

    # -------------------------------------------------------------- interface
    def get_courses(self, platform: str, account: str = "", secret_ref: str = "",
                    cookie_ref: str = "") -> List[Course]:
        if platform not in PLATFORMS:
            raise ValidationError(f"unsupported platform: {platform!r}")
        if platform in ("chaoxing", "zhs") and not account:
            raise ValidationError("请先填写账号和密码")

        password = self._resolve_secret(secret_ref)
        if platform in ("chaoxing", "zhs") and not password:
            raise ValidationError("请先填写账号和密码")

        ok, res = self.backend.query_courses(
            platform, account, password, cookie_ref or None
        )
        if ok:
            return self._to_courses(res)
        raise classify_legacy_error(res if isinstance(res, str) else str(res))
