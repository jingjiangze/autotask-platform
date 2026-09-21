# -*- coding: utf-8 -*-
"""从 cert.pem 中提取授权域名线索

注：cert.pem 实为 ARGO TUNNEL TOKEN（base64 编码的 JSON），
解析逻辑已收敛到 deploy_cf.read_argo_token()，本脚本不再重复实现。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import deploy_cf

p = os.path.join(os.path.expanduser("~"), ".cloudflared", "cert.pem")
raw = open(p, "r", encoding="utf-8").read()

print("=== 文件头部 ===")
print("\n".join(raw.splitlines()[:3]))

print("=== 令牌结构化内容 ===")
try:
    j = deploy_cf.read_argo_token(p)
    # 只打印非敏感字段；apiToken 仅回显长度与前后缀，不输出明文
    for k in ("zoneID", "accountID"):
        print(f"  {k:10} = {j.get(k)}")
    tok = j.get("apiToken") or ""
    print(f"  apiToken   = {tok[:4]}…{tok[-4:]}  (长度 {len(tok)}，不回显明文)")
except Exception as e:
    print("  解析失败:", e)
