/// <reference types="@cloudflare/vitest-plugin/types" />
import { describe, expect, it } from "vitest";
import { SELF } from "cloudflare:test";
import { applyMigrations, DB } from "./helpers";

const BASE = "https://example.com";

async function mkAdmin(name: string): Promise<string> {
  await SELF.fetch(`${BASE}/api/v1/auth/register`, {
    method: "POST", body: JSON.stringify({ username: name, password: "pass-123456" }),
  });
  const id = (await DB.prepare("SELECT id FROM users WHERE username=?").bind(name).first<{ id: string }>())!.id;
  await DB.prepare("UPDATE users SET role='admin' WHERE id=?").bind(id).run();
  const login = await SELF.fetch(`${BASE}/api/v1/auth/login`, {
    method: "POST", body: JSON.stringify({ username: name, password: "pass-123456" }),
  });
  return login.headers.get("Set-Cookie")!.split(";")[0]!;
}

describe("§102 legacy import", () => {
  it("users/orders/credentials 幂等导入：状态映射 done→succeeded，重复导入不产生重复行", async () => {
    await applyMigrations();
    await DB.prepare("INSERT INTO products(id,code,name,platform,enabled,created_at,updated_at) VALUES('p-cx','cx_video','学习通','chaoxing',1,1,1)").run();
    const admin = await mkAdmin("impadmin");
    const payload = {
      users: [{ id: "u-leg-1", username: "legacy_a", pw_hash: "deadbeef", is_admin: 0, created_at: 1700000000000 }],
      orders: [
        { id: "ord-leg-1", user_id: "u-leg-1", platform: "chaoxing", account: "acc1", courses: "1,2", status: "done", created_at: 1700000001000 },
        { id: "ord-leg-2", user_id: "u-leg-1", platform: "zhs", account: "acc2", courses: "", status: "failed", created_at: 1700000002000 },
      ],
      credentials: [
        { order_id: "ord-leg-1", credential_type: "account_password", ciphertext: "enc-v2$AAAA$BBBB", encryption_version: "enc-v2" },
      ],
    };
    const r1 = await SELF.fetch(`${BASE}/api/v1/admin/import/legacy`, {
      method: "POST", headers: { Cookie: admin, "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    expect(r1.status).toBe(200);
    const j1 = (await r1.json()) as { users: { imported: number }; orders: { imported: number }; credentials: { imported: number } };
    expect(j1.users.imported).toBe(1);
    expect(j1.orders.imported).toBe(2);
    expect(j1.credentials.imported).toBe(1);

    // 状态映射
    const o1 = await DB.prepare("SELECT status, product_code, source FROM orders WHERE id='ord-leg-1'").first<{ status: string; product_code: string; source: string }>();
    expect(o1).toMatchObject({ status: "succeeded", product_code: "cx_video", source: "legacy" });

    // 幂等：重放同批次
    await SELF.fetch(`${BASE}/api/v1/admin/import/legacy`, {
      method: "POST", headers: { Cookie: admin, "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const n = await DB.prepare("SELECT COUNT(*) AS n FROM orders WHERE id LIKE 'ord-leg-%'").first<{ n: number }>();
    expect(n?.n).toBe(2);
    const nc = await DB.prepare("SELECT COUNT(*) AS n FROM order_credentials WHERE order_id='ord-leg-1'").first<{ n: number }>();
    expect(nc?.n).toBe(1);

    // 未知 user_id 的订单 → 报错不入库
    const r2 = await SELF.fetch(`${BASE}/api/v1/admin/import/legacy`, {
      method: "POST", headers: { Cookie: admin, "Content-Type": "application/json" },
      body: JSON.stringify({ orders: [{ id: "ord-leg-3", user_id: "u-none", platform: "chaoxing", status: "done", created_at: 1 }] }),
    });
    const j2 = (await r2.json()) as { orders: { imported: number } };
    expect(j2.orders.imported).toBe(0);

    // 审计
    const audit = await DB.prepare("SELECT COUNT(*) AS n FROM audit_events WHERE event_type='LEGACY_IMPORT'").first<{ n: number }>();
    expect(audit?.n).toBe(3); // 三次 POST 各写一条
  });
});
