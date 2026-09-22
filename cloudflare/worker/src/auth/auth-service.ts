/**
 * stage-cloud-05 — 中央认证服务（计划 §79）
 *
 * register / login / logout / current user / audit（计划 §118 子集）。
 * 隐私红线：password_hash 只入库；任何响应/日志不出现口令与明文 token。
 */

import type { D1Database } from "@cloudflare/workers-types";
import {
  LEGACY_SCHEME,
  SCHEME,
  hashPassword,
  verifyLegacyHmac,
  verifyPassword,
} from "./password-compat";
import {
  createSession,
  getSessionUser,
  sessionCookie,
} from "./session-service";

const USERNAME_RE = /^[a-zA-Z0-9_-]{3,32}$/;
const MIN_PASSWORD_LEN = 8;

export interface Env {
  DB: D1Database;
  SESSION_TTL_SECONDS?: string;
  /** stage-cloud-06：legacy 迁移期专用，仅迁移工具/受控环境注入；生产部署留空 */
  LEGACY_HMAC_SECRET?: string;
  /** stage-cloud-08：Executor 一次性 bootstrap 注册 token（§39）；未配置 = 注册关闭 */
  EXECUTOR_BOOTSTRAP_TOKEN?: string;
  /** stage-cloud-09：调度中枢 DO（每 execution_path 一个实例） */
  COORDINATOR: DurableObjectNamespace;
}

export type Json = Record<string, unknown>;

function bad(status: number, code: string, message: string): Response {
  return Response.json({ ok: false, error: { code, message } }, { status });
}

async function audit(
  DB: D1Database,
  eventType: string,
  actorId: string | null,
  entityType: string,
  entityId: string | null,
): Promise<void> {
  await DB.prepare(
    "INSERT INTO audit_events(id,actor_type,actor_id,event_type,entity_type,entity_id,created_at) VALUES(?,?,?,?,?,?,?)",
  )
    .bind(crypto.randomUUID(), "user", actorId, eventType, entityType, entityId, Date.now())
    .run();
}

export async function registerUser(env: Env, request: Request): Promise<Response> {
  let body: Json;
  try {
    body = (await request.json()) as Json;
  } catch {
    return bad(400, "VALIDATION_FAILED", "Body must be JSON");
  }
  const username = String(body["username"] ?? "").trim();
  const password = String(body["password"] ?? "");
  if (!USERNAME_RE.test(username)) {
    return bad(400, "VALIDATION_FAILED", "username must be 3-32 chars [a-zA-Z0-9_-]");
  }
  if (password.length < MIN_PASSWORD_LEN) {
    return bad(400, "VALIDATION_FAILED", `password must be at least ${MIN_PASSWORD_LEN} chars`);
  }
  const exists = await env.DB.prepare("SELECT id FROM users WHERE username = ?")
    .bind(username)
    .first();
  if (exists) {
    return bad(409, "USERNAME_TAKEN", "username already registered");
  }
  const id = crypto.randomUUID();
  const now = Date.now();
  const passwordHash = await hashPassword(password);
  await env.DB.prepare(
    "INSERT INTO users(id,username,password_hash,password_scheme,role,status,registration_source,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
  )
    .bind(id, username, passwordHash, "pbkdf2-sha256-v1", "user", "active", "cloud-api", now, now)
    .run();
  await audit(env.DB, "USER_REGISTERED", id, "user", id);
  return Response.json(
    { ok: true, user: { id, username, role: "user" } },
    { status: 201 },
  );
}

export async function loginUser(env: Env, request: Request): Promise<Response> {
  let body: Json;
  try {
    body = (await request.json()) as Json;
  } catch {
    return bad(400, "VALIDATION_FAILED", "Body must be JSON");
  }
  const username = String(body["username"] ?? "").trim();
  const password = String(body["password"] ?? "");
  const row = await env.DB.prepare(
    "SELECT id,username,password_hash,password_scheme,role,status FROM users WHERE username = ?",
  )
    .bind(username)
    .first<{
      id: string;
      username: string;
      password_hash: string;
      password_scheme: string;
      role: string;
      status: string;
    }>();
  // 统一错误文案：不区分「用户不存在」与「密码错误」（防用户枚举）
  const ok = row
    ? row.status === "active" &&
      (row.password_scheme === SCHEME
        ? await verifyPassword(password, row.password_hash)
        : row.password_scheme === LEGACY_SCHEME
          ? await verifyAndUpgradeLegacy(env, row.id, password, row.password_hash)
          : false)
    : false;
  if (!row || !ok) {
    return bad(401, "INVALID_CREDENTIALS", "invalid username or password");
  }
  const { token, ttlMs } = await createSession(env.DB, row.id, env);
  await audit(env.DB, "USER_LOGIN", row.id, "user", row.id);
  return Response.json(
    { ok: true, user: { id: row.id, username: row.username, role: row.role } },
    { status: 200, headers: { "Set-Cookie": sessionCookie(token, ttlMs) } },
  );
}

/**
 * stage-cloud-06：legacy-hmac-v1 验证 + 成功后立即升级 pbkdf2（计划 §17）。
 * 无 LEGACY_HMAC_SECRET 时一律拒绝 —— 云端没有本地 SECRET 就没有 legacy 能力。
 */
async function verifyAndUpgradeLegacy(
  env: Env,
  userId: string,
  password: string,
  storedHash: string,
): Promise<boolean> {
  const secret = env.LEGACY_HMAC_SECRET;
  if (!secret) return false;
  if (!(await verifyLegacyHmac(password, storedHash, secret))) return false;
  const upgraded = await hashPassword(password);
  await env.DB.prepare(
    "UPDATE users SET password_hash = ?, password_scheme = 'pbkdf2-sha256-v1', updated_at = ? WHERE id = ?",
  )
    .bind(upgraded, Date.now(), userId)
    .run();
  return true;
}

export async function logoutUser(env: Env, request: Request): Promise<Response> {
  const session = await getSessionUser(env.DB, request);
  let revoked = false;
  if (session) {
    const res = await env.DB.prepare(
      "UPDATE auth_sessions SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
    )
      .bind(Date.now(), session.session_id)
      .run();
    revoked = (res.meta.changes ?? 0) > 0;
    await audit(env.DB, "USER_LOGOUT", session.id, "user", session.id);
  }
  // 幂等：无论会话是否存在都返回成功（不泄露会话有效性）
  return Response.json({ ok: true, revoked });
}

export async function currentUser(env: Env, request: Request): Promise<Response> {
  const session = await getSessionUser(env.DB, request);
  if (!session) return bad(401, "AUTH_REQUIRED", "Authentication required");
  return Response.json({
    ok: true,
    user: { id: session.id, username: session.username, role: session.role },
  });
}
