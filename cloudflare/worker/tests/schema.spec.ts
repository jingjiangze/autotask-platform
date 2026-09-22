/// <reference types="@cloudflare/vitest-plugin/types" />
import { beforeEach, describe, expect, inject, it } from "vitest";
import type { D1Database } from "@cloudflare/workers-types";
import { env } from "cloudflare:workers";

// stage-cloud-04 验收（计划 §78）：
//   clean install / 重放幂等 / FK 有效 / 重复键拒绝 / EXPLAIN QUERY PLAN 无全表扫描
// 迁移由 tests/global-setup.ts 在 Node 侧读取，经 provided context 注入。

const DB = (env as unknown as { DB: D1Database }).DB;
const MIGRATIONS = inject("d1Migrations");

const TABLES = [
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

/** 幂等应用全部迁移（IF NOT EXISTS 语句，可安全重放）。
 *  放在 beforeEach：无论插件按文件还是按用例隔离存储都能自愈。 */
async function applyMigrations(): Promise<void> {
  expect(MIGRATIONS.length).toBeGreaterThanOrEqual(2);
  const stmts = MIGRATIONS.flatMap((m) => m.queries.map((q) => DB.prepare(q)));
  await DB.batch(stmts);
}

async function tableNames(): Promise<Set<string>> {
  const res = await DB.prepare(
    "SELECT name FROM sqlite_master WHERE type='table'",
  ).all<{ name: string }>();
  return new Set(res.results.map((r) => r.name));
}

beforeEach(async () => {
  await applyMigrations();
});

describe("stage-cloud-04 D1 schema", () => {
  it("clean install creates all 11 business tables", async () => {
    const names = await tableNames();
    for (const t of TABLES) expect(names.has(t)).toBe(true);
  });

  it("migration rerun is idempotent (no error, no table growth)", async () => {
    const before = await tableNames();
    await applyMigrations(); // 第二次应用
    const after = await tableNames();
    expect(after.size).toBe(before.size);
    for (const t of TABLES) expect(after.has(t)).toBe(true);
  });

  it("foreign keys are enforced (order without user rejected)", async () => {
    let rejected = false;
    try {
      await DB.prepare(
        "INSERT INTO orders(id,user_id,product_code,platform,created_at,updated_at) VALUES('o1','ghost-user','cx_video','chaoxing',1,1)",
      ).run();
    } catch (e) {
      rejected = true;
      expect(String(e)).toMatch(/FOREIGN KEY/i);
    }
    expect(rejected).toBe(true);
  });

  it("duplicate username is rejected by unique constraint", async () => {
    const ins = "INSERT INTO users(id,username,password_hash,created_at,updated_at) VALUES(?,?,?,?,?)";
    await DB.batch([
      DB.prepare(ins).bind("u1", "alice", "h1", 1, 1),
      DB.prepare(ins).bind("u2", "alice", "h2", 2, 2),
    ]).catch(() => undefined);
    const dup = await DB.prepare(ins).bind("u3", "alice", "h3", 3, 3).run();
    // 第二次插入必须失败（batch 里已吞掉一次，这里必须抛）
    // —— sqlite UNIQUE 报错说明约束生效
    let rejected = false;
    try {
      await DB.prepare(ins).bind("u4", "alice", "h4", 4, 4).run();
    } catch {
      rejected = true;
    }
    expect(rejected).toBe(true);
    void dup;
  });

  it("full business chain with valid FKs: user→order→credential→task→attempt→artifact→audit", async () => {
    const now = 1760000000000;
    await DB.batch([
      DB.prepare(
        "INSERT INTO users(id,username,password_hash,password_scheme,created_at,updated_at) VALUES('u1','bob','h','legacy-hmac-v1',?,?)",
      ).bind(now, now),
      DB.prepare(
        "INSERT INTO products(id,code,name,platform,created_at,updated_at) VALUES('p1','cx_video','超星视频','chaoxing',?,?)",
      ).bind(now, now),
      DB.prepare(
        "INSERT INTO orders(id,user_id,product_id,product_code,platform,account,status,created_at,updated_at) VALUES('o1','u1','p1','cx_video','chaoxing','13800000000','pending',?,?)",
      ).bind(now, now),
      // 凭据边界：只有 enc-v2 版本进入 D1（stage-cloud-01b 校准）
      DB.prepare(
        "INSERT INTO order_credentials(id,order_id,credential_type,ciphertext,encryption_version,created_at,updated_at) VALUES('c1','o1','password_bundle','enc:v2:AAEq…','enc-v2-aes-256-gcm',?,?)",
      ).bind(now, now),
      DB.prepare(
        "INSERT INTO tasks(id,order_id,task_type,execution_path,status,created_at,updated_at) VALUES('t1','o1','chaoxing.run','local','queued',?,?)",
      ).bind(now, now),
      DB.prepare(
        "INSERT INTO task_attempts(id,task_id,attempt_no,executor_id,status,created_at,updated_at) VALUES('a1','t1',1,'exec-local-01','running',?,?)",
      ).bind(now, now),
      DB.prepare(
        "INSERT INTO artifacts(id,task_id,attempt_id,artifact_type,r2_object_key,size_bytes,sha256,created_at) VALUES('ar1','t1','a1','stdout','tasks/2026/09/t1/attempts/1/stdout.gz',123,'deadbeef',?)",
      ).bind(now),
      DB.prepare(
        "INSERT INTO audit_events(id,actor_type,actor_id,event_type,entity_type,entity_id,created_at) VALUES('e1','executor','exec-local-01','TASK_STARTED','task','t1',?)",
      ).bind(now),
    ]);
    const o = await DB.prepare("SELECT account,status FROM orders WHERE id='o1'").first<{
      account: string;
      status: string;
    }>();
    expect(o?.account).toBe("13800000000");
    const c = await DB.prepare(
      "SELECT encryption_version FROM order_credentials WHERE id='c1'",
    ).first<{ encryption_version: string }>();
    expect(c?.encryption_version).toBe("enc-v2-aes-256-gcm");
    // attempt 唯一约束：(task_id, attempt_no)
    let dupAttempt = false;
    try {
      await DB.prepare(
        "INSERT INTO task_attempts(id,task_id,attempt_no,status,created_at,updated_at) VALUES('a2','t1',1,'running',?,?)",
      )
        .bind(now, now)
        .run();
    } catch {
      dupAttempt = true;
    }
    expect(dupAttempt).toBe(true);
  });
});

describe("stage-cloud-04 EXPLAIN QUERY PLAN", () => {
  it("core queries use indexes, no full table scans", async () => {
    const cases: Array<[string, string]> = [
      [
        "SELECT * FROM orders WHERE user_id='u1' ORDER BY created_at DESC",
        "idx_orders_user_created",
      ],
      [
        "SELECT * FROM orders WHERE status='pending' ORDER BY created_at DESC",
        "idx_orders_status_created",
      ],
      [
        "SELECT * FROM orders WHERE account='13800000000'",
        "idx_orders_account",
      ],
      [
        "SELECT * FROM tasks WHERE execution_path='local' AND status='queued' ORDER BY priority DESC, created_at",
        "idx_tasks_queue",
      ],
      [
        "SELECT * FROM tasks WHERE status='running' AND lease_expires_at > 1",
        "idx_tasks_lease",
      ],
      [
        "SELECT * FROM auth_sessions WHERE token_hash='abc'",
        "auth_sessions",
      ],
      [
        "SELECT * FROM task_attempts WHERE task_id='t1' ORDER BY attempt_no DESC",
        "idx_attempts_task",
      ],
      [
        "SELECT * FROM audit_events WHERE entity_type='task' AND entity_id='t1' ORDER BY created_at DESC",
        "idx_audit_entity",
      ],
    ];
    for (const [query, indexHint] of cases) {
      const plan = await DB.prepare(`EXPLAIN QUERY PLAN ${query}`).all<{ detail: string }>();
      const details = plan.results.map((r) => r.detail).join(" | ");
      expect(details, `query: ${query}\nplan: ${details}`).toMatch(/SEARCH/);
      expect(details, `query: ${query}\nplan: ${details}`).not.toMatch(/SCAN/);
      if (!indexHint.startsWith("sqlite_autoindex")) {
        expect(details, `query: ${query}\nplan: ${details}`).toContain(indexHint);
      }
    }
  });
});
