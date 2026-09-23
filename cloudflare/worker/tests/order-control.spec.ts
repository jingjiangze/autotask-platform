/// <reference types="@cloudflare/vitest-plugin/types" />
import { expect, describe, it, beforeEach } from "vitest";
import { env, SELF } from "cloudflare:test";
import { applyMigrations, DB } from "./helpers";
import { encryptCredential } from "../src/credentials/credential-service";

// stage-cloud-28：订单控制 / 凭据明文查看 / 查课表 —— 状态机与安全边界

async function seedUserOrder(): Promise<{ cookie: string; orderId: string; userId: string }> {
  const u = `ctl_${Date.now().toString(36)}`;
  const r = await SELF.fetch("https://example.com/api/v1/auth/register", {
    method: "POST",
    body: JSON.stringify({ username: u, password: "ctl-pass-123456" }),
  });
  expect(r.status).toBe(201);
  const lr = await SELF.fetch("https://example.com/api/v1/auth/login", {
    method: "POST",
    body: JSON.stringify({ username: u, password: "ctl-pass-123456" }),
  });
  const cookie = (lr.headers.get("Set-Cookie") ?? "").split(";")[0];
  const userId = (await DB.prepare("SELECT id FROM users WHERE username=?").bind(u).first<{ id: string }>())!.id;
  const orderId = `ctl-order-${Date.now().toString(36)}`;
  await DB.prepare(
    "INSERT INTO orders(id,user_id,product_code,platform,account,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
  ).bind(orderId, userId, "E2E", "chaoxing", "acc", "processing", Date.now(), Date.now()).run();
  return { cookie, orderId, userId };
}

describe("stage-cloud-28 order control", () => {
  beforeEach(applyMigrations);

  it("pause → control=paused; resume → cleared; audit written", async () => {
    const { cookie, orderId } = await seedUserOrder();
    const p = await SELF.fetch(`https://example.com/api/v1/orders/${orderId}/control`, {
      method: "POST", headers: { Cookie: cookie },
      body: JSON.stringify({ action: "pause" }),
    });
    expect(p.status).toBe(200);
    const row = await DB.prepare("SELECT control FROM orders WHERE id=?").bind(orderId).first<{ control: string }>();
    expect(row!.control).toBe("paused");
    const r = await SELF.fetch(`https://example.com/api/v1/orders/${orderId}/control`, {
      method: "POST", headers: { Cookie: cookie },
      body: JSON.stringify({ action: "resume" }),
    });
    expect(r.status).toBe(200);
    const row2 = await DB.prepare("SELECT control FROM orders WHERE id=?").bind(orderId).first<{ control: string }>();
    expect(row2!.control).toBe("");
    const audit = await DB.prepare(
      "SELECT COUNT(*) n FROM audit_events WHERE entity_id=? AND event_type IN ('ORDER_PAUSED','ORDER_RESUMED')",
    ).bind(orderId).first<{ n: number }>();
    expect(audit!.n).toBe(2);
  });

  it("credentials view returns plaintext to owner only (404 for stranger)", async () => {
    const { cookie, orderId } = await seedUserOrder();
    const enc1 = await encryptCredential({ CREDENTIAL_KEY: (env as Record<string, unknown>).CREDENTIAL_KEY } as never, "13800000000");
    const enc2 = await encryptCredential({ CREDENTIAL_KEY: (env as Record<string, unknown>).CREDENTIAL_KEY } as never, "secret-pass");
    await DB.batch([
      DB.prepare(
        "INSERT INTO order_credentials(id,order_id,credential_type,ciphertext,encryption_version,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
      ).bind(`c1-${orderId}`, orderId, "account", enc1.ciphertext, enc1.encryption_version, Date.now(), Date.now()),
      DB.prepare(
        "INSERT INTO order_credentials(id,order_id,credential_type,ciphertext,encryption_version,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
      ).bind(`c2-${orderId}`, orderId, "account_password", enc2.ciphertext, enc2.encryption_version, Date.now(), Date.now()),
    ]);
    const ok = await SELF.fetch(`https://example.com/api/v1/orders/${orderId}/credentials`, {
      method: "GET", headers: { Cookie: cookie },
    });
    expect(ok.status).toBe(200);
    const body = (await ok.json()) as { account: string; password: string };
    expect(body.account).toBe("13800000000");
    expect(body.password).toBe("secret-pass");
    // 他人 → 404（不暴露存在性）
    const stranger = `str_${Date.now().toString(36)}`;
    await SELF.fetch("https://example.com/api/v1/auth/register", {
      method: "POST", body: JSON.stringify({ username: stranger, password: "str-pass-123456" }),
    });
    const lr = await SELF.fetch("https://example.com/api/v1/auth/login", {
      method: "POST",
      body: JSON.stringify({ username: stranger, password: "str-pass-123456" }),
    });
    const other = (lr.headers.get("Set-Cookie") ?? "").split(";")[0];
    const deny = await SELF.fetch(`https://example.com/api/v1/orders/${orderId}/credentials`, {
      method: "GET", headers: { Cookie: other },
    });
    expect(deny.status).toBe(404);
  });

  it("query-courses requires account+password; enqueues chaoxing.courses with priority 5", async () => {
    const { cookie, orderId } = await seedUserOrder();
    const bad = await SELF.fetch(`https://example.com/api/v1/orders/${orderId}/query-courses`, {
      method: "POST", headers: { Cookie: cookie }, body: JSON.stringify({ account: "", password: "" }),
    });
    expect(bad.status).toBe(400);
    const good = await SELF.fetch(`https://example.com/api/v1/orders/${orderId}/query-courses`, {
      method: "POST", headers: { Cookie: cookie },
      body: JSON.stringify({ account: "13800000000", password: "pw", execution_path: "internal" }),
    });
    expect(good.status).toBe(201);
    const { task_id } = (await good.json()) as { task_id: string };
    const t = await DB.prepare("SELECT task_type, execution_path FROM tasks WHERE id=?").bind(task_id)
      .first<{ task_type: string; execution_path: string }>();
    expect(t!.task_type).toBe("chaoxing.courses");
    expect(t!.execution_path).toBe("internal");
    // 凭据已 enc-v2 入库（§33：payload 永不带凭据）
    const cred = await DB.prepare(
      "SELECT COUNT(*) n FROM order_credentials WHERE order_id=? AND encryption_version='enc-v2'",
    ).bind(orderId).first<{ n: number }>();
    expect(cred!.n).toBe(2);
  });
});
