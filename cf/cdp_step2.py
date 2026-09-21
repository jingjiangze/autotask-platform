# -*- coding: utf-8 -*-
"""第二步: 在 hike 课程页找到 11497022 的入口 → 点击 → 抓学习页 URL 里的 RAC"""
import json
import os
import subprocess
import sys
import time
import urllib.request

import websocket

W = r"C:\Users\Administrator\WorkBuddy\2026-09-11-10-49-22"
PORT = 9223
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
PROFILE = os.path.join(W, "cf", "browser_profile")

# 本机请求必须绕过环境代理
os.environ["NO_PROXY"] = "*"
for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
    os.environ.pop(k, None)
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def connect_target():
    data = json.load(OPENER.open(f"http://127.0.0.1:{PORT}/json", timeout=10))
    return [t for t in data if t.get("type") == "page"]


def make_ws(url):
    ws = websocket.create_connection(url, timeout=30, **{"http_no_proxy": True, "https_no_proxy": True} if False else {})
    _id = [0]

    def send(method, params=None):
        _id[0] += 1
        ws.send(json.dumps({"id": _id[0], "method": method, "params": params or {}}))
        while True:
            m = json.loads(ws.recv())
            if m.get("id") == _id[0]:
                return m.get("result", {})

    return ws, send


def main():
    try:
        pages = connect_target()
    except Exception:
        # Edge 不在：重新拉起并注入 cookies（与 cdp_probe 相同逻辑）
        subprocess.Popen([EDGE, f"--remote-debugging-port={PORT}", "--remote-allow-origins=*",
                          f"--user-data-dir={PROFILE}", "--no-first-run",
                          "--window-size=1280,900", "about:blank"],
                         creationflags=0x08000000)
        time.sleep(5)
        pages = connect_target()
        root = json.load(open(os.path.join(W, "fuckCourse", "cookies.json"), encoding="utf-8"))
        ws0, send0 = make_ws(pages[0]["webSocketDebuggerUrl"])
        send0("Network.enable")
        for c in root.get("zhs", []):
            send0("Network.setCookie", {"name": c["name"], "value": c["value"],
                                        "domain": c.get("domain", ".zhihuishu.com"), "path": "/"})
        send0("Page.navigate", {"url": "https://hike.zhihuishu.com/hike/studentcourse?courseId=11497022"})
        time.sleep(8)
        ws0.close()
        pages = connect_target()

    main_page = pages[0]
    print("当前标签页:", main_page.get("url", "")[:80])
    ws, send = make_ws(main_page["webSocketDebuggerUrl"])
    send("Page.enable")
    send("Network.enable")

    # 找到 11497022 相关元素
    js = r"""
    (function(){
      var hits = [];
      document.querySelectorAll('a,[onclick],div[class*=course],li').forEach(function(el){
        var t = (el.outerHTML||'').slice(0,2000);
        if (t.indexOf('11497022') >= 0) {
          hits.push({tag: el.tagName, cls: (el.className||'').toString().slice(0,60),
                     text: (el.textContent||'').trim().slice(0,40),
                     href: el.getAttribute('href') || '', onclick: (el.getAttribute('onclick')||'').slice(0,120)});
        }
      });
      return JSON.stringify(hits.slice(0,8));
    })()
    """
    r = send("Runtime.evaluate", {"expression": js, "returnByValue": True})
    hits = json.loads(r.get("result", {}).get("value", "[]"))
    for h in hits:
        print("HIT:", json.dumps(h, ensure_ascii=False)[:220])

    if not hits:
        print("页面上没有 11497022 元素，可能需要先进入课程详情页")
        return

    # 点击第一个命中的可点元素（模拟真实点击）
    click_js = r"""
    (function(){
      var els = document.querySelectorAll('a,[onclick]');
      for (var i=0;i<els.length;i++){
        if ((els[i].outerHTML||'').indexOf('11497022')>=0){
          els[i].click();
          return 'clicked: '+els[i].tagName+' '+(els[i].textContent||'').trim().slice(0,30);
        }
      }
      return 'not found';
    })()
    """
    r = send("Runtime.evaluate", {"expression": click_js, "returnByValue": True})
    print("click:", r.get("result", {}).get("value"))

    # 等待跳转/新标签，抓所有页面的 URL 和网络请求
    time.sleep(10)
    allp = connect_target()
    for p in allp:
        u = p.get("url", "")
        if any(k in u for k in ("study", "recruit", "zhihuishu")):
            print(f"TAB: {u[:160]}")

    evs_ws = None
    # 在新页面里监听网络 12 秒
    newp = [p for p in allp if "study" in p.get("url", "").lower() or "recruit" in p.get("url", "").lower()]
    tgt = newp[0] if newp else main_page
    try:
        ws2, send2 = make_ws(tgt["webSocketDebuggerUrl"])
        send2("Network.enable")
        got = []
        ws2.settimeout(0.5)
        end = time.time() + 12
        while time.time() < end:
            try:
                m = json.loads(ws2.recv())
                if m.get("method") == "Network.requestWillBeSent":
                    u = m["params"]["request"]["url"]
                    if any(k in u.lower() for k in ("recruit", "pointer", "popup")):
                        got.append(u)
            except websocket.WebSocketTimeoutException:
                continue
            except Exception:
                break
        ws2.close()
        print("\n新页面网络请求中的线索:")
        for u in got[:15]:
            print("  ", u[:200])
        if not got:
            print("  （无）")
    except Exception as e:
        print("新页面监听失败:", e)


if __name__ == "__main__":
    main()
