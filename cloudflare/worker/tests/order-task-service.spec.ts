/// <reference types="@cloudflare/vitest-plugin/types" />
import { beforeEach, describe, expect, it } from "vitest";
import { env, runInDurableObject, SELF } from "cloudflare:test";
import { applyMigrations, DB } from "./helpers";

// stage-cloud-16b 验收（§55/§28/§33）：
// 商品列表 / 订单创建（Idempotency-Key 幂等）/ 任务入队（payload 红线）/
// 全闭环：下单 → 入队 → executor 认领 → ack → complete → D1 镜像终态。

const BASE = "https://example.com";
const BOOTSTRAP = "test-bootstrap-token";

async function mkUser(name: string): Promise<{ cookie: string; id: string }> {
  await SELF.fetch(`${BASE}/api/v1/auth/register`, {
    method: "POST",
    body: JSON.stringify({ username: name, password: "pass-123456" }),
  });
  const login = await SELF.fetch(`${BASE}/api/v1/auth/login`, {
    method: "POST",
    body: JSON.stringify({ username: name, password: "pass-123456" }),
  });
  const row = await DB.prepare("SELECT id FROM users WHERE username=?").bind(name).first<{ id: string }>();
  return { cookie: login.headers.get("Set-Cookie")!.split(";")[0]!, id: row!.id };
}

async function seedProduct(): Promise<void> {
  await DB.prepare(
    "INSERT INTO products(id,code,name,platform,enabled,created_at,updated_at) VALUES(?,?,?,?,1,?,?)",
  )
    .bind("p-1", "KT", "课程代学", "xuexitong", Date.now(), Date.now())
    .run();
}

async function createOrder(cookie: string, idemKey?: string): Promise<{ status: number; orderId: string }> {
  const res = await SELF.fetch(`${BASE}/api/v1/orders`, {
    method: "POST",
    headers: { Cookie: cookie, ...(idemKey ? { "Idempotency-Key": idemKey } : {}) },
    body: JSON.stringify({ product_code: "KT", platform: "xuexitong", account: "stu@x.com" }),
  });
  const body = (await res.json()) as { order_id?: string };
  return { status: res.status, orderId: body.order_id ?? "" };
}

beforeEach(async () => {
  await applyMigrations();
  await DB.batch([
    DB.prepare("DELETE FROM artifacts"),
    DB.prepare("DELETE FROM order_credentials"),
    DB.prepare("DELETE FROM task_attempts"),
    DB.prepare("DELETE FROM tasks"),
    DB.prepare("DELETE FROM idempotency_keys"),
    DB.prepare("DELETE FROM auth_sessions"),
    DB.prepare("DELETE FROM orders"),
    DB.prepare("DELETE FROM products"),
    DB.prepare("DELETE FROM users"),
    DB.prepare("DELETE FROM executor_nodes"),
    DB.prepare("DELETE FROM audit_events"),
  ]);
  await seedProduct();
  // DO 队列与 D1 独立，须一并清空
  for (const path of ["local", "internal", "external"]) {
    const ns = (env as unknown as { COORDINATOR: DurableObjectNamespace }).COORDINATOR;
    await runInDurableObject(ns.get(ns.idFromName(path)), async (_i, state) => {
      await state.storage.deleteAll();
      await state.storage.deleteAlarm();
    });
  }
});

describe("stage-cloud-16b order/task creation", () => {
  it("products list + order create + idempotency-key dedup", async () => {
    const u = await mkUser("u16b");
    const products = await SELF.fetch(`${BASE}/api/v1/products`);
    expect(((await products.json()) as { products: unknown[] }).products.length).toBe(1);

    const first = await createOrder(u.cookie, "idem-1");
    expect(first.status).toBe(201);
    const second = await createOrder(u.cookie, "idem-1");
    expect(second.status).toBe(200);
    expect(second.orderId).toBe(first.orderId); // 幂等：同一 key 同一订单

    const count = await DB.prepare("SELECT COUNT(*) AS n FROM orders").first<{ n: number }>();
    expect(count?.n).toBe(1);
  });

  it("task enqueue: payload redline at source; owner-only detail", async () => {
    const u = await mkUser("u16bt");
    const o = await createOrder(u.cookie);

    const bad = await SELF.fetch(`${BASE}/api/v1/orders/${o.orderId}/tasks`, {
      method: "POST",
      headers: { Cookie: u.cookie },
      body: JSON.stringify({
        execution_path: "internal",
        task_type: "xuexitong.chapter",
        payload: { chapter_id: 1, account_password: "leak" },
      }),
    });
    expect(bad.status).toBe(400); // §33 源头拒绝

    const res = await SELF.fetch(`${BASE}/api/v1/orders/${o.orderId}/tasks`, {
      method: "POST",
      headers: { Cookie: u.cookie },
      body: JSON.stringify({
        execution_path: "internal",
        task_type: "xuexitong.chapter",
        required_capabilities: ["xuexitong"],
        payload: { chapter_id: 1 },
      }),
    });
    expect(res.status).toBe(201);
    const { task_id } = (await res.json()) as { task_id: string };

    // D1 持久真相 + DO 队列同时登记
    const row = await DB.prepare("SELECT status, execution_path FROM tasks WHERE id=?")
      .bind(task_id)
      .first<{ status: string; execution_path: string }>();
    expect(row).toMatchObject({ status: "queued", execution_path: "internal" });

    // 订单进入 processing
    const order = await DB.prepare("SELECT status FROM orders WHERE id=?")
      .bind(o.orderId)
      .first<{ status: string }>();
    expect(order?.status).toBe("processing");

    // detail：owner 可见
    const detail = await SELF.fetch(`${BASE}/api/v1/tasks/${task_id}`, {
      headers: { Cookie: u.cookie },
    });
    expect(detail.status).toBe(200);
  });

  it("full loop: order → task → executor claim → ack → complete → D1 mirrored terminal", async () => {
    const u = await mkUser("u16be");
    const o = await createOrder(u.cookie);
    const t = await SELF.fetch(`${BASE}/api/v1/orders/${o.orderId}/tasks`, {
      method: "POST",
      headers: { Cookie: u.cookie },
      body: JSON.stringify({
        execution_path: "internal",
        task_type: "xuexitong.chapter",
        required_capabilities: ["xuexitong"],
        payload: { chapter_id: 7 },
      }),
    });
    const { task_id } = (await t.json()) as { task_id: string };

    // executor 注册 + 认领
    const reg = await SELF.fetch(`${BASE}/api/executor/v1/register`, {
      method: "POST",
      headers: { Authorization: `Bearer ${BOOTSTRAP}` },
      body: JSON.stringify({
        executor_id: "exec-internal-e2e",
        execution_path: "internal",
        capabilities: ["xuexitong"],
        version: "1.0.0",
      }),
    });
    const token = ((await reg.json()) as { executor_token: string }).executor_token;

    const claim = await SELF.fetch(`${BASE}/api/executor/v1/claim`, {
      method: "POST",
      headers: { Authorization: `Bearer ${token}` },
      body: JSON.stringify({
        executor_id: "exec-internal-e2e",
        execution_path: "internal",
        capabilities: ["xuexitong"],
      }),
    });
    const d = ((await claim.json()) as { task: Record<string, unknown> }).task!;
    expect(d["task_id"]).toBe(task_id);

    // D1 已镜像 leased
    const leased = await DB.prepare("SELECT status, attempt_no, executor_id FROM tasks WHERE id=?")
      .bind(task_id)
      .first<{ status: string; attempt_no: number; executor_id: string }>();
    expect(leased).toMatchObject({ status: "leased", attempt_no: 1, executor_id: "exec-internal-e2e" });

    // ack → running
    const ack = await SELF.fetch(`${BASE}/api/executor/v1/ack`, {
      method: "POST",
      headers: { Authorization: `Bearer ${token}` },
      body: JSON.stringify({
        executor_id: "exec-internal-e2e",
        execution_path: "internal",
        task_id,
        lease_id: d["lease_id"],
      }),
    });
    expect(((await ack.json()) as { status: string }).status).toBe("running");
    const attempt = await DB.prepare("SELECT status FROM task_attempts WHERE id=?")
      .bind(`${task_id}#1`)
      .first<{ status: string }>();
    expect(attempt?.status).toBe("running");

    // complete → succeeded（D1 tasks + attempts + orders 全镜像）
    const done = await SELF.fetch(`${BASE}/api/executor/v1/complete`, {
      method: "POST",
      headers: { Authorization: `Bearer ${token}` },
      body: JSON.stringify({
        executor_id: "exec-internal-e2e",
        execution_path: "internal",
        task_id,
        lease_id: d["lease_id"],
        outcome: "succeeded",
      }),
    });
    expect(((await done.json()) as { status: string }).status).toBe("succeeded");

    const task = await DB.prepare("SELECT status FROM tasks WHERE id=?").bind(task_id).first<{ status: string }>();
    expect(task?.status).toBe("succeeded");
    const order = await DB.prepare("SELECT status FROM orders WHERE id=?").bind(o.orderId).first<{ status: string }>();
    expect(order?.status).toBe("succeeded");
    const audit = await DB.prepare(
      "SELECT event_type FROM audit_events WHERE event_type='TASK_CANCELED'",
    ).first();
    expect(audit).toBeNull();
  });

  it("cancel queued task → canceled; audit written", async () => {
    const u = await mkUser("u16bc");
    const o = await createOrder(u.cookie);
    const t = await SELF.fetch(`${BASE}/api/v1/orders/${o.orderId}/tasks`, {
      method: "POST",
      headers: { Cookie: u.cookie },
      body: JSON.stringify({ execution_path: "local", task_type: "x.chapter", payload: {} }),
    });
    const { task_id } = (await t.json()) as { task_id: string };
    const cancel = await SELF.fetch(`${BASE}/api/v1/tasks/${task_id}`, {
      method: "POST",
      headers: { Cookie: u.cookie },
    });
    expect(((await cancel.json()) as { status: string }).status).toBe("canceled");
    const audit = await DB.prepare(
      "SELECT event_type FROM audit_events WHERE event_type='TASK_CANCELED' AND entity_id=?",
    )
      .bind(task_id)
      .first();
    expect(audit).toBeTruthy();
  });
});
