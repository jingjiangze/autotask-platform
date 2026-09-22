/**
 * stage-cloud-09 — PathCoordinator Durable Object（计划 §41/§83）
 *
 * 调度中枢：每个 execution_path（local/internal/external）一个 DO 实例
 * （idFromName(path)），互不串扰。职责：
 *   - 该路径的任务队列（FIFO，能力匹配过滤）
 *   - 租约签发（claim）与续期（heartbeat）
 *   - 租约过期回收（alarm → stale_suspected，计划 §46）
 *   - retry_wait 定时回队（alarm）
 *
 * 状态转移一律复用 stage-cloud-07 的 TaskStateMachine，禁止绕过。
 * DO 内部消息走 fetch("/message")，stage-cloud-10 由 HTTP API 层暴露。
 * D1 tasks 表仍是持久真相：DO 是调度态缓存，后续 stage 同步双写。
 */

import {
  deepScanForbidden,
  buildTaskDispatch,
  type ExecutionPath,
  type TaskStatus,
} from "../types/protocol";
import { transitionTask } from "../tasks/task-state";

export const DEFAULT_LEASE_TTL_MS = 10 * 60 * 1000;
export const DEFAULT_RETRY_DELAY_MS = 60 * 1000;

interface QueueTask {
  task_id: string;
  order_id: string;
  task_type: string;
  required_capabilities: string[];
  payload: Record<string, unknown>;
  trace_id: string;
  status: TaskStatus;
  attempt_no: number;
  lease_id: string | null;
  lease_expires_at: number | null;
  executor_id: string | null;
  retry_at: number | null;
  enqueued_at: number;
  updated_at: number;
}

type CoordMessage =
  | {
      type: "enqueue";
      task_id: string;
      order_id: string;
      task_type: string;
      required_capabilities: string[];
      payload: Record<string, unknown>;
      trace_id?: string;
    }
  | {
      type: "claim";
      executor_id: string;
      execution_path: ExecutionPath;
      capabilities: string[];
    }
  | { type: "ack"; task_id: string; lease_id: string }
  | { type: "heartbeat"; task_id: string; lease_id: string }
  | {
      type: "complete";
      task_id: string;
      lease_id: string;
      outcome: "succeeded" | "failed" | "retry_wait";
      error_code?: string;
    }
  | { type: "requeue"; task_id: string }
  | { type: "verify-lease"; task_id: string; lease_id: string }
  | { type: "stats" };

function fail(code: string, message: string): Response {
  return Response.json({ ok: false, error: { code, message } }, { status: 400 });
}

export class PathCoordinator implements DurableObject {
  private readonly storage: DurableObjectStorage;
  private readonly env: Record<string, string | undefined>;

  constructor(state: DurableObjectState, env: Record<string, string | undefined>) {
    this.storage = state.storage;
    this.env = env;
  }

  private leaseTtl(): number {
    const n = Number(this.env["LEASE_TTL_MS"]);
    return Number.isFinite(n) && n > 0 ? n : DEFAULT_LEASE_TTL_MS;
  }

  private retryDelay(): number {
    const n = Number(this.env["RETRY_DELAY_MS"]);
    return Number.isFinite(n) && n > 0 ? n : DEFAULT_RETRY_DELAY_MS;
  }

  private async getTask(taskId: string): Promise<QueueTask | null> {
    return (await this.storage.get<QueueTask>(`task:${taskId}`)) ?? null;
  }

  private async putTask(t: QueueTask): Promise<void> {
    t.updated_at = Date.now();
    await this.storage.put(`task:${t.task_id}`, t);
  }

  async fetch(request: Request): Promise<Response> {
    const url = new URL(request.url);
    if (url.pathname === "/message" && request.method === "POST") {
      let msg: CoordMessage;
      try {
        msg = (await request.json()) as CoordMessage;
      } catch {
        return fail("VALIDATION_FAILED", "body must be JSON");
      }
      try {
        return await this.handle(msg);
      } catch (e) {
        return fail("COORDINATOR_ERROR", e instanceof Error ? e.message : "unknown");
      }
    }
    return fail("NOT_FOUND", "unknown coordinator route");
  }

  async handle(msg: CoordMessage): Promise<Response> {
    switch (msg.type) {
      case "enqueue":
        return this.enqueue(msg);
      case "claim":
        return this.claim(msg);
      case "ack":
        return this.ack(msg);
      case "heartbeat":
        return this.heartbeat(msg);
      case "complete":
        return this.complete(msg);
      case "requeue":
        return this.requeue(msg.task_id, Date.now());
      case "verify-lease":
        return this.verifyLease(msg.task_id, msg.lease_id);
      case "stats":
        return this.stats();
      default:
        return fail("VALIDATION_FAILED", "unknown message type");
    }
  }

  private async enqueue(
    msg: Extract<CoordMessage, { type: "enqueue" }>,
  ): Promise<Response> {
    if (typeof msg.task_id !== "string" || msg.task_id.length === 0) {
      return fail("VALIDATION_FAILED", "task_id required");
    }
    // 计划 §33：凭据形 payload 在源头拒绝
    const hits = deepScanForbidden(msg.payload, "");
    if (hits.length > 0) {
      return fail("PAYLOAD_FORBIDDEN", `credential-like keys forbidden: ${hits.join(",")}`);
    }
    const existing = await this.getTask(msg.task_id);
    if (existing && !["succeeded", "failed", "canceled"].includes(existing.status)) {
      return fail("TASK_EXISTS", `task ${msg.task_id} already ${existing.status}`);
    }
    const now = Date.now();
    const task: QueueTask = {
      task_id: msg.task_id,
      order_id: msg.order_id,
      task_type: msg.task_type,
      required_capabilities: msg.required_capabilities ?? [],
      payload: msg.payload ?? {},
      trace_id: msg.trace_id ?? crypto.randomUUID(),
      status: "queued",
      attempt_no: existing ? existing.attempt_no : 0,
      lease_id: null,
      lease_expires_at: null,
      executor_id: null,
      retry_at: null,
      enqueued_at: now,
      updated_at: now,
    };
    await this.putTask(task);
    return Response.json({ ok: true, task_id: task.task_id, status: "queued" }, { status: 201 });
  }

  private async claim(
    msg: Extract<CoordMessage, { type: "claim" }>,
  ): Promise<Response> {
    const now = Date.now();
    const caps = new Set(msg.capabilities ?? []);
    const entries = await this.storage.list<QueueTask>({ prefix: "task:" });
    // FIFO：按 enqueued_at 排序；能力不匹配的跳过（不阻塞后面任务）
    const candidates = [...entries.values()].sort((a, b) => a.enqueued_at - b.enqueued_at);
    for (const t of candidates) {
      if (t.status !== "queued") continue;
      if (!t.required_capabilities.every((c) => caps.has(c))) continue;
      const tr = transitionTask(t.status, "leased");
      if (!tr.ok) continue;
      const leaseId = crypto.randomUUID();
      t.status = "leased";
      t.lease_id = leaseId;
      t.lease_expires_at = now + this.leaseTtl();
      t.executor_id = msg.executor_id;
      t.attempt_no += 1;
      await this.putTask(t);
      const dispatch = buildTaskDispatch({
        task_id: t.task_id,
        order_id: t.order_id,
        attempt_id: `${t.task_id}#${t.attempt_no}`,
        attempt_no: t.attempt_no,
        execution_path: msg.execution_path,
        task_type: t.task_type,
        lease_id: leaseId,
        lease_expires_at: t.lease_expires_at,
        issued_at: now,
        trace_id: t.trace_id,
        required_capabilities: t.required_capabilities,
        payload: t.payload,
      });
      await this.scheduleAlarm(now);
      return Response.json({ ok: true, task: dispatch }, { status: 200 });
    }
    return Response.json({ ok: true, task: null }, { status: 200 });
  }

  private async ack(msg: Extract<CoordMessage, { type: "ack" }>): Promise<Response> {
    const t = await this.getTask(msg.task_id);
    if (!t || t.lease_id !== msg.lease_id) {
      return fail("LEASE_INVALID", "unknown task or lease mismatch");
    }
    const tr = transitionTask(t.status, "running");
    if (!tr.ok) return fail("INVALID_TRANSITION", tr.reason ?? "");
    t.status = "running";
    await this.putTask(t);
    return Response.json({ ok: true, status: "running" });
  }

  private async heartbeat(msg: Extract<CoordMessage, { type: "heartbeat" }>): Promise<Response> {
    const t = await this.getTask(msg.task_id);
    if (!t || t.lease_id !== msg.lease_id) {
      return fail("LEASE_INVALID", "unknown task or lease mismatch");
    }
    if (!["leased", "running"].includes(t.status)) {
      return fail("INVALID_TRANSITION", `cannot heartbeat in ${t.status}`);
    }
    const before = t.lease_expires_at ?? 0;
    t.lease_expires_at = Date.now() + this.leaseTtl();
    await this.putTask(t);
    return Response.json({
      ok: true,
      lease_expires_at: t.lease_expires_at,
      extended_ms: t.lease_expires_at - before,
    });
  }

  private async complete(
    msg: Extract<CoordMessage, { type: "complete" }>,
  ): Promise<Response> {
    const t = await this.getTask(msg.task_id);
    if (!t || t.lease_id !== msg.lease_id) {
      return fail("LEASE_INVALID", "unknown task or lease mismatch");
    }
    const tr = transitionTask(t.status, msg.outcome);
    if (!tr.ok) return fail("INVALID_TRANSITION", tr.reason ?? "");
    t.status = msg.outcome;
    if (msg.outcome === "retry_wait") {
      // 计划 §46/§52：延迟后回队；重试计数已随 claim 递增
      t.retry_at = Date.now() + this.retryDelay();
      await this.putTask(t);
      await this.scheduleAlarm(t.retry_at);
    } else {
      t.lease_id = null;
      t.lease_expires_at = null;
      await this.putTask(t);
    }
    return Response.json({ ok: true, status: t.status });
  }

  private async requeue(taskId: string, now: number): Promise<Response> {
    const t = await this.getTask(taskId);
    if (!t) return fail("TASK_NOT_FOUND", "unknown task");
    // stale → retry_wait → queued 两跳（§46 恢复路径）；其余状态直接跳 queued
    const first =
      t.status === "stale_suspected"
        ? transitionTask(t.status, "retry_wait")
        : transitionTask(t.status, "queued");
    if (!first.ok) return fail("INVALID_TRANSITION", first.reason ?? "");
    if (t.status === "stale_suspected") {
      const second = transitionTask("retry_wait", "queued");
      if (!second.ok) return fail("INVALID_TRANSITION", second.reason ?? "");
    }
    t.status = "queued";
    t.lease_id = null;
    t.lease_expires_at = null;
    t.executor_id = null;
    t.retry_at = null;
    t.enqueued_at = now; // 重新排队到队尾
    await this.putTask(t);
    return Response.json({ ok: true, status: t.status });
  }

  /** 计划 §46：租约过期 → stale_suspected；retry_wait 到期 → 回队。 */
  async alarm(): Promise<void> {
    const now = Date.now();
    const entries = await this.storage.list<QueueTask>({ prefix: "task:" });
    let nextWake: number | null = null;
    for (const t of entries.values()) {
      if (
        ["leased", "running"].includes(t.status) &&
        t.lease_expires_at !== null &&
        t.lease_expires_at <= now
      ) {
        const tr = transitionTask(t.status, "stale_suspected");
        if (tr.ok) {
          t.status = "stale_suspected";
          await this.putTask(t);
        }
      } else if (t.status === "retry_wait" && t.retry_at !== null && t.retry_at <= now) {
        await this.requeue(t.task_id, now);
      }
      if (
        t.status === "retry_wait" &&
        t.retry_at !== null &&
        (nextWake === null || t.retry_at < nextWake)
      ) {
        nextWake = t.retry_at;
      }
    }
    if (nextWake !== null) await this.storage.setAlarm(nextWake);
  }

  private async scheduleAlarm(earliest: number): Promise<void> {
    const current = await this.storage.getAlarm();
    if (current === null || current > earliest) {
      await this.storage.setAlarm(earliest);
    }
  }

  /** stage-cloud-11：凭据解封前置校验 —— 租约存在、匹配、未过期且处于活跃态。 */
  private async verifyLease(taskId: string, leaseId: string): Promise<Response> {
    const t = await this.getTask(taskId);
    if (!t || t.lease_id !== leaseId) {
      return Response.json({ ok: true, valid: false, reason: "LEASE_MISMATCH" });
    }
    if (!["leased", "running"].includes(t.status)) {
      return Response.json({ ok: true, valid: false, reason: `STATUS_${t.status}` });
    }
    if (t.lease_expires_at !== null && t.lease_expires_at <= Date.now()) {
      return Response.json({ ok: true, valid: false, reason: "LEASE_EXPIRED" });
    }
    return Response.json({
      ok: true,
      valid: true,
      order_id: t.order_id,
      lease_expires_at: t.lease_expires_at,
    });
  }

  private async stats(): Promise<Response> {
    const entries = await this.storage.list<QueueTask>({ prefix: "task:" });
    const byStatus: Record<string, number> = {};
    for (const t of entries.values()) {
      byStatus[t.status] = (byStatus[t.status] ?? 0) + 1;
    }
    return Response.json({ ok: true, by_status: byStatus, total: entries.size });
  }
}
