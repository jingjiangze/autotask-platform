"""stage-cloud-12 — Executor 运行时主循环与常驻入口（计划 §36/§49/§91）

运行时循环：claim -> (无任务: sleep) -> ack -> [heartbeat 线程 + handler]
         -> complete -> (可选) 上传工件。凭据在 handler 执行前按需解封。
启动：

    python agent/main.py --runner chaoxing        # 或 TASK_RUNNER=chaoxing
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

# 以脚本方式运行（python agent/main.py）时，把 executor 根目录加入 sys.path，
# 使 agent. / runners. / runtime. 包均可导入。
_PKG_ROOT = Path(__file__).resolve().parent.parent
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

from agent.client import RETRYABLE_CODES, CentralClient  # noqa: E402
from runtime.process import control_tree  # noqa: E402

Handler = Callable[[dict[str, Any], "TaskContext"], dict[str, Any]]


@dataclass
class TaskContext:
    """handler 可用的运行时上下文：凭据解封、工件上传、引擎进程控制。"""
    client: CentralClient
    task_id: str
    lease_id: str
    _credentials: list[dict[str, str]] | None = field(default=None, repr=False)
    proc: Any = field(default=None, repr=False)  # stage-cloud-28：当前引擎子进程（挂起/恢复用）

    def credentials(self) -> list[dict[str, str]]:
        """按需解封凭据（每任务最多一次，缓存于内存，进程结束即丢）。"""
        if self._credentials is None:
            self._credentials = self.client.release_credentials(self.task_id, self.lease_id)
        return self._credentials

    def upload(self, content: bytes, artifact_type: str = "stdout") -> dict[str, Any]:
        return self.client.upload_artifact(self.task_id, self.lease_id, content,
                                           artifact_type=artifact_type)

    # stage-cloud-28：进程树挂起/恢复（实际控制逻辑见 runtime/process.py）
    def suspend_engine(self) -> bool:
        return control_tree(self.proc, suspend=True)

    def resume_engine(self) -> bool:
        return control_tree(self.proc, suspend=False)


class ExecutorRuntime:
    """拉取式运行时主循环。handler 按 task_type 注册；未知类型按 TASK_INVALID 上报。"""

    def __init__(self, client: CentralClient, capabilities: list[str],
                 poll_interval: float = 15.0, heartbeat_interval: float = 30.0,
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
            try:
                task = self.client.claim(self.capabilities)
            except OSError:
                # 瞬时网络/TLS 抖动（如 SSL UNEXPECTED_EOF）：跳过本轮，下轮再拉
                time.sleep(self.poll_interval)
                continue
            if not task:
                time.sleep(self.poll_interval)
                continue
            try:
                self._execute(task)
            except OSError as e:  # 传输层抖动穿透（如 complete 兜底再失败）：不杀主循环
                print(f"[warn] execute transport error: {e}", flush=True)
            done += 1
        return done

    def _execute(self, task: dict[str, Any]) -> None:
        task_id = task["task_id"]
        lease_id = task["lease_id"]
        stop_heartbeat = threading.Event()
        ctx = TaskContext(self.client, task_id, lease_id)
        last_control = "resume"  # 幂等：只在信令变化时切换进程状态

        def hb() -> None:
            nonlocal last_control
            while not stop_heartbeat.wait(self.heartbeat_interval):
                try:
                    resp = self.client.heartbeat(task_id, lease_id)
                    # stage-cloud-28：控制信令（pause/resume）随心跳下发
                    control = resp.get("control") if isinstance(resp, dict) else None
                    if control in ("pause", "resume") and control != last_control:
                        ok = ctx.suspend_engine() if control == "pause" else ctx.resume_engine()
                        print(f"[control] {control} engine -> {'ok' if ok else 'no proc'}", flush=True)
                        last_control = control
                except Exception:
                    pass  # 心跳失败不中断执行；租约到期由云端回收

        t = threading.Thread(target=hb, daemon=True)
        t.start()
        try:
            handler = self.handlers.get(task["task_type"])
            if handler is None:
                self.client.complete(task_id, lease_id, "failed", "TASK_INVALID")
                return
            self.client.ack(task_id, lease_id)
            try:
                result = handler(task["payload"], ctx)
                # 工件必须先于 complete：终态任务的上传会被服务端拒绝（LIVE 实证），
                # 先传后终结避免 result_json 丢失并把 except 误导向二次 complete。
                if self._capture_stdout and isinstance(result, dict):
                    self.client.upload_artifact(
                        task_id, lease_id,
                        json.dumps(result, ensure_ascii=False).encode(), "result_json")
                self.client.complete(task_id, lease_id, "succeeded")
            except Exception as e:  # noqa: BLE001 —— 任何 handler 异常归一为可重试分类
                code = "EXECUTOR_CRASH" if not isinstance(e, TimeoutError) else "PROCESS_TIMEOUT"
                outcome = "retry_wait" if code in RETRYABLE_CODES else "failed"
                self.client.complete(task_id, lease_id, outcome, code)
            finally:
                cleanup = getattr(self, "_cleanup", None)
                if cleanup:
                    try:
                        cleanup(task_id)  # §110：任务结束删除隔离目录（日志已入 R2）
                    except Exception:
                        pass
        finally:
            stop_heartbeat.set()


def main() -> int:  # pragma: no cover - 常驻入口
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--runner", choices=["demo", "chaoxing"], default=None,
                    help="注册真实任务处理器（默认由 TASK_RUNNER 环境变量决定）")
    args = ap.parse_args()

    cfg_url = os.environ.get("CENTRAL_URL", "")
    cfg_id = os.environ.get("EXECUTOR_ID", "")
    cfg_path = os.environ.get("EXECUTION_PATH", "internal")
    cfg_token = os.environ.get("EXECUTOR_TOKEN", "")
    cfg_caps = [c for c in os.environ.get("EXECUTOR_CAPABILITIES", "").split(",") if c]
    if not cfg_url or not cfg_id or not cfg_token:
        print("CENTRAL_URL / EXECUTOR_ID / EXECUTOR_TOKEN must be set", flush=True)
        return 2
    client = CentralClient(cfg_url, cfg_id, cfg_path, token=cfg_token)
    runtime = ExecutorRuntime(
        client, capabilities=cfg_caps,
        poll_interval=float(os.environ.get("POLL_INTERVAL_S", "15")),
        heartbeat_interval=float(os.environ.get("HEARTBEAT_INTERVAL_S", "30")))

    runner = args.runner or os.environ.get("TASK_RUNNER", "demo")
    if runner == "chaoxing":
        from runners.chaoxing_runner import run_chaoxing, query_courses, cleanup_task_dir
        from runners.zhs_runner import run_zhs, query_courses as zhs_query
        runtime.register_handler("chaoxing.run", run_chaoxing)
        runtime.register_handler("chaoxing.courses", query_courses)
        runtime.register_handler("zhs.run", run_zhs)
        runtime.register_handler("zhs.courses", zhs_query)
        runtime._cleanup = cleanup_task_dir  # 任务结束清理隔离目录（§110）
    else:
        runtime.register_handler("demo.echo", lambda p, ctx: {"echo": p})

    try:
        client.node_heartbeat()  # 节点级心跳属 CentralClient（注册后即上报存活）
    except OSError:
        pass  # 瞬时网络抖动不影响常驻循环
    runtime.run_forever()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
