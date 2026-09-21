# -*- coding: utf-8 -*-
"""统计指定订单日志中的风控信号明细"""
import glob
import json
import os
import re
import sys

W = r"C:\Users\Administrator\WorkBuddy\2026-09-11-10-49-22"
PATTERNS = {
    "captcha_验证码": r"验证码",
    "captcha_en": r"captcha",
    "http_403": r"status[^0-9]{0,15}403|HTTP.{0,6}403|403\s*[-:]?\s*[Ff]orbidden",
    "forbidden": r"[Ff]orbidden|无权限|没有被授权",
    "risk_风控": r"风控|异常行为|操作频繁|次数限制|too many",
    "login_fail": r"登录失败|用户名或密码错误|[Ll]ogin [Ff]ailed",
    "network": r"[Mm]ax [Rr]etries|timed out|[Cc]onnection[A-Z]",
    "isPassed_false": r"isPassed.{0,3}false",
    "isPassed_true": r"isPassed.{0,3}true",
    "enc_签名": r"enc=|'enc'",}

oid_prefix = sys.argv[1]
d = glob.glob(os.path.join(W, "orders", oid_prefix + "*"))[0]
paths = [os.path.join(d, "log.txt"), os.path.join(d, "logs", "chaoxing.log")]
out = {"order": oid_prefix[:8], "signals": {}, "samples": {}}
for p in paths:
    if not os.path.exists(p):
        continue
    raw = open(p, "rb").read().decode("utf-8", errors="replace")
    for name, pat in PATTERNS.items():
        ms = re.findall(pat, raw)
        out["signals"][name] = out["signals"].get(name, 0) + len(ms)
        if ms and name not in out["samples"]:
            idx = re.search(pat, raw)
            out["samples"][name] = raw[max(0, idx.start() - 60):idx.end() + 80].replace("\n", " | ")[-140:]
print(json.dumps(out, ensure_ascii=False, indent=1))
