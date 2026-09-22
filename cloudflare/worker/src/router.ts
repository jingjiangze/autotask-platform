/**
 * stage-cloud-05 — API 路由（计划 §71 router.ts / §55 用户 API 起步）
 *
 * 本阶段只挂认证路由；orders/tasks 等 API 由后续 stage 注册。
 */

import type { Env } from "./auth/auth-service";
import {
  currentUser,
  loginUser,
  logoutUser,
  registerUser,
} from "./auth/auth-service";
import { registerExecutor } from "./executors/executor-auth";
import {
  executorAck,
  executorClaim,
  executorComplete,
  executorHeartbeat,
} from "./executors/executor-api";
import { ERROR_CODES, errorResponse, type ErrorCode } from "./errors";

export { ERROR_CODES, errorResponse };
export type { ErrorCode };

export async function route(request: Request, env: Env): Promise<Response | undefined> {
  const { pathname } = new URL(request.url);

  if (pathname === "/api/v1/auth/register") {
    if (request.method !== "POST") return errorResponse(405, "METHOD_NOT_ALLOWED");
    return registerUser(env, request);
  }
  if (pathname === "/api/v1/auth/login") {
    if (request.method !== "POST") return errorResponse(405, "METHOD_NOT_ALLOWED");
    return loginUser(env, request);
  }
  if (pathname === "/api/v1/auth/logout") {
    if (request.method !== "POST") return errorResponse(405, "METHOD_NOT_ALLOWED");
    return logoutUser(env, request);
  }
  if (pathname === "/api/v1/me") {
    if (request.method !== "GET") return errorResponse(405, "METHOD_NOT_ALLOWED");
    return currentUser(env, request);
  }
  // stage-cloud-08：Executor 注册（§39）——后续 stage 在 /api/executor/v1/* 挂任务拉取等
  if (pathname === "/api/executor/v1/register") {
    if (request.method !== "POST") return errorResponse(405, "METHOD_NOT_ALLOWED");
    return registerExecutor(env, request);
  }
  // stage-cloud-10：Executor Pull 协议（§42/§43）
  const executorPull: Record<string, (env: Env, req: Request) => Promise<Response>> = {
    claim: executorClaim,
    ack: executorAck,
    heartbeat: executorHeartbeat,
    complete: executorComplete,
  };
  if (pathname.startsWith("/api/executor/v1/")) {
    const action = pathname.slice("/api/executor/v1/".length);
    const handler = executorPull[action];
    if (handler) {
      if (request.method !== "POST") return errorResponse(405, "METHOD_NOT_ALLOWED");
      return handler(env, request);
    }
  }
  return undefined; // 未匹配 —— 交回 index 处理 /health 与 404
}
