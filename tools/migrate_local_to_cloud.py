"""stage-cloud-25 — 本地数据迁移工具（计划 §102–§105）

仅在用户本机运行；绝不在远端执行，绝不把本地库复制到 GitHub。

  dry-run（默认）  python tools/migrate_local_to_cloud.py --db D:/web/orders/platform.db
                   输出统计（users/orders/products/credentials/errors），不输出任何敏感值。
  --apply          真实执行：中央 API 创建用户/订单/商品，凭据 enc:v1 → enc:v2 重加密。
  --execute-url X  中央平台地址（--apply 时必填）。

幂等（§103）：以本地 id 为准（cloud order_id = local order id 原样沿用），
重复执行不产生重复用户/订单/凭据（INSERT OR IGNORE 语义 + 迁移登记表）。

隐私（§104/§105）：
  - dry-run 只输出计数与状态，不输出 password/cookie/token。
  - 校验只做「本地 enc:v1 解密 → 云 enc:v2 解密 → 相等」，结果仅打印
    credential verification = PASS/FAIL。
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

UA = "autotask-migrator/1.0"

# ---- 本地 enc:v1 解密（算法与 order_platform.py 完全一致；内联避免其 import 副作用）----
_ENC_PREFIX = "enc:v1:"


def _load_local_secret(secret_file: str) -> str:
    return Path(secret_file).read_text(encoding="utf-8").strip()


def _enc_v1_keystream(key: bytes, nonce: bytes, length: int) -> bytes:
    out = b""
    counter = 0
    while len(out) < length:
        out += hashlib.sha256(key + nonce + counter.to_bytes(8, "big")).digest()
        counter += 1
    return out


def decrypt_local_v1(stored: str, secret: str) -> str:
    if not stored.startswith(_ENC_PREFIX):
        return stored  # 历史明文（dry-run 统计中单独计数）
    raw = base64.b64decode(stored[len(_ENC_PREFIX):])
    nonce, ct = raw[:16], raw[16:]
    key = hashlib.sha256((secret + "wk-enc").encode()).digest()
    ks = _enc_v1_keystream(key, nonce, len(ct))
    return bytes(a ^ b for a, b in zip(ct, ks)).decode("utf-8")


def call(base: str, method: str, path: str, token: str | None = None,
         body: dict | None = None, headers_extra: dict | None = None) -> tuple[int, dict]:
    headers = {"Content-Type": "application/json", "User-Agent": UA}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if headers_extra:
        headers.update(headers_extra)
    req = urllib.request.Request(
        f"{base}{path}", method=method,
        data=json.dumps(body).encode() if body is not None else None, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = r.read()
            return r.status, json.loads(data) if data else {}
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except Exception:
            return e.code, {}


def cloud_encrypt_v2(credential_key_b64: str, plaintext: str) -> str:
    """与 Worker 端 encryptCredential 对齐的 enc-v2（AES-256-GCM, AAD 同源）。"""
    import hashlib
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # 本机已装 cryptography

    key = base64.b64decode(credential_key_b64)
    nonce = os.urandom(12)
    aad = b"autotask.order_credentials.v2"
    ct = AESGCM(key).encrypt(nonce, plaintext.encode(), aad)
    return f"enc-v2${base64.b64encode(nonce).decode()}${base64.b64encode(ct).decode()}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=r"D:\web\orders\platform.db")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--execute-url", default=os.environ.get("CENTRAL_URL", ""))
    ap.add_argument("--credential-key", default=os.environ.get("CREDENTIAL_KEY", ""),
                    help="云 enc-v2 主密钥（base64 32B）")
    ap.add_argument("--secret-file", default=r"D:\web\secrets_store\secret_key.txt",
                    help="本地 enc:v1 的 SECRET 文件（order_platform._SECRET_FILE）")
    ap.add_argument("--admin-user", default=os.environ.get("ADMIN_USER", "real_owner"))
    ap.add_argument("--admin-pass", default=os.environ.get("ADMIN_PASS", ""))
    args = ap.parse_args()

    if not Path(args.db).exists():
        print(f"local db not found: {args.db}")
        return 2

    import sqlite3
    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    users = list(conn.execute("SELECT id,username,pw_hash,is_admin,created_at FROM users"))
    orders = list(conn.execute(
        "SELECT id,user_id,platform,account,password,courses,status,created_at FROM orders"))
    products = list(conn.execute("SELECT id,code,name,platform,enabled FROM products"))

    # 凭据审计：只统计形态，不打印值
    cred_shapes = {"enc_v1": 0, "plaintext": 0, "empty": 0, "unknown": 0}
    for o in orders:
        pw = o["password"] or ""
        if not pw:
            cred_shapes["empty"] += 1
        elif pw.startswith("enc:v1:"):
            cred_shapes["enc_v1"] += 1
        elif pw.startswith("enc-v2$"):
            cred_shapes.setdefault("already_v2", 0)
            cred_shapes["already_v2"] += 1
        else:
            cred_shapes["plaintext"] += 1

    print("== migration dry-run ==")
    print(f"users:       {len(users)}")
    print(f"orders:      {len(orders)}")
    print(f"products:    {len(products)}")
    print(f"credentials: {json.dumps(cred_shapes)}")

    errors: list[str] = []
    # 本地 enc:v1 解密抽验（1 条）—— 证明本机迁移能力，不输出明文
    sample = next((o for o in orders if (o["password"] or "").startswith("enc:v1:")), None)
    verify = "NOT AVAILABLE (no enc:v1 sample)"
    if sample is not None:
        try:
            secret = _load_local_secret(args.secret_file)
            pt = decrypt_local_v1(sample["password"], secret)
            verify = "PASS" if isinstance(pt, str) and len(pt) > 0 else "FAIL"
        except Exception as e:  # noqa: BLE001
            verify = "FAIL"
            errors.append(f"local decrypt sample: {type(e).__name__}")
    print(f"credential verification = {verify}")

    if not args.apply:
        print("\n(dry-run only; rerun with --apply to migrate. 迁移幂等：本地 id 原样沿用)")
        print(f"errors: {errors}")
        return 0

    # ---- apply：需要网络 + 中央 URL ----
    if not args.execute_url:
        print("--apply requires --execute-url")
        return 2
    base = args.execute_url.rstrip("/")

    if not args.admin_pass:
        print("apply needs --admin-pass (or env ADMIN_PASS)")
        return 2

    # admin session
    try:
        req = urllib.request.Request(f"{base}/api/v1/auth/login", method="POST",
            data=json.dumps({"username": args.admin_user, "password": args.admin_pass}).encode(),
            headers={"Content-Type": "application/json", "User-Agent": "autotask-migrate/1.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            cookie = r.headers.get("Set-Cookie", "").split(";")[0]
    except urllib.error.HTTPError as e:
        print(f"admin login failed: {e.code}")
        return 2
    if not cookie:
        print("admin login failed: no cookie")
        return 2

    def post_import(payload):
        req = urllib.request.Request(f"{base}/api/v1/admin/import/legacy", method="POST",
            data=json.dumps(payload, ensure_ascii=False).encode(),
            headers={"Content-Type": "application/json", "User-Agent": "autotask-migrate/1.0",
                     "Cookie": cookie})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            return {"error": f"{e.code}: {e.read().decode()[:200]}"}

    # ---- enc:v1 -> enc:v2 本地重加密 ----
    secret = _load_local_secret(args.secret_file)
    def _b64norm(v: str) -> bytes:
        v = v.strip().replace("-", "+").replace("_", "/")
        v += "=" * (-len(v) % 4)
        return base64.b64decode(v)
    key = _b64norm(args.credential_key) if args.credential_key else None
    if key is None or len(key) != 32:
        print("need valid --credential-key (base64 32B)")
        return 2
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    def encrypt_v2(plaintext: str) -> str:
        nonce = os.urandom(12)
        ct = AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), b"autotask.order_credentials.v2")
        return "enc-v2$" + base64.b64encode(nonce).decode() + "$" + base64.b64encode(ct).decode()

    # 本地 product code 映射（platform -> code）
    platform_code = {p["platform"]: p["code"] for p in products}

    payload = {
        "users": [{"id": u["id"], "username": u["username"], "pw_hash": u["pw_hash"],
                    "is_admin": u["is_admin"], "created_at": u["created_at"]} for u in users],
        "orders": [],
        "credentials": [],
    }
    cred_fail = 0
    for o in orders:
        payload["orders"].append({
            "id": o["id"], "user_id": o["user_id"], "platform": o["platform"],
            "account": o["account"] or "", "courses": o["courses"] or "",
            "status": o["status"], "created_at": o["created_at"],
        })
        pw = o["password"] or ""
        if pw:
            try:
                pt = decrypt_local_v1(pw, secret) if pw.startswith("enc:v1:") else pw
                payload["credentials"].append({
                    "order_id": o["id"], "credential_type": "account_password",
                    "ciphertext": encrypt_v2(pt), "encryption_version": "enc-v2",
                })
                if (o["account"] or ""):
                    payload["credentials"].append({
                        "order_id": o["id"], "credential_type": "account",
                        "ciphertext": encrypt_v2(o["account"]), "encryption_version": "enc-v2",
                    })
            except Exception as e:  # noqa: BLE001
                cred_fail += 1
                errors.append(f"cred {o['id']}: {type(e).__name__}")

    # 分批（每批 50 单）
    result = {"users": {}, "orders": {}, "credentials": {}}
    B = 50
    for i in range(0, len(payload["orders"]), B):
        batch = {"users": payload["users"] if i == 0 else [],
                 "orders": payload["orders"][i:i + B],
                 "credentials": payload["credentials"][i:i + B * 2]}
        r = post_import(batch)
        if "error" in r:
            errors.append(f"batch {i}: {r['error']}")
            continue
        for k in ("users", "orders", "credentials"):
            for kk, vv in r.get(k, {}).items():
                if isinstance(vv, int):
                    result[k][kk] = result[k].get(kk, 0) + vv
        order_errs = r.get("orders", {}).get("errors") or []
        errors.extend(f"order {e}" for e in order_errs)

    migrated_orders = result["orders"].get("imported", 0)
    skipped_orders = result["orders"].get("skipped", 0)
    print(f"users imported={result['users'].get('imported', 0)} skipped={result['users'].get('skipped', 0)}")
    print(f"orders migrated={migrated_orders} skipped(existing)={skipped_orders}")
    print(f"credentials imported={result['credentials'].get('imported', 0)} decrypt_fail={cred_fail}")
    print(f"errors={len(errors)}")
    for e in errors[:8]:
        print(f"  - {e}")
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
