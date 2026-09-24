/**
 * stage-cloud-31 — §58 Central Admin：用户/订单/执行器/任务/失败任务/系统状态。
 *
 * 全部 API 仅 admin session 可达（router 层门控）。执行器条目绝不包含
 * token_hash（§58）；订单条目绝不含凭据/密文（§56）。
 * 列表分页一律 cursor-based（§56），limit 上限 100。
 */

import type { Env } from "../auth/auth-service";
import { errorResponse } from "../errors";

const MAX_LIMIT = 100;

function limit(url: URL, fallback = 50): number {
  const n = Number(url.searchParams.get("limit") ?? fallback);
  return Number.isFinite(n) && n > 0 ? Math.min(n, MAX_LIMIT) : fallback;
}

function ok(body: Record<string, unknown>): Response {
  return Response.json({ ok: true, ...body });
}

/** §58 系统状态：各实体计数概览。 */
export async function adminStats(env: Env, _request: Request): Promise<Response> {
  const one = async (sql: string) => {
    const r = await env.DB.prepare(sql).first<{ n: number }>();
    return r?.n ?? 0;
  };
  const [users, orders, ordersActive, tasks, tasksRunning, tasksFailed, executors, executorsOnline] =
    await Promise.all([
      one("SELECT COUNT(*) n FROM users"),
      one("SELECT COUNT(*) n FROM orders"),
      one("SELECT COUNT(*) n FROM orders WHERE status IN ('pending','processing')"),
      one("SELECT COUNT(*) n FROM tasks"),
      one("SELECT COUNT(*) n FROM tasks WHERE status IN ('queued','leased','running','retry_wait')"),
      one("SELECT COUNT(*) n FROM tasks WHERE status='failed'"),
      one("SELECT COUNT(*) n FROM executor_nodes WHERE enabled=1"),
      one("SELECT COUNT(*) n FROM executor_nodes WHERE enabled=1 AND last_seen_at > (strftime('%s','now')*1000 - 120000)"),
    ]);
  return ok({
    stats: {
      users,
      orders,
      orders_active: ordersActive,
      tasks,
      tasks_running: tasksRunning,
      tasks_failed: tasksFailed,
      executors,
      executors_online: executorsOnline,
    },
  });
}

/** §58 用户列表（无密码 hash）。 */
export async function adminUsers(env: Env, request: Request): Promise<Response> {
  const url = new URL(request.url);
  const rows = await env.DB.prepare(
    "SELECT id, username, role, status, created_at FROM users ORDER BY created_at DESC LIMIT ?",
  )
    .bind(limit(url))
    .all();
  return ok({ users: rows.results });
}

/** §58 订单列表（含用户名 join；无凭据字段；cursor 分页）。 */
export async function adminOrders(env: Env, request: Request): Promise<Response> {
  const url = new URL(request.url);
  const cursor = url.searchParams.get("cursor");
  const rows = cursor
    ? await env.DB.prepare(
        "SELECT o.id, o.user_id, u.username, o.product_code, o.platform, o.status, o.created_at, o.updated_at FROM orders o JOIN users u ON o.user_id=u.id WHERE o.created_at < ? ORDER BY o.created_at DESC LIMIT ?",
      )
        .bind(Number(cursor), limit(url))
        .all()
    : await env.DB.prepare(
        "SELECT o.id, o.user_id, u.username, o.product_code, o.platform, o.status, o.created_at, o.updated_at FROM orders o JOIN users u ON o.user_id=u.id ORDER BY o.created_at DESC LIMIT ?",
      )
        .bind(limit(url))
        .all();
  const results = rows.results as Record<string, unknown>[];
  const nextCursor = results.length === limit(url) ? results[results.length - 1]!["created_at"] : null;
  return ok({ orders: results, next_cursor: nextCursor });
}

/** §58 任务列表（可按状态过滤；含 error_code/执行路径/attempt）。 */
export async function adminTasks(env: Env, request: Request): Promise<Response> {
  const url = new URL(request.url);
  const status = url.searchParams.get("status");
  const lim = limit(url);
  const rows = status
    ? await env.DB.prepare(
        "SELECT t.id, t.order_id, t.task_type, t.execution_path, t.status, t.attempt_no, t.max_attempts, t.error_code, t.executor_id, t.created_at, t.updated_at FROM tasks t WHERE t.status=? ORDER BY t.updated_at DESC LIMIT ?",
      )
        .bind(status, lim)
        .all()
    : await env.DB.prepare(
        "SELECT t.id, t.order_id, t.task_type, t.execution_path, t.status, t.attempt_no, t.max_attempts, t.error_code, t.executor_id, t.created_at, t.updated_at FROM tasks t ORDER BY t.updated_at DESC LIMIT ?",
      )
        .bind(lim)
        .all();
  return ok({ tasks: rows.results });
}

/** §58 执行器列表：ID/Path/Version/Online/Last Heartbeat/Capabilities —— 无 token。 */
export async function adminExecutors(env: Env, _request: Request): Promise<Response> {
  const rows = await env.DB.prepare(
    "SELECT id, name, execution_path, version, capabilities_json, enabled, status, last_seen_at, created_at FROM executor_nodes ORDER BY execution_path, created_at DESC LIMIT 200",
  ).all();
  return ok({ executors: rows.results });
}

/** §58/§118：启用/禁用执行器（禁用后 pull 一律 DENY），写 EXECUTOR_DISABLED 审计。 */
export async function adminExecutorToggle(env: Env, request: Request, executorId: string): Promise<Response> {
  let body: Record<string, unknown>;
  try {
    body = (await request.json()) as Record<string, unknown>;
  } catch {
    return errorResponse(400, "VALIDATION_FAILED");
  }
  const enabled = body["enabled"];
  if (enabled !== 0 && enabled !== 1) return errorResponse(400, "VALIDATION_FAILED");
  const res = await env.DB.prepare(
    "UPDATE executor_nodes SET enabled=?, updated_at=? WHERE id=?",
  )
    .bind(enabled, Date.now(), executorId)
    .run();
  if (!res.meta.changes) return errorResponse(404, "NOT_FOUND");
  const session = await import("../auth/session-service").then((m) => m.getSessionUser(env.DB, request));
  await env.DB.prepare(
    "INSERT INTO audit_events(id,actor_type,actor_id,event_type,entity_type,entity_id,created_at) VALUES(?,?,?,?,?,?,?)",
  )
    .bind(
      crypto.randomUUID(),
      "admin",
      session?.id ?? null,
      enabled ? "EXECUTOR_ENABLED" : "EXECUTOR_DISABLED",
      "executor",
      executorId,
      Date.now(),
    )
    .run();
  return ok({ executor_id: executorId, enabled });
}

/* ---- stage-cloud-37 — §58 商品上架管理 ---- */

/** §58 商品列表：全字段（products 无敏感列）。 */
export async function adminProducts(env: Env, _request: Request): Promise<Response> {
  const rows = await env.DB.prepare(
    "SELECT id, code, name, description, platform, enabled, sort_order, config_json, created_at, updated_at FROM products ORDER BY sort_order, created_at DESC LIMIT 200",
  ).all();
  return ok({ products: rows.results });
}

const PRODUCT_PLATFORMS = ["chaoxing", "zhs", "zhsqr"];

/** §58 商品上架/编辑：按 code 幂等 upsert；写 PRODUCT_UPSERTED 审计。 */
export async function adminProductUpsert(env: Env, request: Request): Promise<Response> {
  let body: Record<string, unknown>;
  try {
    body = (await request.json()) as Record<string, unknown>;
  } catch {
    return errorResponse(400, "VALIDATION_FAILED");
  }
  const code = String(body["code"] ?? "").trim().toLowerCase();
  const name = String(body["name"] ?? "").trim();
  const platform = String(body["platform"] ?? "").trim().toLowerCase();
  const description = String(body["description"] ?? "").trim();
  const sortOrder = Number.isInteger(body["sort_order"]) ? (body["sort_order"] as number) : 0;
  let configJson = "{}";
  if (body["config_json"] !== undefined) {
    try {
      configJson = JSON.stringify(JSON.parse(String(body["config_json"])));
    } catch {
      return errorResponse(400, "VALIDATION_FAILED");
    }
  }
  if (!/^[a-z0-9_-]{2,32}$/.test(code) || !name || !PRODUCT_PLATFORMS.includes(platform)) {
    return errorResponse(400, "VALIDATION_FAILED");
  }
  const now = Date.now();
  await env.DB.prepare(
    `INSERT INTO products(id,code,name,description,platform,enabled,sort_order,config_json,created_at,updated_at)
     VALUES(?,?,?,?,?,1,?,?,?,?)
     ON CONFLICT(code) DO UPDATE SET name=excluded.name, description=excluded.description,
       platform=excluded.platform, sort_order=excluded.sort_order, config_json=excluded.config_json, updated_at=excluded.updated_at`,
  )
    .bind(`prod-${code}`, code, name, description, platform, sortOrder, configJson, now, now)
    .run();
  const session = await import("../auth/session-service").then((m) => m.getSessionUser(env.DB, request));
  await env.DB.prepare(
    "INSERT INTO audit_events(id,actor_type,actor_id,event_type,entity_type,entity_id,created_at) VALUES(?,?,?,?,?,?,?)",
  )
    .bind(crypto.randomUUID(), "admin", session?.id ?? null, "PRODUCT_UPSERTED", "product", code, now)
    .run();
  return ok({ code, name, platform, enabled: 1 });
}

/** §58 上架/下架（下架后前台橱窗与下单立即不可见），写审计。 */
export async function adminProductToggle(env: Env, request: Request, code: string): Promise<Response> {
  let body: Record<string, unknown>;
  try {
    body = (await request.json()) as Record<string, unknown>;
  } catch {
    return errorResponse(400, "VALIDATION_FAILED");
  }
  const enabled = body["enabled"];
  if (enabled !== 0 && enabled !== 1) return errorResponse(400, "VALIDATION_FAILED");
  const res = await env.DB.prepare("UPDATE products SET enabled=?, updated_at=? WHERE code=?")
    .bind(enabled, Date.now(), code)
    .run();
  if (!res.meta.changes) return errorResponse(404, "NOT_FOUND");
  const session = await import("../auth/session-service").then((m) => m.getSessionUser(env.DB, request));
  await env.DB.prepare(
    "INSERT INTO audit_events(id,actor_type,actor_id,event_type,entity_type,entity_id,created_at) VALUES(?,?,?,?,?,?,?)",
  )
    .bind(
      crypto.randomUUID(),
      "admin",
      session?.id ?? null,
      enabled ? "PRODUCT_ENABLED" : "PRODUCT_DISABLED",
      "product",
      code,
      Date.now(),
    )
    .run();
  return ok({ code, enabled });
}
