"""stage-cloud-28 LIVE E2E — 查课表 / 暂停恢复 / 优先级 / 凭据明文查看。

前置：本机可达中央（自定义域）；tools_query_courses.py 与真实引擎可用。
用法：python e2e_control.py --base <URL> --bootstrap <TOKEN>
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

UA = "autotask-executor/1.0"
PY = r"C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
RESULTS: list[tuple[str, str]] = []


def check(name: str, ok: bool, note: str = ""):
    RESULTS.append((name, "LIVE PASS" if ok else "LIVE FAIL"))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name} {note}")


def call(method: str, path: str, token: str | None = None, body: dict | None = None,
         cookie: str | None = None):
    headers = {"Content-Type": "application/json", "User-Agent": UA}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if cookie:
        headers["Cookie"] = cookie
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, method=method, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            p = r.read()
            return r.status, json.loads(p) if p[:1] in (b"{", b"[") else p
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except Exception:
            return e.code, {}


def main() -> int:
    global base
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--bootstrap", required=True)
    ap.add_argument("--account", required=True, help="真实网课账号（查课 LIVE 用）")
    ap.add_argument("--password", required=True)
    args = ap.parse_args()
    base = args.base.rstrip("/")

    # 登录真实用户
    req = urllib.request.Request(f"{base}/api/v1/auth/login", method="POST",
        data=json.dumps({"username": "real_owner", "password": "real-pass-123456"}).encode(),
        headers={"Content-Type": "application/json", "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        cookie = r.headers.get("Set-Cookie", "").split(";")[0]
    check("real_owner login", bool(cookie))

    # 1. 凭据明文查看（cloud-real-001 已有 enc-v2 种子）
    st, cred = call("GET", "/api/v1/orders/cloud-real-001/credentials", cookie=cookie)
    check("credentials view (owner plaintext)", st == 200 and len(cred.get("password", "")) > 0,
          f"account={cred.get('account', '')[:4]}***")

    # 2. 新订单 + 查课表 LIVE（真实登录 tools_query_courses）
    st, o = call("POST", "/api/v1/orders", cookie=cookie,
                 body={"product_code": "E2E", "platform": "chaoxing", "account": args.account})
    check("order create for courses query", st == 201)
    oid = o["order_id"]
    st, q = call("POST", f"/api/v1/orders/{oid}/query-courses", cookie=cookie,
                 body={"account": args.account, "password": args.password,
                       "platform": "chaoxing", "execution_path": "local"})
    check("query-courses enqueue (creds enc-v2 stored)", st == 201)
    qtask = q["task_id"]

    # 本机真实 Executor（同 e2e_real）
    env = os.environ.copy()
    env.update({"CENTRAL_URL": base, "EXECUTOR_ID": f"exec-local-ctl{str(int(time.time()))[-5:]}",
                "EXECUTION_PATH": "local", "EXECUTOR_TOKEN": "",
                "EXECUTOR_CAPABILITIES": "chaoxing", "TASK_RUNNER": "chaoxing",
                "POLL_INTERVAL_S": "3", "HEARTBEAT_INTERVAL_S": "3", "PYTHONIOENCODING": "utf-8"})
    # 注册拿 token
    st, reg = call("POST", "/api/executor/v1/register", args.bootstrap,
                   {"executor_id": env["EXECUTOR_ID"], "execution_path": "local",
                    "capabilities": ["chaoxing"], "version": "1.0.0"})
    check("executor register (local)", st == 201)
    env["EXECUTOR_TOKEN"] = reg["executor_token"]
    here = os.path.dirname(os.path.abspath(__file__))
    proc = subprocess.Popen([PY, "-u", os.path.join(here, "executor_runtime.py"), "--runner", "chaoxing"],
                            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
                            cwd=here)
    try:
        # 轮询查课任务
        courses, detail = [], {}
        for _ in range(60):
            time.sleep(3)
            st, detail = call("GET", f"/api/v1/tasks/{qtask}", cookie=cookie)
            t = detail.get("task") or {}
            if t.get("status") in ("succeeded", "failed", "canceled"):
                break
        check("courses query task succeeded (real login)", task_ok := (detail.get("task") or {}).get("status") == "succeeded")
        art = next((a for a in detail.get("artifacts", []) if a["artifact_type"] == "result_json"), None)
        if art:
            st2, res = call("GET", f"/api/v1/orders/{oid}/artifacts/{art['id']}", cookie=cookie)
            data = json.loads(res) if isinstance(res, (bytes, str)) else res
            courses = (data or {}).get("courses", [])
        check(f"course list returned via result_json (n={len(courses)})", len(courses) > 0)

        # 3. 暂停/恢复 LIVE：用已完成课程（引擎 ~2 分钟扫完退出），运行中挂起→恢复→完成
        done_course = next((str(c["id"]) for c in courses if isinstance(c, dict) and "254722149" in str(c.get("id", ""))), "254722149")
        st, t = call("POST", f"/api/v1/orders/{oid}/tasks", cookie=cookie,
                     body={"execution_path": "local", "task_type": "chaoxing.run",
                           "required_capabilities": ["chaoxing"],
                           "payload": {"courses": done_course,
                                       "speed": 2.0, "timeout_seconds": 600}})
        check("run task enqueued", st == 201, f"resp={json.dumps(t, ensure_ascii=False)[:200]} courses0={json.dumps(courses[0], ensure_ascii=False)[:120] if courses else 'none'}")
        if st != 201:
            return 1
        run_task = t["task_id"]
        st, _ = call("GET", f"/api/v1/tasks/{run_task}", cookie=cookie)
        # 等进入 running
        for _ in range(30):
            time.sleep(2)
            st, d = call("GET", f"/api/v1/tasks/{run_task}", cookie=cookie)
            if (d.get("task") or {}).get("status") == "running":
                break
        st, p = call("POST", f"/api/v1/orders/{oid}/control", cookie=cookie, body={"action": "pause"})
        check("pause accepted (order processing)", st == 200)
        time.sleep(8)  # ≥2 个心跳（3s 间隔），Executor 应用挂起
        st, r = call("POST", f"/api/v1/orders/{oid}/control", cookie=cookie, body={"action": "resume"})
        check("resume accepted", st == 200)
        # 等任务完成
        final = {}
        for _ in range(150):
            time.sleep(3)
            st, final = call("GET", f"/api/v1/tasks/{run_task}", cookie=cookie)
            if (final.get("task") or {}).get("status") in ("succeeded", "failed"):
                break
        check("run task completed after pause/resume",
              (final.get("task") or {}).get("status") == "succeeded")

        # 4. 优先级
        st, pr = call("POST", f"/api/v1/orders/{oid}/control", cookie=cookie,
                      body={"action": "priority", "priority": 9})
        check("priority update (queued hot-reload)", st == 200)
    finally:
        proc.terminate()

    print("\n== stage-cloud-28 control E2E ==")
    fails = 0
    for name, verdict in RESULTS:
        print(f"  {verdict:<12} {name}")
        fails += verdict != "LIVE PASS"
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
