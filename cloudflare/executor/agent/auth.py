"""§72 agent/auth —— 节点身份与凭据装载（plan §39/§40）。

Executor 身份三元组（executor_id / execution_path / token）只来自环境变量或
受保护本地 Secret 文件，禁止写入 Git/代码；token 仅存内存。
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class ExecutorAuth:
    central_url: str
    executor_id: str
    execution_path: str
    token: str
    capabilities: list[str]

    @classmethod
    def from_env(cls) -> "ExecutorAuth | None":
        """从环境装配；缺任一必填项返回 None（由调用方决定退出码）。"""
        url = os.environ.get("CENTRAL_URL", "")
        eid = os.environ.get("EXECUTOR_ID", "")
        path = os.environ.get("EXECUTION_PATH", "internal")
        token = os.environ.get("EXECUTOR_TOKEN", "")
        caps = [c for c in os.environ.get("EXECUTOR_CAPABILITIES", "").split(",") if c]
        if not url or not eid or not token:
            return None
        return cls(url, eid, path, token, caps)
