# -*- coding: utf-8 -*-
"""浏览器登录流程: 打开知到门户 → 等用户扫码 → 自动抓课程卡 → 点击学习 → 抓 RAC + 弹题接口"""
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

    def drain(self, sec, kws):
        hits, net_log = [], []
        end = time.time() + sec
        self.ws.settimeout(0.5)
        while time.time() < end:
            try:
                m = json.loads(self.ws.recv())
                if m.get("method") == "Network.requestWillBeSent":
                    u = m["params"]["request"]["url"]
                    net_log.append(u)
                    if any(k.lower() in u.lower() for k in kws):
                        hits.append(u)
            except websocket.WebSocketTimeoutException:
                continue
            except Exception:
                break
        self.ws.settimeout(30)
        return hits, net_log

    def eval(self, expr):
        r = self.send("Runtime.evaluate", {"expression": expr, "returnByValue": True})
        return r.get("result", {}).get("value")

    def nav(self, url):
        self.send("Page.navigate", {"url": url})


def wait_login(tab, minutes=6):
    print("请在弹出的浏览器窗口中完成知到登录（扫码/账密均可）…")
    end = time.time() + minutes * 60
    while time.time() < end:
        try:
            url = tab.eval("location.href")
            txt = (tab.eval("(document.body.innerText||'').replace(/\\s+/g,' ').slice(0,120)")) or ""
            if "login" not in url and ("我的课程" in txt or "课程" in txt or "学习" in txt):
                print(f"检测到登录成功: {url[:90]}")
                return True
            time.sleep(5)
            print(f"  等待登录中… ({int((time.time()+minutes*60-time.time())//60)}分剩余) url={url[:70]}")
        except Exception:
            try:
                tab.ws.close()
            except Exception:
                pass
            pages = get_pages()
            tab.__init__(pages[0]["webSocketDebuggerUrl"])
        time.sleep(1)
    return False


def main():
    subprocess.run(["taskkill", "/F", "/IM", "msedge.exe"], capture_output=True)
    time.sleep(2)
    subprocess.Popen([EDGE, f"--remote-debugging-port={PORT}", "--remote-allow-origins=*",
                      f"--user-data-dir={PROFILE}", "--no-first-run",
                      "--window-size=1280,900",
                      "https://onlineweb.zhihuishu.com/onlinestu"],
                     creationflags=0x08000000)
    time.sleep(6)
    tab = Tab(get_pages()[0]["webSocketDebuggerUrl"])
    tab.send("Page.enable")
    tab.send("Network.enable")

    # 等待用户登录
    if not wait_login(tab, 6):
        print("超时未登录，退出")
        return

    # 登录后打开课程列表（带 courseId 的页面）
    tab.nav(f"https://hike.zhihuishu.com/hike/studentcourse?courseId={COURSE}")
    time.sleep(8)
    print("当前页面:", tab.eval("location.href")[:100])
    body = tab.eval("(document.body.innerText||'').replace(/\\s+/g,' ').slice(0,300)") or ""
    print("页面正文:", body[:200])

    # 找目标课程入口并点击
    clicked = tab.eval("""
    (function(){
      var els=document.querySelectorAll('a,button,div,li,span');
      for (var i=0;i<els.length;i++){
        var t=(els[i].textContent||'').trim();
        var h=(els[i].href||'');
        if((t.indexOf('大学生职业发展')>=0||h.indexOf('11497022')>=0)&&(t.length<60)){
          els[i].click(); return 'clicked: '+t.slice(0,30)+' '+(els[i].tagName);
        }
      }
      return 'no entry';
    })()
    """)
    print("[点击]", clicked)

    # 抓新标签页/网络中的 RAC 与弹题接口
    hits, net_log = tab.drain(15, ["recruit", "pointer", "popup", "study"])
    pages = get_pages()
    racs = set()
    for p in pages:
        u = p.get("url", "")
        print("TAB:", u[:150])
        import re as _re
        for m in _re.findall(r"recruitAndCourseId[=:\"']+([A-Za-z0-9_-]{6,})", u):
            racs.add(m)
    print("\n网络命中:")
    for u in hits[:15]:
        print("  ", u[:200])
        import re as _re
        for m in _re.findall(r"recruitAndCourseId[=:\"']+([A-Za-z0-9_-]{6,})", u):
            racs.add(m)
        for m in _re.findall(r"recruitId[=:\"']+(\d{6,})", u):
            racs.add("recruitId=" + m)

    # 若进入学习页，监听弹题/进度接口
    studyp = [p for p in get_pages() if "study" in p.get("url", "").lower()]
    if studyp:
        try:
            st = Tab(studyp[-1]["webSocketDebuggerUrl"])
            st.send("Network.enable")
            hits2, net2 = st.drain(20, ["pointer", "popup", "recruit", "studyservice"])
            print("\n学习页请求线索:")
            for u in (hits2 + [x for x in net2 if "recruit" in x.lower()])[:20]:
                print("  ", u[:220])
        except Exception as e:
            print("学习页监听失败:", e)

    print("\n=== RAC 候选 ===")
    for r in racs:
        print("  ", r)
    json.dump(sorted(racs), open(os.path.join(W, "cf", "rac_found.json"), "w", encoding="utf-8"))


if __name__ == "__main__":
    main()
