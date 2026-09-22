/**
 * stage-cloud-05 — Opaque Session（计划 §15/16）
 *
 * 与本地 wk_token（uid:HMAC，无过期/无吊销）刻意不同：
 *   随机 token + D1 auth_sessions + 过期 + 吊销。
 * D1 只存 SHA-256(token)；明文 token 只存在于 Cookie 与响应当次。
 */

import type { D1Database } from "@cloudflare/workers-types";

export const SESSION_COOKIE = "cf_session";
export const SESSION_TTL_MS_DEFAULT = 7 * 24 * 60 * 60 * 1000; // 7 天

export interface SessionUserRow {
  id: string;
  username: string;
  role: string;
  status: string;
  session_id: string;
  expires_at: number;
}

export function sha256Hex(input: string): Promise<string> {
  return crypto.subtle
    .digest("SHA-256", new TextEncoder().encode(input))
    .then((buf) => [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join(""));
}

export function parseCookies(request: Request): Record<string, string> {
  const out: Record<string, string> = {};
  const raw = request.headers.get("Cookie") ?? "";
  for (const part of raw.split(";")) {
    const idx = part.indexOf("=");
    if (idx <= 0) continue;
    out[part.slice(0, idx).trim()] = part.slice(idx + 1).trim();
  }
  return out;
}

export function sessionCookie(token: string, ttlMs: number): string {
  const maxAge = Math.floor(ttlMs / 1000);
  // 计划 §15：HttpOnly / Secure / SameSite=Lax / Path=/
  return `${SESSION_COOKIE}=${token}; Max-Age=${maxAge}; Path=/; HttpOnly; Secure; SameSite=Lax`;
}

function sessionTtlMs(env: { SESSION_TTL_SECONDS?: string }): number {
  const n = Number.parseInt(env.SESSION_TTL_SECONDS ?? "", 10);
  return Number.isFinite(n) && n > 0 ? n * 1000 : SESSION_TTL_MS_DEFAULT;
}

export async function createSession(
  DB: D1Database,
  userId: string,
  env: { SESSION_TTL_SECONDS?: string },
): Promise<{ token: string; ttlMs: number }> {
  const bytes = crypto.getRandomValues(new Uint8Array(32));
  let token = btoa(String.fromCharCode(...bytes))
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
  const tokenHash = await sha256Hex(token);
  const now = Date.now();
  const ttlMs = sessionTtlMs(env);
  await DB.prepare(
    "INSERT INTO auth_sessions(id,user_id,token_hash,created_at,expires_at) VALUES(?,?,?,?,?)",
  )
    .bind(crypto.randomUUID(), userId, tokenHash, now, now + ttlMs)
    .run();
  return { token, ttlMs };
}

export async function getSessionUser(
  DB: D1Database,
  request: Request,
): Promise<SessionUserRow | null> {
  const token = parseCookies(request)[SESSION_COOKIE];
  if (!token) return null;
  const tokenHash = await sha256Hex(token);
  const row = await DB.prepare(
    `SELECT s.id AS session_id, s.expires_at, s.revoked_at, u.id, u.username, u.role, u.status
     FROM auth_sessions s JOIN users u ON u.id = s.user_id
     WHERE s.token_hash = ?`,
  )
    .bind(tokenHash)
    .first<SessionUserRow & { revoked_at: number | null }>();
  if (!row) return null;
  const now = Date.now();
  if (row.expires_at <= now) return null; // 过期
  if (row.revoked_at !== null) return null; // 已吊销
  if (row.status !== "active") return null; // 用户被禁用
  return row;
}

export async function revokeSession(DB: D1Database, request: Request): Promise<boolean> {
  const token = parseCookies(request)[SESSION_COOKIE];
  if (!token) return false;
  const tokenHash = await sha256Hex(token);
  const res = await DB.prepare(
    "UPDATE auth_sessions SET revoked_at = ? WHERE token_hash = ? AND revoked_at IS NULL",
  )
    .bind(Date.now(), tokenHash)
    .run();
  return (res.meta.changes ?? 0) > 0;
}
