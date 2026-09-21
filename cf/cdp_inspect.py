# -*- coding: utf-8 -*-
"""通用页面侦察：URL/标题/链接/登录态"""
import json
import os
import sys
import time
import urllib.request

import websocket

W = r"C:\Users\Administrator\WorkBuddy\2026-09-11-10-49-22"
PORT = 9223
os.environ["NO_PROXY"] = "*"
for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
    os.environ.pop(k, None)
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

url_filter = sys.argv[1] if len(sys.argv) > 1 else None


def get_pages():
    return [t for t in json.load(OPENER.open(f"http://127.0.0.1:{PORT}/json", timeout=10))
            if t.get("type") == "page"]


def make_ws(url):
    ws = websocket.create_connection(url, timeout=30)
    _id = [0]

    def send(method, params=None):
        _id[0] += 1
        ws.send(json.dumps({"id": _id[0], "method": method, "params": params or {}}))
        while True:
            m = json.loads(ws.recv())
            if m.get("id") == _id[0]:
                return m.get("result", {})

    return ws, send


pages = get_pages()
print("标签页数:", len(pages))
for i, p in enumerate(pages):
    print(f"  [{i}] {p.get('url','')[:100]}")

# 找匹配的页面或第一个
target = pages[0]
if url_filter:
    for p in pages:
        if url_filter in p.get("url", ""):
            target = p
            break

ws, send = make_ws(target["webSocketDebuggerUrl"])
r = send("Runtime.evaluate", {"expression": """
JSON.stringify({
  url: location.href,
  title: document.title,
  login: !!(document.querySelector('.avatar,img[class*=avatar],.username,[class*=user]')),
  bodySnippet: (document.body.innerText||'').replace(/\\s+/g,' ').slice(0,300),
  links: Array.from(document.querySelectorAll('a')).map(a=>({t:(a.textContent||'').trim().slice(0,30),h:a.href})).filter(x=>x.h&&x.t).slice(0,25)
})
""", "returnByValue": True})
info = json.loads(r.get("result", {}).get("value", "{}"))
print("\nURL:", info.get("url"))
print("标题:", info.get("title"))
print("疑似登录态:", info.get("login"))
print("正文片段:", info.get("bodySnippet"))
print("\n链接:")
for l in info.get("links", []):
    print(f"  {l['t'][:28]:30s} {l['h'][:90]}")
