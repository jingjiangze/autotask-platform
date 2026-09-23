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
import { registerExecutor, executorNodeHeartbeat } from "./executors/executor-auth";
import {
  executorAck,
  executorClaim,
  executorComplete,
  executorHeartbeat,
} from "./executors/executor-api";
import { ERROR_CODES, errorResponse, type ErrorCode } from "./errors";
import { getSessionUser } from "./auth/session-service";
import { releaseCredentials, storeCredential } from "./credentials/credential-service";
import { myOrders, orderDetail, orderByAccount } from "./orders/order-query";
import { guestQuery } from "./orders/guest-query";
import {
  cancelTask,
  createOrder,
  createOrderTask,
  listProducts,
  taskDetail,
} from "./orders/order-service";
import { downloadArtifact, uploadArtifact } from "./artifacts/artifact-service";
import { orderControl, orderCredentialsView, orderQueryCourses } from "./orders/order-control";

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
    // stage-cloud-11：凭据解封（租约门控）
    credentials: releaseCredentials,
    // stage-cloud-14：工件上传（raw body；元数据在 query）
    artifacts: uploadArtifact,
    // stage-cloud-13：节点级心跳
    "node-heartbeat": executorNodeHeartbeat,
  };
  if (pathname.startsWith("/api/executor/v1/")) {
    const action = pathname.slice("/api/executor/v1/".length);
    const handler = executorPull[action];
    if (handler) {
      if (request.method !== "POST") return errorResponse(405, "METHOD_NOT_ALLOWED");
      return handler(env, request);
    }
  }
  // stage-cloud-11：管理端凭据写入（admin session 门控）
  if (pathname === "/api/v1/admin/credentials") {
    if (request.method !== "POST") return errorResponse(405, "METHOD_NOT_ALLOWED");
    const session = await getSessionUser(env.DB, request);
    if (!session) return errorResponse(401, "AUTH_REQUIRED");
    if (session.role !== "admin") return errorResponse(403, "FORBIDDEN");
    return storeCredential(env, request);
  }
  // stage-cloud-16：中央查询 API（§57/§88）
  if (pathname === "/api/v1/my/orders") {
    if (request.method !== "GET") return errorResponse(405, "METHOD_NOT_ALLOWED");
    return myOrders(env, request);
  }
  const detailMatch = /^\/api\/v1\/orders\/([A-Za-z0-9_-]+)$/.exec(pathname);
  if (detailMatch) {
    if (request.method !== "GET") return errorResponse(405, "METHOD_NOT_ALLOWED");
    return orderDetail(env, request, detailMatch[1]!);
  }
  if (pathname === "/api/v1/admin/orders/by-account") {
    if (request.method !== "GET") return errorResponse(405, "METHOD_NOT_ALLOWED");
    return orderByAccount(env, request);
  }
  // stage-cloud-16b：订单/任务创建（§55，E2E 前置）
  if (pathname === "/api/v1/products") {
    if (request.method !== "GET") return errorResponse(405, "METHOD_NOT_ALLOWED");
    return listProducts(env, request);
  }
  if (pathname === "/api/v1/orders") {
    if (request.method === "GET") return myOrders(env, request);
    if (request.method === "POST") return createOrder(env, request);
    return errorResponse(405, "METHOD_NOT_ALLOWED");
  }
  if (pathname.startsWith("/api/v1/orders/") && pathname.endsWith("/tasks")) {
    const oid = pathname.slice("/api/v1/orders/".length, -"/tasks".length);
    if (request.method !== "POST") return errorResponse(405, "METHOD_NOT_ALLOWED");
    return createOrderTask(env, request, oid);
  }
  // stage-cloud-28：订单控制 / 凭据明文查看 / 查课表
  const orderSub = /^\/api\/v1\/orders\/([A-Za-z0-9_-]+)\/(control|credentials|query-courses)$/.exec(
    pathname,
  );
  if (orderSub) {
    const oid = orderSub[1]!;
    if (orderSub[2] === "control") {
      if (request.method !== "POST") return errorResponse(405, "METHOD_NOT_ALLOWED");
      return orderControl(env, request, oid);
    }
    if (orderSub[2] === "credentials") {
      if (request.method !== "GET") return errorResponse(405, "METHOD_NOT_ALLOWED");
      return orderCredentialsView(env, request, oid);
    }
    if (request.method !== "POST") return errorResponse(405, "METHOD_NOT_ALLOWED");
    return orderQueryCourses(env, request, oid);
  }
  const taskMatch = /^\/api\/v1\/tasks\/([A-Za-z0-9_-]+)$/.exec(pathname);
  if (taskMatch) {
    if (request.method === "GET") return taskDetail(env, request, taskMatch[1]!);
    if (request.method === "POST") return cancelTask(env, request, taskMatch[1]!);
    return errorResponse(405, "METHOD_NOT_ALLOWED");
  }
  // stage-cloud-14：工件下载（owner/admin，R2 流式）
  const artifactMatch = /^\/api\/v1\/orders\/([A-Za-z0-9_-]+)\/artifacts\/([A-Za-z0-9_-]+)$/.exec(
    pathname,
  );
  if (artifactMatch) {
    if (request.method !== "GET") return errorResponse(405, "METHOD_NOT_ALLOWED");
    return downloadArtifact(env, request, artifactMatch[1]!, artifactMatch[2]!);
  }
  // stage-cloud-17：访客查单（无 proof code，仅单号前缀；校准结论）
  if (pathname === "/api/v1/guest/query") {
    if (request.method !== "POST") return errorResponse(405, "METHOD_NOT_ALLOWED");
    return guestQuery(env, request);
  }
  return undefined; // 未匹配 —— 交回 index 处理 /health 与 404
}
