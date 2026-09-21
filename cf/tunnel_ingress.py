# -*- coding: utf-8 -*-
"""tunnel_ingress.py — cloudflared ingress 配置的薄封装

新增原因：项目只有 cf/deploy_cert.py，而它会**整体覆写** wk_config.yml（只留一个 hostname）。
用它加域名会把 order. 规则冲掉。本模块只做「增删单个 hostname」，保留其它条目。

设计：查询 → 判断 → 最小修改 → 回读验证；幂等；结构化返回。
用 yaml 解析而非行文本拼装（项目已装 PyYAML），避免缩进/顺序出错。
不重启进程 —— 由调用方决定（见 reload_cloudflared）。
"""
import json
import os
import shutil
import subprocess
import sys
import time

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
CF_HOME = os.path.join(os.path.expanduser("~"), ".cloudflared")
CFG = os.path.join(CF_HOME, "wk_config.yml")
CFD = os.path.join(HERE, "cloudflared.exe")


def _result(success, action, error_code=None, retryable=False, **kw):
    d = {"success": success, "action": action, "error_code": error_code,
         "retryable": retryable}
    d.update(kw)
    return d


def load_config():
    if not os.path.exists(CFG):
        return None
    with open(CFG, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _save(cfg):
    """写回配置。锁文件式的 403 兜底始终放在最后。"""
    ing = [r for r in (cfg.get("ingress") or []) if r.get("hostname")]
    ing.append({"service": "http_status:404"})
    cfg["ingress"] = ing
    with open(CFG, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False, default_flow_style=False)


def list_hostnames():
    cfg = load_config()
    if cfg is None:
        return _result(False, "list_ingress", error_code="CONFIG_NOT_FOUND",
                       message=f"未找到 {CFG}")
    hosts = [{"hostname": r.get("hostname"), "service": r.get("service")}
             for r in (cfg.get("ingress") or []) if r.get("hostname")]
    return _result(True, "list_ingress", config=CFG, count=len(hosts), hostnames=hosts)


def _backup():
    if os.path.exists(CFG):
        dst = f"{CFG}.bak_{time.strftime('%Y%m%d_%H%M%S')}"
        shutil.copy2(CFG, dst)
        return dst
    return None


def ensure_hostname(hostname, service, origin_request=None):
    """幂等新增/更新单个 hostname，保留其它条目"""
    cfg = load_config()
    if cfg is None:
        return _result(False, "ensure_ingress", error_code="CONFIG_NOT_FOUND",
                       message=f"未找到 {CFG}")
    if "tunnel" not in cfg:
        return _result(False, "ensure_ingress", error_code="BAD_CONFIG",
                       message="配置缺少 tunnel 段，拒绝改写")

    cur = next((r for r in (cfg.get("ingress") or []) if r.get("hostname") == hostname), None)
    if cur and cur.get("service") == service and \
            (origin_request is None or cur.get("originRequest") == origin_request):
        return _result(True, "ensure_ingress", status="already_exists",
                       hostname=hostname, service=service,
                       message="已存在且配置一致，未做修改")

    bk = _backup()
    entry = {"hostname": hostname, "service": service}
    if origin_request:
        entry["originRequest"] = origin_request
    cfg["ingress"] = [r for r in (cfg.get("ingress") or [])
                      if r.get("hostname") != hostname] + [entry]
    _save(cfg)

    after = load_config()
    ok = any(r.get("hostname") == hostname and r.get("service") == service
             for r in (after.get("ingress") or []))
    return _result(True, "ensure_ingress",
                   status="updated" if cur else "created",
                   hostname=hostname, service=service, verified=ok,
                   backup=bk,
                   all_hostnames=[r.get("hostname") for r in (after.get("ingress") or [])
                                  if r.get("hostname")])


def remove_hostname(hostname):
    cfg = load_config()
    if cfg is None:
        return _result(False, "remove_ingress", error_code="CONFIG_NOT_FOUND")
    before = [r.get("hostname") for r in (cfg.get("ingress") or []) if r.get("hostname")]
    if hostname not in before:
        return _result(True, "remove_ingress", status="not_found", hostname=hostname)
    bk = _backup()
    cfg["ingress"] = [r for r in (cfg.get("ingress") or []) if r.get("hostname") != hostname]
    _save(cfg)
    after = [r.get("hostname") for r in (load_config().get("ingress") or [])
             if r.get("hostname")]
    return _result(True, "remove_ingress", status="removed", hostname=hostname,
                   verified=hostname not in after, backup=bk, all_hostnames=after)


def route_dns(hostname, tunnel_name="wk-platform"):
    """建 CNAME 指向隧道（证书模式，无需 API Token）"""
    env = os.environ.copy()
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        env.pop(k, None)
    env["NO_PROXY"] = "*"
    r = subprocess.run([CFD, "tunnel", "route", "dns", tunnel_name, hostname],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=env, timeout=120)
    out = ((r.stdout or "") + (r.stderr or "")).strip()
    ok = r.returncode == 0 or "already exists" in out.lower() or "Added CNAME" in out
    return _result(ok, "route_dns", status="ok" if ok else "failed",
                   hostname=hostname, output=out[:300],
                   error_code=None if ok else "DNS_ROUTE_FAILED")


def reload_cloudflared(wait_s=60):
    """杀掉 cloudflared，交由看护进程按当前配置重新拉起（health_manager 每 30s 检查）"""
    subprocess.run(["taskkill", "/F", "/IM", "cloudflared.exe"], capture_output=True,
                   text=True, encoding="gbk", errors="replace")
    t0 = time.time()
    while time.time() - t0 < wait_s:
        time.sleep(3)
        r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq cloudflared.exe"],
                           capture_output=True, text=True, encoding="gbk", errors="replace")
        if "cloudflared.exe" in (r.stdout or ""):
            return _result(True, "reload_cloudflared", status="restarted",
                           elapsed_s=round(time.time() - t0, 1))
    return _result(False, "reload_cloudflared", status="not_restarted",
                   error_code="WATCHDOG_TIMEOUT", retryable=True,
                   message="看护未在预期时间内拉起，需手动检查")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
    if cmd == "list":
        print(json.dumps(list_hostnames(), ensure_ascii=False, indent=2))
    elif cmd == "add":
        print(json.dumps(ensure_hostname(sys.argv[2], sys.argv[3]), ensure_ascii=False, indent=2))
    elif cmd == "remove":
        print(json.dumps(remove_hostname(sys.argv[2]), ensure_ascii=False, indent=2))
    elif cmd == "dns":
        print(json.dumps(route_dns(sys.argv[2]), ensure_ascii=False, indent=2))
    else:
        print("用法: python tunnel_ingress.py [list|add <host> <service>|remove <host>|dns <host>]")
        sys.exit(1)
