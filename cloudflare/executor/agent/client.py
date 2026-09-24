"""stage-cloud-12 — 中央平台 HTTP 客户端（计划 §34/§38）

仅依赖 Python 标准库（urllib），不引入第三方包。
与中央 Worker 的协议为 autotask.executor/v1（见 cloudflare/worker/src/types/protocol.ts）：

    register       POST /api/executor/v1/register        （bootstrap token，仅首次）
    claim          POST /api/executor/v1/claim           拉取任务（空队列 task=null）
    ack            POST /api/executor/v1/ack             leased -> running
    heartbeat      POST /api/executor/v1/heartbeat       续租（建议每 30s）
    credentials    POST /api/executor/v1/credentials     凭租约解封（§33：payload 永不带凭据）
    complete       POST /api/executor/v1/complete        succeeded|failed|retry_wait
    artifacts      POST /api/executor/v1/artifacts       上传 stdout/日志（raw body）
    node-heartbeat POST /api/executor/v1/node-heartbeat  节点级存活（§45）
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

PROTOCOL = "autotask.executor/v1"
RETRYABLE_CODES = {"NETWORK_TIMEOUT", "NETWORK_ERROR", "EXECUTOR_CRASH", "PROCESS_TIMEOUT"}


class CentralClient:
    """中央平台 HTTP 客户端（线程安全：urllib 每次新建连接）。"""

    def __init__(self, base_url: str, executor_id: str, execution_path: str,
                 token: str | None = None, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.executor_id = executor_id
        self.execution_path = execution_path
        self.token = token
        self.timeout = timeout

    # ---- low level ----
    def _post(self, path: str, payload: dict[str, Any] | None = None,
              raw: bytes | None = None, query: dict[str, str] | None = None,
              content_type: str | None = None) -> tuple[int, Any]:
        url = f"{self.base_url}{path}"
        if query:
            from urllib.parse import urlencode
            url += "?" + urlencode(query)
        headers = {"Content-Type": content_type or "application/json",
                   "User-Agent": "autotask-executor/1.0"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        body = raw if raw is not None else json.dumps(payload or {}).encode()
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = resp.read()
                return resp.status, json.loads(data) if data else {}
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read())
            except Exception:
                return e.code, {}

    # ---- protocol ----
    def register(self, bootstrap_token: str, name: str, version: str,
                 capabilities: list[str]) -> dict[str, Any]:
        """仅首次：用 bootstrap token 换取 executor_token（仅显示一次，调用方负责持久化）。"""
        old = self.token
        self.token = bootstrap_token
        try:
            status, body = self._post("/api/executor/v1/register", {
                "executor_id": self.executor_id,
                "execution_path": self.execution_path,
                "name": name,
                "version": version,
                "capabilities": capabilities,
            })
        finally:
            self.token = old
        if status != 201:
            raise RuntimeError(f"register failed: {status} {body}")
        return body

    def claim(self, capabilities: list[str]) -> dict[str, Any] | None:
        status, body = self._post("/api/executor/v1/claim", {
            "executor_id": self.executor_id,
            "execution_path": self.execution_path,
            "capabilities": capabilities,
        })
        if status != 200:
            raise RuntimeError(f"claim failed: {status} {body}")
        return body.get("task")

    def ack(self, task_id: str, lease_id: str) -> dict[str, Any]:
        return self._checked(self._post("/api/executor/v1/ack", self._ctx(task_id, lease_id)))

    def heartbeat(self, task_id: str, lease_id: str) -> dict[str, Any]:
        return self._checked(self._post("/api/executor/v1/heartbeat", self._ctx(task_id, lease_id)))

    def complete(self, task_id: str, lease_id: str, outcome: str,
                 error_code: str | None = None) -> dict[str, Any]:
        payload = self._ctx(task_id, lease_id) | {"outcome": outcome}
        if error_code:
            payload["error_code"] = error_code
        return self._checked(self._post("/api/executor/v1/complete", payload))

    def release_credentials(self, task_id: str, lease_id: str) -> list[dict[str, Any]]:
        status, body = self._post("/api/executor/v1/credentials", self._ctx(task_id, lease_id))
        if status != 200:
            raise RuntimeError(f"credential release denied: {status} {body}")
        return body.get("credentials", [])

    def upload_artifact(self, task_id: str, lease_id: str, content: bytes,
                        artifact_type: str = "stdout",
                        content_type: str = "text/plain") -> dict[str, Any]:
        query = {
            "executor_id": self.executor_id,
            "execution_path": self.execution_path,
            "task_id": task_id,
            "lease_id": lease_id,
            "artifact_type": artifact_type,
            "content_type": content_type,
        }
        status, body = self._post("/api/executor/v1/artifacts", raw=content, query=query,
                                  content_type=content_type)
        if status != 201:
            raise RuntimeError(f"artifact upload failed: {status} {body}")
        return body

    def node_heartbeat(self) -> dict[str, Any]:
        status, body = self._post("/api/executor/v1/node-heartbeat", self._ctx("", ""))
        if status != 200:
            raise RuntimeError(f"node heartbeat failed: {status} {body}")
        return body

    # ---- helpers ----
    def _ctx(self, task_id: str, lease_id: str) -> dict[str, Any]:
        return {
            "executor_id": self.executor_id,
            "execution_path": self.execution_path,
            "task_id": task_id,
            "lease_id": lease_id,
        }

    @staticmethod
    def _checked(result: tuple[int, Any]) -> dict[str, Any]:
        status, body = result
        if status != 200:
            raise RuntimeError(f"request failed: {status} {body}")
        return body
