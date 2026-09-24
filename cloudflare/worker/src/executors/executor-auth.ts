/**
 * stage-cloud-08 — Executor 注册与认证（计划 §39/§40/§82）
 *
 * 注册：一次性 bootstrap token（管理员 Secret）→ 生成 executor_id 专属
 *       executor_token（仅响应中显示一次），D1 只存 SHA-256 hash。
 * 认证：token + executor_id + execution_path 三元校验（§40）——
 *       exec-internal-01 伪造 execution_path=external 必须被拒。
 */

import type { Env } from "../auth/auth-service";
import {
  EXECUTION_PATHS,
  type ExecutionPath,
} from "../types/protocol";
import { sha256Hex } from "../crypto/hashing";

// stage-cloud-31：注册流拆至 executor-service（§71）；保留再导出兼容 router
export { registerExecutor } from "./executor-service";
import { errorResponse, type ErrorCode } from "../errors";

const EXECUTOR_ID_RE = /^exec-[a-z0-9-]{2,32}$/;

export interface ExecutorNodeRow {
  id: string;
  name: string;
  execution_path: string;
  token_hash: string;
  version: string;
  capabilities_json: string;
  enabled: number;
  status: string;
}

function bad(status: number, code: ErrorCode): Response {
  return errorResponse(status, code);
}

function timingSafeEqualHex(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

function bearerToken(request: Request): string | null {
  const h = request.headers.get("Authorization") ?? "";
  const m = /^Bearer\s+(.+)$/.exec(h);
  return m ? m[1]!.trim() : null;
}

async function audit(
  DB: Env["DB"],
  eventType: string,
  actorId: string | null,
  entityId: string | null,
): Promise<void> {
  await DB.prepare(
    "INSERT INTO audit_events(id,actor_type,actor_id,event_type,entity_type,entity_id,created_at) VALUES(?,?,?,?,?,?,?)",
  )
    .bind(crypto.randomUUID(), "executor", actorId, eventType, "executor", entityId, Date.now())
    .run();
}

/**
 * stage-cloud-13 — 节点级心跳（§45）：POST /api/executor/v1/node-heartbeat
 * 刷新 last_seen_at；返回租约过期提示（Executor 据此恢复）。
 */
export async function executorNodeHeartbeat(env: Env, request: Request): Promise<Response> {
  let body: Record<string, unknown>;
  try {
    body = (await request.json()) as Record<string, unknown>;
  } catch {
    return bad(400, "VALIDATION_FAILED");
  }
  const executorId = String(body["executor_id"] ?? "");
  const executionPath = String(body["execution_path"] ?? "");
  if (!executorId || !executionPath) return bad(400, "VALIDATION_FAILED");
  const auth = await authenticateExecutor(env, request, {
    executor_id: executorId,
    execution_path: executionPath,
  });
  if ("code" in auth) return bad(auth.status, auth.code);
  return Response.json({
    ok: true,
    server_time: Date.now(),
    lease_ttl_ms: 10 * 60 * 1000,
  });
}

export interface AuthenticatedExecutor {
  row: ExecutorNodeRow;
}

export type ExecutorAuthFailure = {
  status: 401 | 403;
  code: "EXECUTOR_AUTH_FAILED" | "EXECUTOR_DISABLED" | "EXECUTOR_PATH_MISMATCH";
};

export async function authenticateExecutor(
  env: Env,
  request: Request,
  expected: { executor_id: string; execution_path: string },
): Promise<AuthenticatedExecutor | ExecutorAuthFailure> {
  const token = bearerToken(request);
  if (!token) return { status: 401, code: "EXECUTOR_AUTH_FAILED" };
  const row = await env.DB.prepare(
    "SELECT id,name,execution_path,token_hash,version,capabilities_json,enabled,status FROM executor_nodes WHERE id = ?",
  )
    .bind(expected.executor_id)
    .first<ExecutorNodeRow>();
  if (!row) return { status: 401, code: "EXECUTOR_AUTH_FAILED" };
  if (!timingSafeEqualHex(await sha256Hex(token), row.token_hash)) {
    return { status: 401, code: "EXECUTOR_AUTH_FAILED" };
  }
  if (!row.enabled) {
    return { status: 403, code: "EXECUTOR_DISABLED" };
  }
  // §40：路径伪造拒绝 —— token 正确但 path 不匹配同样 DENY
  if (row.execution_path !== expected.execution_path) {
    return { status: 403, code: "EXECUTOR_PATH_MISMATCH" };
  }
  // stage-cloud-13：每次认证成功即视为节点存活信号（best-effort，不阻塞主流程）
  try {
    await env.DB.prepare(
      "UPDATE executor_nodes SET last_seen_at = ?, status = 'online' WHERE id = ?",
    )
      .bind(Date.now(), row.id)
      .run();
  } catch {
    /* 心跳回写失败不影响本次请求 */
  }
  return { row };
}
