"""stage-cloud-29 — 商品同步：本地 order_platform.products → 云端 D1 products。

幂等：云端 id 固定为 prod-<本地id>，重复执行只更新不重复插入。
用法：python sync_products.py
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import os
import sys

LOCAL_DB = r"D:\web\orders\platform.db"


def wrangler(sql: str) -> bool:
    r = subprocess.run(
        ["npx", "wrangler", "d1", "execute", "autotask-central", "--remote", "--command", sql],
        capture_output=True, text=True, shell=(os.name == "nt"),
        encoding="utf-8", errors="replace", cwd=os.path.join(os.path.dirname(__file__), "..", "cloudflare", "worker"))
    ok = r.returncode == 0 and "Executed" in (r.stdout + r.stderr)
    if not ok:
        print("  FAIL:", (r.stdout + r.stderr)[-300:])
    return ok


def esc(s: str) -> str:
    return s.replace("'", "''")


def main() -> int:
    conn = sqlite3.connect(f"file:{LOCAL_DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM products WHERE enabled=1 ORDER BY sort").fetchall()
    n = 0
    for r in rows:
        d = dict(r)
        pid = f"prod-local-{d['id']}"
        desc = d["desc"] or ""
        price = d["price"] or ""
        config = json.dumps({"price": price, "source": "local_sync"}, ensure_ascii=False)
        now = "strftime('%s','now')*1000"
        sql = (
            "INSERT INTO products(id,code,name,description,platform,enabled,sort_order,config_json,created_at,updated_at) "
            f"VALUES('{pid}','{esc(d['code'])}','{esc(d['name'])}','{esc(desc)}','{esc(d['platform'])}',"
            f"{1 if d['enabled'] else 0},{d['sort']},'{esc(config)}',{now},{now}) "
            "ON CONFLICT(id) DO UPDATE SET code=excluded.code, name=excluded.name, description=excluded.description, "
            "platform=excluded.platform, enabled=excluded.enabled, sort_order=excluded.sort_order, "
            "config_json=excluded.config_json, updated_at=excluded.updated_at"
        )
        ok = wrangler(sql)
        print(f"  {'OK ' if ok else 'FAIL'} {d['code']:<10} {d['name']}（{price}）")
        n += ok
    print(f"synced {n}/{len(rows)} products")
    return 0 if n == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
