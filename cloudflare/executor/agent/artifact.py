"""§72 agent/artifact —— 工件上传（plan §31/§67）。

优先 presign 短时授权（PUT，15min），服务端不可用时回退 bearer 直传。
"""

from __future__ import annotations


class ArtifactUploader:
    def __init__(self, client):
        self._client = client

    def upload(self, task_id: str, lease_id: str, content: bytes,
               artifact_type: str = "stdout", content_type: str = "text/plain"):
        return self._client.upload_artifact(task_id, lease_id, content,
                                            artifact_type=artifact_type,
                                            content_type=content_type)

    def upload_json(self, task_id: str, lease_id: str, result: dict,
                    artifact_type: str = "result_json"):
        import json
        return self.upload(task_id, lease_id,
                           json.dumps(result, ensure_ascii=False).encode(),
                           artifact_type=artifact_type,
                           content_type="application/json")
