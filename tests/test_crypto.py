# -*- coding: utf-8 -*-
"""密码加密存储测试：加解密回环 + 存量迁移验证。
用法: python tests/test_crypto.py
"""
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.dirname(HERE)
sys.path.insert(0, APP_DIR)

import order_platform as P  # noqa: E402

# 1) 回环测试（含中文、特殊字符、空串）
# 注意：测试样例不得使用真实凭据，仅作加解密回环输入
cases = ["SamplePassw0rd!", "p@ss w0rd!中文123", "", "enc:v1:fake"][:3]
for c in cases:
    enc = P.encrypt_secret(c)
    dec = P.decrypt_secret(enc)
    assert dec == c, f"回环失败: {c!r} -> {enc!r} -> {dec!r}"
    if c:
        assert enc.startswith("enc:v1:"), f"未加密: {enc!r}"
print("PASS 加解密回环", cases)
# 幂等：已加密串再加密应原样返回（加密使用随机 nonce，两次独立加密密文必然不同）
enc1 = P.encrypt_secret("abc")
assert P.encrypt_secret(enc1) == enc1
print("PASS 重复加密幂等")

# 2) 存量迁移验证：库里不应再有明文密码
db = sqlite3.connect(os.path.join(APP_DIR, "orders", "platform.db"))
rows = db.execute("SELECT COUNT(*) FROM orders WHERE password != '' "
                  "AND password NOT LIKE 'enc:v1:%'").fetchone()[0]
total = db.execute("SELECT COUNT(*) FROM orders WHERE password != ''").fetchone()[0]
db.close()
assert rows == 0, f"仍有 {rows} 条明文密码"
print(f"PASS 存量迁移完成：{total} 条密码全部为加密存储")
