# -*- coding: utf-8 -*-
"""SyntheticCourseAdapter — deterministic synthetic course lists (F07).

Never touches requests/urllib/subprocess/socket: inputs are only used to pick a
scenario. Scenario is selected by an explicit argument or by a synthetic account
sentinel (SYN-*), never by a real account format.
"""
from __future__ import annotations

from typing import List

from cloud_test.adapters.base import (
    AuthError,
    Course,
    RateLimitedError,
    TransientError,
    ValidationError,
)
from cloud_test.adapters.course import PLATFORMS

#: account sentinel -> scenario
ACCOUNT_SCENARIOS = {
    "SYN-AUTH-FAIL": "auth_error",
    "SYN-EMPTY": "empty",
    "SYN-RATE-LIMIT": "rate_limited",
    "SYN-TRANSIENT": "transient_error",
}

SCENARIOS = ("success", "empty", "auth_error", "rate_limited", "transient_error")

SYNTHETIC_COURSES = (
    Course(id="SYN-COURSE-001", name="Synthetic Web Development", kind="知到课"),
    Course(id="SYN-COURSE-002", name="Synthetic Mathematics", kind="共享课"),
    Course(id="SYN-COURSE-003", name="Synthetic Safety Test", kind="知到课"),
)


class SyntheticCourseAdapter:
    name = "synthetic-course"
    is_synthetic = True

    def __init__(self, scenario: str = "success", courses=SYNTHETIC_COURSES):
        if scenario not in SCENARIOS:
            raise ValidationError(f"unknown synthetic course scenario: {scenario!r}")
        self._scenario = scenario
        self._courses = list(courses)
        self.calls: List[tuple] = []  # introspection for contract tests

    def get_courses(self, platform: str, account: str = "", secret_ref: str = "",
                    cookie_ref: str = "") -> List[Course]:
        if platform not in PLATFORMS:
            raise ValidationError(f"unsupported platform: {platform!r}")
        self.calls.append((platform, account, secret_ref, cookie_ref))

        if platform in ("chaoxing", "zhs") and not account:
            raise ValidationError("请先填写账号和密码")

        scenario = ACCOUNT_SCENARIOS.get((account or "").upper(), self._scenario)
        if scenario == "empty":
            return []
        if scenario == "auth_error":
            raise AuthError("登录失败（synthetic）")
        if scenario == "rate_limited":
            raise RateLimitedError("操作过于频繁，请稍后再试（synthetic）")
        if scenario == "transient_error":
            raise TransientError("查询超时（synthetic）")
        return list(self._courses)
