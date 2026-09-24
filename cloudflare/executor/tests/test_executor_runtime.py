"""stage-cloud-12 验收：假中央服务器 + 运行时闭环（无外部依赖）。"""

from __future__ import annotations

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.client import CentralClient
from agent.main import ExecutorRuntime


class FakeCentral(BaseHTTPRequestHandler):
    """按 autotask.executor/v1 协议模拟的最小中央端。"""

    protocol_version = "HTTP/1.1"
    seen: list[tuple[str, dict]] = []  # (path, body)
    creds_released = False

    def log_message(self, *args):  # 静默
        pass

    def _json(self, obj, status=200):
        data = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        body = json.loads(raw) if raw and self.headers.get("Content-Type") == "application/json" else raw
        FakeCentral.seen.append((self.path, body))

        if self.path.startswith("/api/executor/v1/pull") or self.path.startswith("/api/executor/v1/claim"):
            if not FakeCentral.creds_released:  # 第一轮发任务
                FakeCentral.creds_released = True
                self._json({"ok": True, "task": {
                    "protocol": "autotask.executor/v1", "message_type": "task.dispatch",
                    "task_id": "t-1", "order_id": "o-1", "attempt_id": "t-1#1",
                    "attempt_no": 1, "execution_path": "internal",
                    "task_type": "demo.echo", "lease_id": "lease-1",
                    "lease_expires_at": 9999999999999, "issued_at": 1,
                    "trace_id": "tr-1", "required_capabilities": ["demo"],
                    "payload": {"hello": "world"},
                }})
            else:
                self._json({"ok": True, "task": None})
        elif "/bootstrap" in self.path:
            self._json({"ok": True, "task": {"task_id": "t-1", "task_type": "demo.echo"},
                        "credentials": [{"credential_type": "account_password",
                                         "plaintext": "pw-secret"}]})
        elif self.path.endswith("/start") or self.path.endswith("/ack"):
            self._json({"ok": True, "status": "running"})
        elif self.path.endswith("/heartbeat"):
            self._json({"ok": True, "lease_expires_at": 9999999999999})
        elif self.path.endswith("/complete"):
            self._json({"ok": True, "status": body.get("outcome", "succeeded")})
        elif self.path.endswith("/credentials"):
            self._json({"ok": True, "order_id": "o-1",
                        "credentials": [{"credential_type": "account_password",
                                         "plaintext": "pw-secret"}]})
        elif self.path.endswith("/artifacts"):
            self._json({"ok": True, "artifact_id": "a-1", "size_bytes": len(raw)}, 201)
        else:
            self._json({"ok": True})


class TestExecutorRuntime(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeCentral)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def test_full_loop_claim_ack_complete_upload(self):
        client = CentralClient(self.base, "exec-test-01", "internal", token="tok")
        runtime = ExecutorRuntime(client, capabilities=["demo"],
                                  poll_interval=0.01, handler_stdout=False)
        captured = {}

        def handler(payload, ctx):
            creds = ctx.credentials()  # 按需解封
            captured["creds"] = creds
            ctx.upload(b"done", artifact_type="result_json")
            return {"echo": payload, "used": creds[0]["plaintext"]}

        runtime.register_handler("demo.echo", handler)
        processed = runtime.run_forever(max_tasks=1)

        self.assertEqual(processed, 1)
        self.assertEqual(captured["creds"][0]["plaintext"], "pw-secret")
        paths = [p.split("?")[0] for p, _ in FakeCentral.seen]  # artifacts 带 query
        self.assertIn("/api/executor/v1/tasks/t-1/start", paths)
        self.assertIn("/api/executor/v1/tasks/t-1/bootstrap", paths)
        self.assertIn("/api/executor/v1/artifacts", paths)
        # §47 fencing：attempt_id 必须随 start/complete 回传
        starts = [b for p, b in FakeCentral.seen if p.endswith("/start")]
        self.assertEqual(starts[0].get("attempt_id"), "t-1#1")
        completes = [b for p, b in FakeCentral.seen if p.endswith("/complete")]
        self.assertEqual(completes[0].get("attempt_id"), "t-1#1")
        # complete 在 bootstrap 之后（凭据只在租约内使用）
        self.assertLess(paths.index("/api/executor/v1/tasks/t-1/bootstrap"),
                        paths.index("/api/executor/v1/complete"))

    def test_unknown_task_type_reports_task_invalid(self):
        client = CentralClient(self.base, "exec-test-02", "internal", token="tok")
        runtime = ExecutorRuntime(client, capabilities=["demo"], poll_interval=0.01)
        FakeCentral.creds_released = True  # 下发空任务 → 用手造 claim 响应测未知类型
        FakeCentral.seen.clear()

        # 直接调 _execute 模拟未知 task_type
        runtime._execute({"task_id": "t-x", "lease_id": "l-x", "task_type": "nope.type", "attempt_id": "t-x#1"})
        fails = [b for p, b in FakeCentral.seen if p.endswith("/fail")]
        self.assertEqual(fails[-1].get("error_code"), "TASK_INVALID")

    def test_handler_crash_maps_to_retry(self):
        client = CentralClient(self.base, "exec-test-03", "internal", token="tok")
        runtime = ExecutorRuntime(client, capabilities=["demo"], poll_interval=0.01)

        def boom(payload, ctx):
            raise RuntimeError("engine exploded")

        runtime.register_handler("demo.echo", boom)
        FakeCentral.seen.clear()
        runtime._execute({"task_id": "t-c", "lease_id": "l-c", "task_type": "demo.echo", "attempt_id": "t-c#1"})
        completes = [b for p, b in FakeCentral.seen if p.endswith("/complete")]
        self.assertEqual(completes[-1]["outcome"], "retry_wait")
        self.assertEqual(completes[-1]["error_code"], "EXECUTOR_CRASH")


if __name__ == "__main__":
    unittest.main()
