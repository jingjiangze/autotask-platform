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
import time
import urllib.error
import urllib.request
from typing import Any

PROTOCOL = "autotask.executor/v1"
RETRYABLE_CODES = {"NETWORK_TIMEOUT", "NETWORK_ERROR", "EXECUTOR_CRASH", "PROCESS_TIMEOUT"}


class CentralClient:
    """中央平台 HTTP 客户端（线程安全：urllib 每次新建连接）。"""

    def __init__(self, base_url: str, executor_id: str, execution_path: str,
                 token: str | None = None, timeout: float = 30.0,
                 version: str = "1.0.0", capacity: int = 1, active_tasks: int = 0):
        self.base_url = base_url.rstrip("/")
        self.executor_id = executor_id
        self.execution_path = execution_path
        self.token = token
        self.timeout = timeout
        # §34/§41：pull/node-heartbeat 附带的上报字段
        self.version = version
        self.capacity = capacity
        self.active_tasks = active_tasks

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
        """§34 pull：上报 version/capacity/active_tasks；响应 task.dispatch 含 attempt_id（§32）。"""
        status, body = self._post("/api/executor/v1/pull", {
            "executor_id": self.executor_id,
            "execution_path": self.execution_path,
            "version": self.version,
            "capacity": self.capacity,
            "active_tasks": self.active_tasks,
            "capabilities": capabilities,
        })
        if status != 200:
            raise RuntimeError(f"pull failed: {status} {body}")
        return body.get("task")

    def start(self, task_id: str, lease_id: str, attempt_id: str) -> dict[str, Any]:
        """§38 start（原 ack 别名）：leased -> running；attempt_id 三元 fencing（§47）。"""
        return self._checked(self._post(
            f"/api/executor/v1/tasks/{task_id}/start",
            self._ctx(task_id, lease_id) | {"attempt_id": attempt_id},
        ))

    def heartbeat(self, task_id: str, lease_id: str, attempt_id: str = "") -> dict[str, Any]:
        payload = self._ctx(task_id, lease_id)
        if attempt_id:
            payload["attempt_id"] = attempt_id
        return self._checked(self._post("/api/executor/v1/heartbeat", payload))

    def complete(self, task_id: str, lease_id: str, outcome: str,
                 error_code: str | None = None, attempt_id: str = "") -> dict[str, Any]:
        payload = self._ctx(task_id, lease_id) | {"outcome": outcome}
        if error_code:
            payload["error_code"] = error_code
        if attempt_id:
            payload["attempt_id"] = attempt_id
        return self._checked(self._post("/api/executor/v1/complete", payload))

    def fail(self, task_id: str, lease_id: str, error_code: str,
             outcome: str = "failed", attempt_id: str = "") -> dict[str, Any]:
        """§38 fail：失败专用形（error_code 必填）。"""
        payload = self._ctx(task_id, lease_id) | {"outcome": outcome, "error_code": error_code}
        if attempt_id:
            payload["attempt_id"] = attempt_id
        return self._checked(self._post("/api/executor/v1/fail", payload))

    def cancel_ack(self, task_id: str, lease_id: str, attempt_id: str = "") -> dict[str, Any]:
        """§38 cancel-ack：确认停止任务（running→cancel_requested→canceled）。"""
        payload = self._ctx(task_id, lease_id)
        if attempt_id:
            payload["attempt_id"] = attempt_id
        return self._checked(self._post("/api/executor/v1/cancel-ack", payload))

    def bootstrap(self, task_id: str, lease_id: str, attempt_id: str = "") -> dict[str, Any]:
        """§35 tasks/{id}/bootstrap：租约门控的凭据+任务元数据（仅内存，禁止落盘/日志）。"""
        payload = self._ctx(task_id, lease_id)
        if attempt_id:
            payload["attempt_id"] = attempt_id
        status, body = self._post(f"/api/executor/v1/tasks/{task_id}/bootstrap", payload)
        if status != 200:
            raise RuntimeError(f"bootstrap denied: {status} {body}")
        return body

    def release_credentials(self, task_id: str, lease_id: str) -> list[dict[str, Any]]:
        status, body = self._post("/api/executor/v1/credentials", self._ctx(task_id, lease_id))
        if status != 200:
            raise RuntimeError(f"credential release denied: {status} {body}")
        return body.get("credentials", [])

    def presign_artifact(self, task_id: str, lease_id: str, artifact_type: str = "stdout",
                         content_type: str = "text/plain") -> str | None:
        """§31：申请短时（15min）上传授权 URL；服务端未配置密钥时返回 None（回退直传）。"""
        status, body = self._post("/api/executor/v1/artifacts/presign", {
            "executor_id": self.executor_id,
            "execution_path": self.execution_path,
            "task_id": task_id,
            "lease_id": lease_id,
            "artifact_type": artifact_type,
            "content_type": content_type,
        })
        if status != 200:
            return None
        return body.get("upload_url")

    def upload_artifact(self, task_id: str, lease_id: str, content: bytes,
                        artifact_type: str = "stdout",
                        content_type: str = "text/plain") -> dict[str, Any]:
        # 优先 §31 presign 流（PUT 短时 URL）；不可用则回退 bearer 直传（POST）
        upload_url = self.presign_artifact(task_id, lease_id, artifact_type, content_type)
        if upload_url:
            req = urllib.request.Request(upload_url, data=content,
                                         headers={"Content-Type": content_type,
                                                  "User-Agent": "autotask-executor/1.0"},
                                         method="PUT")
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    data = resp.read()
                    if resp.status not in (200, 201):
                        raise RuntimeError(f"presigned upload failed: {resp.status}")
                    return json.loads(data) if data else {}
            except urllib.error.HTTPError as e:
                raise RuntimeError(f"presigned upload failed: {e.code}") from e
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
        status, body = self._post("/api/executor/v1/node-heartbeat",
                                  self._ctx("", "") | {"version": self.version,
                                                       "capacity": self.capacity,
                                                       "active_tasks": self.active_tasks})
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
