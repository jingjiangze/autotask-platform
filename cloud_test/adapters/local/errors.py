# -*- coding: utf-8 -*-
"""Legacy -> unified error classification.

This is the ONLY place where legacy text is inspected. The existing platform
returns ``(ok, "错误串")`` tuples (query_courses) or bare exit codes; the
adapter boundary converts them into the typed taxonomy once. Everything above
the adapter sees types only (docs/ADAPTER_CONTRACT.md §2, F25).
"""
from __future__ import annotations

from cloud_test.adapters.base import (
    AdapterError,
    AuthError,
    PermanentError,
    RateLimitedError,
    TransientError,
    ValidationError,
)

__all__ = ["classify_legacy_error"]

_RATE = ("繁忙", "过于频繁", "请稍后再试", "too many", "rate limit")
_AUTH = ("登录失败", "账号或密码", "用户名或密码", "未查询到课程", "登录态失效", "cookies", "未选课")
_VALIDATION = ("参数不足", "参数", "不支持", "请先填写", "请至少")
_TRANSIENT = ("timeout", "timed out", "超时", "connection", "max retries", "网络", "remote end closed")


def classify_legacy_error(message: str) -> AdapterError:
    """Map a legacy message into the unified taxonomy (never guesses by exit code)."""
    msg = (message or "").strip()
    low = msg.lower()
    if any(k in low for k in _RATE):
        return RateLimitedError(msg)
    if any(k in low for k in _TRANSIENT):
        return TransientError(msg)
    if any(k in low for k in _AUTH):
        return AuthError(msg)
    if any(k in low for k in _VALIDATION):
        return ValidationError(msg)
    return PermanentError(msg or "unknown legacy failure")
