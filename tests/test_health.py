# -*- coding: utf-8 -*-
"""健康检查接口测试：/health 返回 200 且字段完整。
用法: python tests/test_health.py
"""
import json
import urllib.request

op = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 绕过系统代理
r = op.open("http://127.0.0.1:8766/health", timeout=5)
data = json.loads(r.read().decode())
assert r.status == 200, f"HTTP {r.status}"
for key in ("status", "database", "queue"):
    assert key in data, f"缺少字段 {key}"
assert data["database"] == "ok", data
print("PASS /health:", data)
