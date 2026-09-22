# -*- coding: utf-8 -*-
"""ORDER / QUERY / OWNERSHIP 基线：下单加密落库、我的订单、访客查单、越权拒绝

复用 session 级共享用户（users.a 下单者 / users.b 越权探测者），
不再新增注册，避免触碰注册限流（5 次/分钟/IP）。
"""
import uuid

import requests

from conftest import BASELINE_ACCOUNT, BASELINE_PW


def _new_order(platform, sess, code="cx_video", courses="264628209"):
    r = platform.post(sess, f"/buy/{code}",
                      {"account": BASELINE_ACCOUNT, "password": BASELINE_PW,
                       "courses": courses},
                      allow_redirects=False)
    assert r.status_code == 302, f"下单失败: {r.status_code}"
    return r.headers["Location"].rsplit("/", 1)[-1]


def test_buy_creates_order_with_encrypted_password(platform, users):
    oid = _new_order(platform, users.a)
    assert len(oid) == 32  # uuid4.hex

    rows = platform.sql("SELECT * FROM orders WHERE id=?", (oid,))
    assert len(rows) == 1
    o = rows[0]
    assert o["status"] == "pending"
    assert o["account"] == BASELINE_ACCOUNT, "账号明文入库（现状事实，边界文档登记）"
    assert o["password"].startswith("enc:v1:"), "密码必须以 enc:v1: 密文入库"
    assert BASELINE_PW not in o["password"], "密码明文不得出现在库中"

    # 详情页：本人可见，但绝不能回显明文密码 / 密文
    r = users.a.get(platform.base + f"/order/{oid}")
    assert r.status_code == 200
    assert BASELINE_PW not in r.text
    assert "enc:v1:" not in r.text

    # 供后续用例复用（模块内顺序执行）
    test_buy_creates_order_with_encrypted_password.oid = oid


def test_my_orders_scoped_to_owner(platform, users):
    oid = test_buy_creates_order_with_encrypted_password.oid
    r = users.a.get(platform.base + "/my")
    assert r.status_code == 200 and oid[:8] in r.text

    r = users.b.get(platform.base + "/my")
    assert r.status_code == 200 and oid[:8] not in r.text, "他人订单不得出现在我的订单"


def test_ownership_denied_for_other_user(platform, users):
    oid = test_buy_creates_order_with_encrypted_password.oid

    # 详情页：非归属用户 → 订单不存在页（真实行为：200 + 错误页，非 403）
    r = users.b.get(platform.base + f"/order/{oid}")
    assert "订单不存在" in r.text and BASELINE_ACCOUNT not in r.text

    # 扫码单选课接口：非归属用户 → ok=False
    r = platform.post(users.b, f"/order/{oid}/set_courses", {},
                      json={"courses": "1"})
    body = r.json()
    assert body.get("ok") is False and "订单不存在" in body.get("error", "")


def test_guest_query_limited_fields(platform, users):
    oid = test_buy_creates_order_with_encrypted_password.oid

    # 完整单号查单
    r = requests.Session().post(platform.base + "/query",
                                data={"oid": oid},
                                headers={"Origin": platform.base,
                                         "Referer": platform.base + "/"})
    assert "查询结果" in r.text
    assert oid[:8] in r.text
    # 隐私红线：查单页不回显账号 / 明文密码 / 密文
    assert BASELINE_ACCOUNT not in r.text
    assert BASELINE_PW not in r.text
    assert "enc:v1:" not in r.text

    # 前 8 位能力凭据查单（真实访客查单语义）
    r = requests.Session().post(platform.base + "/query",
                                data={"oid": oid[:8]},
                                headers={"Origin": platform.base,
                                         "Referer": platform.base + "/"})
    assert "查询结果" in r.text and oid[:8] in r.text

    # <6 位 → 不查；随机不存在 → 无结果
    for bad in (oid[:5], uuid.uuid4().hex[:8]):
        r = requests.Session().post(platform.base + "/query",
                                    data={"oid": bad},
                                    headers={"Origin": platform.base,
                                             "Referer": platform.base + "/"})
        assert "查询结果" not in r.text
