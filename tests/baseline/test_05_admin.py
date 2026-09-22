# -*- coding: utf-8 -*-
"""ADMIN 基线：管理员引导口令、后台门禁、设置持久化

非管理员探测复用共享用户 users.b，不再新增注册（限流预算）。
"""
import requests


def test_admin_gate(platform, users):
    # 非管理员 → 拒绝页（真实行为：200 + 仅管理员可访问）
    r = users.b.get(platform.base + "/admin")
    assert "仅管理员可访问" in r.text and "管理后台" not in r.text

    # 非管理员 POST /admin/tune → 403
    r = platform.post(users.b, "/admin/tune", {"concurrency": "3"})
    assert r.status_code == 403

    # 匿名访问保护后台 → 302 登录
    r = requests.Session().get(platform.base + "/admin", allow_redirects=False)
    assert r.status_code == 302 and "/login" in r.headers.get("Location", "")


def test_admin_bootstrap_password_and_tune(platform):
    # 管理员口令来自真实启动路径：ensure_admin_password 检测默认口令后
    # 生成随机口令写入 secrets_store/admin_password.txt（隔离目录内）
    admin_pw = platform.admin_password()
    s = requests.Session()
    r = platform.login(s, "admin", admin_pw)
    assert r.status_code == 302 and "wk_token" in s.cookies, "引导管理员口令应可登录"
    # 旧默认口令必须已失效
    s2 = requests.Session()
    r = platform.login(s2, "admin", "admin123")
    assert "wk_token" not in s2.cookies, "默认 admin123 必须被轮换"

    r = s.get(platform.base + "/admin")
    assert r.status_code == 200 and "管理后台" in r.text
    # 后台页面不得出现会话/凭据形态内容
    assert "wk_token=" not in r.text

    # 设置持久化（真实 settings 通道）
    r = platform.post(s, "/admin/tune", {"concurrency": "2", "jobs": "3"},
                      allow_redirects=False)
    assert r.status_code == 302
    rows = {x["key"]: x["value"] for x in platform.sql(
        "SELECT key,value FROM settings WHERE key IN ('concurrency','jobs')")}
    assert rows["concurrency"] == "2" and rows["jobs"] == "3"
