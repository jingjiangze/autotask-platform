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

  it("returns unified error contract for unknown paths", async () => {
    const res = await SELF.fetch("https://example.com/nope");
    expect(res.status).toBe(404);
    const body = (await res.json()) as { ok: boolean; error: { code: string; message: string } };
    expect(body.ok).toBe(false);
    expect(body.error.code).toBe("NOT_FOUND");
    expect(typeof body.error.message).toBe("string");
  });
});
