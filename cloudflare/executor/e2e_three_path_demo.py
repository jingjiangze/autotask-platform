"""stage-cloud-34 — 三路 demo E2E：internal/external 走真实中央链路。

路径：login → 建单 → 各路径建 demo.echo 任务 → GitHub runner pull → 执行 →
complete → 中央查询最终态 + result_json 工件。Local 真实引擎链路已由
e2e_control.py 覆盖（12/12 PASS），此处不重复。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

UA = "autotask-e2e/1.0"


def call(method: str, url: str, token=None, cookie=None, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, method=method, data=data, headers={
        "Content-Type": "application/json", "User-Agent": UA,
        **({"Cookie": cookie} if cookie else {}),
        **({"Authorization": f"Bearer {token}"} if token else {}),
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def run_path(base: str, cookie: str, oid: str, path: str) -> dict:
    rep: dict = {"path": path}
    st, t = call("POST", f"{base}/api/v1/orders/{oid}/tasks", cookie=cookie,
                 body={"execution_path": path, "task_type": "demo.echo",
                       "required_capabilities": ["demo"],
                       "payload": {"msg": f"hello-from-{path}", "ts": int(time.time() * 1000)}})
    rep["create"] = st
    if st != 201:
        rep["detail"] = t
        return rep
    tid = t["task_id"]
    rep["task_id"] = tid
    for _ in range(40):  # ~3min：GitHub runner poll 10s
        time.sleep(4.5)
        st, d = call("GET", f"{base}/api/v1/tasks/{tid}", cookie=cookie)
        task = (d.get("task") or {})
        rep["status"] = task.get("status")
        if task.get("status") in ("succeeded", "failed", "canceled"):
            atts = d.get("attempts") or [{}]
            rep["executor_id"] = atts[0].get("executor_id")
            art = next((a for a in d.get("artifacts", []) if a["artifact_type"] == "result_json"), None)
            if art:
                st2, res = call("GET", f"{base}/api/v1/orders/{oid}/artifacts/{art['id']}", cookie=cookie)
                rep["artifact_status"] = st2
                try:
                    rep["artifact"] = json.loads(res) if isinstance(res, (bytes, str)) else res
                except Exception:
                    rep["artifact"] = str(res)[:120]
            return rep
    return rep


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="https://executor.jiangjiangze.icu")
    args = ap.parse_args()
    base = args.base

    ok = True
    for i in range(5):
        try:
            st, _ = call("GET", f"{base}/health")
            if st == 200:
                break
        except urllib.error.URLError:
            time.sleep(0.5 * (i + 1))
    print(f"check central health                     PASS" if st == 200 else "FAIL health")
    ok &= st == 200

    st, r = call("POST", f"{base}/api/v1/auth/login",
                 body={"username": "real_owner", "password": "real-pass-123456"})
    cookie = ""
    try:
        req = urllib.request.Request(f"{base}/api/v1/auth/login", method="POST",
                                     data=json.dumps({"username": "real_owner", "password": "real-pass-123456"}).encode(),
                                     headers={"Content-Type": "application/json", "User-Agent": UA})
        with urllib.request.urlopen(req, timeout=30) as r2:
            cookie = r2.headers.get("Set-Cookie", "").split(";")[0]
    except Exception as e:
        pass
    print(f"check real_owner login                   {'PASS' if cookie else 'FAIL'}")
    ok &= bool(cookie)

    st, o = call("POST", f"{base}/api/v1/orders", cookie=cookie,
                 body={"product_code": "cx_video", "platform": "chaoxing", "account": "demo-e2e"})
    print(f"check demo order create                  {'PASS' if st == 201 else 'FAIL ' + str(o)[:80]}")
    ok &= st == 201
    oid = o["order_id"]

    results = {}
    for path in ("internal", "external"):
        rep = run_path(base, cookie, oid, path)
        results[path] = rep
        good = (rep.get("create") == 201 and rep.get("status") == "succeeded"
                and rep.get("executor_id", "").startswith(f"exec-{path}-01")
                and isinstance(rep.get("artifact"), dict)
                and rep["artifact"].get("echo", {}).get("msg") == f"hello-from-{path}")
        print(f"check {path:<9} demo E2E (pull→run→result)   {'PASS' if good else 'FAIL ' + json.dumps(rep, ensure_ascii=False)[:200]}")
        ok &= good

    print("SUMMARY: " + ("3/3 PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
