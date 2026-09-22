/// <reference types="@cloudflare/vitest-plugin/types" />
import { beforeEach, describe, expect, it } from "vitest";
import { SELF } from "cloudflare:test";
import { applyMigrations, DB } from "./helpers";

// stage-cloud-05 验收（计划 §79）：
// register / wrong password / login / cookie 形态 / protected API /
// logout / 过期 DENY / 吊销 DENY —— 全部禁止假 PASS。

const BASE = "https://example.com";
const USER = { username: "alice", password: "correct-horse-battery" };

async function register(username: string, password: string): Promise<Response> {
  return SELF.fetch(`${BASE}/api/v1/auth/register`, {
    method: "POST",
    body: JSON.stringify({ username, password }),
  });
}

async function login(username: string, password: string): Promise<Response> {
  return SELF.fetch(`${BASE}/api/v1/auth/login`, {
    method: "POST",
    body: JSON.stringify({ username, password }),
  });
}

function cookieOf(res: Response): string | null {
  return res.headers.get("Set-Cookie");
}

beforeEach(async () => {
  await applyMigrations();
});

describe("stage-cloud-05 central auth", () => {
  it("register PASS: creates user with pbkdf2 hash, no plaintext anywhere", async () => {
    const res = await register(USER.username, USER.password);
    expect(res.status).toBe(201);
    const body = (await res.json()) as { ok: boolean; user: { role: string } };
    expect(body.ok).toBe(true);
    expect(body.user.role).toBe("user");

    const row = await DB.prepare(
      "SELECT password_hash, password_scheme FROM users WHERE username='alice'",
    ).first<{ password_hash: string; password_scheme: string }>();
    expect(row?.password_scheme).toBe("pbkdf2-sha256-v1");
    expect(row?.password_hash.startsWith("pbkdf2-sha256-v1$")).toBe(true);
    expect(row?.password_hash).not.toContain(USER.password);

    // 审计事件落库
    const audit = await DB.prepare(
      "SELECT event_type FROM audit_events WHERE event_type='USER_REGISTERED'",
    ).first();
    expect(audit).toBeTruthy();
  });

  it("duplicate register → 409; bad username/password → 400", async () => {
    await register(USER.username, USER.password);
    const dup = await register(USER.username, USER.password);
    expect(dup.status).toBe(409);
    const badName = await register("ab", USER.password);
    expect(badName.status).toBe(400);
    const shortPw = await register("bob", "short");
    expect(shortPw.status).toBe(400);
  });

  it("wrong password → 401, no session cookie", async () => {
    await register(USER.username, USER.password);
    const res = await login(USER.username, "wrong-password-99");
    expect(res.status).toBe(401);
    const body = (await res.json()) as { ok: boolean; error: { code: string } };
    expect(body.ok).toBe(false);
    expect(body.error.code).toBe("INVALID_CREDENTIALS");
    expect(cookieOf(res)).toBeNull();
    // 不存在的用户返回同一错误（防枚举）
    const ghost = await login("nobody-here", USER.password);
    expect(ghost.status).toBe(401);
    expect(((await ghost.json()) as { error: { code: string } }).error.code).toBe(
      "INVALID_CREDENTIALS",
    );
  });

  it("login PASS: opaque token cookie with HttpOnly/Secure/SameSite=Lax/Path=/, D1 stores only sha256", async () => {
    await register(USER.username, USER.password);
    const res = await login(USER.username, USER.password);
    expect(res.status).toBe(200);

    const cookie = cookieOf(res) ?? "";
    expect(cookie.startsWith("cf_session=")).toBe(true);
    expect(cookie).toContain("HttpOnly");
    expect(cookie).toContain("Secure");
    expect(cookie).toContain("SameSite=Lax");
    expect(cookie).toContain("Path=/");
    const token = cookie.split(";")[0]!.split("=")[1]!;
    expect(token.length).toBeGreaterThanOrEqual(40);

    const row = await DB.prepare(
      "SELECT token_hash, expires_at, revoked_at FROM auth_sessions",
    ).first<{ token_hash: string; expires_at: number; revoked_at: number | null }>();
    expect(row?.token_hash).toBeTruthy();
    expect(row?.token_hash).toHaveLength(64); // sha256 hex
    expect(row?.token_hash).not.toContain(token); // 明文 token 不入库
    expect(row?.revoked_at).toBeNull();
    expect(row!.expires_at).toBeGreaterThan(Date.now());
  });

  it("protected API: /api/v1/me PASS with cookie, AUTH_REQUIRED without", async () => {
    await register(USER.username, USER.password);
    const loginRes = await login(USER.username, USER.password);
    const cookie = cookieOf(loginRes)!.split(";")[0]!;

    const me = await SELF.fetch(`${BASE}/api/v1/me`, {
      headers: { Cookie: cookie },
    });
    expect(me.status).toBe(200);
    const body = (await me.json()) as { ok: boolean; user: { username: string; role: string } };
    expect(body.ok).toBe(true);
    expect(body.user.username).toBe(USER.username);
    expect(body.user.role).toBe("user");

    const anon = await SELF.fetch(`${BASE}/api/v1/me`);
    expect(anon.status).toBe(401);
    expect(((await anon.json()) as { error: { code: string } }).error.code).toBe(
      "AUTH_REQUIRED",
    );
  });

  it("logout PASS: session revoked, /me then DENY", async () => {
    await register(USER.username, USER.password);
    const loginRes = await login(USER.username, USER.password);
    const cookie = cookieOf(loginRes)!.split(";")[0]!;

    const out = await SELF.fetch(`${BASE}/api/v1/auth/logout`, {
      method: "POST",
      headers: { Cookie: cookie },
    });
    expect(out.status).toBe(200);

    const row = await DB.prepare(
      "SELECT revoked_at FROM auth_sessions WHERE revoked_at IS NOT NULL",
    ).first();
    expect(row).toBeTruthy();

    const me = await SELF.fetch(`${BASE}/api/v1/me`, {
      headers: { Cookie: cookie },
    });
    expect(me.status).toBe(401);
  });

  it("expired session → DENY", async () => {
    await register(USER.username, USER.password);
    const loginRes = await login(USER.username, USER.password);
    const cookie = cookieOf(loginRes)!.split(";")[0]!;
    await DB.prepare("UPDATE auth_sessions SET expires_at = ?").bind(Date.now() - 1000).run();

    const me = await SELF.fetch(`${BASE}/api/v1/me`, {
      headers: { Cookie: cookie },
    });
    expect(me.status).toBe(401);
  });

  it("unknown path keeps unified error contract; /health unchanged", async () => {
    const nf = await SELF.fetch(`${BASE}/api/v1/nothing`);
    expect(nf.status).toBe(404);
    expect(((await nf.json()) as { error: { code: string } }).error.code).toBe("NOT_FOUND");
    const health = await SELF.fetch(`${BASE}/health`);
    expect(health.status).toBe(200);
  });
});
