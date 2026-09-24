# -*- coding: utf-8 -*-
"""AUTH 基线：注册 / 登录 / 登出 / 会话形态 / 保护路由

注册限流预算：本文件消耗 3 次（成功 1 + 预期失败 2）。
wk_token 形态断言并入注册成功用例，避免额外注册消耗限流预算。
"""
import requests


def test_register_login_logout_wk_token(platform):
    # --- 注册（真实注册口令校验）---
    s = requests.Session()
    r = platform.register(s, "baseline_auth_a", "pass1234")
    assert r.status_code == 302, f"注册失败: {r.status_code} body={r.text[:400]}"

    # --- 错误口令登录 → 错误页（200）且不发会话（真实行为，非 302）---
    r = platform.login(s, "baseline_auth_a", "WRONG-pw")
    assert r.status_code == 200 and "用户名或密码错误" in r.text
    assert "wk_token" not in s.cookies, "错误口令不应签发 wk_token"

    # --- 正确登录 → wk_token = uid:HMAC(SHA-256) 形态，无过期/无服务端状态 ---
    r = platform.login(s, "baseline_auth_a", "pass1234")
    assert r.status_code == 302
    tok = s.cookies.get("wk_token")
    assert tok and ":" in tok, f"wk_token 应为 uid:签名 形态，实际: {tok!r}"
    uid, sig = tok.split(":", 1)
    assert uid.isdigit() and len(sig) == 64, "签名应为 SHA-256 hex（64 位）"
    rows = platform.sql("SELECT id FROM users WHERE username='baseline_auth_a'")
    assert str(rows[0]["id"]) == uid, "token 中的 uid 必须对应真实用户 id"

    # --- 登录态访问保护路由 ---
    r = s.get(platform.base + "/my", allow_redirects=False)
    assert r.status_code == 200 and "我的订单" in r.text

    # --- logout → 会话失效（本地会话无服务端吊销，登出仅清 cookie）---
    r = s.get(platform.base + "/logout", allow_redirects=False)
    assert r.status_code == 302
    r = s.get(platform.base + "/my", allow_redirects=False)
    assert r.status_code == 302 and "/login" in r.headers.get("Location", "")


def test_register_wrong_reg_code(platform):
    s = requests.Session()
    r = platform.register(s, "baseline_auth_bad", "pass1234", code="WRONG-CODE")
    assert "注册口令不正确" in r.text
    rows = platform.sql("SELECT * FROM users WHERE username='baseline_auth_bad'")
    assert not rows, "错误注册口令不应建号"


def test_register_short_password(platform):
    s = requests.Session()
    r = platform.register(s, "baseline_auth_short", "123")
    assert "密码至少 4 位" in r.text
    rows = platform.sql("SELECT * FROM users WHERE username='baseline_auth_short'")
    assert not rows


def test_anon_protected_route_redirects(platform):
    r = requests.Session().get(platform.base + "/my", allow_redirects=False)
    assert r.status_code == 302 and "/login" in r.headers.get("Location", "")
