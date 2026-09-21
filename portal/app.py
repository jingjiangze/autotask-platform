# -*- coding: utf-8 -*-
"""app.py — 门户入口（Flask + Waitress）

规范约束：
  §二十一 只绑 127.0.0.1
  §46    错误不泄露内部信息（无 traceback / 路径 / token）
  §47    日志脱敏
  §57    /health 不含敏感信息
"""
import json
import os
import secrets
import sys

from flask import Flask, jsonify, request
from flask.sessions import SecureCookieSessionInterface

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
import db
from utils import auth as auth_util
from utils import weblog


class PortalSessionInterface(SecureCookieSessionInterface):
    """让 Secure 属性随实际协议变化：
    走 Cloudflare HTTPS 时带 Secure；本地 http://127.0.0.1 调试时不带，
    否则本地登录态会因为 cookie 不发送而失效（此前踩过的坑）。"""

    def get_cookie_secure(self, app):
        try:
            return request.headers.get("X-Forwarded-Proto", "").lower() == "https"
        except Exception:
            return False

    def get_cookie_httponly(self, app):
        return True

    def get_cookie_samesite(self, app):
        return "Lax"


def _secret_key():
    """会话签名密钥：优先凭据管理器，其次本地密钥文件（不写进源码/日志）"""
    key = None
    try:
        sys.path.insert(0, r"D:\web")
        import crypto_manager as cm
        key = cm.secret_get("portal_session_key")
        if not key:
            key = secrets.token_urlsafe(48)
            cm.secret_set("portal_session_key", key)
    except Exception:
        pass
    if not key:
        kf = os.path.join(config.DATA_DIR, ".session_key")
        if os.path.exists(kf):
            key = open(kf, encoding="utf-8").read().strip()
        if not key:
            key = secrets.token_urlsafe(48)
            with open(kf, "w", encoding="utf-8") as f:
                f.write(key)
    return key


def create_app():
    app = Flask(__name__, template_folder=config.TEMPLATE_DIR,
                static_folder=config.STATIC_DIR)
    app.secret_key = _secret_key()
    app.session_interface = PortalSessionInterface()
    app.config.update(
        SESSION_COOKIE_NAME=config.SESSION_COOKIE_NAME,
        PERMANENT_SESSION_LIFETIME=config.SESSION_LIFETIME_HOURS * 3600,
        JSON_AS_ASCII=False,
        MAX_CONTENT_LENGTH=512 * 1024,
    )

    db.init_db()

    # 首次启动：生成门户管理员口令（哈希入库；明文只进凭据管理器）
    if not auth_util.has_admin():
        pwd = auth_util.gen_admin_password()
        auth_util.set_admin_password(pwd)
        weblog.info("已生成门户管理员初始口令（明文仅存 Windows 凭据管理器）")
        print("\n" + "=" * 64)
        print(" 门户管理员初始口令（请立即记录/修改）：", pwd)
        print(" 用户名任意，口令如上；登录地址 /admin/login")
        print("=" * 64 + "\n")

    from routes.public import bp as public_bp
    from routes.api import bp as api_bp
    from routes.admin import bp as admin_bp
    app.register_blueprint(public_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(admin_bp)

    @app.context_processor
    def inject_globals():
        s = db.get_settings()
        return {"site": s, "is_admin": auth_util.is_admin()}

    @app.after_request
    def _harden(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        return resp

    # ---------- 错误处理：不泄露内部信息 ----------
    @app.errorhandler(404)
    def _404(e):
        if request.path.startswith("/api/"):
            return jsonify({"ok": False, "error": "not_found"}), 404
        return jsonify({"ok": False, "error": "not_found"}), 404

    @app.errorhandler(500)
    def _500(e):
        weblog.error("internal error on %s", request.path)
        return jsonify({"ok": False, "error": "internal_error"}), 500

    @app.errorhandler(Exception)
    def _any(e):
        from werkzeug.exceptions import HTTPException
        if isinstance(e, HTTPException):
            return e
        weblog.error("unhandled: %s", type(e).__name__)      # 不含 traceback 细节
        return jsonify({"ok": False, "error": "internal_error"}), 500

    # ---------- 健康检查（§57）----------
    @app.route("/health")
    def health():
        from services import openlist_service
        db_ok = True
        try:
            with db.ro() as con:                # 必须用 with：直接 __enter__() 会因
                con.execute("SELECT 1").fetchone()   # 上下文管理器被 GC 而提前 close
        except Exception as e:
            db_ok = False
            weblog.warn("health: 数据库检查失败 %s", type(e).__name__)
        ol_ok, _ol_code = openlist_service.health()
        body = {"ok": bool(db_ok), "portal": True, "database": db_ok, "openlist": ol_ok}
        return jsonify(body), (200 if db_ok else 503)

    weblog.info("门户应用初始化完成")
    return app


if __name__ == "__main__":
    application = create_app()
    if "--dev" in sys.argv:
        application.run(host=config.PORTAL_HOST, port=config.PORTAL_PORT, debug=False)
    else:
        from waitress import serve
        weblog.info("Waitress 启动 @ %s:%s", config.PORTAL_HOST, config.PORTAL_PORT)
        serve(application, host=config.PORTAL_HOST, port=config.PORTAL_PORT,
              threads=config.PORTAL_THREADS)
