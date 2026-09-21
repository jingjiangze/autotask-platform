# -*- coding: utf-8 -*-
"""启动 cloudflared tunnel login，正确提取授权 URL 并打开默认浏览器"""
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
CFD = os.path.join(HERE, "cloudflared.exe")
LOG = os.path.join(HERE, "login_url.txt")

proc = subprocess.Popen([CFD, "tunnel", "login"],
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        text=True, encoding="utf-8", errors="replace",
                        creationflags=subprocess.CREATE_NO_WINDOW)

url = None
deadline = time.time() + 30
buf = ""
while time.time() < deadline:
    line = proc.stdout.readline()
    if not line:
        time.sleep(0.2)
        continue
    buf += line
    # URL 可能被换行截断：拼接续行
    m = re.search(r"https://dash\.cloudflare\.com/argotunnel\S*", buf.replace("\n", ""))
    if m:
        url = m.group(0)
        break

if not url:
    print("未能获取授权 URL，原始输出：")
    print(buf)
    sys.exit(1)

open(LOG, "w", encoding="utf-8").write(url)
print("授权 URL:")
print(url)
try:
    os.startfile(url)  # 用默认浏览器打开
    print("\n已在默认浏览器中打开，请在页面上选择域名并点击 Authorize 授权。")
except Exception as e:
    print(f"\n自动打开失败({e})，请手动复制上面的链接到浏览器打开。")
