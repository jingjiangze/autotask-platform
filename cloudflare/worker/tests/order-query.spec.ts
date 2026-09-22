/// <reference types="@cloudflare/vitest-plugin/types" />
import { beforeEach, describe, expect, it } from "vitest";
import { SELF } from "cloudflare:test";
import { applyMigrations, DB } from "./helpers";

// stage-cloud-16 验收（§57/§88）：
// my_orders 本人列表 / 详情越权 404（与本地一致不暴露存在性）/
// admin 按账号查单（/qacc 语义）+ 审计 / 普通用户 FORBIDDEN。

const BASE = "https://example.com";

async function mkUser(name: string): Promise<{ cookie: string; id: string; role: string }> {
  await SELF.fetch(`${BASE}/api/v1/auth/register`, {
    method: "POST",
    body: JSON.stringify({ username: name, password: "pass-123456" }),
  });
  const login = await SELF.fetch(`${BASE}/api/v1/auth/login`, {
    method: "POST",
    body: JSON.stringify({ username: name, password: "pass-123456" }),
  });
  const row = await DB.prepare("SELECT id, role FROM users WHERE username=?")
    .bind(name)
    .first<{ id: string; role: string }>();
  return { cookie: login.headers.get("Set-Cookie")!.split(";")[0]!, id: row!.id, role: row!.role };
}

async function seedOrder(orderId: string, userId: string, account: string): Promise<void> {
  await DB.prepare(
    "INSERT INTO orders(id,user_id,product_code,platform,account,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
  )
    .bind(orderId, userId, "KT", "xuexitong", account, "processing", Date.now(), Date.now())
    .run();
}

beforeEach(async () => {
  await applyMigrations();
  await DB.batch([
    DB.prepare("DELETE FROM order_credentials"),
    DB.prepare("DELETE FROM task_attempts"),
    DB.prepare("DELETE FROM tasks"),
    DB.prepare("DELETE FROM auth_sessions"),
    DB.prepare("DELETE FROM orders"),
    DB.prepare("DELETE FROM users"),
    DB.prepare("DELETE FROM audit_events"),
  ]);
});

describe("stage-cloud-16 central query API", () => {
  it("my/orders lists only own orders, newest first; anonymous 401", async () => {
    const anon = await SELF.fetch(`${BASE}/api/v1/my/orders`);
    expect(anon.status).toBe(401);

    const alice = await mkUser("alice16");
    const bob = await mkUser("bob16");
    await seedOrder("ord-a1", alice.id, "acc-a1");
    await seedOrder("ord-a2", alice.id, "acc-a2");
    await seedOrder("ord-b1", bob.id, "acc-b1");

    const res = await SELF.fetch(`${BASE}/api/v1/my/orders`, {
      headers: { Cookie: alice.cookie },
    });
    expect(res.status).toBe(200);
    const body = (await res.json()) as {
      orders: { order_id: string; account: string }[];
      total: number;
    };
    expect(body.total).toBe(2);
    expect(body.orders.map((o) => o.order_id)).toEqual(["ord-a2", "ord-a1"]); // 新→旧
    expect(body.orders.every((o) => o.order_id.startsWith("ord-a"))).toBe(true);
  });

  it("order detail: owner OK, other user 404 (no existence leak), admin OK", async () => {
    const alice = await mkUser("alice16d");
    const bob = await mkUser("bob16d");
    await seedOrder("ord-d1", alice.id, "acc-d1");
    await DB.prepare("UPDATE users SET role='admin' WHERE username='bob16d'").run();

    const own = await SELF.fetch(`${BASE}/api/v1/orders/ord-d1`, {
      headers: { Cookie: alice.cookie },
    });
    expect(own.status).toBe(200);

    const other = await SELF.fetch(`${BASE}/api/v1/orders/ord-d1`, {
      headers: { Cookie: (await mkUser("eve16")).cookie },
    });
    expect(other.status).toBe(404);

    const admin = await SELF.fetch(`${BASE}/api/v1/orders/ord-d1`, {
      headers: { Cookie: bob.cookie },
    });
    expect(admin.status).toBe(200);

    const missing = await SELF.fetch(`${BASE}/api/v1/orders/ord-nope`, {
      headers: { Cookie: alice.cookie },
    });
    expect(missing.status).toBe(404);
  });

  it("admin query by account (/qacc semantics) → results + audit; user → 403; short account → 400", async () => {
    const alice = await mkUser("alice16q");
    const boss = await mkUser("boss16q");
    await DB.prepare("UPDATE users SET role='admin' WHERE username='boss16q'").run();
    await seedOrder("ord-q1", alice.id, "victim@example.com");
    await seedOrder("ord-q2", alice.id, "victim@example.com");

    const denied = await SELF.fetch(
      `${BASE}/api/v1/admin/orders/by-account?account=victim@example.com`,
      { headers: { Cookie: alice.cookie } },
    );
    expect(denied.status).toBe(403);

    const short = await SELF.fetch(
      `${BASE}/api/v1/admin/orders/by-account?account=v`,
      { headers: { Cookie: boss.cookie } },
    );
    expect(short.status).toBe(400);

    const res = await SELF.fetch(
      `${BASE}/api/v1/admin/orders/by-account?account=victim@example.com`,
      { headers: { Cookie: boss.cookie } },
    );
    expect(res.status).toBe(200);
    const body = (await res.json()) as { total: number };
    expect(body.total).toBe(2);

    const audit = await DB.prepare(
      "SELECT metadata_json FROM audit_events WHERE event_type='ORDERS_QUERIED_BY_ACCOUNT'",
    ).first<{ metadata_json: string }>();
    expect(JSON.parse(audit!.metadata_json)).toMatchObject({ hits: 2 });
  });
});
