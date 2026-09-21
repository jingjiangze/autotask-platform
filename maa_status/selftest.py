# -*- coding: utf-8 -*-
"""本机自检：启动服务 → 验证 认证 / 探针 / API → 关闭。

用法：<venv python> selftest.py
"""
import base64
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
CONF = json.load(open(os.path.join(HERE, "config.json"), encoding="utf-8"))
BASE = "http://127.0.0.1:%d" % CONF["port"]
AUTH = "Basic " + base64.b64encode(
    ("%s:%s" % (CONF["username"], CONF["password"])).encode()).decode()


def port_open(port):
    s = socket.socket()
    try:
        return s.connect_ex(("127.0.0.1", port)) == 0
    finally:
        s.close()


def wait_port(port, timeout=15):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if port_open(port):
            return True
        time.sleep(0.4)
    return False


def get(url, auth=None, timeout=20):
    req = urllib.request.Request(url, headers={"Authorization": auth} if auth else {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "ignore")


proc = None
# 生产环境本来就常驻一个实例（计划任务 MAA-StatusBoard）。若端口已在监听，
# 不要另起一个——那一个会因为单实例锁立刻退出，而下面所有检查其实是在打
# 已运行的那个服务，容易把「测试了旧实例」误当成「新代码通过」。
REUSED = port_open(CONF["port"])
if not REUSED:
    proc = subprocess.Popen([PY, os.path.join(HERE, "server.py")],
                            cwd=HERE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
results = []
try:
    if not wait_port(CONF["port"]):
        print("FAIL 服务未起来")
        sys.exit(1)
    time.sleep(0.5)
    if REUSED:
        print("== 注意：%d 已在监听，复用该实例进行自检（未启动新实例）。" % CONF["port"])
        print("   要验证新代码，先停服务：taskkill 掉 server.py 进程后重跑。")

    code, body = get(BASE + "/health")
    results.append(("探针 /health 免认证", code == 200 and '"ok"' in body, code))

    code, body = get(BASE + "/")
    results.append(("匿名访问 / 返回登录页", code == 200 and "登录" in body, code))

    code, _ = get(BASE + "/api/status")
    results.append(("匿名访问 /api/status 被拒", code == 401, code))

    code, body = get(BASE + "/api/status", AUTH)
    ok = code == 200
    data = {}
    if ok:
        data = json.loads(body)
    results.append(("Basic 认证后 /api/status 200", ok, code))

    # 表单登录 → 拿 Cookie（不跟随重定向，否则 Set-Cookie 落在最终响应上）
    import urllib.parse

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    form = urllib.parse.urlencode({"username": CONF["username"],
                                   "password": CONF["password"]}).encode()
    cookie = None
    hdrs = {}
    try:
        op = urllib.request.build_opener(NoRedirect)
        req = urllib.request.Request(
            BASE + "/login", data=form,
            headers={"Content-Type": "application/x-www-form-urlencoded"})
        try:
            with op.open(req, timeout=20) as r:
                hdrs, code = r.headers, r.status
        except urllib.error.HTTPError as e:
            hdrs, code = e.headers, e.code
        sc = hdrs.get_all("Set-Cookie") or []
        cookie = [c.split(";")[0] for c in sc if c.startswith("maa_sess=")]
        results.append(("表单登录 302 + 下发 Cookie", code == 302 and bool(cookie), (code, sc)))
    except Exception as e:
        results.append(("表单登录 302 + 下发 Cookie", False, repr(e)))

    if cookie:
        req = urllib.request.Request(BASE + "/", headers={"Cookie": cookie[0]})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                body, code = r.read().decode("utf-8", "ignore"), r.status
        except urllib.error.HTTPError as e:
            code, body = e.code, ""
        results.append(("Cookie 会话可访问页面", code == 200 and "MAA 夜间进度" in body, code))
        req = urllib.request.Request(BASE + "/api/status", headers={"Cookie": cookie[0]})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                code = r.status
        except urllib.error.HTTPError as e:
            code = e.code
        results.append(("Cookie 会话可访问 API", code == 200, code))

    # 错误密码必须被拒
    bad_form = urllib.parse.urlencode({"username": CONF["username"], "password": "x"}).encode()
    try:
        urllib.request.urlopen(urllib.request.Request(BASE + "/login", data=bad_form,
                              headers={"Content-Type": "application/x-www-form-urlencoded"}), timeout=20)
        results.append(("错误密码被拒", False, "竟然 200"))
    except urllib.error.HTTPError as e:
        results.append(("错误密码被拒", e.code == 401, e.code))

    code, _ = get(BASE + "/api/report/2026-09-21", AUTH)
    results.append(("报告全文接口", code == 200, code))

    code, _ = get(BASE + "/api/report/../../windows/win.ini", AUTH)
    results.append(("路径穿越被拒 (404)", code == 404, code))

    # 计划任务面板必须真的取到数据（COM 线程初始化缺陷的回归哨兵：
    # 服务端是多线程，win32com 忘调 CoInitialize 时会静默返回 error 字段）
    tk = (data or {}).get("tasks") or {}
    results.append(("计划任务面板有数据（无 COM 错误）",
                    tk.get("error") is None and len(tk.get("tasks") or []) >= 1,
                    tk.get("error") or "任务数=%d" % len(tk.get("tasks") or [])))

    # 第二实例必须退出
    p2 = subprocess.Popen([PY, os.path.join(HERE, "server.py")], cwd=HERE,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(3.5)
    exited = p2.poll() is not None
    results.append(("第二实例自动退出（单实例锁）", exited, p2.poll()))
    if not exited:
        p2.kill()

    print("== 数据快照 ==")
    if data:
        sn = data["sanity"]
        print("  状态:", "运行中" if data["running"] else "未运行",
              "| 窗口内:", data["in_window"],
              "| 最新理智:", sn["current"],
              "| 实测净消耗:", sn["track"]["spent"],
              "| 肉鸽截图:", data["roguelike"]["count"],
              "| 关机:", data["tasks"]["shutdown_next"])
        print("  队列:", ", ".join("%s=%s" % (r["key"], r["state"]) for r in data["queue"]["rows"]))
finally:
    if proc is not None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()

print("== 自检结果 ==")
bad = 0
for name, ok, extra in results:
    print(("  PASS " if ok else "  FAIL ") + name + ("  (%s)" % extra if not ok else ""))
    bad += 0 if ok else 1
print("FAILED:", bad)
sys.exit(1 if bad else 0)
