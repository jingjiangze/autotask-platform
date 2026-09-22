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
  return undefined; // 未匹配 —— 交回 index 处理 /health 与 404
}
