# -*- coding: utf-8 -*-
"""CDP 浏览器驱动：注入知到 cookies → 打开 hike 课程页 → 抓 recruitAndCourseId"""
import json
import os
import subprocess
import sys
import time
import urllib.request

import websocket

W = r"C:\Users\Administrator\WorkBuddy\2026-09-11-10-49-22"
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
PROFILE = os.path.join(W, "cf", "browser_profile")
PORT = 9223


def launch():
    # 只清理我们 profile 的实例，不动用户自己的浏览器
    ps = f"Get-CimInstance Win32_Process -Filter \"Name='msedge.exe'\" | Where-Object {{$_.CommandLine -like '*browser_profile*'}} | ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force }}"
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True)
    time.sleep(2)
    subprocess.Popen([EDGE, f"--remote-debugging-port={PORT}",
                      "--remote-allow-origins=*",
                      f"--user-data-dir={PROFILE}", "--no-first-run",
                      "--window-size=1280,900", "about:blank"],
                     creationflags=0x08000000)
    time.sleep(5)


def cdp_connect():
    data = json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json"))
    pages = [t for t in data if t.get("type") == "page"]
    ws = websocket.create_connection(pages[0]["webSocketDebuggerUrl"], timeout=30)
    _id = [0]

    def send(method, params=None):
        _id[0] += 1
        ws.send(json.dumps({"id": _id[0], "method": method, "params": params or {}}))
        while True:
            msg = json.loads(ws.recv())
            if msg.get("id") == _id[0]:
                return msg.get("result", {})

    def events(sec):
        end = time.time() + sec
        got = []
        ws.settimeout(0.5)
        while time.time() < end:
            try:
                m = json.loads(ws.recv())
                if m.get("method", "").startswith("Network."):
                    got.append(m)
            except websocket.WebSocketTimeoutException:
                continue
            except Exception:
                break
        ws.settimeout(30)
        return got

    return ws, send, events


def main():
    if "--kill" in sys.argv:
        subprocess.run(["taskkill", "/F", "/IM", "msedge.exe"], capture_output=True)
        time.sleep(2)
    launch()
    ws, send, events = cdp_connect()
    send("Network.enable")
    send("Page.enable")

    # 1. 注入扫码 cookies（.zhihuishu.com 全域）
    root = json.load(open(os.path.join(W, "fuckCourse", "cookies.json"), encoding="utf-8"))
    n = 0
    for c in root.get("zhs", []):
        p = {"name": c["name"], "value": c["value"], "domain": c.get("domain", ".zhihuishu.com"),
             "path": c.get("path", "/")}
        r = send("Network.setCookie", p)
        if r.get("success"):
            n += 1
    print(f"cookies 注入: {n}/{len(root.get('zhs', []))}")

    # 2. 刷新 SSO（走一次 onlineservice 服务链）
    send("Page.navigate", {"url": "https://passport.zhihuishu.com/login?service=https%3A%2F%2Fonlineservice-api.zhihuishu.com%2Flogin%2Fgologin"})
    time.sleep(6)

    # 3. 打开 hike 课程列表页
    send("Network.enable")
    send("Page.navigate", {"url": "https://hike.zhihuishu.com/hike/studentcourse?courseId=11497022"})
    time.sleep(8)
    evs = events(10)
    urls = []
    for m in evs:
        u = (m["params"].get("request") or {}).get("url", "")
        if "recruit" in u.lower() or "pointer" in u.lower():
            urls.append(u)
    # 页面里也搜
    js = {"expression": "document.documentElement.innerHTML.length"}
    info = send("Runtime.evaluate", js)
    print("page len:", info.get("result", {}).get("value"))

    # 4. 用 JS 抓页面里所有 recruit 线索
    js2 = {"expression": "JSON.stringify((document.documentElement.innerHTML.match(/recruit[^\"']{0,40}/gi)||[]).slice(0,20))"}
    r2 = send("Runtime.evaluate", js2)
    val = r2.get("result", {}).get("value", "[]")
    print("page recruit hints:", val[:500])

    print("\n网络请求中的 recruit 线索:")
    for u in urls[:10]:
        print("  ", u[:180])
    if not urls:
        print("  （无）")

    ws.close()


if __name__ == "__main__":
    main()
