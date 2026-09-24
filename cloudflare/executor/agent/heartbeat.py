"""§72 agent/heartbeat —— 任务心跳泵（plan §41/§44）。

每 heartbeat_interval 续租一次；心跳响应可携带控制信令（pause/resume，
stage-cloud-28），通过回调作用于引擎进程树。心跳失败不中断执行。
"""

from __future__ import annotations

import threading
from typing import Callable


class HeartbeatPump:
    def __init__(self, client, task_id: str, lease_id: str, attempt_id: str,
                 interval: float, on_control: Callable[[str], None]):
        self._client = client
        self._task_id = task_id
        self._lease_id = lease_id
        self._attempt_id = attempt_id
        self._interval = interval
        self._on_control = on_control
        self._stop = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self._loop, daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                resp = self.client_resp()
                control = resp.get("control") if isinstance(resp, dict) else None
                if control in ("pause", "resume"):
                    self._on_control(control)
            except Exception:
                pass  # 心跳失败不中断执行；租约到期由云端回收

    def client_resp(self):
        return self._client.heartbeat(self._task_id, self._lease_id, self._attempt_id)
