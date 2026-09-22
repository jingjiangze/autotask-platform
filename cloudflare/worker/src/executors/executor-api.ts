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
  const path = String(body["execution_path"]);
  return coordinator(env, path, {
    type: "claim",
    executor_id: String(body["executor_id"]),
    execution_path: path,
    capabilities: Array.isArray(body["capabilities"]) ? body["capabilities"] : [],
  });
}

export async function executorAck(env: Env, request: Request): Promise<Response> {
  const body = await readBody(request);
  if (!body) return errorResponse(400, "VALIDATION_FAILED");
  const denied = await guard(env, request, body);
  if (denied) return denied;
  if (typeof body["task_id"] !== "string" || typeof body["lease_id"] !== "string") {
    return errorResponse(400, "VALIDATION_FAILED");
  }
  return coordinator(env, String(body["execution_path"]), {
    type: "ack",
    task_id: body["task_id"],
    lease_id: body["lease_id"],
  });
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
  return coordinator(env, String(body["execution_path"]), {
    type: "complete",
    task_id: body["task_id"],
    lease_id: body["lease_id"],
    outcome,
    error_code: typeof body["error_code"] === "string" ? body["error_code"] : undefined,
  });
}
