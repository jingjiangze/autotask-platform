"""§72 agent/lease —— 租约生命周期操作（plan §38/§44/§47）。

start/complete/fail/cancel_ack 全部携带 attempt_id 三元 fencing；
错误分类到 outcome 的映射见 result.py。
"""

from __future__ import annotations

from .result import classify_error


class LeaseOps:
    def __init__(self, client):
        self._client = client

    def start(self, task_id: str, lease_id: str, attempt_id: str):
        return self._client.start(task_id, lease_id, attempt_id)

    def heartbeat(self, task_id: str, lease_id: str, attempt_id: str):
        return self._client.heartbeat(task_id, lease_id, attempt_id)

    def complete_success(self, task_id: str, lease_id: str, attempt_id: str):
        return self._client.complete(task_id, lease_id, "succeeded", attempt_id=attempt_id)

    def complete_failure(self, task_id: str, lease_id: str, attempt_id: str, error: Exception):
        code = classify_error(error)
        outcome = "retry_wait" if code in {"NETWORK_TIMEOUT", "NETWORK_ERROR", "EXECUTOR_CRASH", "PROCESS_TIMEOUT"} else "failed"
        return self._client.complete(task_id, lease_id, outcome, code, attempt_id)

    def cancel_ack(self, task_id: str, lease_id: str, attempt_id: str):
        return self._client.cancel_ack(task_id, lease_id, attempt_id)
