# -*- coding: utf-8 -*-
"""auth.py — 门户后台鉴权（独立于 OpenList 与订单平台，规范 §14）

规范约束：
  §十四 后台接口必须独立鉴权，不能与公开页共用权限逻辑
  §十九 密钥优先 Windows 凭据管理器（复用 crypto_manager.secret_get/set）
  §20  日志脱敏
不新建第二套复杂用户系统：只有一个门户管理员口令（哈希存 portal.db 的 site_settings）。
"""
import hashlib
import hmac
import os
import secrets
import sys
import time

from flask import request, session

import config

# 复用项目既有凭据后端（Windows Credential Manager）；失败则降级为仅哈希校验
_CM = None
try:
    sys.path.insert(0, r"D:\web")
    import crypto_manager as _CM
except Exception:
    _CM = None

_HASH_KEY = "portal_admin_pwd_hash"
_SALT_KEY = "portal_admin_pwd_salt"

_rl = {}          # 登录限流：{ip: [ts,...]}


def _hash(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                               salt.encode("utf-8"), 120_000).hex()


def has_admin():
    import db
    s = db.get_settings()
    return bool(s.get(_HASH_KEY))


def set_admin_password(password):
    import db
    salt = secrets.token_hex(16)
    db.set_setting(_SALT_KEY, salt)
    db.set_setting(_HASH_KEY, _hash(password, salt))
    # 明文只暂存凭据管理器，方便用户取回；DB 只存哈希
    if _CM:
        try:
            _CM.secret_set(config.CRED_PORTAL_ADMIN, password)
        except Exception:
            pass


def gen_admin_password():
    return secrets.token_urlsafe(18)


def verify_password(password):
    import db
    s = db.get_settings()
    h, salt = s.get(_HASH_KEY), s.get(_SALT_KEY)
    if not h or not salt:
        return False
    return hmac.compare_digest(_hash(password, salt), h)


def rate_limit_ok(ip, limit=None, window=None):
    limit = limit or config.LOGIN_RATE_LIMIT
    window = window or config.LOGIN_RATE_WINDOW
    now = time.time()
    lst = [t for t in _rl.get(ip, []) if now - t < window]
    if len(lst) >= limit:
        _rl[ip] = lst
        return False
    lst.append(now)
    _rl[ip] = lst
    if len(_rl) > 5000:
        _rl.clear()
    return True


def login(ip):
    session["portal_admin"] = True
    session["ts"] = int(time.time())
    session.permanent = True
    _rl.pop(ip, None)


def logout():
    session.pop("portal_admin", None)
    session.pop("ts", None)


def is_admin():
    if not session.get("portal_admin"):
        return False
    age = time.time() - float(session.get("ts") or 0)
    if age > config.SESSION_LIFETIME_HOURS * 3600:
        logout()
        return False
    return True


def client_ip():
    """真实 IP：仅当对端是回环（cloudflared 回源）时才采信 CF-Connecting-IP，
    其余一律用 TCP 对端 —— 与 order_platform 同一策略，防伪造头。"""
    ip = request.remote_addr or "?"
    if ip in ("127.0.0.1", "::1"):
        ip = (request.headers.get("CF-Connecting-IP") or "").strip() or ip
    return ip


def cookie_secure():
    """仅当请求确实经 HTTPS 时才带 Secure，避免破坏本地 http 访问
    （教训：直接 secure=True 会让本地 127.0.0.1 登录失效）"""
    return (request.headers.get("X-Forwarded-Proto", "").lower() == "https")
