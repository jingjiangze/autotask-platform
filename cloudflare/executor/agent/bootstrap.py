"""§72 agent/bootstrap —— 凭据解封缓存（plan §35/§36/§110）。

凭据经 tasks/{id}/bootstrap 租约门控获取；仅在内存缓存、任务结束即丢，
禁止落盘/写日志（§36 红线）。
"""

from __future__ import annotations


class CredentialCache:
    def __init__(self, client, task_id: str, lease_id: str, attempt_id: str):
        self._client = client
        self._task_id = task_id
        self._lease_id = lease_id
        self._attempt_id = attempt_id
        self._credentials: list[dict[str, str]] | None = None

    def get(self) -> list[dict[str, str]]:
        if self._credentials is None:
            self._credentials = self._client.bootstrap(
                self._task_id, self._lease_id, self._attempt_id)["credentials"]
        return self._credentials
