/// <reference types="@cloudflare/vitest-plugin/types" />
import { describe, expect, it } from "vitest";
import { SELF } from "cloudflare:test";
import { applyMigrations, DB } from "./helpers";

const BASE = "https://example.com";
const BOOTSTRAP = "test-bootstrap-token";

async function mkAdmin(name: string): Promise<string> {
  await SELF.fetch(`${BASE}/api/v1/auth/register`, {
    method: "POST",
    body: JSON.stringify({ username: name, password: "pass-123456" }),
  });
  const id = (await DB.prepare("SELECT id FROM users WHERE username=?").bind(name).first<{ id: string }>())!.id;
  await DB.prepare("UPDATE users SET role='admin' WHERE id=?").bind(id).run();
  const login = await SELF.fetch(`${BASE}/api/v1/auth/login`, {
    method: "POST",
    body: JSON.stringify({ username: name, password: "pass-123456" }),
  });
  return login.headers.get("Set-Cookie")!.split(";")[0]!;
}

describe("§58 admin products", () => {
  it("非 admin 建商品 → 403；admin upsert 幂等 + 下架生效 + 审计", async () => {
    await applyMigrations();
    // 普通用户
    await SELF.fetch(`${BASE}/api/v1/auth/register`, {
      method: "POST", body: JSON.stringify({ username: "puser", password: "pass-123456" }),
    });
    const uLogin = await SELF.fetch(`${BASE}/api/v1/auth/login`, {
      method: "POST", body: JSON.stringify({ username: "puser", password: "pass-123456" }),
    });
    const uCookie = uLogin.headers.get("Set-Cookie")!.split(";")[0]!;
    const deny = await SELF.fetch(`${BASE}/api/v1/admin/products`, {
      method: "POST", headers: { Cookie: uCookie },
      body: JSON.stringify({ code: "x1", name: "X", platform: "chaoxing" }),
    });
    expect(deny.status).toBe(403);

    const admin = await mkAdmin("padmin");
    // 上架
    const up = await SELF.fetch(`${BASE}/api/v1/admin/products`, {
      method: "POST", headers: { Cookie: admin },
      body: JSON.stringify({ code: "spec_prod", name: "规格商品", platform: "chaoxing",
        description: "d", sort_order: 2, config_json: '{"price":"¥9.9"}' }),
    });
    expect(up.status).toBe(200);
    // 同 code 重复 upsert → 更新而非重复行
    await SELF.fetch(`${BASE}/api/v1/admin/products`, {
      method: "POST", headers: { Cookie: admin },
      body: JSON.stringify({ code: "spec_prod", name: "规格商品2", platform: "chaoxing" }),
    });
    const rows = await DB.prepare("SELECT COUNT(*) AS n FROM products WHERE code='spec_prod'").first<{ n: number }>();
    expect(rows?.n).toBe(1);
    // 非法 platform / code → 400
    const bad = await SELF.fetch(`${BASE}/api/v1/admin/products`, {
      method: "POST", headers: { Cookie: admin },
      body: JSON.stringify({ code: "spec_prod", name: "x", platform: "nope" }),
    });
    expect(bad.status).toBe(400);
    // 下架
    const off = await SELF.fetch(`${BASE}/api/v1/admin/products/spec_prod/enabled`, {
      method: "POST", headers: { Cookie: admin }, body: JSON.stringify({ enabled: 0 }),
    });
    expect(((await off.json()) as { enabled: number }).enabled).toBe(0);
    // 审计齐
    const audit = await DB.prepare(
      "SELECT event_type FROM audit_events WHERE event_type IN ('PRODUCT_UPSERTED','PRODUCT_DISABLED') ORDER BY created_at",
    ).all();
    expect(audit.results.map((r) => r.event_type)).toContain("PRODUCT_UPSERTED");
    expect(audit.results.map((r) => r.event_type)).toContain("PRODUCT_DISABLED");
  });
});
