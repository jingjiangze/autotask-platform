/**
 * stage-cloud-31 — §71 executors/executor-service：节点注册（§39/§40/§82）。
 * 自 executor-auth.ts 拆出；认证（authenticateExecutor）仍在 executor-auth。
 */

import type { Env } from "../auth/auth-service";
import { errorResponse, type ErrorCode } from "../errors";
import { sha256Hex } from "../crypto/hashing";
import { EXECUTION_PATHS, type ExecutionPath } from "../types/protocol";

const EXECUTOR_ID_RE = /^exec-[a-z0-9-]{2,32}$/;

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

export async function registerExecutor(env: Env, request: Request): Promise<Response> {
  const bootstrap = env.EXECUTOR_BOOTSTRAP_TOKEN;
  if (!bootstrap) {
    return bad(503, "BOOTSTRAP_DISABLED");
  }
  if (!timingSafeEqualHex(await sha256Hex(bearerToken(request) ?? ""), await sha256Hex(bootstrap))) {
    return bad(403, "BOOTSTRAP_INVALID");
  }
  let body: Record<string, unknown>;
  try {
    body = (await request.json()) as Record<string, unknown>;
  } catch {
    return bad(400, "VALIDATION_FAILED");
  }
  const executorId = String(body["executor_id"] ?? "");
  const executionPath = String(body["execution_path"] ?? "");
  const name = String(body["name"] ?? executorId);
  const version = String(body["version"] ?? "0.0.0");
  const capabilities = body["capabilities"];
  if (!EXECUTOR_ID_RE.test(executorId)) {
    return bad(400, "VALIDATION_FAILED");
  }
  if (!EXECUTION_PATHS.includes(executionPath as ExecutionPath)) {
    return bad(400, "VALIDATION_FAILED");
  }
  if (!Array.isArray(capabilities) || capabilities.some((c) => typeof c !== "string")) {
    return bad(400, "VALIDATION_FAILED");
  }
  const exists = await env.DB.prepare("SELECT id FROM executor_nodes WHERE id = ?")
    .bind(executorId)
    .first();
  if (exists) {
    return bad(409, "EXECUTOR_EXISTS");
  }
  const bytes = crypto.getRandomValues(new Uint8Array(32));
  const executorToken = btoa(String.fromCharCode(...bytes))
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
  const now = Date.now();
  await env.DB.prepare(
    "INSERT INTO executor_nodes(id,name,execution_path,token_hash,version,capabilities_json,enabled,status,created_at,updated_at) VALUES(?,?,?,?,?,?,1,'offline',?,?)",
  )
    .bind(
      executorId,
      name,
      executionPath,
      await sha256Hex(executorToken),
      version,
      JSON.stringify(capabilities),
      now,
      now,
    )
    .run();
  await audit(env.DB, "EXECUTOR_REGISTERED", executorId, executorId);
  return Response.json(
    {
      ok: true,
      executor_id: executorId,
      executor_token: executorToken, // 仅此一次显示（计划 §39）
      note: "store the token locally (Credential Manager / protected file); it cannot be recovered",
    },
    { status: 201 },
  );
}

