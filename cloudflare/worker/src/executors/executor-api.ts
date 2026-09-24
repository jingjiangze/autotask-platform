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
import { releaseCredentials } from "../storage/credentials";
import { mirrorTaskState } from "./task-dispatch";

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
    attempt_id: typeof body["attempt_id"] === "string" ? body["attempt_id"] : undefined,
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
  const res = await coordinator(env, String(body["execution_path"]), {
    type: "heartbeat",
    task_id: body["task_id"],
    lease_id: body["lease_id"],
    attempt_id: typeof body["attempt_id"] === "string" ? body["attempt_id"] : undefined,
  });
  // stage-cloud-28：控制信令随心跳下发 —— Executor 据此挂起/恢复引擎子进程
  if (res.status === 200) {
    try {
      const out = (await res.clone().json()) as Record<string, unknown>;
      const ctrl = await env.DB.prepare("SELECT control FROM orders WHERE id=(SELECT order_id FROM tasks WHERE id=?)")
        .bind(String(body["task_id"]))
        .first<{ control: string | null }>();
      out["control"] = ctrl?.control === "paused" ? "pause" : "resume";
      return Response.json(out, { status: 200 });
    } catch {
      return res; // 解析失败回退原始响应
    }
  }
  return res;
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
    attempt_id: typeof body["attempt_id"] === "string" ? body["attempt_id"] : undefined,
  });
  if (res.status === 200) {
    try {
      const out = (await res.clone().json()) as { status?: string; idempotent?: boolean };
      // 幂等路径（重复 complete，§15/§53）：任务状态早已镜像过，跳过重写，
      // 否则迟到请求携带的 error_code 会污染已 succeeded 的任务记录。
      if (out.status && !out.idempotent) {
        await mirrorTaskState(
          env,
          String(body["task_id"]),
          String(body["lease_id"]),
          executorId2,
          out.status,
          typeof body["error_code"] === "string" ? body["error_code"] : undefined,
          typeof body["attempt_id"] === "string" ? body["attempt_id"] : undefined,
        );
      }
    } catch {
      /* 镜像失败不影响 complete 响应 */
    }
  }
  return res;
}

/**
 * stage-cloud-31 — §38 fail：complete 的失败专用形（error_code 必填）。
 * outcome 允许 failed|retry_wait；错误分类决定是否自动重试（§52）。
 */
export async function executorFail(env: Env, request: Request): Promise<Response> {
  const body = await readBody(request);
  if (!body) return errorResponse(400, "VALIDATION_FAILED");
  const denied = await guard(env, request, body);
  if (denied) return denied;
  const outcome = body["outcome"] ?? "failed";
  if (
    typeof body["task_id"] !== "string" ||
    typeof body["lease_id"] !== "string" ||
    typeof body["error_code"] !== "string" ||
    (outcome !== "failed" && outcome !== "retry_wait")
  ) {
    return errorResponse(400, "VALIDATION_FAILED");
  }
  const req2 = new Request(request.url, {
    method: "POST",
    headers: request.headers,
    body: JSON.stringify({ ...body, outcome }),
  });
  return executorComplete(env, req2);
}

/**
 * stage-cloud-31 — §38 cancel-ack：Executor 确认停止任务。
 * DO 侧走 §23 两跳（running→cancel_requested→canceled）；D1 镜像 canceled。
 */
export async function executorCancelAck(env: Env, request: Request): Promise<Response> {
  const body = await readBody(request);
  if (!body) return errorResponse(400, "VALIDATION_FAILED");
  const denied = await guard(env, request, body);
  if (denied) return denied;
  if (typeof body["task_id"] !== "string" || typeof body["lease_id"] !== "string") {
    return errorResponse(400, "VALIDATION_FAILED");
  }
  const res = await coordinator(env, String(body["execution_path"]), {
    type: "cancel-ack",
    task_id: body["task_id"],
    lease_id: body["lease_id"],
    attempt_id: typeof body["attempt_id"] === "string" ? body["attempt_id"] : undefined,
  });
  if (res.status === 200) {
    const now = Date.now();
    await env.DB
      .prepare("UPDATE tasks SET status='canceled', finished_at=COALESCE(finished_at,?), updated_at=? WHERE id=?")
      .bind(now, now, String(body["task_id"]))
      .run()
      .catch(() => {});
  }
  return res;
}

/**
 * stage-cloud-31 — §35 tasks/{id}/bootstrap：租约门控的凭据+任务元数据下发。
 * 与 /credentials 等价（沿用其租约校验），额外附带 task 元信息；凭据仅存内存。
 */
export async function executorBootstrap(
  env: Env,
  request: Request,
  taskId: string,
): Promise<Response> {
  const body = await readBody(request);
  if (!body) return errorResponse(400, "VALIDATION_FAILED");
  const denied = await guard(env, request, body);
  if (denied) return denied;
  // 复用 /credentials 的租约门控解封（body 须含 task_id/lease_id）
  const inner = new Request(request.url, {
    method: "POST",
    headers: request.headers,
    body: JSON.stringify({ ...body, task_id: taskId }),
  });
  const credRes = await releaseCredentials(env, inner);
  if (credRes.status !== 200) return credRes;
  const meta = await env.DB
    .prepare("SELECT task_type, execution_path FROM tasks WHERE id=?")
    .bind(taskId)
    .first<{ task_type: string; execution_path: string }>();
  const creds = (await credRes.json()) as { credentials?: unknown };
  return Response.json({
    ok: true,
    task: { task_id: taskId, ...(meta ?? {}) },
    credentials: creds.credentials ?? [],
    cookies: null,
  });
}

/**
 * stage-cloud-35 — §115：任务状态只读查询（孤儿扫描用）。
 * GET tasks/{id}/state?executor_id=&execution_path=（query 传参，GET 无 body）。
 */
export async function executorTaskState(
  env: Env,
  request: Request,
  taskId: string,
): Promise<Response> {
  const url = new URL(request.url);
  const executorId = url.searchParams.get("executor_id") ?? "";
  const executionPath = url.searchParams.get("execution_path") ?? "";
  if (!executorId || !executionPath) return errorResponse(400, "VALIDATION_FAILED");
  const auth = await authenticateExecutor(env, request, {
    executor_id: executorId,
    execution_path: executionPath,
  });
  if ("code" in auth) return errorResponse(auth.status, auth.code);
  return coordinator(env, executionPath, { type: "state", task_id: taskId });
}
