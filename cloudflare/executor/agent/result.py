"""§72 agent/result —— 结果分类与上报（plan §50/§52）。

RETRYABLE_CODES 与 contracts/error-codes.json 的 auto_retryable 保持一致。
"""

from __future__ import annotations

RETRYABLE_CODES = {"NETWORK_TIMEOUT", "NETWORK_ERROR", "EXECUTOR_CRASH", "PROCESS_TIMEOUT"}


def classify_error(e: Exception) -> str:
    """handler 异常 → §50 错误码；超时映射 PROCESS_TIMEOUT，其余 EXECUTOR_CRASH。"""
    return "PROCESS_TIMEOUT" if isinstance(e, TimeoutError) else "EXECUTOR_CRASH"


def is_retryable(code: str) -> bool:
    return code in RETRYABLE_CODES
