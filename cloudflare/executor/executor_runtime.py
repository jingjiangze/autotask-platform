"""stage-cloud-12 — Executor 运行时骨架（计划 §36/§49/§91）

仅依赖 Python 标准库（urllib），不引入第三方包。
与中央 Worker 的协议为 autotask.executor/v1（见 cloudflare/worker/src/types/protocol.ts）：

    register       POST /api/executor/v1/register        （bootstrap token，仅首次）
    claim          POST /api/executor/v1/claim           拉取任务（空队列 task=null）
    ack            POST /api/executor/v1/ack             leased -> running
    heartbeat      POST /api/executor/v1/heartbeat       续租（建议每 60s）
    credentials    POST /api/executor/v1/credentials     凭租约解封（§33：payload 永不带凭据）
    complete       POST /api/executor/v1/complete        succeeded|failed|retry_wait
    artifacts      POST /api/executor/v1/artifacts       上传 stdout/日志（raw body）
    node-heartbeat POST /api/executor/v1/node-heartbeat  节点级存活（§45）

运行时循环：claim -> (无任务: sleep) -> ack -> [heartbeat 线程 + handler]
         -> complete -> (可选) 上传工件。凭据在 handler 执行前按需解封。
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable

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


Handler = Callable[[dict[str, Any], "TaskContext"], dict[str, Any]]


@dataclass
class TaskContext:
    """handler 可用的运行时上下文：凭据解封与工件上传。"""
    client: CentralClient
    task_id: str
    lease_id: str
    _credentials: list[dict[str, str]] | None = field(default=None, repr=False)

    def credentials(self) -> list[dict[str, str]]:
        """按需解封凭据（每任务最多一次，缓存于内存，进程结束即丢）。"""
        if self._credentials is None:
            self._credentials = self.client.release_credentials(self.task_id, self.lease_id)
        return self._credentials

    def upload(self, content: bytes, artifact_type: str = "stdout") -> dict[str, Any]:
        return self.client.upload_artifact(self.task_id, self.lease_id, content,
                                           artifact_type=artifact_type)


class ExecutorRuntime:
    """拉取式运行时主循环。handler 按 task_type 注册；未知类型按 TASK_INVALID 上报。"""

    def __init__(self, client: CentralClient, capabilities: list[str],
                 poll_interval: float = 5.0, heartbeat_interval: float = 60.0,
                 handler_stdout: bool = True):
        self.client = client
        self.capabilities = capabilities
        self.poll_interval = poll_interval
        self.heartbeat_interval = heartbeat_interval
        self.handlers: dict[str, Handler] = {}
        self.stop_event = threading.Event()
        self._capture_stdout = handler_stdout

    def register_handler(self, task_type: str, handler: Handler) -> None:
        self.handlers[task_type] = handler

    def run_forever(self, max_tasks: int | None = None) -> int:
        """阻塞主循环；返回处理任务数。测试可设 max_tasks 限次退出。"""
        done = 0
        while not self.stop_event.is_set():
            if max_tasks is not None and done >= max_tasks:
                return done
            task = self.client.claim(self.capabilities)
            if not task:
                time.sleep(self.poll_interval)
                continue
            self._execute(task)
            done += 1
        return done

    def _execute(self, task: dict[str, Any]) -> None:
        task_id = task["task_id"]
        lease_id = task["lease_id"]
        stop_heartbeat = threading.Event()

        def hb() -> None:
            while not stop_heartbeat.wait(self.heartbeat_interval):
                try:
                    self.client.heartbeat(task_id, lease_id)
                except Exception:
                    pass  # 心跳失败不中断执行；租约到期由云端回收

        t = threading.Thread(target=hb, daemon=True)
        t.start()
        try:
            handler = self.handlers.get(task["task_type"])
            if handler is None:
                self.client.complete(task_id, lease_id, "failed", "TASK_INVALID")
                return
            ctx = TaskContext(self.client, task_id, lease_id)
            self.client.ack(task_id, lease_id)
            try:
                result = handler(task["payload"], ctx)
                self.client.complete(task_id, lease_id, "succeeded")
                if self._capture_stdout and isinstance(result, dict):
                    self.client.upload_artifact(
                        task_id, lease_id,
                        json.dumps(result, ensure_ascii=False).encode(), "result_json")
            except Exception as e:  # noqa: BLE001 —— 任何 handler 异常归一为可重试分类
                code = "EXECUTOR_CRASH" if not isinstance(e, TimeoutError) else "PROCESS_TIMEOUT"
                outcome = "retry_wait" if code in RETRYABLE_CODES else "failed"
                self.client.complete(task_id, lease_id, outcome, code)
        finally:
            stop_heartbeat.set()


def main() -> int:  # pragma: no cover - 常驻入口
    cfg_url = os.environ.get("CENTRAL_URL", "")
    cfg_id = os.environ.get("EXECUTOR_ID", "")
    cfg_path = os.environ.get("EXECUTION_PATH", "internal")
    cfg_token = os.environ.get("EXECUTOR_TOKEN", "")
    if not cfg_url or not cfg_id or not cfg_token:
        print("CENTRAL_URL / EXECUTOR_ID / EXECUTOR_TOKEN must be set", flush=True)
        return 2
    client = CentralClient(cfg_url, cfg_id, cfg_path, token=cfg_token)
    runtime = ExecutorRuntime(client, capabilities=os.environ.get("EXECUTOR_CAPABILITIES", "").split(","))
    runtime.node_heartbeat()
    runtime.run_forever()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
