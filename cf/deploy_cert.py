# -*- coding: utf-8 -*-
"""Cloudflare Tunnel 发布（证书模式，无需 API Token）
================================================
前置: 已执行 `cloudflared tunnel login` 并在浏览器里授权域名
      → 生成 %USERPROFILE%\\.cloudflared\\cert.pem

用法:
    python deploy_cert.py <完整访问域名> [本地端口]
    python deploy_cert.py order.example.com 8766
    python deploy_cert.py status
    python deploy_cert.py stop
"""
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
CFD = os.path.join(HERE, "cloudflared.exe")
CF_HOME = os.path.join(os.path.expanduser("~"), ".cloudflared")
CERT = os.path.join(CF_HOME, "cert.pem")
CONFIG_YML = os.path.join(CF_HOME, "wk_config.yml")
STATE = os.path.join(HERE, "cf_state.json")
TUNNEL_NAME = "wk-platform"


def _clean_env():
    """cloudflared 必须裸连：清掉本机残留代理变量，否则回取资源会失败"""
    env = os.environ.copy()
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        env.pop(k, None)
    env["NO_PROXY"] = "*"
    return env


def run(args, check=True):
    r = subprocess.run([CFD] + args, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=_clean_env())
    if check and r.returncode != 0:
        raise RuntimeError(f"cloudflared {' '.join(args)} 失败:\n{r.stdout}\n{r.stderr}")
    return (r.stdout or "") + (r.stderr or "")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd in ("status", "stop"):
        if cmd == "stop":
            subprocess.run(["taskkill", "/F", "/IM", "cloudflared.exe"], capture_output=True)
            print("已停止本地 cloudflared")
            return
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq cloudflared.exe"],
                             capture_output=True, text=True, encoding="gbk",
                             errors="replace").stdout or ""
        st = json.load(open(STATE, encoding="utf-8")) if os.path.exists(STATE) else {}
        print(f"cloudflared: {'运行中' if 'cloudflared.exe' in out else '未运行'}")
        if st:
            print(f"域名: https://{st.get('domain')} → 127.0.0.1:{st.get('port')}")
        return

    if not cmd:
        print(__doc__)
        sys.exit(1)
    domain = cmd.strip().lower()
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 8766

    if not os.path.exists(CERT):
        print("✗ 未找到授权凭证 cert.pem")
        print("  请先运行: cloudflared tunnel login 并在浏览器中授权域名")
        sys.exit(1)
    print("✔ 授权凭证已就绪")

    # 1. 创建/复用命名隧道
    out = run(["tunnel", "list", "-o", "json"])
    tunnels = []
    try:
        tunnels = json.loads(out)
    except Exception:
        pass
    if not any(t.get("name") == TUNNEL_NAME for t in tunnels):
        print(run(["tunnel", "create", TUNNEL_NAME]).strip())
    else:
        print(f"✔ 隧道已存在: {TUNNEL_NAME}")
    tid = next((t["id"] for t in tunnels if t.get("name") == TUNNEL_NAME), None)
    if tid is None:
        for t in json.loads(run(["tunnel", "list", "-o", "json"])):
            if t.get("name") == TUNNEL_NAME:
                tid = t["id"]
    print(f"✔ 隧道 ID: {tid}")

    # 2. 域名解析（cloudflared 自动创建 CNAME）
    r = run(["tunnel", "route", "dns", "wk-platform"], check=False)
    out = run(["tunnel", "route", "dns", TUNNEL_NAME, domain], check=False)
    print(out.strip() or r.strip())

    # 3. 写本机配置
    cred = os.path.join(CF_HOME, f"{tid}.json")
    with open(CONFIG_YML, "w", encoding="utf-8") as f:
        f.write(f"""tunnel: {tid}
credentials-file: {cred}

ingress:
  - hostname: {domain}
    service: http://127.0.0.1:{port}
  - service: http_status:404
""")
    print(f"✔ 配置写入: {CONFIG_YML}")

    json.dump({"tunnel_id": tid, "domain": domain, "port": port},
              open(STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    # 4. 后台静默启动
    subprocess.run(["taskkill", "/F", "/IM", "cloudflared.exe"], capture_output=True)
    time.sleep(1)
    log = open(os.path.join(HERE, "cloudflared.log"), "ab")
    p = subprocess.Popen([CFD, "tunnel", "--config", CONFIG_YML, "--no-autoupdate", "run", TUNNEL_NAME],
                         stdout=log, stderr=subprocess.STDOUT,
                         creationflags=subprocess.CREATE_NO_WINDOW, env=_clean_env())
    time.sleep(18)
    if p.poll() is None:
        print(f"\n🎉 发布成功: https://{domain}")
    else:
        print("\n⚠ 启动失败，请看 cf\\cloudflared.log")


if __name__ == "__main__":
    main()
