/// <reference types="@cloudflare/vitest-plugin/types" />
import { expect, describe, it } from "vitest";
import { SELF } from "cloudflare:test";

// stage-cloud-03 验收：health PASS + 统一错误契约骨架（计划 §77/§116）

describe("GET /health", () => {
  it("returns 200 with ok:true payload", async () => {
    const res = await SELF.fetch("https://example.com/health");
    expect(res.status).toBe(200);
    const body = (await res.json()) as Record<string, unknown>;
    expect(body["ok"]).toBe(true);
    expect(body["service"]).toBe("autotask-central");
    expect(typeof body["time"]).toBe("number");
  });

  it("rejects non-GET on /health", async () => {
    const res = await SELF.fetch("https://example.com/health", { method: "POST" });
    expect(res.status).toBe(405);
    const body = (await res.json()) as { ok: boolean; error: { code: string } };
    expect(body.ok).toBe(false);
    expect(body.error.code).toBe("METHOD_NOT_ALLOWED");
  });

  it("returns unified error contract for unknown API paths", async () => {
    const res = await SELF.fetch("https://example.com/api/v1/nope");
    expect(res.status).toBe(404);
    const body = (await res.json()) as { ok: boolean; error: { code: string; message: string } };
    expect(body.ok).toBe(false);
    expect(body.error.code).toBe("NOT_FOUND");
    expect(typeof body.error.message).toBe("string");
  });

  // stage-cloud-27：非 API 的 GET 由 Worker 托管前端（SPA）接管
  it("serves SPA shell for non-API GET paths", async () => {
    const res = await SELF.fetch("https://example.com/");
    expect(res.status).toBe(200);
    expect(res.headers.get("Content-Type")).toContain("text/html");
    const html = await res.text();
    expect(html).toContain("自动任务平台");
    expect(html).toContain("/api/v1/auth/login");
  });
});
