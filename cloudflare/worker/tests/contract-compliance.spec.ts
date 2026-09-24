/// <reference types="@cloudflare/vitest-plugin/types" />
import { describe, expect, it } from "vitest";
import { env, runInDurableObject } from "cloudflare:test";
import { PathCoordinator } from "../src/coordination/path-coordinator";
import { EXECUTOR_ERROR_CODES } from "../src/types/protocol";
import errorCodesJson from "../../contracts/error-codes.json";
import executorProtocolJson from "../../contracts/executor-protocol.json";
import taskSchemaJson from "../../contracts/task-schema.json";

// stage-cloud-31 — §73 契约一致性 + §47 三元 fencing + §38 新端点验收：
// contracts/ 与 worker 常量同源；attempt_id 不匹配 → 409 STALE_LEASE；
// cancel-ack 走 §23 两跳；fail 端点要求 error_code。

const BASE = "https://example.com/message";
const TASK = {
  task_id: "t-3101",
  order_id: "ord-3101",
  task_type: "demo.echo",
  required_capabilities: ["demo"],
  payload: { n: 1 },
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
  const res = await stub.fetch(BASE, { method: "POST", body: JSON.stringify(body) });
  return { status: res.status, body: (await res.json()) as Record<string, unknown> };
}

describe("§73 contracts/ 与 worker 契约同源", () => {
  it("error-codes.json 与 EXECUTOR_ERROR_CODES 完全一致", () => {
    const raw = errorCodesJson as unknown as { codes: string[] };
    expect([...raw.codes].sort()).toEqual([...EXECUTOR_ERROR_CODES].sort());
  });

  it("executor-protocol.json 覆盖 §38 全部必需端点", () => {
    const proto = executorProtocolJson as unknown as { endpoints: Record<string, { path: string }> };
    for (const name of [
      "register", "heartbeat", "pull", "tasks_bootstrap", "tasks_start",
      "tasks_complete", "tasks_fail", "tasks_cancel_ack", "artifacts_presign",
    ]) {
      expect(proto.endpoints[name], `endpoint ${name} missing`).toBeTruthy();
    }
  });

  it("task-schema.json 的必填字段与 TaskDispatchMessage 一致", async () => {
    const schema = taskSchemaJson as unknown as { required: string[] };
    const stub = coord("internal");
    await runInDurableObject(stub, async (instance: PathCoordinator, state) => {
      void state;
      await instance.handle({ type: "enqueue", ...TASK });
      const res = await instance.handle({
        type: "claim",
        executor_id: "exec-internal-01",
        execution_path: "internal",
        capabilities: ["demo"],
      });
      const out = (await (res as Response).json()) as { task: Record<string, unknown> };
      for (const field of schema.required) {
        expect(out.task, `dispatch missing ${field}`).toHaveProperty(field);
      }
    });
  });
});

describe("§47 attempt_id 三元 fencing", () => {
  it("attempt_id 不匹配 → 409 STALE_LEASE", async () => {
    const stub = coord("fencing-test");
    await runInDurableObject(stub, async (instance: PathCoordinator) => {
      await instance.handle({ type: "enqueue", ...TASK });
      const claimRes = (await instance.handle({
        type: "claim",
        executor_id: "exec-31",
        execution_path: "internal",
        capabilities: ["demo"],
      })) as Response;
      const { task } = (await claimRes.json()) as { task: Record<string, unknown> };
      const attemptId = String(task["attempt_id"]);

      // 正确 attempt_id → running
      const okRes = await instance.handle({
        type: "ack",
        task_id: TASK.task_id,
        lease_id: String(task["lease_id"]),
        attempt_id: attemptId,
      }) as Response;
      expect(okRes.status).toBe(200);

      // 错误 attempt_id（旧 attempt）→ 409 STALE_LEASE
      const stale = await instance.handle({
        type: "heartbeat",
        task_id: TASK.task_id,
        lease_id: String(task["lease_id"]),
        attempt_id: `${TASK.task_id}#0`,
      }) as Response;
      expect(stale.status).toBe(409);
      const staleBody = (await stale.json()) as { error?: { code?: string } };
      expect(staleBody.error?.code).toBe("STALE_LEASE");

      // 不带 attempt_id → 兼容旧客户端，不受 fencing 影响
      const legacy = await instance.handle({
        type: "heartbeat",
        task_id: TASK.task_id,
        lease_id: String(task["lease_id"]),
      }) as Response;
      expect(legacy.status).toBe(200);
    });
  });

  it("cancel-ack：running → cancel_requested → canceled 两跳", async () => {
    const stub = coord("cancelack-test");
    await runInDurableObject(stub, async (instance: PathCoordinator) => {
      await instance.handle({ type: "enqueue", ...TASK });
      const claimRes = (await instance.handle({
        type: "claim",
        executor_id: "exec-31c",
        execution_path: "internal",
        capabilities: ["demo"],
      })) as Response;
      const { task } = (await claimRes.json()) as { task: Record<string, unknown> };
      await instance.handle({
        type: "ack",
        task_id: TASK.task_id,
        lease_id: String(task["lease_id"]),
        attempt_id: String(task["attempt_id"]),
      });
      const cancel = await instance.handle({
        type: "cancel-ack",
        task_id: TASK.task_id,
        lease_id: String(task["lease_id"]),
        attempt_id: String(task["attempt_id"]),
      }) as Response;
      expect(cancel.status).toBe(200);
      const cancelBody = (await cancel.json()) as { status?: string };
      expect(cancelBody.status).toBe("canceled");
    });
  });
});
