"""§72 runners/zhs_qr_runner —— 知到扫码流程（plan §65/§66）。

QR 是真人交互流程（App 扫码）：云端任务只能进入
WAITING_MANUAL_VERIFICATION 语义（§96），禁止伪造 PASS。
当前云端未建 QR 上传/展示链路（R5 范围），此 runner 如实上报 NOT AVAILABLE。
"""

from __future__ import annotations

from typing import Any


def run_zhs_qr(payload: dict[str, Any], ctx) -> dict[str, Any]:
    """task_type=zhs_qr.run 的占位 handler：如实返回不可用状态，不触发重试风暴。"""
    return {
        "status": "WAITING_MANUAL_VERIFICATION",
        "available": False,
        "reason": "zhs_qr requires interactive App scan; QR upload/display chain not built yet (plan S65/S66, R5)",
        "task_id": ctx.task_id,
    }
