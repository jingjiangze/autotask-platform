/// <reference types="@cloudflare/vitest-plugin/types" />
import { beforeEach, describe, expect, it } from "vitest";
import { env, runInDurableObject } from "cloudflare:test";
import { applyMigrations } from "./helpers";
import { validateTaskDispatch } from "../src/types/protocol";
import { PathCoordinator } from "../src/coordination/path-coordinator";

// stage-cloud-09 验收（计划 §41/§83）：
// enqueue 凭据红线 / FIFO+能力匹配 claim / dispatch 契约校验零违规 /
// 租约 ack+heartbeat / 过期 alarm → stale_suspected / retry_wait 定时回队。

const BASE = "https://example.com/message";
const TASK = {
  task_id: "t-0001",
  order_id: "ord-1001",
  task_type: "xuexitong.chapter",
  required_capabilities: ["xuexitong"],
  payload: { chapter_id: 42, mode: "fast" },
};

type Stub = DurableObjectStub;

function coord(path: string): Stub {
  const ns = (env as unknown as { COORDINATOR: DurableObjectNamespace }).COORDINATOR;
  return ns.get(ns.idFromName(path));
}

async function msg(stub: Stub, body: Record<string, unknown>): Promise<{
  status: number;
  body: Record<string, unknown>;
}> {
  const res = await stub.fetch(BASE, {
    method: "POST",
    body: JSON.stringify(body),
  });
  return { status: res.status, body: (await res.json()) as Record<string, unknown> };
}

const CLAIM = {
  type: "claim",
  executor_id: "exec-internal-01",
  execution_path: "internal",
  capabilities: ["xuexitong"],
} as const;

beforeEach(async () => {
  await applyMigrations(); // 保持与其他 spec 相同的初始化节奏
  // DO storage 与 D1 独立，跨用例必须显式清理（同一 idFromName 实例复用）
  for (const path of ["internal", "external"]) {
    await runInDurableObject(coord(path), async (_i, state) => {
      await state.storage.deleteAll();
      await state.storage.deleteAlarm();
    });
  }
});

describe("stage-cloud-09 enqueue", () => {
  it("enqueue PASS → queued, 201", async () => {
    const r = await msg(coord("internal"), { type: "enqueue", ...TASK });
    expect(r.status).toBe(201);
    expect(r.body["status"]).toBe("queued");

    const s = await msg(coord("internal"), { type: "stats" });
    expect((s.body["by_status"] as Record<string, number>)["queued"]).toBe(1);
  });

  it("payload with credential-like key → PAYLOAD_FORBIDDEN (§33 at source)", async () => {
    const r = await msg(coord("internal"), {
      type: "enqueue",
      ...TASK,
      payload: { account: "a", account_password: "x" },
    });
    expect(r.status).toBe(400);
    expect(r.body["error"]).toMatchObject({ code: "PAYLOAD_FORBIDDEN" });
  });

  it("duplicate non-terminal enqueue → TASK_EXISTS; re-enqueue after terminal ok", async () => {
    const stub = coord("internal");
    await msg(stub, { type: "enqueue", ...TASK });
    const dup = await msg(stub, { type: "enqueue", ...TASK });
    expect(dup.status).toBe(400);
    expect(dup.body["error"]).toMatchObject({ code: "TASK_EXISTS" });
  });

  it("path isolation: same task_id enqueued on different path DOs is independent", async () => {
    await msg(coord("internal"), { type: "enqueue", ...TASK });
    const r = await msg(coord("external"), { type: "enqueue", ...TASK });
    expect(r.status).toBe(201);
  });
});

describe("stage-cloud-09 claim + lease lifecycle", () => {
  it("claim returns contract-valid dispatch (validateTaskDispatch clean)", async () => {
    const stub = coord("internal");
    await msg(stub, { type: "enqueue", ...TASK });
    const r = await msg(stub, CLAIM);
    expect(r.status).toBe(200);
    const dispatch = r.body["task"] as Record<string, unknown>;
    expect(dispatch).not.toBeNull();
    const violations = validateTaskDispatch(dispatch);
    expect(violations).toEqual([]);
    expect(dispatch["attempt_no"]).toBe(1);
    expect(dispatch["executor_id"]).toBeUndefined(); // 协议字段不含 executor_id
  });

  it("claim on empty / capability-mismatched queue → task null", async () => {
    const stub = coord("internal");
    const empty = await msg(stub, CLAIM);
    expect(empty.body["task"]).toBeNull();

    await msg(stub, {
      type: "enqueue",
      ...TASK,
      required_capabilities: ["special-cap"],
    });
    const mismatch = await msg(stub, CLAIM);
    expect(mismatch.body["task"]).toBeNull();
  });

  it("capability-mismatched head does not block later matching task (FIFO skip)", async () => {
    const stub = coord("internal");
    // 队头能力不匹配，第二任务匹配 —— claim 应拿到第二个（不阻塞）
    await msg(stub, {
      type: "enqueue",
      ...TASK,
      required_capabilities: ["other-cap"],
    });
    await msg(stub, { type: "enqueue", ...TASK, task_id: "t-0002" });
    const r = await msg(stub, CLAIM);
    expect((r.body["task"] as Record<string, unknown>)["task_id"]).toBe("t-0002");
  });

  it("ack leased→running; wrong lease_id → LEASE_INVALID", async () => {
    const stub = coord("internal");
    await msg(stub, { type: "enqueue", ...TASK });
    const c = await msg(stub, CLAIM);
    const dispatch = c.body["task"] as Record<string, unknown>;

    const bad = await msg(stub, {
      type: "ack",
      task_id: TASK.task_id,
      lease_id: "forged-lease",
    });
    expect(bad.body["error"]).toMatchObject({ code: "LEASE_INVALID" });

    const ok = await msg(stub, {
      type: "ack",
      task_id: TASK.task_id,
      lease_id: dispatch["lease_id"],
    });
    expect(ok.body).toMatchObject({ ok: true, status: "running" });
  });

  it("heartbeat extends lease_expires_at", async () => {
    const stub = coord("internal");
    await msg(stub, { type: "enqueue", ...TASK });
    const c = await msg(stub, CLAIM);
    const d = c.body["task"] as Record<string, unknown>;
    const hb = await msg(stub, {
      type: "heartbeat",
      task_id: TASK.task_id,
      lease_id: d["lease_id"],
    });
    expect(hb.body["ok"]).toBe(true);
    expect(Number(hb.body["lease_expires_at"])).toBeGreaterThan(
      Number(d["lease_expires_at"]) - 1000,
    );
  });

  it("complete succeeded → terminal", async () => {
    const stub = coord("internal");
    await msg(stub, { type: "enqueue", ...TASK });
    const c = await msg(stub, CLAIM);
    const d = c.body["task"] as Record<string, unknown>;
    await msg(stub, {
      type: "ack",
      task_id: TASK.task_id,
      lease_id: d["lease_id"],
    });
    const done = await msg(stub, {
      type: "complete",
      task_id: TASK.task_id,
      lease_id: d["lease_id"],
      outcome: "succeeded",
    });
    expect(done.body).toMatchObject({ ok: true, status: "succeeded" });
    const s = await msg(stub, { type: "stats" });
    expect((s.body["by_status"] as Record<string, number>)["succeeded"]).toBe(1);
  });
});

describe("stage-cloud-09 alarm recovery (§46)", () => {
  it("expired lease → alarm → stale_suspected → requeue → claimable again", async () => {
    const stub = coord("internal");
    await msg(stub, { type: "enqueue", ...TASK });
    const c = await msg(stub, CLAIM);
    expect(c.body["task"]).not.toBeNull();

    await runInDurableObject(
      stub,
      async (instance: PathCoordinator, state: DurableObjectState) => {
        // 把租约拨到过去，触发 §46 stale 检测
        const t = (await state.storage.get<Record<string, unknown>>("task:t-0001"))!;
        t["lease_expires_at"] = Date.now() - 1000;
        await state.storage.put("task:t-0001", t);
        await instance.alarm();
      },
    );

    const s = await msg(stub, { type: "stats" });
    expect((s.body["by_status"] as Record<string, number>)["stale_suspected"]).toBe(1);

    const rq = await msg(stub, { type: "requeue", task_id: TASK.task_id });
    expect(rq.body).toMatchObject({ ok: true, status: "queued" });

    const c2 = await msg(stub, CLAIM);
    const d2 = c2.body["task"] as Record<string, unknown>;
    expect(d2).not.toBeNull();
    expect(d2["attempt_no"]).toBe(2); // 第二次尝试
    expect(d2["lease_id"]).not.toBe((c.body["task"] as Record<string, unknown>)["lease_id"]);
  });

  it("retry_wait → alarm after retry_at → back to queued, attempt preserved", async () => {
    const stub = coord("internal");
    await msg(stub, { type: "enqueue", ...TASK });
    const c = await msg(stub, CLAIM);
    const d = c.body["task"] as Record<string, unknown>;
    await msg(stub, { type: "ack", task_id: TASK.task_id, lease_id: d["lease_id"] });
    const w = await msg(stub, {
      type: "complete",
      task_id: TASK.task_id,
      lease_id: d["lease_id"],
      outcome: "retry_wait",
      error_code: "NETWORK_TIMEOUT",
    });
    expect(w.body).toMatchObject({ ok: true, status: "retry_wait" });

    await runInDurableObject(
      stub,
      async (instance: PathCoordinator, state: DurableObjectState) => {
        const t = (await state.storage.get<Record<string, unknown>>("task:t-0001"))!;
        t["retry_at"] = Date.now() - 1000;
        await state.storage.put("task:t-0001", t);
        await instance.alarm();
      },
    );

    const s = await msg(stub, { type: "stats" });
    expect((s.body["by_status"] as Record<string, number>)["queued"]).toBe(1);

    const c2 = await msg(stub, CLAIM);
    expect((c2.body["task"] as Record<string, unknown>)["attempt_no"]).toBe(2);
  });
});
