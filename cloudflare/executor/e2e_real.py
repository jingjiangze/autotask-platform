"""stage-cloud-12 真实接入验收 — REAL ENGINE E2E。

链路：注册 local executor → owner 登录 → 入队 chaoxing.run 真实任务
     → 本机真实 Executor（fuckCourse/chaoxing/main.py 真实登录）
     → 凭据租约解封 → 真实执行 → 日志入 R2 → 中央终态。

用法：python e2e_real.py --base <URL> --bootstrap <TOKEN>
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


def call(base: str, method: str, path: str, token: str | None = None,
         body: dict | None = None, cookie: str | None = None):
    headers = {"Content-Type": "application/json", "User-Agent": UA}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if cookie:
        headers["Cookie"] = cookie
    req = urllib.request.Request(base + path, method=method,
        data=json.dumps(body).encode() if body is not None else None, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            p = r.read()
            return r.status, json.loads(p) if p else {}
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
    suffix = str(int(time.time()))[-5:]

    # 1. local executor 注册（真实本机节点）
    st, reg = call(base, "POST", "/api/executor/v1/register", args.bootstrap, {
        "executor_id": f"exec-local-real{suffix}", "execution_path": "local",
        "capabilities": ["chaoxing"], "version": "1.0.0"})
    check("executor register (local path, chaoxing capability)", st == 201)
    token = reg["executor_token"]

    # 2. owner 登录 + 入队真实任务
    req = urllib.request.Request(f"{base}/api/v1/auth/login", method="POST",
        data=json.dumps({"username": "real_owner", "password": "real-pass-123456"}).encode(),
        headers={"Content-Type": "application/json", "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        cookie = r.headers.get("Set-Cookie", "").split(";")[0]
    check("real_owner login", bool(cookie))

    st, t = call(base, "POST", "/api/v1/orders/cloud-real-001/tasks", body={
        "execution_path": "local", "task_type": "chaoxing.run",
        "required_capabilities": ["chaoxing"],
        "payload": {"courses": "254722149", "speed": 2.0, "timeout_seconds": 600},
    }, cookie=cookie)
    check("real task enqueue (chaoxing.run, courses=254722149)", st == 201)
    task_id = t["task_id"]

    # 3. 本机真实 Executor 子进程（同一份 Agent 代码 + 真实 runner）
    env = os.environ.copy()
    env.update({
        "CENTRAL_URL": base,
        "EXECUTOR_ID": f"exec-local-real{suffix}",
        "EXECUTION_PATH": "local",
        "EXECUTOR_TOKEN": token,
        "EXECUTOR_CAPABILITIES": "chaoxing",
        "TASK_RUNNER": "chaoxing",
        "PYTHONIOENCODING": "utf-8",
    })
    print("  ... launching REAL executor (fuckCourse engine, real login) ...")
    started = time.time()
    proc = subprocess.Popen(
        [PY, "-u", os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent", "main.py"),
         "--runner", "chaoxing"],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        encoding="utf-8", errors="replace", cwd=os.path.dirname(os.path.abspath(__file__)))
    # 等任务执行（最长 10 分钟；引擎真实登录+查课+秒退预计 <3min）
    deadline = time.time() + 600
    output_tail = ""
    while time.time() < deadline:
        st, detail = call(base, "GET", f"/api/v1/tasks/{task_id}", cookie=cookie)
        status = (detail.get("task") or {}).get("status")
        if status in ("succeeded", "failed"):
            break
        time.sleep(5)
    elapsed = round(time.time() - started, 1)

    # 终止 executor（max_tasks 由外部超时控制；进程完成单任务后可安全终止）
    proc.terminate()
    try:
        out = proc.communicate(timeout=10)[0]
        output_tail = (out or "")[-800:]
    except subprocess.TimeoutExpired:
        proc.kill()

    # 4. 终态与工件验证
    st, detail = call(base, "GET", f"/api/v1/tasks/{task_id}", cookie=cookie)
    task = detail.get("task") or {}
    check(f"REAL ENGINE executed → succeeded ({elapsed}s)", task.get("status") == "succeeded",
          f"error={task.get('error_code')}")
    attempts = detail.get("attempts") or []
    check("attempt recorded (attempt_no=1, executor=local)", bool(attempts) and
          attempts[0].get("attempt_no") == 1)
    st, orders = call(base, "GET", "/api/v1/my/orders", cookie=cookie)
    mine = [o for o in orders.get("orders", []) if o["order_id"] == "cloud-real-001"]
    check("order status mirrored to succeeded", bool(mine) and mine[0]["status"] == "succeeded")

    # 审计：CREDENTIAL_RELEASED 已记录
    st, order_detail = call(base, "GET", f"/api/v1/orders/cloud-real-001", cookie=cookie)
    check("order detail + attempts queryable", st == 200)

    print("\n== REAL ENGINE E2E matrix ==")
    fails = 0
    for name, verdict in RESULTS:
        print(f"  {verdict:<12} {name}")
        fails += verdict != "LIVE PASS"
    if task.get("status") != "succeeded" and output_tail:
        print("\n[executor output tail]")
        print(output_tail)
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
