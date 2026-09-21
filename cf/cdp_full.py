# -*- coding: utf-8 -*-
"""单次运行全流程: 启动Edge → 注入cookies → 打开hike课程 → 点击进学习页 → 抓 RAC/弹题请求"""
import json
import os
import subprocess
import time
import urllib.request

import websocket

W = r"C:\Users\Administrator\WorkBuddy\2026-09-11-10-49-22"
PORT = 9223
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
PROFILE = os.path.join(W, "cf", "browser_profile")
COURSE = "11497022"

os.environ["NO_PROXY"] = "*"
for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
    os.environ.pop(k, None)
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

net_log = []          # 抓包到的请求 URL
DETAIL = []


def get_pages():
    return [t for t in json.load(OPENER.open(f"http://127.0.0.1:{PORT}/json", timeout=10))
            if t.get("type") == "page"]


class Tab:
    def __init__(self, ws_url):
        self.ws = websocket.create_connection(ws_url, timeout=30)
        self._id = 0

    def send(self, method, params=None):
        self._id += 1
        self.ws.send(json.dumps({"id": self._id, "method": method, "params": params or {}}))
        self.ws.settimeout(30)
        while True:
            m = json.loads(self.ws.recv())
            if m.get("id") == self._id:
                return m.get("result", {})

    def drain(self, sec, keywords):
        """收 sec 秒网络事件，返回命中的 URL"""
        hits = []
        end = time.time() + sec
        self.ws.settimeout(0.5)
        while time.time() < end:
            try:
                m = json.loads(self.ws.recv())
                if m.get("method") == "Network.requestWillBeSent":
                    u = m["params"]["request"]["url"]
                    net_log.append(u)
                    if any(k.lower() in u.lower() for k in keywords):
                        hits.append(u)
            except websocket.WebSocketTimeoutException:
                continue
            except Exception:
                break
        self.ws.settimeout(30)
        return hits

    def eval(self, expr):
        r = self.send("Runtime.evaluate", {"expression": expr, "returnByValue": True})
        return r.get("result", {}).get("value")

    def nav(self, url):
        self.send("Page.navigate", {"url": url})


def main():
    # 0. 清理我们 profile 的旧 Edge
    subprocess.run(["taskkill", "/F", "/IM", "msedge.exe"], capture_output=True)
    time.sleep(2)
    subprocess.Popen([EDGE, f"--remote-debugging-port={PORT}", "--remote-allow-origins=*",
                      f"--user-data-dir={PROFILE}", "--no-first-run",
                      "--window-size=1280,900", "about:blank"],
                     creationflags=0x08000000)
    time.sleep(5)
    tab = Tab(get_pages()[0]["webSocketDebuggerUrl"])
    tab.send("Network.enable")
    tab.send("Page.enable")

    # 1. 注入 cookies
    root = json.load(open(os.path.join(W, "fuckCourse", "cookies.json"), encoding="utf-8"))
    n = sum(1 for c in root.get("zhs", [])
            if tab.send("Network.setCookie", {"name": c["name"], "value": c["value"],
                                              "domain": c.get("domain", ".zhihuishu.com"),
                                              "path": "/"}).get("success"))
    print(f"[1] cookies 注入 {n}/{len(root.get('zhs', []))}")

    # 2. SSO 刷新
    tab.nav("https://passport.zhihuishu.com/login?service=https%3A%2F%2Fonlineservice-api.zhihuishu.com%2Flogin%2Fgologin")
    time.sleep(6)
    print("[2] SSO 刷新完成, 当前:", tab.eval("location.href")[:80])

    # 3. 打开 hike 课程页
    tab.nav(f"https://hike.zhihuishu.com/hike/studentcourse?courseId={COURSE}")
    time.sleep(8)
    info = json.loads(tab.eval("""
    JSON.stringify({url: location.href, title: document.title,
      body: (document.body.innerText||'').replace(/\\s+/g,' ').slice(0,200),
      links: Array.from(document.querySelectorAll('a')).map(a=>({t:(a.textContent||'').trim().slice(0,25),h:a.href})).filter(x=>x.t).slice(0,20)})
    """))
    print(f"[3] 页面: {info['title'][:50]}")
    print("    正文:", info["body"][:150])
    for l in info["links"]:
        print("    链接:", l["t"], "->", l["h"][:90])

    # 4. 找课程入口并点击（关键词：学习/继续/进入）
    clicked = tab.eval("""
    (function(){
      var kws=['继续学习','开始学习','进入学习','去学习','学习'];
      for (var ki=0; ki<kws.length; ki++){
        var els=document.querySelectorAll('a,button,div[class*=btn],span');
        for (var i=0;i<els.length;i++){
          var t=(els[i].textContent||'').trim();
          if(t && t.indexOf(kws[ki])>=0 && t.length<12){ els[i].click(); return 'clicked['+kws[ki]+'] '+t; }
        }
      }
      return 'no entry';
    })()
    """)
    print(f"[4] 点击: {clicked}")

    # 5. 抓跳转与网络（15 秒）
    hits = tab.drain(15, ["recruit", "pointer", "popup", "studyvideo", "studyh5"])
    time.sleep(2)
    pages = get_pages()
    print(f"[5] 标签页:")
    for p in pages:
        print("    ", p.get("url", "")[:150])

    # 6. 若进入了学习页，进一步抓 15 秒弹题/进度请求
    studypage = None
    for p in pages:
        u = p.get("url", "")
        if "studyvideo" in u.lower() or "studyh5" in u.lower() or "recruitandcourseid" in u.lower():
            studypage = p
            break
    if studypage:
        st = Tab(studypage["webSocketDebuggerUrl"])
        st.send("Network.enable")
        hits2 = st.drain(15, ["recruit", "pointer", "popup", "loadvideo"])
        print(f"[6] 学习页网络线索: {len(hits2)}")
        for u in hits2[:10]:
            print("    ", u[:200])
        rac = __import__("re").findall(r"recruitAndCourseId[=:'\"]+([A-Za-z0-9_-]{6,})", studypage["url"])
        print(f"[7] URL 中的 RAC: {rac}")
    else:
        print("[6] 未进入学习页")

    print("\n=== 全部抓到的关键请求 ===")
    seen = set()
    for u in net_log:
        if any(k in u.lower() for k in ("recruit", "pointer", "popup")) and u not in seen:
            seen.add(u)
            print("  ", u[:220])


if __name__ == "__main__":
    main()
