/// <reference types="@cloudflare/vitest-plugin/types" />
import { beforeEach, describe, expect, it } from "vitest";
import { env, runInDurableObject } from "cloudflare:test";
import { applyMigrations } from "./helpers";
import { PathCoordinator } from "../src/coordinator/path-coordinator";

// stage-cloud-15 验收（计划 §15/§44/§52/§53）：
// 重试预算（max_attempts 耗尽 → failed 不再回队）/
// complete 幂等（重复上报不重复计）/ fencing（旧租约在新租约后 DENY）。

const BASE = "https://example.com/message";
const CLAIM = {
  type: "claim",
  executor_id: "exec-internal-15",
  execution_path: "internal",
  capabilities: ["xuexitong"],
} as const;

type Stub = DurableObjectStub;

function coord(path: string): Stub {
  const ns = (env as unknown as { COORDINATOR: DurableObjectNamespace }).COORDINATOR;
  return ns.get(ns.idFromName(path));
}

async function msg(stub: Stub, body: Record<string, unknown>): Promise<{
  status: number;
  body: Record<string, unknown>;
}> {
  const res = await stub.fetch(BASE, { method: "POST", body: JSON.stringify(body) });
  return { status: res.status, body: (await res.json()) as Record<string, unknown> };
}

async function enqueue(stub: Stub, taskId: string, extra: Record<string, unknown> = {}) {
  const r = await msg(stub, {
    type: "enqueue",
    task_id: taskId,
    order_id: `ord-${taskId}`,
    task_type: "xuexitong.chapter",
    required_capabilities: ["xuexitong"],
    payload: { chapter_id: 1 },
    ...extra,
  });
  expect(r.status).toBe(201);
}

async function claim(stub: Stub): Promise<Record<string, unknown>> {
  const r = await msg(stub, CLAIM);
  const t = r.body["task"] as Record<string, unknown> | null;
  expect(t).not.toBeNull();
  return t!;
}

beforeEach(async () => {
  await applyMigrations();
  await runInDurableObject(coord("internal"), async (_i, state) => {
    await state.storage.deleteAll();
    await state.storage.deleteAlarm();
  });
});

describe("stage-cloud-15 retry budget / idempotency / fencing", () => {
  it("retry budget exhausted → failed, no further requeue", async () => {
    const stub = coord("internal");
    await enqueue(stub, "t-budget", { max_attempts: 2 });

    // 尝试 1
    const d1 = await claim(stub);
    await msg(stub, { type: "ack", task_id: "t-budget", lease_id: d1["lease_id"] });
    const w1 = await msg(stub, {
      type: "complete",
      task_id: "t-budget",
      lease_id: d1["lease_id"],
      outcome: "retry_wait",
      error_code: "NETWORK_TIMEOUT",
    });
    expect(w1.body["status"]).toBe("retry_wait");

    // alarm 立即回队（拨 retry_at 到过去）
    await runInDurableObject(stub, async (i: PathCoordinator, state: DurableObjectState) => {
      const t = (await state.storage.get<Record<string, unknown>>("task:t-budget"))!;
      t["retry_at"] = Date.now() - 1000;
      await state.storage.put("task:t-budget", t);
      await i.alarm();
    });

    // 尝试 2（预算内最后一次）
    const d2 = await claim(stub);
    expect(d2["attempt_no"]).toBe(2);
    await msg(stub, { type: "ack", task_id: "t-budget", lease_id: d2["lease_id"] });
    const w2 = await msg(stub, {
      type: "complete",
      task_id: "t-budget",
      lease_id: d2["lease_id"],
      outcome: "retry_wait",
      error_code: "NETWORK_TIMEOUT",
    });
    expect(w2.body).toMatchObject({ status: "failed", reason: "RETRY_BUDGET_EXHAUSTED" });

    // 终态：不可再 claim，也不可 requeue
    const s = await msg(stub, { type: "stats" });
    expect((s.body["by_status"] as Record<string, number>)["failed"]).toBe(1);
    const rq = await msg(stub, { type: "requeue", task_id: "t-budget" });
    expect(rq.status).toBe(400);
  });

  it("default budget = 2 when max_attempts omitted", async () => {
    const stub = coord("internal");
    await enqueue(stub, "t-default");
    const d1 = await claim(stub);
    await msg(stub, { type: "ack", task_id: "t-default", lease_id: d1["lease_id"] });
    await msg(stub, {
      type: "complete",
      task_id: "t-default",
      lease_id: d1["lease_id"],
      outcome: "retry_wait",
    });
    await runInDurableObject(stub, async (i: PathCoordinator, state: DurableObjectState) => {
      const t = (await state.storage.get<Record<string, unknown>>("task:t-default"))!;
      t["retry_at"] = Date.now() - 1000;
      await state.storage.put("task:t-default", t);
      await i.alarm();
    });
    const d2 = await claim(stub);
    await msg(stub, { type: "ack", task_id: "t-default", lease_id: d2["lease_id"] });
    const w2 = await msg(stub, {
      type: "complete",
      task_id: "t-default",
      lease_id: d2["lease_id"],
      outcome: "retry_wait",
    });
    expect(w2.body["reason"]).toBe("RETRY_BUDGET_EXHAUSTED");
  });

  it("duplicate complete after terminal → idempotent ok, status unchanged", async () => {
    const stub = coord("internal");
    await enqueue(stub, "t-idem");
    const d = await claim(stub);
    await msg(stub, { type: "ack", task_id: "t-idem", lease_id: d["lease_id"] });
    const first = await msg(stub, {
      type: "complete",
      task_id: "t-idem",
      lease_id: d["lease_id"],
      outcome: "succeeded",
    });
    expect(first.body).toMatchObject({ ok: true, status: "succeeded" });

    const dup = await msg(stub, {
      type: "complete",
      task_id: "t-idem",
      lease_id: d["lease_id"],
      outcome: "succeeded",
    });
    expect(dup.status).toBe(200);
    expect(dup.body).toMatchObject({ ok: true, status: "succeeded", idempotent: true });

    const s = await msg(stub, { type: "stats" });
    expect((s.body["by_status"] as Record<string, number>)["succeeded"]).toBe(1);
  });

  it("fencing: stale old lease DENIED after reissue", async () => {
    const stub = coord("internal");
    await enqueue(stub, "t-fence");
    const d1 = await claim(stub);
    const oldLease = d1["lease_id"] as string;

    // 模拟原租约过期被回收并重新签发
    await runInDurableObject(stub, async (i: PathCoordinator, state: DurableObjectState) => {
      const t = (await state.storage.get<Record<string, unknown>>("task:t-fence"))!;
      t["lease_expires_at"] = Date.now() - 1000;
      await state.storage.put("task:t-fence", t);
      await i.alarm();
    });
    await msg(stub, { type: "requeue", task_id: "t-fence" });
    const d2 = await claim(stub);
    expect(d2["lease_id"]).not.toBe(oldLease);

    // 旧租约的 ack / heartbeat / complete 全部被 fence 拒绝
    for (const type of ["ack", "heartbeat", "complete"] as const) {
      const r = await msg(stub, {
        type,
        task_id: "t-fence",
        lease_id: oldLease,
        ...(type === "complete" ? { outcome: "succeeded" } : {}),
      });
      expect(r.body["error"]).toMatchObject({ code: "LEASE_INVALID" });
    }
    // 新租约正常
    const ok = await msg(stub, {
      type: "ack",
      task_id: "t-fence",
      lease_id: d2["lease_id"],
    });
    expect(ok.body).toMatchObject({ ok: true, status: "running" });
  });
});
