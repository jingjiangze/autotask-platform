/// <reference types="@cloudflare/vitest-plugin/types" />
import { expect, inject } from "vitest";
import type { D1Database } from "@cloudflare/workers-types";
import { env } from "cloudflare:workers";

/** 测试共享助手：迁移应用 / DB 访问（迁移由 global-setup 在 Node 侧读取注入）。 */

export const DB = (env as unknown as { DB: D1Database }).DB;
export const MIGRATIONS = inject("d1Migrations");

export const TABLES = [
  "users",
  "auth_sessions",
  "products",
  "orders",
  "order_credentials",
  "tasks",
  "task_attempts",
  "executor_nodes",
  "artifacts",
  "audit_events",
  "idempotency_keys",
];

/** 幂等应用全部迁移（IF NOT EXISTS 语句，可安全重放）。 */
export async function applyMigrations(): Promise<void> {
  expect(MIGRATIONS.length).toBeGreaterThanOrEqual(2);
  const stmts = MIGRATIONS.flatMap((m) => m.queries.map((q) => DB.prepare(q)));
  await DB.batch(stmts);
}
