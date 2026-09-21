# -*- coding: utf-8 -*-
"""openlist_service.py — 门户与 OpenList 的唯一交互出口（规范 §五十一）

硬约束：
  ✅ 只调用 OpenList API
  ❌ 绝不遍历任何 Windows 目录树（规范 §7 / §65）—— 不调用目录遍历类库
  ❌ 绝不出现 "Q:\\" 字面量
  ✅ 读写操作只有 list / get，**没有任何写、删、移动、复制**
  ✅ 固定超时 + 有限重试（仅网络类）；403/404 不重试（§五十四/五十五）
"""
import json
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import config

sys.path.insert(0, r"D:\web")
try:
    import crypto_manager as _CM
except Exception:
    _CM = None

_tok_lock = threading.Lock()
_tok = {"value": None, "exp": 0.0}
_tok_fail = {"count": 0, "until": 0.0}


class OpenListError(Exception):
    def __init__(self, code, message="", retryable=False):
        super().__init__(message or code)
        self.code = code
        self.retryable = retryable


def _creds():
    """从 Windows 凭据管理器取 OpenList 只读用途的管理员凭据（规范 §十九）"""
    u = p = None
    if _CM:
        try:
            u = _CM.secret_get(config.CRED_OPENLIST_USER)
            p = _CM.secret_get(config.CRED_OPENLIST_PASS)
        except Exception:
            u = p = None
    return u, p


def credentials_configured():
    u, p = _creds()
    return bool(u and p)


def _request(method, path, body=None, token=None, timeout=None):
    url = config.OPENLIST_BASE + path
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = token
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)

    # 绕开本机残留代理变量（与项目其它模块一致做法）
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    last = None
    attempts = 1 + config.OPENLIST_RETRY_NETWORK
    for i in range(attempts):
        try:
            with opener.open(req, timeout=timeout or config.OPENLIST_READ_TIMEOUT) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            # HTTP 层错误不重试（403/404/400 等）
            try:
                payload = json.loads(e.read().decode("utf-8"))
            except Exception:
                payload = {}
            code = {401: "openlist_unauthorized", 403: "openlist_forbidden",
                    404: "openlist_not_found", 400: "openlist_bad_request"}.get(
                        e.code, f"openlist_http_{e.code}")
            raise OpenListError(code, json.dumps(payload, ensure_ascii=False)[:200],
                                retryable=False)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = e
            if i < attempts - 1:
                time.sleep(0.6 * (i + 1))
                continue
            raise OpenListError("openlist_unavailable", str(e)[:200], retryable=True)
    raise OpenListError("openlist_unavailable", str(last)[:200], retryable=True)


def _token(force=False):
    """登录 OpenList 取 JWT，缓存到接近过期（token_expires_in=12h）"""
    with _tok_lock:
        now = time.time()
        if not force and _tok["value"] and now < _tok["exp"]:
            return _tok["value"]
        if now < _tok_fail["until"]:
            raise OpenListError("openlist_login_backoff", "登录失败冷却中", retryable=True)

        u, p = _creds()
        if not (u and p):
            raise OpenListError("openlist_credentials_missing",
                                "未配置 OpenList 凭据（Windows 凭据管理器）")
        try:
            j = _request("POST", "/api/auth/login", {"username": u, "password": p})
        except OpenListError as e:
            _tok_fail["count"] += 1
            _tok_fail["until"] = now + min(60 * _tok_fail["count"], 600)
            raise
        if not j or j.get("code") != 200 or not (j.get("data") or {}).get("token"):
            _tok_fail["count"] += 1
            _tok_fail["until"] = now + min(60 * _tok_fail["count"], 600)
            raise OpenListError("openlist_login_failed", "OpenList 登录返回异常")
        _tok_fail["count"] = 0
        _tok["value"] = j["data"]["token"]
        _tok["exp"] = now + 11 * 3600          # 略早于 12h 过期
        return _tok["value"]


def _call(path, body, method="POST"):
    for attempt in (1, 2):
        tok = _token(force=(attempt == 2))
        try:
            j = _request(method, path, body, token=tok)
        except OpenListError as e:
            if e.code == "openlist_unauthorized" and attempt == 1:
                continue                        # token 过期 -> 强制刷新一次
            raise
        if not isinstance(j, dict):
            raise OpenListError("openlist_bad_response", "非 JSON 响应")
        return j
    raise OpenListError("openlist_login_failed", "重试后仍失败")


# ---------------- 对外能力（全部只读） ----------------

def list_directory(path="/", page=1, per_page=200, refresh=False):
    """列目录。返回标准化结果 —— 门户只认这个结构，不暴露原始响应。"""
    if not isinstance(path, str) or not path.startswith("/"):
        raise OpenListError("openlist_bad_path", "路径必须以 / 开头")
    if ".." in path or "\\" in path or ":" in path:
        raise OpenListError("openlist_bad_path", "路径含非法字符")   # §四十五 穿越防护

    j = _call("/api/fs/list", {"path": path, "password": "", "page": page,
                               "per_page": per_page, "refresh": refresh,
                               "keyword": ""})
    if j.get("code") != 200:
        raise OpenListError("openlist_list_failed", str(j.get("message"))[:200])
    data = j.get("data") or {}
    items = []
    for c in (data.get("content") or []):
        items.append({
            "name": c.get("name"),
            "is_dir": bool(c.get("is_dir")),
            "size": c.get("size") or 0,
            "modified": c.get("modified") or "",
            "type": c.get("type") or 0,
        })
    items.sort(key=lambda x: (not x["is_dir"], x["name"] or ""))
    return {"path": path, "total": data.get("total") or 0, "items": items}


def get_entry(path):
    """取单个目录/文件元信息（用于发布前校验，规范 §五十六）"""
    if not isinstance(path, str) or not path.startswith("/") or ".." in path or "\\" in path:
        raise OpenListError("openlist_bad_path", "路径非法")
    j = _call("/api/fs/get", {"path": path, "password": ""})
    if j.get("code") != 200:
        return None
    return j.get("data")


def directory_exists(path):
    """发布前校验：存在 + 是目录 + 可访问。异常时返回 (False, 原因)"""
    try:
        d = get_entry(path)
    except OpenListError as e:
        return False, e.code
    if not d:
        return False, "openlist_not_found"
    if not d.get("is_dir"):
        return False, "not_a_directory"
    return True, "ok"


def search_paths(keyword, path="/", limit=50):
    """在 OpenList 内按文件名搜索（仅管理员目录选择器使用）。
    注意：门户的**公开搜索**不调用它，只查 Portal DB（规范 §三十二）。"""
    j = _call("/api/fs/search", {"parent": path, "keywords": keyword,
                                 "scope": 0, "page": 1, "per_page": limit,
                                 "password": ""})
    if j.get("code") != 200:
        raise OpenListError("openlist_search_failed", str(j.get("message"))[:200])
    return (j.get("data") or {}).get("content") or []


def sensitive_warnings(path):
    """目录敏感词告警（规范 §四十四）—— 只提示，不替代人工确认"""
    low = (path or "").lower()
    return [k for k in config.SENSITIVE_DIR_KEYWORDS if k.lower() in low]


def health():
    """健康检查：不返回任何敏感信息"""
    try:
        _token()
        return True, "ok"
    except OpenListError as e:
        return False, e.code
