/**
 * stage-cloud-10 — Executor Pull API（计划 §42/§43/§84）
 *
 * /api/executor/v1/claim|ack|heartbeat|complete：
 *   1. body 中的 executor_id + execution_path 三元认证（stage-cloud-08）
 *   2. 按 execution_path 路由到对应 PathCoordinator DO 实例（stage-cloud-09）
 *   3. 转发内部消息；claim 空 → 200 {task:null}（轮询间隔由 Executor 控制）
 *
 * 凭据红线（§33）：dispatch payload 不含凭据；凭据经 stage-cloud-11
 * 的 lease 门控解封端点单独获取。
 */

import type { Env } from "../auth/auth-service";
import { authenticateExecutor } from "./executor-auth";
import { errorResponse } from "../errors";

async function readBody(request: Request): Promise<Record<string, unknown> | null> {
  try {
    return (await request.json()) as Record<string, unknown>;
  } catch {
    return null;
  }
}

async function coordinator(
  env: Env,
  executionPath: string,
  message: Record<string, unknown>,
): Promise<Response> {
  const ns = env.COORDINATOR;
  const stub = ns.get(ns.idFromName(executionPath));
  return stub.fetch("https://do/message", {
    method: "POST",
    body: JSON.stringify(message),
  });
}

/** DO 是调度态、D1 是持久真相：ack/complete 转发成功后镜像写 D1（stage-cloud-16b）。 */
async function mirrorTaskState(
  env: Env,
  taskId: string,
  leaseId: string,
  executorId: string,
  status: string,
  errorCode?: string,
): Promise<void> {
  const now = Date.now();
  const task = await env.DB.prepare("SELECT attempt_no, order_id FROM tasks WHERE id=?")
    .bind(taskId)
    .first<{ attempt_no: number; order_id: string }>();
  if (!task) return; // 非 D1 登记的任务（纯 DO 测试）无需镜像
  const attemptId = `${taskId}#${task.attempt_no}`;
  if (status === "running") {
    await env.DB.batch([
      env.DB.prepare(
        "UPDATE tasks SET status='running', executor_id=?, lease_id=?, started_at=COALESCE(started_at,?), last_heartbeat_at=?, updated_at=? WHERE id=?",
      ).bind(executorId, leaseId, now, now, now, taskId),
      env.DB.prepare(
        `INSERT INTO task_attempts(id,task_id,attempt_no,executor_id,lease_id,status,started_at,last_heartbeat_at,created_at,updated_at)
         VALUES(?,?,?,?,?,'running',?,?,?,?)
         ON CONFLICT(id) DO UPDATE SET status='running', last_heartbeat_at=excluded.last_heartbeat_at, updated_at=excluded.updated_at`,
      ).bind(attemptId, taskId, task.attempt_no, executorId, leaseId, now, now, now, now),
    ]);
  } else if (["succeeded", "failed", "retry_wait"].includes(status)) {
    const finished = ["succeeded", "failed"].includes(status);
    await env.DB.batch([
      env.DB.prepare(
        "UPDATE tasks SET status=?, error_code=COALESCE(?,error_code), finished_at=COALESCE(finished_at,?), updated_at=? WHERE id=?",
      ).bind(status, errorCode ?? null, finished ? now : null, now, taskId),
      env.DB.prepare(
        `UPDATE task_attempts SET status=?, finished_at=COALESCE(finished_at,?), error_code=COALESCE(?,error_code), updated_at=?
         WHERE task_id=? AND attempt_no=(SELECT attempt_no FROM tasks WHERE id=?) AND status IN ('running','leased')`,
      ).bind(status === "retry_wait" ? "failed" : status, finished ? now : null, errorCode ?? null, now, taskId, taskId),
      ...(finished
        ? [
            env.DB.prepare("UPDATE orders SET status=?, updated_at=? WHERE id=?").bind(
              status,
              now,
              task.order_id,
            ),
          ]
        : []),
    ]);
  }
}

/** 认证 + 转发的公共骨架；认证失败返回 Response，成功返回 null。 */
async function guard(
  env: Env,
  request: Request,
  body: Record<string, unknown>,
): Promise<Response | null> {
  const executorId = String(body["executor_id"] ?? "");
  const executionPath = String(body["execution_path"] ?? "");
  if (!executorId || !executionPath) {
    return errorResponse(400, "VALIDATION_FAILED");
  }
  const auth = await authenticateExecutor(env, request, {
    executor_id: executorId,
    execution_path: executionPath,
  });
  if ("code" in auth) {
    return errorResponse(auth.status, auth.code);
  }
  return null;
}

export async function executorClaim(env: Env, request: Request): Promise<Response> {
  const body = await readBody(request);
  if (!body) return errorResponse(400, "VALIDATION_FAILED");
  const denied = await guard(env, request, body);
  if (denied) return denied;
  const executorId = String(body["executor_id"]);
  const path = String(body["execution_path"]);
  const res = await coordinator(env, path, {
    type: "claim",
    executor_id: executorId,
    execution_path: path,
    capabilities: Array.isArray(body["capabilities"]) ? body["capabilities"] : [],
  });
  // D1 镜像：leased + 租约字段 + attempt_no（DO 调度态 → D1 持久真相）
  if (res.status === 200) {
    try {
      const cloned = res.clone();
      const out = (await cloned.json()) as { task: Record<string, unknown> | null };
      const t = out.task;
      if (t) {
        await env.DB.prepare(
          "UPDATE tasks SET status='leased', executor_id=?, lease_id=?, lease_expires_at=?, attempt_no=?, updated_at=? WHERE id=? AND status IN ('queued','retry_wait')",
        )
          .bind(
            executorId,
            t["lease_id"],
            t["lease_expires_at"],
            t["attempt_no"],
            Date.now(),
            t["task_id"],
          )
          .run();
      }
    } catch {
      /* 镜像失败不影响领取响应 */
    }
  }
  return res;
}

export async function executorAck(env: Env, request: Request): Promise<Response> {
  const body = await readBody(request);
  if (!body) return errorResponse(400, "VALIDATION_FAILED");
  const denied = await guard(env, request, body);
  if (denied) return denied;
  if (typeof body["task_id"] !== "string" || typeof body["lease_id"] !== "string") {
    return errorResponse(400, "VALIDATION_FAILED");
  }
  const executorId = String(body["executor_id"]);
  const res = await coordinator(env, String(body["execution_path"]), {
    type: "ack",
    task_id: body["task_id"],
    lease_id: body["lease_id"],
  });
  if (res.status === 200) {
    await mirrorTaskState(env, String(body["task_id"]), String(body["lease_id"]), executorId, "running");
  }
  return res;
}

export async function executorHeartbeat(env: Env, request: Request): Promise<Response> {
  const body = await readBody(request);
  if (!body) return errorResponse(400, "VALIDATION_FAILED");
  const denied = await guard(env, request, body);
  if (denied) return denied;
  if (typeof body["task_id"] !== "string" || typeof body["lease_id"] !== "string") {
    return errorResponse(400, "VALIDATION_FAILED");
  }
  return coordinator(env, String(body["execution_path"]), {
    type: "heartbeat",
    task_id: body["task_id"],
    lease_id: body["lease_id"],
  });
}

export async function executorComplete(env: Env, request: Request): Promise<Response> {
  const body = await readBody(request);
  if (!body) return errorResponse(400, "VALIDATION_FAILED");
  const denied = await guard(env, request, body);
  if (denied) return denied;
  const outcome = body["outcome"];
  if (
    typeof body["task_id"] !== "string" ||
    typeof body["lease_id"] !== "string" ||
    (outcome !== "succeeded" && outcome !== "failed" && outcome !== "retry_wait")
  ) {
    return errorResponse(400, "VALIDATION_FAILED");
  }
  const executorId2 = String(body["executor_id"]);
  const res = await coordinator(env, String(body["execution_path"]), {
    type: "complete",
    task_id: body["task_id"],
    lease_id: body["lease_id"],
    outcome,
    error_code: typeof body["error_code"] === "string" ? body["error_code"] : undefined,
  });
  if (res.status === 200) {
    try {
      const out = (await res.clone().json()) as { status?: string };
      if (out.status) {
        await mirrorTaskState(
          env,
          String(body["task_id"]),
          String(body["lease_id"]),
          executorId2,
          out.status,
          typeof body["error_code"] === "string" ? body["error_code"] : undefined,
        );
      }
    } catch {
      /* 镜像失败不影响 complete 响应 */
    }
  }
  return res;
}
