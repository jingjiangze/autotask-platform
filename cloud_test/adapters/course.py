# -*- coding: utf-8 -*-
"""CourseAdapter contract — covers F07 course query (marked TEST ADAPTER).

Declaration only. Local implementation wraps query_courses()/_query_courses()
(order_platform.py:1082/1093, subprocess tools_query_courses.py, JSON
{"ok":true,"courses":[{"id","name","kind"}]} | {"ok":false,"error":...}).

Cloud implementation returns deterministic synthetic courses (SYN-COURSE-00x)
or replay fixtures. Parameter order mirrors the local signature so the same
contract suite can drive both sides.
"""
from __future__ import annotations

from typing import List, Protocol, runtime_checkable

from cloud_test.adapters.base import AdapterSurface, Course

#: Frozen platform vocabulary (F07 / ADAPTER_CONTRACT §5.3).
PLATFORMS = ("chaoxing", "zhs", "zhs_cookie")

__all__ = ["CourseAdapter", "PLATFORMS"]


@runtime_checkable
class CourseAdapter(AdapterSurface, Protocol):
    """Course discovery capability."""

    def get_courses(
        self,
        platform: str,
        account: str = "",
        secret_ref: str = "",
        cookie_ref: str = "",
    ) -> List[Course]:
        """Return the courses available to an account.

        Contract notes:
        - ``platform`` must be one of PLATFORMS, else ValidationError.
        - ``secret_ref`` is an opaque reference to a password (never the
          password itself in cloud mode); ``cookie_ref`` is used for the
          ``zhs_cookie`` post-QR mode.
        - An empty result set is returned as ``[]``. The legacy "未查询到课程"
          string is translated by the Local adapter into AuthError.
        - Wrong credentials -> AuthError; throttling -> RateLimitedError.
        """
        ...
