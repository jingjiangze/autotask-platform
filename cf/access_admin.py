# -*- coding: utf-8 -*-
"""access_admin.py — Cloudflare Access 薄封装

设计约束（遵循 Agent 自动化规范）：
  * 复用：HTTP 出口复用 deploy_cf.api_raw()；凭据复用 crypto_manager 的 Credential Manager 后端；
          cert.pem 解析复用 deploy_cf.read_argo_token()。本文件不重复实现任何上述能力。
  * 结构化返回：所有函数返回 dict，含 success / action / error_code / retryable。
  * 幂等：一律「查询 → 判断 → 最小修改 → 回读验证」；已存在则跳过，不重复创建。
  * 无屏幕模拟：只用 REST API 与 HTTP 探测。

新增原因：项目内不存在任何 Cloudflare Access 相关代码（已审计 cf/ 全目录），属真实缺口。
本文件不含业务逻辑，只做参数校验 + 调用 + 结果整形。

凭据：Cloudflare API Token 存 Windows Credential Manager（WorkBuddy::cloudflare_api_token），
      不写入源码 / Git / 日志 / Q:\\Web / 明文文件。
"""
import json
import os
import ssl
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)                 # D:\web
for p in (HERE, ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)

import deploy_cf                              # 复用现有 CF 客户端（api_raw / read_argo_token）
import crypto_manager                         # 复用现有凭据后端（Credential Manager）

TOKEN_NAME = "cloudflare_api_token"           # Credential Manager 中的条目名
DEFAULT_SESSION = "24h"

# ---------- 错误分类（§18）----------
_RETRYABLE_5XX = True
_STATUS_MAP = {
    400: ("INVALID_ARGUMENT", False),
    401: ("AUTH_FAILED", False),
    403: ("PERMISSION_DENIED", False),
    404: ("NOT_FOUND", False),
    409: ("CONFLICT", False),
    429: ("RATE_LIMITED", True),
}


def _result(success, action, error_code=None, retryable=False, **kw):
    d = {"success": success, "action": action, "error_code": error_code,
         "retryable": retryable}
    d.update(kw)
    return d


def classify(status, payload=None):
    """把 HTTP 状态 + CF 错误体映射为 (error_code, retryable)"""
    if status in _STATUS_MAP:
        code, retry = _STATUS_MAP[status]
        # CF 的 403 有时是 auth.forbidden(1010)，语义是"权限不足"
        errs = ((payload or {}).get("errors") or [{}])
        if status == 403 and errs[0].get("error") == "auth.forbidden":
            return "PERMISSION_DENIED", False
        return code, retry
    if isinstance(status, int) and 500 <= status < 600:
        return "SERVER_ERROR", _RETRYABLE_5XX
    return ("NETWORK_TIMEOUT", True) if status == "TIMEOUT" else ("UNKNOWN", False)


def _mask(tok):
    return (tok[:4] + "…" + tok[-4:]) if tok and len(tok) > 12 else "****"


# ---------- 凭据（§17：不落盘）----------
def token_status():
    """检查令牌来源与类型。绝不返回令牌明文。"""
    cm = crypto_manager.secret_get(TOKEN_NAME)
    if cm:
        return _result(True, "token_status", source="credential_manager",
                       location=f"WorkBuddy::{TOKEN_NAME}", masked=_mask(cm),
                       writable_expected=True)
    try:
        argo = deploy_cf.read_argo_token()
        return _result(True, "token_status", source="cert_pem_embedded",
                       location=r"%USERPROFILE%\.cloudflared\cert.pem",
                       masked=_mask(argo["apiToken"]), writable_expected=False,
                       message="内嵌令牌对 Access 只读，创建会 403 auth.forbidden")
    except Exception as e:
        return _result(False, "token_status", error_code="NO_CREDENTIAL",
                       message=str(e))


def token_set(token):
    """写入 Credential Manager。"""
    if not token or len(token) < 20:
        return _result(False, "token_set", error_code="INVALID_ARGUMENT",
                       message="令牌长度异常")
    crypto_manager.secret_set(TOKEN_NAME, token.strip())
    return _result(True, "token_set", resource_id=f"WorkBuddy::{TOKEN_NAME}",
                   status="stored", masked=_mask(token))


def token_delete():
    ok = crypto_manager.secret_delete(TOKEN_NAME)
    return _result(ok, "token_delete", status="deleted" if ok else "not_found")


def _cred():
    """返回 (token, account_id, source)。优先 Credential Manager。"""
    tok = crypto_manager.secret_get(TOKEN_NAME)
    argo = deploy_cf.read_argo_token()
    if tok:
        return tok, argo["accountID"], "credential_manager"
    return argo["apiToken"], argo["accountID"], "cert_pem_embedded"


def _call(method, path, **kw):
    """统一调用出口：复用 deploy_cf.api_raw，做错误分类。返回 (ok, payload, err)"""
    tok, acct, src = _cred()
    try:
        status, j, raw = deploy_cf.api_raw(method, path, tok, **kw)
    except Exception as e:
        return False, None, {"code": "NETWORK_ERROR", "retryable": True,
                             "message": f"{type(e).__name__}: {e}"}
    if j is None:
        return False, None, {"code": "NON_JSON_RESPONSE", "retryable": status >= 500,
                             "message": f"HTTP {status} {raw}"}
    if not j.get("success"):
        code, retry = classify(status, j)
        return False, None, {"code": code, "retryable": retry, "http_status": status,
                             "message": json.dumps(j.get("errors"), ensure_ascii=False)}
    return True, j.get("result"), None


# ---------- Access Applications ----------
def list_apps():
    ok, res, err = _call("GET", f"/accounts/{_cred()[1]}/access/apps")
    if not ok:
        return _result(False, "list_access_apps", **{"error_code": err["code"],
                                                     "retryable": err["retryable"],
                                                     "message": err["message"]})
    apps = [{"id": a.get("id"), "name": a.get("name"), "domain": a.get("domain"),
             "session_duration": a.get("session_duration")} for a in (res or [])]
    return _result(True, "list_access_apps", count=len(apps), apps=apps)


def get_app(app_id):
    ok, res, err = _call("GET", f"/accounts/{_cred()[1]}/access/apps/{app_id}")
    if not ok:
        return _result(False, "get_access_app", error_code=err["code"],
                       retryable=err["retryable"], message=err["message"])
    return _result(True, "get_access_app", resource_id=app_id, app=res)


def find_app_by_domain(domain):
    ok, res, err = _call("GET", f"/accounts/{_cred()[1]}/access/apps")
    if not ok:
        return _result(False, "find_access_app", error_code=err["code"],
                       retryable=err["retryable"], message=err["message"])
    for a in (res or []):
        if a.get("domain") == domain:
            return _result(True, "find_access_app", found=True, resource_id=a.get("id"),
                           app={"id": a.get("id"), "name": a.get("name"),
                                "domain": a.get("domain"),
                                "session_duration": a.get("session_duration")})
    return _result(True, "find_access_app", found=False, resource_id=None, app=None)


def ensure_app(name, domain, session_duration=DEFAULT_SESSION):
    """幂等：先查 → 存在则跳过 → 不存在才创建 → 回读验证"""
    f = find_app_by_domain(domain)
    if not f["success"]:
        return _result(False, "ensure_access_app", error_code=f["error_code"],
                       retryable=f["retryable"], message=f["message"], domain=domain)
    if f["found"]:
        return _result(True, "ensure_access_app", status="already_exists",
                       resource_id=f["resource_id"], domain=domain, app=f["app"],
                       message="已存在，未做修改")

    body = {
        "name": name, "domain": domain, "type": "self_hosted",
        "session_duration": session_duration,
        "auto_redirect_to_identity": False,
        "app_launcher_visible": False,
        "enable_binding_cookie": False,
        "http_only_cookie_attribute": True,
        "same_site_cookie_attribute": "lax",
        "skip_interstitial": False,
    }
    ok, res, err = _call("POST", f"/accounts/{_cred()[1]}/access/apps", json=body)
    if not ok:
        return _result(False, "ensure_access_app", error_code=err["code"],
                       retryable=err["retryable"], status="create_failed",
                       domain=domain, message=err["message"])
    app_id = res.get("id")
    v = get_app(app_id)                       # 回读验证（§14）
    return _result(True, "ensure_access_app", status="created", resource_id=app_id,
                   domain=domain, verified=v["success"], app=v.get("app"))


# ---------- Access Policies ----------
def list_policies(app_id):
    ok, res, err = _call("GET", f"/accounts/{_cred()[1]}/access/apps/{app_id}/policies")
    if not ok:
        return _result(False, "list_access_policies", error_code=err["code"],
                       retryable=err["retryable"], message=err["message"])
    pols = [{"id": p.get("id"), "name": p.get("name"), "decision": p.get("decision"),
             "include": p.get("include")} for p in (res or [])]
    return _result(True, "list_access_policies", count=len(pols), policies=pols)


def delete_policy(app_id, policy_id):
    ok, res, err = _call("DELETE",
                         f"/accounts/{_cred()[1]}/access/apps/{app_id}/policies/{policy_id}")
    if not ok:
        return _result(False, "delete_access_policy", error_code=err["code"],
                       retryable=err["retryable"], message=err["message"])
    return _result(True, "delete_access_policy", status="deleted", resource_id=policy_id)


def update_policy(app_id, policy_id, name, decision, include, precedence=1):
    body = {"name": name, "decision": decision, "include": include,
            "exclude": [], "require": [], "precedence": precedence}
    ok, res, err = _call("PUT",
                         f"/accounts/{_cred()[1]}/access/apps/{app_id}/policies/{policy_id}",
                         json=body)
    if not ok:
        return _result(False, "update_access_policy", error_code=err["code"],
                       retryable=err["retryable"], status="update_failed",
                       message=err["message"])
    v = list_policies(app_id)                       # 回读验证（§14）
    now = next((p for p in v.get("policies", []) if p["id"] == policy_id), None)
    conv = bool(now and now.get("name") == name and now.get("decision") == decision
                and _norm_include(now.get("include")) == _norm_include(include))
    return _result(True, "update_access_policy", status="updated",
                   resource_id=policy_id, policy=now, verified=conv)


def _norm_include(inc):
    """归一化 include 便于比较（顺序无关）"""
    def key(d):
        return json.dumps(d, sort_keys=True, ensure_ascii=False)
    return sorted((key(x) for x in (inc or [])), key=lambda s: s)


def ensure_policy(app_id, name, decision, include, precedence=1):
    """幂等 + 收敛：同名策略不存在则创建；已存在但内容不同则**更新**；相同则跳过。
    遵循「查询 → 判断 → 修改 → 验证」，并回读校验最终状态。"""
    lp = list_policies(app_id)
    if not lp["success"]:
        return _result(False, "ensure_access_policy", error_code=lp["error_code"],
                       retryable=lp["retryable"], message=lp["message"])
    existing = next((p for p in lp["policies"] if p["name"] == name), None)

    if existing:
        same = (existing.get("decision") == decision
                and _norm_include(existing.get("include")) == _norm_include(include))
        if same:
            return _result(True, "ensure_access_policy", status="already_exists",
                           resource_id=existing["id"], policy=existing,
                           message="已存在且内容一致，未做修改")
        u = update_policy(app_id, existing["id"], name, decision, include, precedence)
        if not u["success"]:
            return u
        v = list_policies(app_id)                       # 回读验证
        now = next((p for p in v.get("policies", []) if p["name"] == name), None)
        conv = bool(now and now.get("decision") == decision
                    and _norm_include(now.get("include")) == _norm_include(include))
        return _result(True, "ensure_access_policy", status="updated",
                       resource_id=existing["id"], policy=now, verified=conv,
                       message="原策略内容不同，已更新")

    body = {"name": name, "decision": decision, "include": include,
            "exclude": [], "require": [], "precedence": precedence}
    ok, res, err = _call("POST",
                         f"/accounts/{_cred()[1]}/access/apps/{app_id}/policies", json=body)
    if not ok:
        return _result(False, "ensure_access_policy", error_code=err["code"],
                       retryable=err["retryable"], status="create_failed",
                       message=err["message"])
    return _result(True, "ensure_access_policy", status="created",
                   resource_id=res.get("id"),
                   policy={"id": res.get("id"), "name": res.get("name"),
                           "decision": res.get("decision"), "include": res.get("include")})


# ---------- Zero Trust 组织（Access 登录页的前置条件）----------
DEFAULT_TEAM = "jiangjiangze"


def get_org():
    """读取 Zero Trust 组织。不存在 -> exists=False；无权限 -> 结构化错误。"""
    ok, res, err = _call("GET", f"/accounts/{_cred()[1]}/access/organizations")
    if not ok:
        return _result(False, "get_access_org", error_code=err["code"],
                       retryable=err["retryable"], message=err["message"])
    orgs = res if isinstance(res, list) else ([res] if res else [])
    if not orgs:
        return _result(True, "get_access_org", exists=False, org=None)
    o = orgs[0]
    return _result(True, "get_access_org", exists=True,
                   org={"id": o.get("id"), "name": o.get("name"),
                        "auth_domain": o.get("auth_domain")})


def ensure_org(team_name=DEFAULT_TEAM, name=None):
    """幂等：组织已存在则跳过，不存在才创建（auth_domain = <team>.cloudflareaccess.com）"""
    g = get_org()
    if not g["success"]:
        return _result(False, "ensure_access_org", error_code=g["error_code"],
                       retryable=g["retryable"], status="probe_failed",
                       message=g["message"])
    if g.get("exists"):
        return _result(True, "ensure_access_org", status="already_exists", org=g["org"])

    body = {"name": name or team_name,
            "auth_domain": f"{team_name}.cloudflareaccess.com",
            "is_ui_read_only": False,
            "login_design": {}}
    ok, res, err = _call("POST", f"/accounts/{_cred()[1]}/access/organizations", json=body)
    if not ok:
        return _result(False, "ensure_access_org", error_code=err["code"],
                       retryable=err["retryable"], status="create_failed",
                       message=err["message"])
    v = get_org()                              # 回读验证
    return _result(True, "ensure_access_org", status="created",
                   verified=bool(v.get("exists")), org=v.get("org"))


# ---------- 端到端验证（HTTP 探测，非屏幕模拟）----------
class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def verify_protected(hostname, timeout=20):
    """探测域名是否已被 Access 接管：期望 302/303 → *.cloudflareaccess.com，
    或响应头出现 cf-access 相关字段。"""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    op = urllib.request.build_opener(_NoRedirect, urllib.request.HTTPSHandler(context=ctx))
    try:
        with op.open(urllib.request.Request(f"https://{hostname}/",
                                            headers={"User-Agent": "wk-health/1.0"}),
                     timeout=timeout) as r:
            status, hdrs, loc = r.status, dict(r.headers), None
    except urllib.error.HTTPError as e:
        status, hdrs, loc = e.code, dict(e.headers or {}), e.headers.get("Location")
    except Exception as e:
        return _result(False, "verify_access_protection", error_code="NETWORK_ERROR",
                       retryable=True, hostname=hostname, message=str(e))
    protected = bool(loc and "cloudflareaccess.com" in loc) or any(
        k.lower().startswith("cf-access") for k in hdrs)
    return _result(True, "verify_access_protection", hostname=hostname, status=status,
                   location=loc, protected=protected,
                   message="已被 Access 保护" if protected
                   else "未检测到 Access 跳转（可能未生效或仍在传播）")


# ---------- 编排：一次性把目标全部配好（幂等，可重复执行）----------
DEFAULT_TARGETS = [
    {"name": "OpenList", "domain": "pan.jiangjiangze.icu"},
    {"name": "OrderPlatform", "domain": "order.jiangjiangze.icu"},
]
DEFAULT_POLICY_NAME = "qq-only"


def setup(targets=None, policy_name=DEFAULT_POLICY_NAME,
          email_domain="qq.com", session_duration=DEFAULT_SESSION,
          team_name=DEFAULT_TEAM, ensure_organization=True):
    """按操作台顺序执行：组织 → 每个目标的 Application → 策略 → 保护验证
    全程 API，幂等，可重复执行；不涉及任何屏幕操作。"""
    targets = targets or DEFAULT_TARGETS
    include = [{"email_domain": {"domain": email_domain}}]
    out = {"success": True, "action": "access_setup", "organization": None,
           "targets": []}

    if ensure_organization:
        o = ensure_org(team_name)
        out["organization"] = o
        if not o["success"]:
            out["success"] = False
            out["error_code"] = "ORG_UNAVAILABLE"
            out["retryable"] = o.get("retryable", False)
            out["message"] = ("无法读取/创建 Zero Trust 组织，通常是令牌缺少 "
                              "Access: Organizations 权限")
            return out

    for t in targets:
        rec = {"domain": t["domain"], "name": t["name"]}
        a = ensure_app(t["name"], t["domain"], session_duration)
        rec["app"] = a
        if not a["success"]:
            out["success"] = False
            rec["policy"] = None
            rec["verify"] = None
            out["targets"].append(rec)
            continue
        p = ensure_policy(a["resource_id"], policy_name, "allow", include)
        rec["policy"] = p
        if not p["success"]:
            out["success"] = False
        rec["verify"] = verify_protected(t["domain"])
        out["targets"].append(rec)
    if not out["success"]:
        out["error_code"] = "ACCESS_SETUP_INCOMPLETE"
        out["retryable"] = False
    return out


# ---------- 薄 CLI ----------
def _cli():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "status":
        print(json.dumps({"token": token_status(), "apps": list_apps()},
                         ensure_ascii=False, indent=2))
    elif cmd == "setup":
        print(json.dumps(setup(), ensure_ascii=False, indent=2))
    elif cmd == "verify":
        doms = sys.argv[2:] or [t["domain"] for t in DEFAULT_TARGETS]
        print(json.dumps([verify_protected(d) for d in doms], ensure_ascii=False, indent=2))
    elif cmd == "token-check":
        print(json.dumps(token_status(), ensure_ascii=False, indent=2))
    elif cmd == "org":
        print(json.dumps(get_org(), ensure_ascii=False, indent=2))
    elif cmd == "token-set":
        # 支持两种输入：环境变量 CF_TOKEN_IN（便于自动化）或交互式输入（不回显）
        tok = (os.environ.get("CF_TOKEN_IN") or "").strip()
        if not tok:
            import getpass
            tok = getpass.getpass("粘贴 Cloudflare API Token（不回显）: ").strip()
        print(json.dumps(token_set(tok), ensure_ascii=False, indent=2))
    elif cmd == "token-delete":
        print(json.dumps(token_delete(), ensure_ascii=False, indent=2))
    else:
        print("用法: python access_admin.py [status|setup|verify|token-check|token-set|token-delete]")
        sys.exit(1)


if __name__ == "__main__":
    _cli()
