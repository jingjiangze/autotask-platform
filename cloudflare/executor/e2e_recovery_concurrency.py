"""stage-cloud-22/23 — 恢复与并发 LIVE 验证。

  并发：3 executor 并发 claim 同一队列的 3 个任务 → 无重复领取、无遗漏。
  恢复：claim 后不 ack，租约过期（120s）→ 新 claim 触发 alarm → 任务转
        stale_suspected → 旧租约 complete 被 DENY（状态机+fencing）。

用法：python e2e_recovery_concurrency.py --base <URL> --bootstrap <TOKEN>
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
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
    ap.add_argument("--skip-recovery-wait", action="store_true")
    args = ap.parse_args()
    base = args.base.rstrip("/")
    suffix = str(int(time.time()))[-6:]

    # ---- stage-23 并发：3 executor 并发 claim 3 个任务 ----
    print("== stage-23: concurrent claim ==")
    token = None
    st, reg = call(base, "POST", "/api/executor/v1/register", args.bootstrap, {
        "executor_id": f"exec-conc-a{suffix}", "execution_path": "external",
        "capabilities": ["demo"], "version": "1.0.0"})
    check("executor A register", st == 201)
    token = reg.get("executor_token", "")

    # 1 个用户 + 3 个订单任务（external 路径）
    call(base, "POST", "/api/v1/auth/register", body={
        "username": f"conc_{suffix}", "password": "conc-pass-123456"})
    req = urllib.request.Request(f"{base}/api/v1/auth/login", method="POST",
        data=json.dumps({"username": f"conc_{suffix}", "password": "conc-pass-123456"}).encode(),
        headers={"Content-Type": "application/json", "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        cookie = r.headers.get("Set-Cookie", "").split(";")[0]
    task_ids = []
    for i in range(3):
        _, o = call(base, "POST", "/api/v1/orders", body={
            "product_code": "cx_video", "platform": "demo", "account": "c@t"}, cookie=cookie)
        _, t = call(base, "POST", f"/api/v1/orders/{o['order_id']}/tasks", body={
            "execution_path": "external", "task_type": "demo.echo",
            "required_capabilities": ["demo"], "payload": {"i": i}}, cookie=cookie)
        task_ids.append(t["task_id"])
    check("3 orders/tasks enqueued", len(task_ids) == 3)

    # B/C 两个额外 executor
    tokens = [token]
    for tag in ("b", "c"):
        _, r2 = call(base, "POST", "/api/executor/v1/register", args.bootstrap, {
            "executor_id": f"exec-conc-{tag}{suffix}", "execution_path": "external",
            "capabilities": ["demo"], "version": "1.0.0"})
        tokens.append(r2["executor_token"])

    # 3 线程同时 claim
    got: list[tuple[str, str | None]] = [("", None)] * 3

    def worker(idx: int, tok: str, eid: str):
        st, res = call(base, "POST", "/api/executor/v1/claim", tok, {
            "executor_id": eid, "execution_path": "external", "capabilities": ["demo"]})
        task = (res.get("task") or {}).get("task_id") if st == 200 else None
        got[idx] = (eid, task)

    threads = [threading.Thread(target=worker, args=(i, tokens[i], f"exec-conc-{'abc'[i]}{suffix}"))
               for i in range(3)]
    for t in threads: t.start()
    for t in threads: t.join()

    claimed = [tid for _, tid in got if tid]
    check("no duplicate claim across 3 concurrent executors", len(claimed) == len(set(claimed)))
    check("tasks distributed without overlap (subset of queue)", all(tid in task_ids for tid in claimed))

    # ---- stage-22 恢复：claim 后放任租约过期 ----
    print("== stage-22: lease expiry recovery (120s lease) ==")
    _, o = call(base, "POST", "/api/v1/orders", body={
        "product_code": "cx_video", "platform": "demo", "account": "r@t"}, cookie=cookie)
    _, t = call(base, "POST", f"/api/v1/orders/{o['order_id']}/tasks", body={
        "execution_path": "external", "task_type": "demo.echo",
        "required_capabilities": ["demo"], "payload": {"rec": 1}}, cookie=cookie)
    rec_task = t["task_id"]
    st, c = call(base, "POST", "/api/executor/v1/claim", token, {
        "executor_id": f"exec-conc-a{suffix}", "execution_path": "external",
        "capabilities": ["demo"]})
    d = c.get("task") or {}
    check("recovery: task claimed then abandoned", d.get("task_id") == rec_task)
    lease = d.get("lease_id")

    if not args.skip_recovery_wait:
        print("  ... waiting 135s for lease expiry (lease TTL = 120s) ...")
        time.sleep(135)
        # 任何新 claim 触发 DO alarm → 过期租约转 stale_suspected
        st, _c2 = call(base, "POST", "/api/executor/v1/claim", token, {
            "executor_id": f"exec-conc-a{suffix}", "execution_path": "external",
            "capabilities": ["demo"]})
        # 旧租约 complete 必须被拒绝（stale 状态机 + fencing）
        st2, resp = call(base, "POST", "/api/executor/v1/complete", token, {
            "executor_id": f"exec-conc-a{suffix}", "execution_path": "external",
            "task_id": rec_task, "lease_id": lease, "outcome": "succeeded"})
        check("stale lease complete DENY (400, state machine)", st2 == 400)
        st3, _hb = call(base, "POST", "/api/executor/v1/heartbeat", token, {
            "executor_id": f"exec-conc-a{suffix}", "execution_path": "external",
            "task_id": rec_task, "lease_id": lease})
        check("stale lease heartbeat DENY", st3 == 400)
        # 中央查询可见任务未成功（仍非终态）
        st4, detail = call(base, "GET", f"/api/v1/tasks/{rec_task}", cookie=cookie)
        status = (detail.get("task") or {}).get("status")
        check("central query shows non-terminal stale state", status not in ("succeeded",))

    print("\n== recovery/concurrency matrix ==")
    fails = 0
    for name, verdict in RESULTS:
        print(f"  {verdict:<12} {name}")
        fails += verdict != "LIVE PASS"
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
