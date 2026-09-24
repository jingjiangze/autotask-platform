/// <reference types="@cloudflare/vitest-plugin/types" />
import { describe, expect, it } from "vitest";
import { env } from "cloudflare:test";
import { applyMigrations, DB } from "./helpers";
import { adminExecutors } from "../src/admin/admin-service";

// stage-cloud-31 — §58 验收：执行器列表永不泄漏 token_hash（§58 红线）。
// 权限门控（401/403）在 router 层，与既有 admin 端点同模式。

describe("§58 admin API", () => {
  it("执行器列表：输出含能力/在线信息，绝无 token_hash/token 字段", async () => {
    await applyMigrations();
    await DB.prepare(
      "INSERT INTO executor_nodes(id,name,execution_path,token_hash,version,capabilities_json,enabled,status,created_at,updated_at) VALUES(?,?,?,?,?,?,1,'offline',?,?)",
    )
      .bind("exec-admin-spec", "spec", "local", "deadbeef", "1.0.0", '["demo"]', Date.now(), Date.now())
      .run();
    const res = await adminExecutors(env as never, new Request("https://x/"));
    const body = (await res.json()) as { executors: Record<string, unknown>[] };
    const row = body.executors.find((x) => x["id"] === "exec-admin-spec");
    expect(row).toBeTruthy();
    expect(row).not.toHaveProperty("token_hash");
    expect(row).not.toHaveProperty("token");
    expect(row).toHaveProperty("capabilities_json");
    expect(row).toHaveProperty("last_seen_at");
  });
});
