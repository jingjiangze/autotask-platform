"""stage-cloud-14/19 — R2 工件链路 LIVE 验证（E2E-12/13 补测）。

前置：网络可达 workers.dev（代理已生效）。
流程：注册 executor → 用户登录 → 建订单/任务 → claim → 上传工件（R2）
     → 所有者下载比对 → 他人下载 404 → 完成任务。

用法：python e2e_artifacts.py --base <URL> --bootstrap <TOKEN>
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlencode

UA = "autotask-executor/1.0"
RESULTS: list[tuple[str, str]] = []


def check(name: str, ok: bool):
    RESULTS.append((name, "LIVE PASS" if ok else "LIVE FAIL"))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}")


def call(base: str, method: str, path: str, token: str | None = None,
         body: dict | None = None, cookie: str | None = None, raw: bytes | None = None):
    headers = {"Content-Type": "application/json", "User-Agent": UA}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if cookie:
        headers["Cookie"] = cookie
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(f"{base}{path}", method=method, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            payload = r.read()
            return r.status, json.loads(payload) if payload[:1] in (b"{", b"[") else payload
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except Exception:
            return e.code, {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--bootstrap", required=True)
    args = ap.parse_args()
    base = args.base.rstrip("/")
    suffix = str(int(time.time()))[-6:]

    # executor 注册（internal）
    st, reg = call(base, "POST", "/api/executor/v1/register", args.bootstrap, {
        "executor_id": f"exec-internal-art{suffix}", "execution_path": "internal",
        "capabilities": ["demo"], "version": "1.0.0"})
    check("executor register", st == 201)
    token = reg.get("executor_token", "")

    # 用户登录
    call(base, "POST", "/api/v1/auth/register", body={
        "username": f"art_user_{suffix}", "password": "art-pass-123456"})
    req = urllib.request.Request(f"{base}/api/v1/auth/login", method="POST",
        data=json.dumps({"username": f"art_user_{suffix}", "password": "art-pass-123456"}).encode(),
        headers={"Content-Type": "application/json", "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        cookie = r.headers.get("Set-Cookie", "").split(";")[0]
    check("user login (cookie)", bool(cookie))

    # 订单 + 任务
    st, order = call(base, "POST", "/api/v1/orders", body={
        "product_code": "E2E", "platform": "demo", "account": "art@test"}, cookie=cookie)
    check("order create", st == 201)
    st, task = call(base, "POST", f"/api/v1/orders/{order['order_id']}/tasks", body={
        "execution_path": "internal", "task_type": "demo.echo",
        "required_capabilities": ["demo"], "payload": {"n": 1}}, cookie=cookie)
    check("task create", st == 201)
    task_id = task["task_id"]

    # claim → 上传工件
    st, claim = call(base, "POST", "/api/executor/v1/claim", token, {
        "executor_id": f"exec-internal-art{suffix}", "execution_path": "internal",
        "capabilities": ["demo"]})
    d = claim.get("task") or {}
    lease = d.get("lease_id", "")
    check("claim (lease issued)", st == 200 and d.get("task_id") == task_id)

    content = f"stdout from live artifact test {suffix}\n".encode()
    q = urlencode({
        "executor_id": f"exec-internal-art{suffix}", "execution_path": "internal",
        "task_id": task_id, "lease_id": lease,
        "artifact_type": "stdout", "content_type": "text/plain"})
    st, up = call(base, "POST", f"/api/executor/v1/artifacts?{q}", token, raw=content)
    check("E2E-12 artifact upload → R2 (sha256 recorded)", st == 201 and up.get("sha256"))
    artifact_id = up.get("artifact_id", "")

    # 所有者下载比对
    st2, downloaded = call(base, "GET",
                           f"/api/v1/orders/{order['order_id']}/artifacts/{artifact_id}",
                           cookie=cookie)
    check("E2E-13 owner download matches content",
          st2 == 200 and (downloaded if isinstance(downloaded, bytes) else b"") == content)

    # 他人下载 → 404
    call(base, "POST", "/api/v1/auth/register", body={
        "username": f"art_other_{suffix}", "password": "other-pass-123456"})
    req = urllib.request.Request(f"{base}/api/v1/auth/login", method="POST",
        data=json.dumps({"username": f"art_other_{suffix}", "password": "other-pass-123456"}).encode(),
        headers={"Content-Type": "application/json", "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        other_cookie = r.headers.get("Set-Cookie", "").split(";")[0]
    st3, _ = call(base, "GET", f"/api/v1/orders/{order['order_id']}/artifacts/{artifact_id}",
                  cookie=other_cookie)
    check("stranger download → 404 (no leak)", st3 == 404)

    # complete 收尾
    st4, done = call(base, "POST", "/api/executor/v1/complete", token, {
        "executor_id": f"exec-internal-art{suffix}", "execution_path": "internal",
        "task_id": task_id, "lease_id": lease, "outcome": "succeeded"})
    check("task complete (terminal mirror)", st4 == 200 and done.get("status") == "succeeded")

    print("\n== artifact E2E matrix ==")
    fails = 0
    for name, verdict in RESULTS:
        print(f"  {verdict:<12} {name}")
        fails += verdict != "LIVE PASS"
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
