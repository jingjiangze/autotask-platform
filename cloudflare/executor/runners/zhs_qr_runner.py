"""§72 runners/zhs_qr_runner —— 知到扫码流程（plan §65/§66，stage-cloud-42 落地）。

全链路（对齐本地 order_platform.py qr_start/qr_thread 实现）：
  1. passport.zhihuishu.com/qrCodeLogin/getLoginQrImg → qrToken + PNG
  2. PNG 以 artifact_type="qr_png" 上传 R2 → UI（订单维度 owner 授权下载）展示
  3. 轮询 getLoginQrInfo：0=已扫码待确认 / 1=确认 → oncePassword 换 cookies
  4. cookies.json 写入订单目录（TASK_ROOT/<order_id>/cookies.json，任务清理不触碰）
  5. 以 tools_query_courses.py zhs_cookie 模式查课表 → result_json（含 courses）
     → UI 沿用既有勾课提交流程（zhs.run + payload.qr=true）

QR 是真人交互流程：超时/拒绝/过期一律如实返回，不伪造 PASS（§96）。
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import time
from typing import Any

import requests

from runners.chaoxing_runner import PYEXE, TASK_ROOT, build_chaoxing_env

QUERY_TOOL = r"D:\web\tools_query_courses.py"
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36"


def _cookies_path(order_id: str) -> str:
    d = os.path.join(TASK_ROOT, order_id)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "cookies.json")


def _save_cookies(session: requests.Session, order_id: str) -> str:
    """cookies 写订单目录（跨任务复用：zhs.run payload.qr=true 时读取）。"""
    cpath = _cookies_path(order_id)
    data: dict[str, Any] = {}
    if os.path.isfile(cpath):
        try:
            data = json.load(open(cpath, encoding="utf-8"))
        except Exception:  # noqa: BLE001 —— 损坏文件直接重建
            data = {}
    data["zhs"] = [
        {"name": k, "value": v, "domain": ".zhihuishu.com"}
        for k, v in requests.utils.dict_from_cookiejar(session.cookies).items()
    ]
    json.dump(data, open(cpath, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    return cpath


def _query_courses_with_cookies(ctx, cpath: str, timeout: int = 180) -> dict[str, Any]:
    """zhs_cookie 模式查课表（复用 build_chaoxing_env 的隔离环境）。"""
    env, work, log_path = build_chaoxing_env(ctx.task_id, "", "")
    env["FUCKCOURSE_COOKIES"] = cpath
    cmd = [PYEXE, QUERY_TOOL, "zhs_cookie", cpath]
    started = time.time()
    proc = subprocess.Popen(
        cmd, env=env, cwd=work, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
    ctx.proc = proc
    stdout = ""
    try:
        stdout, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate(timeout=15)
    finally:
        ctx.proc = None
    data: dict[str, Any] = {}
    for line in reversed((stdout or "").strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                data = json.loads(line)
                break
            except json.JSONDecodeError:
                continue
    if os.path.isfile(log_path):
        with open(log_path, "rb") as f:
            ctx.upload(f.read(), artifact_type="stdout")
    return {
        "ok": bool(data.get("ok")),
        "courses": data.get("courses", []) if data.get("ok") else [],
        "error": None if data.get("ok") else (data.get("error") or "query failed"),
        "elapsed_s": round(time.time() - started, 1),
    }


def run_zhs_qr(payload: dict[str, Any], ctx) -> dict[str, Any]:
    """task_type=zhs_qr.run：取码 → 上传 → 轮询 → cookies → 查课表。"""
    order_id = str(payload.get("order_id") or "").strip() or ctx.task_id
    timeout_s = int(payload.get("timeout_seconds") or 240)

    s = requests.Session()
    s.headers.update({"User-Agent": _UA})
    try:
        r = s.get("https://passport.zhihuishu.com/qrCodeLogin/getLoginQrImg", timeout=15).json()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "qr_state": "error", "error": f"获取二维码失败: {e}"}
    qr_token = str(r.get("qrToken") or "")
    if not qr_token or not r.get("img"):
        return {"ok": False, "qr_state": "error", "error": "二维码响应缺少 qrToken/img"}
    ctx.upload(base64.b64decode(r["img"]), artifact_type="qr_png", content_type="image/png")

    deadline = time.time() + timeout_s
    scanned = False
    while time.time() < deadline:
        time.sleep(2)
        try:
            info = s.get(
                "https://passport.zhihuishu.com/qrCodeLogin/getLoginQrInfo",
                params={"qrToken": qr_token}, timeout=10).json()
        except Exception:  # noqa: BLE001 —— 网络抖动继续轮询
            continue
        st = info.get("status")
        if st == 0:
            scanned = True  # 已扫码，等待 App 确认
        elif st == 1:
            s.get(
                "https://passport.zhihuishu.com/login",
                params={"service": "https://onlineservice-api.zhihuishu.com/login/gologin",
                        "pwd": info.get("oncePassword")}, timeout=10)
            cpath = _save_cookies(s, order_id)
            q = _query_courses_with_cookies(ctx, cpath)
            if not q["ok"]:
                raise QRUserError(f"扫码成功但查询课表失败：{q['error']}", "QR_QUERY_FAILED")
            return {
                "ok": True,
                "qr_state": "confirmed",
                "scanned": scanned,
                "courses": q["courses"],
            }
        elif st in (2, 3):
            raise QRUserError("二维码已过期，请重新扫码" if st == 2 else "已在 App 上取消登录",
                              "QR_EXPIRED" if st == 2 else "QR_CANCELED")
    raise QRUserError("二维码超时未扫码，请重新扫码", "QR_EXPIRED")
