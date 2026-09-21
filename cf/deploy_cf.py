# -*- coding: utf-8 -*-
"""Cloudflare Tunnel 一键部署：把本地平台发布到你的域名
================================================
用法:
  1. 填好同目录 cf_config.json（api_token / domain / port）
  2. python deploy_cf.py            # 建隧道 + 配 DNS + 启动服务
     python deploy_cf.py status     # 查看隧道与 DNS 状态
     python deploy_cf.py stop       # 停止本地 cloudflared

所需 Token 权限（CF 后台 → My Profile → API Tokens → Create Custom Token）:
  Account  | Cloudflare Tunnel | Edit
  Account  | Account Settings  | Read
  Zone     | DNS               | Edit
  Zone     | Zone              | Read
  Zone Resources: Include → Specific zone → 你的域名
"""
import json
import os
import subprocess
import sys
import time

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
CONF = os.path.join(HERE, "cf_config.json")
CFD = os.path.join(HERE, "cloudflared.exe")
API = "https://api.cloudflare.com/client/v4"
STATE = os.path.join(HERE, "cf_state.json")


def load_conf():
    if not os.path.exists(CONF):
        print(f"缺少配置文件: {CONF}")
        print('请复制 cf_config.example.json 为 cf_config.json 并填写 api_token / domain')
        sys.exit(1)
    return json.load(open(CONF, encoding="utf-8"))


def api_raw(method, path, token, **kw):
    """底层 HTTP 出口：返回 (http_status, json_or_None, raw_text_or_None)。
    不抛异常，便于调用方按状态码做错误分类（§18）。
    api() 也基于它实现，保证本文件只有一个请求出口。"""
    r = requests.request(method, API + path,
                         headers={"Authorization": f"Bearer {token}"}, timeout=30, **kw)
    try:
        j = r.json()
    except Exception:
        j = None
    return r.status_code, j, (r.text[:200] if j is None else None)


def api(method, path, token, **kw):
    """保持原有行为不变：成功返回 result，失败抛 RuntimeError"""
    status, j, raw = api_raw(method, path, token, **kw)
    if j is None:
        raise RuntimeError(f"{status} {raw}")
    if not j.get("success"):
        raise RuntimeError(f"{status} {json.dumps(j.get('errors'), ensure_ascii=False)}")
    return j["result"]


def read_argo_token(cert_path=None):
    """解析 cert.pem 的 ARGO TUNNEL TOKEN -> {zoneID, accountID, apiToken}

    注意：cert.pem **不是 X.509 证书**，而是 base64 编码的 JSON。
    实测该内嵌 apiToken 对 Cloudflare Access 只读
    （POST /accounts/{id}/access/apps 返回 403 auth.forbidden），
    仅够读取诊断；需要写权限时必须另配令牌并存入 Credential Manager。
    """
    import base64
    import re as _re
    p = cert_path or os.path.join(os.path.expanduser("~"), ".cloudflared", "cert.pem")
    txt = open(p, encoding="utf-8", errors="replace").read()
    m = _re.search(r"-----BEGIN ARGO TUNNEL TOKEN-----(.*?)-----END ARGO TUNNEL TOKEN-----",
                   txt, _re.S)
    if not m:
        raise RuntimeError("cert.pem 中未找到 ARGO TUNNEL TOKEN 段")
    return json.loads(base64.b64decode("".join(m.group(1).split())))


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "deploy"
    if cmd not in ("deploy", "stop", "status"):
        print("用法: python deploy_cf.py [deploy|status|stop]")
        sys.exit(1)

    if cmd == "stop":
        subprocess.run(["taskkill", "/F", "/IM", "cloudflared.exe"], capture_output=True)
        print("已停止本地 cloudflared")
        return

    if cmd == "status":
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq cloudflared.exe"],
                             capture_output=True, text=True, encoding="gbk",
                             errors="replace").stdout or ""
        running = "cloudflared.exe" in out
        st = json.load(open(STATE, encoding="utf-8")) if os.path.exists(STATE) else {}
        print(f"cloudflared 进程: {'运行中' if running else '未运行'}")
        if st:
            print(f"域名: https://{st.get('domain')}  →  本地端口 {st.get('port')}")
            print(f"隧道 ID: {st.get('tunnel_id')}  账号: {st.get('account_id')}")
            if not running:
                print("提示: 重新启动用 python deploy_cf.py")
        else:
            print("尚未部署过（无 cf_state.json）")
        return

    conf = load_conf()
    token = conf["api_token"].strip()
    domain = conf["domain"].strip().lower()
    port = int(conf.get("port", 8766))
    root = ".".join(domain.split(".")[-2:])  # 主域名（example.com）

    # 1. 校验 token + 定位账号
    accounts = api("GET", "/accounts", token)
    if not accounts:
        raise RuntimeError("该 Token 未绑定任何账号（权限需含 Account Settings: Read）")
    acct = accounts[0]["id"]
    print(f"✔ 账号: {accounts[0].get('name')} ({acct})")

    # 2. 定位 zone
    zones = api("GET", f"/zones?name={root}", token)
    if not zones:
        raise RuntimeError(f"未找到域名 {root}（Token 的 Zone 权限要包含该域名）")
    zone_id = zones[0]["id"]
    print(f"✔ 域名: {root} ({zone_id})")

    # 3. 找/建隧道
    tunnels = api("GET", f"/accounts/{acct}/cfd_tunnel?is_deleted=false", token)
    tname = f"wk-platform-{port}"
    tunnel = next((t for t in tunnels if t["name"] == tname), None)
    if tunnel is None:
        tunnel = api("POST", f"/accounts/{acct}/cfd_tunnel", token,
                     json={"name": tname, "config_src": "cloudflare"})
        print(f"✔ 已创建隧道: {tname} ({tunnel['id']})")
    else:
        print(f"✔ 复用已有隧道: {tname} ({tunnel['id']})")

    tid = tunnel["id"]

    # 4. ingress 配置: 域名 → 本地端口
    api("PUT", f"/accounts/{acct}/cfd_tunnel/{tid}/configurations", token,
        json={"config": {"ingress": [
            {"hostname": domain, "service": f"http://127.0.0.1:{port}"},
            {"service": "http_status:404"}]}})
    print(f"✔ 已配置路由: {domain} → http://127.0.0.1:{port}")

    # 5. DNS CNAME
    recs = api("GET", f"/zones/{zone_id}/dns_records?name={domain}", token)
    target = f"{tid}.cfargotunnel.com"
    if recs:
        api("PUT", f"/zones/{zone_id}/dns_records/{recs[0]['id']}", token,
            json={"type": "CNAME", "name": domain, "content": target, "proxied": True})
        print(f"✔ 已更新 DNS: {domain} → {target}")
    else:
        api("POST", f"/zones/{zone_id}/dns_records", token,
            json={"type": "CNAME", "name": domain, "content": target, "proxied": True})
        print(f"✔ 已创建 DNS: {domain} → {target}")

    # 6. 取隧道 token 并启动
    ttoken = api("GET", f"/accounts/{acct}/cfd_tunnel/{tid}/token", token)
    json.dump({"account_id": acct, "zone_id": zone_id, "tunnel_id": tid,
               "domain": domain, "port": port}, open(STATE, "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)

    subprocess.run(["taskkill", "/F", "/IM", "cloudflared.exe"], capture_output=True)
    time.sleep(1)
    log = open(os.path.join(HERE, "cloudflared.log"), "ab")
    p = subprocess.Popen([CFD, "tunnel", "--no-autoupdate", "run", "--token", ttoken],
                         stdout=log, stderr=subprocess.STDOUT,
                         creationflags=subprocess.CREATE_NO_WINDOW)
    time.sleep(20)
    if p.poll() is None:
        print(f"\n🎉 发布成功: https://{domain}")
        print("（cloudflared 已在后台静默运行；停止: python deploy_cf.py stop）")
    else:
        print("\n⚠ cloudflared 启动失败，请看 cf/cloudflared.log")


if __name__ == "__main__":
    main()
