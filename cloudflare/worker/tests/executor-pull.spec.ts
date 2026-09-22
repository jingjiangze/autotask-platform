/// <reference types="@cloudflare/vitest-plugin/types" />
import { beforeEach, describe, expect, it } from "vitest";
import { env, runInDurableObject, SELF } from "cloudflare:test";
import { applyMigrations } from "./helpers";

// stage-cloud-10 验收（计划 §42/§43/§84）：
// HTTP claim/ack/heartbeat/complete 全链路（08 三元认证 + 09 DO 转发），
// 认证失败 DENY / 路径伪造 DENY / 空队列 task:null。

const BASE = "https://example.com";
const BOOTSTRAP = "test-bootstrap-token";

interface RegBody {
  ok: boolean;
  executor_token: string;
}

async function registerExecutor(
  executorId: string,
  path: string,
  capabilities: string[],
): Promise<string> {
  const res = await SELF.fetch(`${BASE}/api/executor/v1/register`, {
    method: "POST",
    headers: { Authorization: `Bearer ${BOOTSTRAP}` },
    body: JSON.stringify({
      executor_id: executorId,
      execution_path: path,
      capabilities,
      version: "1.0.0",
    }),
  });
  expect(res.status).toBe(201);
  const body = (await res.json()) as RegBody;
  return body.executor_token;
}

function call(
  action: string,
  token: string,
  body: Record<string, unknown>,
): Promise<Response> {
  return SELF.fetch(`${BASE}/api/executor/v1/${action}`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body: JSON.stringify(body),
  });
}

type Stub = DurableObjectStub;

function coord(path: string): Stub {
  const ns = (env as unknown as { COORDINATOR: DurableObjectNamespace }).COORDINATOR;
  return ns.get(ns.idFromName(path));
}

async function enqueueOn(path: string, taskId: string): Promise<void> {
  const res = await coord(path).fetch("https://do/message", {
    method: "POST",
    body: JSON.stringify({
      type: "enqueue",
      task_id: taskId,
      order_id: `ord-${taskId}`,
      task_type: "xuexitong.chapter",
      required_capabilities: ["xuexitong"],
      payload: { chapter_id: 1 },
    }),
  });
  expect(res.status).toBe(201);
}

const CLAIM_BODY = {
  executor_id: "exec-internal-01",
  execution_path: "internal",
  capabilities: ["xuexitong"],
};

beforeEach(async () => {
  await applyMigrations();
  for (const path of ["internal", "external"]) {
    await runInDurableObject(coord(path), async (_i, state) => {
      await state.storage.deleteAll();
      await state.storage.deleteAlarm();
    });
  }
});

describe("stage-cloud-10 executor pull API", () => {
  it("full loop: register → claim → ack → heartbeat → complete", async () => {
    const token = await registerExecutor("exec-internal-01", "internal", ["xuexitong"]);
    await enqueueOn("internal", "t-http-1");

    // claim
    const c = await call("claim", token, CLAIM_BODY);
    expect(c.status).toBe(200);
    const d = (await c.json() as { task: Record<string, unknown> }).task;
    expect(d).not.toBeNull();
    expect(d["task_id"]).toBe("t-http-1");
    expect(d["protocol"]).toBe("autotask.executor/v1");

    // ack
    const a = await call("ack", token, {
      ...CLAIM_BODY,
      task_id: d["task_id"],
      lease_id: d["lease_id"],
    });
    expect(a.body ? ((await a.json()) as { status: string }).status : "").toBe("running");

    // heartbeat
    const h = await call("heartbeat", token, {
      ...CLAIM_BODY,
      task_id: d["task_id"],
      lease_id: d["lease_id"],
    });
    const hb = (await h.json()) as { ok: boolean; lease_expires_at: number };
    expect(hb.ok).toBe(true);
    expect(hb.lease_expires_at).toBeGreaterThan(Date.now());

    // complete
    const f = await call("complete", token, {
      ...CLAIM_BODY,
      task_id: d["task_id"],
      lease_id: d["lease_id"],
      outcome: "succeeded",
    });
    expect(((await f.json()) as { status: string }).status).toBe("succeeded");
  });

  it("claim on empty path → 200 {task:null}", async () => {
    const token = await registerExecutor("exec-internal-02", "internal", ["xuexitong"]);
    const c = await call("claim", token, {
      ...CLAIM_BODY,
      executor_id: "exec-internal-02",
    });
    expect(c.status).toBe(200);
    expect((await c.json() as { task: unknown }).task).toBeNull();
  });

  it("wrong token → 401 EXECUTOR_AUTH_FAILED", async () => {
    await registerExecutor("exec-internal-03", "internal", ["xuexitong"]);
    const c = await call("claim", "forged-token", CLAIM_BODY);
    expect(c.status).toBe(401);
    expect(((await c.json()) as { error: { code: string } }).error.code).toBe(
      "EXECUTOR_AUTH_FAILED",
    );
  });

  it("path forgery DENY: internal executor claiming on external path → 403", async () => {
    const token = await registerExecutor("exec-internal-04", "internal", ["xuexitong"]);
    const c = await call("claim", token, {
      executor_id: "exec-internal-04",
      execution_path: "external",
      capabilities: ["xuexitong"],
    });
    expect(c.status).toBe(403);
    expect(((await c.json()) as { error: { code: string } }).error.code).toBe(
      "EXECUTOR_PATH_MISMATCH",
    );
  });

  it("unknown action → 404; GET → 405", async () => {
    const token = await registerExecutor("exec-internal-05", "internal", ["xuexitong"]);
    const nf = await call("nonexistent", token, CLAIM_BODY);
    expect(nf.status).toBe(404);
    const res = await SELF.fetch(`${BASE}/api/executor/v1/claim`, { method: "GET" });
    expect(res.status).toBe(405);
  });
});
