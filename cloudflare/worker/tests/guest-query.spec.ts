/// <reference types="@cloudflare/vitest-plugin/types" />
import { beforeEach, describe, expect, it } from "vitest";
import { SELF } from "cloudflare:test";
import { applyMigrations, DB } from "./helpers";

// stage-cloud-17 验收（§58/§89 + 校准结论）：
// 前缀命中 / 未命中 / 短前缀 400 / 受限视图（无 account/note）/ 审计。

const BASE = "https://example.com";

async function seedOrder(orderId: string, account: string, note: string): Promise<void> {
  await DB.prepare(
    "INSERT INTO users(id,username,password_hash,role,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
  )
    .bind("u-g1", "owner", "x", "user", "active", Date.now(), Date.now())
    .run();
  await DB.prepare(
    "INSERT INTO orders(id,user_id,product_code,platform,account,status,note,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
  )
    .bind(orderId, "u-g1", "KT", "xuexitong", account, "processing", note, Date.now(), Date.now())
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

describe("stage-cloud-17 guest query", () => {
  it("prefix hit → restricted view (no account/note), audit written", async () => {
    await seedOrder("g20260922abcd0001", "secret@example.com", "[进度] 100% 内部信息");
    const res = await SELF.fetch(`${BASE}/api/v1/guest/query`, {
      method: "POST",
      body: JSON.stringify({ code: "g20260922" }),
    });
    expect(res.status).toBe(200);
    const body = (await res.json()) as {
      orders: Record<string, unknown>[];
      total: number;
    };
    expect(body.total).toBe(1);
    const view = JSON.stringify(body.orders[0]);
    expect(view).not.toContain("secret@example.com");
    expect(view).not.toContain("内部信息");
    expect(body.orders[0]!["status"]).toBe("processing");

    const audit = await DB.prepare(
      "SELECT event_type, actor_type FROM audit_events WHERE event_type='GUEST_QUERIED'",
    ).first<{ event_type: string; actor_type: string }>();
    expect(audit).toMatchObject({ event_type: "GUEST_QUERIED", actor_type: "guest" });
  });

  it("no hit → total 0; short prefix → 400", async () => {
    const miss = await SELF.fetch(`${BASE}/api/v1/guest/query`, {
      method: "POST",
      body: JSON.stringify({ code: "zzzzzzzz9999" }),
    });
    expect(miss.status).toBe(200);
    expect(((await miss.json()) as { total: number }).total).toBe(0);

    const short = await SELF.fetch(`${BASE}/api/v1/guest/query`, {
      method: "POST",
      body: JSON.stringify({ code: "abc" }),
    });
    expect(short.status).toBe(400);
  });
});
