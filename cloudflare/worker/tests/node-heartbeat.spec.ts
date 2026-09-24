/// <reference types="@cloudflare/vitest-plugin/types" />
import { beforeEach, describe, expect, it } from "vitest";
import { SELF } from "cloudflare:test";
import { applyMigrations, DB } from "./helpers";

// stage-cloud-13 验收（§45）：
// 任意认证成功调用刷新 last_seen_at + status=online /
// node-heartbeat 端点返回服务器时间 / 错误 token 不刷新。

const BASE = "https://example.com";
const BOOTSTRAP = "test-bootstrap-token";

async function register(executorId: string, path: string): Promise<string> {
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

async function nodeState(executorId: string) {
  return DB.prepare("SELECT last_seen_at, status FROM executor_nodes WHERE id=?")
    .bind(executorId)
    .first<{ last_seen_at: number | null; status: string }>();
}

beforeEach(async () => {
  await applyMigrations();
  await DB.batch([
    DB.prepare("DELETE FROM executor_nodes"),
    DB.prepare("DELETE FROM audit_events"),
  ]);
});

describe("stage-cloud-13 node heartbeat", () => {
  it("node-heartbeat refreshes last_seen_at and marks online", async () => {
    const token = await register("exec-internal-13h", "internal");
    let st = await nodeState("exec-internal-13h");
    expect(st?.last_seen_at).toBeNull(); // 注册后未活动

    const res = await SELF.fetch(`${BASE}/api/executor/v1/node-heartbeat`, {
      method: "POST",
      headers: { Authorization: `Bearer ${token}` },
      body: JSON.stringify({
        executor_id: "exec-internal-13h",
        execution_path: "internal",
      }),
    });
    expect(res.status).toBe(200);
    const body = (await res.json()) as { ok: boolean; server_time: number };
    expect(body.ok).toBe(true);
    expect(body.server_time).toBeGreaterThan(0);

    st = await nodeState("exec-internal-13h");
    expect(st?.status).toBe("online");
    expect(st?.last_seen_at).toBeGreaterThan(0);
  });

  it("forged token does not refresh heartbeat", async () => {
    await register("exec-internal-13x", "internal");
    const res = await SELF.fetch(`${BASE}/api/executor/v1/node-heartbeat`, {
      method: "POST",
      headers: { Authorization: "Bearer forged" },
      body: JSON.stringify({
        executor_id: "exec-internal-13x",
        execution_path: "internal",
      }),
    });
    expect(res.status).toBe(401);
    const st = await nodeState("exec-internal-13x");
    expect(st?.last_seen_at).toBeNull();
  });
});
