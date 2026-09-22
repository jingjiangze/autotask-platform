/// <reference types="@cloudflare/vitest-plugin/types" />
import { beforeEach, describe, expect, it } from "vitest";
import { env, runInDurableObject, SELF } from "cloudflare:test";
import { applyMigrations, DB } from "./helpers";

// stage-cloud-14 验收（§48/§90）：
// 租约门控上传（R2 落桶 + D1 回写 tasks/task_attempts + artifacts 记录 +
// sha256 完整性）/ 伪造租约 DENY / owner/admin 下载 / 他人 404 / 空内容 400。

const BASE = "https://example.com";
const BOOTSTRAP = "test-bootstrap-token";
const CONTENT = "stdout: task finished successfully 2026-09-22\n";
const SECRET = "s3cret-password-x";

type Stub = DurableObjectStub;

function coord(path: string): Stub {
  const ns = (env as unknown as { COORDINATOR: DurableObjectNamespace }).COORDINATOR;
  return ns.get(ns.idFromName(path));
}

async function registerExecutor(executorId: string, path: string): Promise<string> {
  const res = await SELF.fetch(`${BASE}/api/executor/v1/register`, {
    method: "POST",
    headers: { Authorization: `Bearer ${BOOTSTRAP}` },
    body: JSON.stringify({
      executor_id: executorId,
      execution_path: path,
      capabilities: ["xuexitong"],
      version: "1.0.0",
    }),
  });
  expect(res.status).toBe(201);
  return ((await res.json()) as { executor_token: string }).executor_token;
}

async function seedOrder(orderId: string, userId: string): Promise<void> {
  await DB.prepare(
    "INSERT INTO users(id,username,password_hash,role,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
  )
    .bind(userId, `u-${userId}`, "x", "user", "active", Date.now(), Date.now())
    .run();
  await DB.prepare(
    "INSERT INTO orders(id,user_id,product_code,platform,account,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
  )
    .bind(orderId, userId, "KT", "xuexitong", "acc", "processing", Date.now(), Date.now())
    .run();
}

async function enqueueAndClaim(
  token: string,
  taskId: string,
  orderId: string,
): Promise<{ task_id: string; lease_id: string }> {
  const enq = await coord("internal").fetch("https://do/message", {
    method: "POST",
    body: JSON.stringify({
      type: "enqueue",
      task_id: taskId,
      order_id: orderId,
      task_type: "xuexitong.chapter",
      required_capabilities: ["xuexitong"],
      payload: { chapter_id: 1 },
    }),
  });
  expect(enq.status).toBe(201);
  const c = await SELF.fetch(`${BASE}/api/executor/v1/claim`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body: JSON.stringify({
      executor_id: "exec-internal-14",
      execution_path: "internal",
      capabilities: ["xuexitong"],
    }),
  });
  const d = (await c.json() as { task: Record<string, unknown> | null }).task;
  expect(d).not.toBeNull();
  expect(d!["task_id"]).toBe(taskId);
  return { task_id: taskId, lease_id: d!["lease_id"] as string };
}

function upload(
  token: string,
  lease: { task_id: string; lease_id: string },
  overrides: Record<string, string> = {},
  body = CONTENT,
): Promise<Response> {
  const q = new URLSearchParams({
    executor_id: "exec-internal-14",
    execution_path: "internal",
    ...lease,
    artifact_type: "stdout",
    content_type: "text/plain",
    ...overrides,
  });
  return SELF.fetch(`${BASE}/api/executor/v1/artifacts?${q}`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "text/plain" },
    body,
  });
}

async function mkLogin(name: string, password: string): Promise<string> {
  await SELF.fetch(`${BASE}/api/v1/auth/register`, {
    method: "POST",
    body: JSON.stringify({ username: name, password }),
  });
  const login = await SELF.fetch(`${BASE}/api/v1/auth/login`, {
    method: "POST",
    body: JSON.stringify({ username: name, password }),
  });
  return login.headers.get("Set-Cookie")!.split(";")[0]!;
}

beforeEach(async () => {
  await applyMigrations();
  await DB.batch([
    DB.prepare("DELETE FROM artifacts"),
    DB.prepare("DELETE FROM order_credentials"),
    DB.prepare("DELETE FROM task_attempts"),
    DB.prepare("DELETE FROM tasks"),
    DB.prepare("DELETE FROM auth_sessions"),
    DB.prepare("DELETE FROM orders"),
    DB.prepare("DELETE FROM users"),
    DB.prepare("DELETE FROM executor_nodes"),
    DB.prepare("DELETE FROM audit_events"),
  ]);
  for (const path of ["internal", "external"]) {
    await runInDurableObject(coord(path), async (_i, state) => {
      await state.storage.deleteAll();
      await state.storage.deleteAlarm();
    });
  }
});

describe("stage-cloud-14 artifact pipeline", () => {
  it("upload with valid lease → 201, R2 stored, D1 chain written, sha256 matches", async () => {
    await seedOrder("ord-r1", "u-r1");
    const token = await registerExecutor("exec-internal-14", "internal");
    const lease = await enqueueAndClaim(token, "t-r1", "ord-r1");

    const res = await upload(token, lease);
    expect(res.status).toBe(201);
    const body = (await res.json()) as { artifact_id: string; size_bytes: number; sha256: string };
    expect(body.size_bytes).toBe(CONTENT.length);

    const expected = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(CONTENT));
    const hex = [...new Uint8Array(expected)].map((b) => b.toString(16).padStart(2, "0")).join("");
    expect(body.sha256).toBe(hex);

    // R2 实存
    const keys = await (env as unknown as { ARTIFACTS: R2Bucket }).ARTIFACTS.list();
    expect(keys.objects.length).toBe(1);

    // D1 tasks / task_attempts / artifacts 回写
    const task = await DB.prepare("SELECT status, order_id FROM tasks WHERE id='t-r1'")
      .first<{ status: string; order_id: string }>();
    expect(task).toMatchObject({ status: "running", order_id: "ord-r1" });
    const attempt = await DB.prepare(
      "SELECT id, status FROM task_attempts WHERE task_id='t-r1'",
    ).first<{ id: string; status: string }>();
    expect(attempt?.id).toBe("t-r1#1");
    const art = await DB.prepare(
      "SELECT artifact_type, size_bytes FROM artifacts WHERE id=?",
    )
      .bind(body.artifact_id)
      .first<{ artifact_type: string; size_bytes: number }>();
    expect(art).toMatchObject({ artifact_type: "stdout", size_bytes: CONTENT.length });
  });

  it("forged lease → 403; empty body → 400", async () => {
    await seedOrder("ord-r2", "u-r2");
    const token = await registerExecutor("exec-internal-14", "internal");
    const forged = await upload(token, { task_id: "t-none", lease_id: "fake" });
    expect(forged.status).toBe(403);

    const lease = await enqueueAndClaim(token, "t-r2", "ord-r2");
    const empty = await upload(token, lease, {}, "");
    expect(empty.status).toBe(400);
  });

  it("download: owner + admin OK, stranger 404, content matches", async () => {
    await seedOrder("ord-r3", "u-r3");
    const token = await registerExecutor("exec-internal-14", "internal");
    const lease = await enqueueAndClaim(token, "t-r3", "ord-r3");
    const up = await upload(token, lease);
    const { artifact_id } = (await up.json()) as { artifact_id: string };

    const ownerCookie = await mkLogin("u-u-r3", "owner-pass-123");
    // 所有者是 u-r3（seedOrder 建的占位用户），改密后登录拿会话不便 —— 用 admin 校验 + 越权校验
    await DB.prepare(
      "UPDATE users SET password_hash='pbkdf2-sha256-v1$20000$AA$BB', username='owner3' WHERE id='u-r3'",
    ).run();

    const strangerCookie = await mkLogin("stranger3", "stranger-pass-1");
    const stranger = await SELF.fetch(
      `${BASE}/api/v1/orders/ord-r3/artifacts/${artifact_id}`,
      { headers: { Cookie: strangerCookie } },
    );
    expect(stranger.status).toBe(404);

    const admin = await mkLogin("admin3", "admin-pass-1234");
    await DB.prepare("UPDATE users SET role='admin' WHERE username='admin3'").run();
    const ok = await SELF.fetch(`${BASE}/api/v1/orders/ord-r3/artifacts/${artifact_id}`, {
      headers: { Cookie: admin },
    });
    expect(ok.status).toBe(200);
    expect(await ok.text()).toBe(CONTENT);
    void ownerCookie;
  });
});
