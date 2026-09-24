"""stage-cloud-12/31 — Executor 运行时主循环与常驻入口（计划 §36/§49/§91/§72）

目录分层（§72）：本文件只保留 TaskContext/ExecutorRuntime 装配与 CLI 入口；
身份=agent/auth，心跳=agent/heartbeat，租约=agent/lease，凭据=agent/bootstrap，
结果分类=agent/result，工件=agent/artifact，清理=agent/cleanup，
进程控制=runtime/process，环境装配=runtime/environment，脱敏=runtime/logs。

启动：

    python agent/main.py --runner chaoxing        # 或 TASK_RUNNER=chaoxing
"""

from __future__ import annotations

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

from agent.artifact import ArtifactUploader  # noqa: E402
from agent.auth import ExecutorAuth  # noqa: E402
from agent.bootstrap import CredentialCache  # noqa: E402
from agent.cleanup import safe_invoke  # noqa: E402
from agent.client import CentralClient  # noqa: E402
from agent.heartbeat import HeartbeatPump  # noqa: E402
from agent.lease import LeaseOps  # noqa: E402
from runtime.process import control_tree  # noqa: E402

Handler = Callable[[dict[str, Any], "TaskContext"], dict[str, Any]]


@dataclass
class TaskContext:
    """handler 可用的运行时上下文：凭据解封、工件上传、引擎进程控制。"""
    client: CentralClient
    task_id: str
    lease_id: str
    attempt_id: str = ""  # §32/§47：dispatch 下发的 attempt_id，回报时三元 fencing
    _credentials: CredentialCache | None = field(default=None, repr=False)
    proc: Any = field(default=None, repr=False)  # stage-cloud-28：当前引擎子进程（挂起/恢复用）

    def credentials(self) -> list[dict[str, str]]:
        """按需解封凭据（§35 bootstrap：租约门控；仅内存缓存，任务结束即丢）。"""
        if self._credentials is None:
            self._credentials = CredentialCache(
                self.client, self.task_id, self.lease_id, self.attempt_id)
        return self._credentials.get()

    def upload(self, content: bytes, artifact_type: str = "stdout",
               content_type: str = "text/plain") -> dict[str, Any]:
        return ArtifactUploader(self.client).upload(
            self.task_id, self.lease_id, content, artifact_type=artifact_type,
            content_type=content_type)

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
        self._lease = LeaseOps(client)

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
        attempt_id = str(task.get("attempt_id") or f"{task_id}#{task.get('attempt_no', 1)}")
        ctx = TaskContext(self.client, task_id, lease_id, attempt_id)
        last_control = "resume"  # 幂等：只在信令变化时切换进程状态

        def on_control(control: str) -> None:
            nonlocal last_control
            if control == last_control:
                return
            ok = ctx.suspend_engine() if control == "pause" else ctx.resume_engine()
            print(f"[control] {control} engine -> {'ok' if ok else 'no proc'}", flush=True)
            last_control = control

        pump = HeartbeatPump(self.client, task_id, lease_id, attempt_id,
                             self.heartbeat_interval, on_control)
        pump.start()
        try:
            handler = self.handlers.get(task["task_type"])
            if handler is None:
                self.client.fail(task_id, lease_id, "TASK_INVALID", "failed", attempt_id)
                return
            self._lease.start(task_id, lease_id, attempt_id)
            try:
                result = handler(task["payload"], ctx)
                # 工件必须先于 complete：终态任务的上传会被服务端拒绝（LIVE 实证），
                # 先传后终结避免 result_json 丢失并把 except 误导向二次 complete。
                if self._capture_stdout and isinstance(result, dict):
                    ArtifactUploader(self.client).upload_json(task_id, lease_id, result)
                self._lease.complete_success(task_id, lease_id, attempt_id)
            except Exception as e:  # noqa: BLE001 —— 任何 handler 异常归一为可重试分类
                self._lease.complete_failure(task_id, lease_id, attempt_id, e)
            finally:
                safe_invoke(getattr(self, "_cleanup", None), task_id)  # §110
        finally:
            pump.stop()


def main() -> int:  # pragma: no cover - 常驻入口
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--runner", choices=["demo", "chaoxing"], default=None,
                    help="注册真实任务处理器（默认由 TASK_RUNNER 环境变量决定）")
    args = ap.parse_args()

    auth = ExecutorAuth.from_env()
    if auth is None:
        print("CENTRAL_URL / EXECUTOR_ID / EXECUTOR_TOKEN must be set", flush=True)
        return 2
    client = CentralClient(auth.central_url, auth.executor_id, auth.execution_path,
                           token=auth.token)
    runtime = ExecutorRuntime(
        client, capabilities=auth.capabilities,
        poll_interval=float(os.environ.get("POLL_INTERVAL_S", "15")),
        heartbeat_interval=float(os.environ.get("HEARTBEAT_INTERVAL_S", "30")))

    runner = args.runner or os.environ.get("TASK_RUNNER", "demo")
    if runner == "chaoxing":
        from runners.chaoxing_runner import run_chaoxing, query_courses, cleanup_task_dir
        from runners.zhs_qr_runner import run_zhs_qr
        from runners.zhs_runner import run_zhs, query_courses as zhs_query
        runtime.register_handler("chaoxing.run", run_chaoxing)
        runtime.register_handler("chaoxing.courses", query_courses)
        runtime.register_handler("zhs.run", run_zhs)
        runtime.register_handler("zhs.courses", zhs_query)
        runtime.register_handler("zhs_qr.run", run_zhs_qr)  # §65：云端如实上报不可用
        runtime._cleanup = cleanup_task_dir  # 任务结束清理隔离目录（§110）
    else:
        runtime.register_handler("demo.echo", lambda p, ctx: {"echo": p})

    # §115：启动孤儿扫描 —— 中央终态/无主/租约过期的残留任务目录按策略清理
    try:
        from agent.cleanup import scan_orphans
        from runners.chaoxing_runner import TASK_ROOT
        rep = scan_orphans(client, TASK_ROOT, client.executor_id)
        if rep["checked"]:
            print(f"[orphan-scan] checked={rep['checked']} deleted={len(rep['deleted'])} "
                  f"killed={len(rep['killed'])} kept={len(rep['kept'])}", flush=True)
    except Exception as e:  # noqa: BLE001 —— 扫描失败不阻塞常驻主循环
        print(f"[orphan-scan] skipped: {e}", flush=True)

    try:
        client.node_heartbeat()  # 节点级心跳（§41：version/capacity/active_tasks）
    except OSError:
        pass  # 瞬时网络抖动不影响常驻循环
    runtime.run_forever()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
