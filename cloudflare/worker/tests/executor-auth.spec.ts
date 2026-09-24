/// <reference types="@cloudflare/vitest-plugin/types" />
import { beforeEach, describe, expect, it } from "vitest";
import { env, SELF } from "cloudflare:test";
import { applyMigrations, DB } from "./helpers";
import { authenticateExecutor } from "../src/executors/executor-auth";

// stage-cloud-08 验收（计划 §39/§40/§82）：
// bootstrap token 注册 / token 仅显示一次 / D1 只存 hash /
// token+executor_id+path 三元校验 / 禁用 DENY / 跨路径伪造 DENY。

const BASE = "https://example.com";
const BOOTSTRAP = "test-bootstrap-token";

interface RegisterBody {
  ok: boolean;
  executor_id: string;
  executor_token: string;
}

async function register(
  body: Record<string, unknown>,
  token: string = BOOTSTRAP,
): Promise<Response> {
  return SELF.fetch(`${BASE}/api/executor/v1/register`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body: JSON.stringify(body),
  });
}

const VALID_EXECUTOR = {
  executor_id: "exec-internal-01",
  execution_path: "internal",
  name: "内网执行器 01",
  version: "1.0.0",
  capabilities: ["xuexitong", "reorder"],
};

beforeEach(async () => {
  await applyMigrations();
  // applyMigrations 只幂等建表不清数据 —— executor 用例间需隔离
  await DB.batch([
    DB.prepare("DELETE FROM executor_nodes"),
    DB.prepare("DELETE FROM audit_events"),
  ]);
});

describe("stage-cloud-08 executor registration", () => {
  it("register PASS: 201, token shown once, D1 stores only SHA-256 hash, audit row", async () => {
    const res = await register(VALID_EXECUTOR);
    expect(res.status).toBe(201);
    const body = (await res.json()) as RegisterBody;
    expect(body.ok).toBe(true);
    expect(body.executor_id).toBe("exec-internal-01");
    expect(body.executor_token.length).toBeGreaterThanOrEqual(32);

    const row = await DB.prepare(
      "SELECT token_hash, execution_path, enabled, status FROM executor_nodes WHERE id='exec-internal-01'",
    ).first<{ token_hash: string; execution_path: string; enabled: number; status: string }>();
    // 明文 token 绝不入库
    expect(row?.token_hash).not.toBe(body.executor_token);
    expect(row?.token_hash.length).toBe(64);
    expect(row?.execution_path).toBe("internal");
    expect(row?.enabled).toBe(1);
    expect(row?.status).toBe("offline");

    const audit = await DB.prepare(
      "SELECT event_type FROM audit_events WHERE event_type='EXECUTOR_REGISTERED' AND entity_id='exec-internal-01'",
    ).first();
    expect(audit).toBeTruthy();
  });

  it("wrong bootstrap token → 403 BOOTSTRAP_INVALID", async () => {
    const res = await register(VALID_EXECUTOR, "wrong-token");
    expect(res.status).toBe(403);
    const body = (await res.json()) as { error: { code: string } };
    expect(body.error.code).toBe("BOOTSTRAP_INVALID");
  });

  it("no bootstrap configured → 503 BOOTSTRAP_DISABLED", async () => {
    const res = await SELF.fetch(`${BASE}/api/executor/v1/register`, {
      method: "POST",
      headers: { Authorization: "Bearer x" },
      body: JSON.stringify(VALID_EXECUTOR),
    });
    // env 绑定已配置；此用例改为直接调用函数层验证未配置分支
    const { registerExecutor } = await import("../src/executors/executor-auth");
    const r = await registerExecutor(
      { DB } as unknown as Parameters<typeof registerExecutor>[0],
      new Request("https://example.com/api/executor/v1/register", { method: "POST" }),
    );
    expect(r.status).toBe(503);
    const b = (await r.json()) as { error: { code: string } };
    expect(b.error.code).toBe("BOOTSTRAP_DISABLED");
    void res;
  });

  it("invalid executor_id / execution_path / capabilities → 400", async () => {
    const cases = [
      { ...VALID_EXECUTOR, executor_id: "bad_id!" },
      { ...VALID_EXECUTOR, execution_path: "orbit" },
      { ...VALID_EXECUTOR, capabilities: "all" },
    ];
    for (const body of cases) {
      const res = await register(body);
      expect(res.status).toBe(400);
      const b = (await res.json()) as { error: { code: string } };
      expect(b.error.code).toBe("VALIDATION_FAILED");
    }
  });

  it("duplicate executor_id → 409 EXECUTOR_EXISTS", async () => {
    await register(VALID_EXECUTOR);
    const res = await register(VALID_EXECUTOR);
    expect(res.status).toBe(409);
    const b = (await res.json()) as { error: { code: string } };
    expect(b.error.code).toBe("EXECUTOR_EXISTS");
  });
});

describe("stage-cloud-08 executor authentication (§40 triple check)", () => {
  async function registered() {
    const res = await register(VALID_EXECUTOR);
    const body = (await res.json()) as RegisterBody;
    return body.executor_token;
  }

  const workerEnv = env as unknown as Parameters<typeof authenticateExecutor>[0];

  function req(token: string): Request {
    return new Request("https://example.com/api/executor/v1/heartbeat", {
      method: "POST",
      headers: { Authorization: `Bearer ${token}` },
    });
  }

  it("valid token + id + path PASS", async () => {
    const token = await registered();
    const out = await authenticateExecutor(workerEnv, req(token), {
      executor_id: "exec-internal-01",
      execution_path: "internal",
    });
    expect("row" in out).toBe(true);
    if (!("row" in out)) throw new Error("expected success");
    expect(out.row.id).toBe("exec-internal-01");
  });

  it("wrong token → 401 EXECUTOR_AUTH_FAILED", async () => {
    await registered();
    const out = await authenticateExecutor(workerEnv, req("forged-token"), {
      executor_id: "exec-internal-01",
      execution_path: "internal",
    });
    expect("code" in out && out.code).toBe("EXECUTOR_AUTH_FAILED");
  });

  it("disabled executor → 403 EXECUTOR_DISABLED", async () => {
    const token = await registered();
    await DB.prepare("UPDATE executor_nodes SET enabled=0 WHERE id='exec-internal-01'").run();
    const out = await authenticateExecutor(workerEnv, req(token), {
      executor_id: "exec-internal-01",
      execution_path: "internal",
    });
    expect("code" in out && out.code).toBe("EXECUTOR_DISABLED");
  });

  it("path forgery DENY: exec-internal-01 claiming external → 403 EXECUTOR_PATH_MISMATCH", async () => {
    const token = await registered();
    const out = await authenticateExecutor(workerEnv, req(token), {
      executor_id: "exec-internal-01",
      execution_path: "external",
    });
    expect("code" in out && out.code).toBe("EXECUTOR_PATH_MISMATCH");
  });
});
