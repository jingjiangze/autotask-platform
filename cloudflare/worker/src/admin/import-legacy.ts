/**
 * stage-cloud-38 — §102/§106：本地平台一次性导入（Admin 门控）。
 *
 * 迁移工具（本机）负责：legacy enc:v1 本地解密 → enc:v2 重加密 → 密文提交；
 * 本端只做幂等入库（INSERT OR IGNORE by id），不做任何解密。
 * 密码兼容：legacy-hmac-v1 原样入库（§17），用户首次登录后可升级。
 */

import type { Env } from "../auth/auth-service";
import { errorResponse } from "../errors";

interface LegacyUser {
  id: string;
  username: string;
  pw_hash: string;
  is_admin: number;
  created_at: number;
}

interface LegacyOrder {
  id: string;
  user_id: string;
  platform: string;
  account: string;
  courses: string;
  status: string;
  created_at: number;
}

interface LegacyCredential {
  order_id: string;
  credential_type: string;
  ciphertext: string;
  encryption_version: string;
}

const STATUS_MAP: Record<string, string> = {
  done: "succeeded",
  succeeded: "succeeded",
  failed: "failed",
  canceled: "canceled",
};

const PLATFORM_PRODUCT: Record<string, string> = {
  chaoxing: "cx_video",
  zhs: "zhs_video",
  zhsqr: "zhs_qr",
};

export async function adminImportLegacy(env: Env, request: Request): Promise<Response> {
  let body: { users?: LegacyUser[]; orders?: LegacyOrder[]; credentials?: LegacyCredential[] };
  try {
    body = (await request.json()) as typeof body;
  } catch {
    return errorResponse(400, "VALIDATION_FAILED");
  }
  const users = Array.isArray(body.users) ? body.users : [];
  const orders = Array.isArray(body.orders) ? body.orders : [];
  const credentials = Array.isArray(body.credentials) ? body.credentials : [];
  if (users.length + orders.length + credentials.length === 0) {
    return errorResponse(400, "VALIDATION_FAILED");
  }

  const now = Date.now();
  let usersImported = 0;
  let usersSkipped = 0;
  for (const u of users) {
    if (!u?.id || !u.username || !u.pw_hash) {
      usersSkipped++;
      continue;
    }
    const res = await env.DB.prepare(
      `INSERT INTO users(id,username,password_hash,password_scheme,role,status,registration_source,created_at,updated_at)
       VALUES(?,?,?,?,?, 'active','legacy-import',?,?)
       ON CONFLICT(id) DO NOTHING`,
    )
      .bind(u.id, u.username, u.pw_hash, "legacy-hmac-v1", u.is_admin ? "admin" : "user", u.created_at || now, now)
      .run();
    if (res.meta.changes) usersImported++;
    else usersSkipped++;
  }

  let ordersImported = 0;
  let ordersSkipped = 0;
  const orderErrors: string[] = [];
  for (const o of orders) {
    const status = STATUS_MAP[o?.status ?? ""];
    const productCode = PLATFORM_PRODUCT[o?.platform ?? ""];
    if (!o?.id || !o.user_id || !status || !productCode) {
      orderErrors.push(`${o?.id ?? "?"}: invalid fields`);
      ordersSkipped++;
      continue;
    }
    // user_id 外键必须已存在（先导 users）
    const user = await env.DB.prepare("SELECT id FROM users WHERE id=?").bind(o.user_id).first();
    if (!user) {
      orderErrors.push(`${o.id}: user ${o.user_id} missing`);
      ordersSkipped++;
      continue;
    }
    const res = await env.DB.prepare(
      `INSERT INTO orders(id,user_id,product_code,platform,courses,status,note,source,created_at,updated_at)
       VALUES(?,?,?,?,?,?, '历史订单（本地平台迁移）','legacy',?,?)
       ON CONFLICT(id) DO NOTHING`,
    )
      .bind(o.id, o.user_id, productCode, o.platform, o.courses || "", status, o.created_at || now, now)
      .run();
    if (res.meta.changes) ordersImported++;
    else ordersSkipped++;
  }

  let credentialsImported = 0;
  for (const c of credentials) {
    if (!c?.order_id || !c.credential_type || !c.ciphertext || c.encryption_version !== "enc-v2") continue;
    // 幂等 id：cred-legacy-{order_id}-{type}
    await env.DB.prepare(
      `INSERT INTO order_credentials(id,order_id,credential_type,ciphertext,encryption_version,created_at,updated_at)
       VALUES(?,?,?,?,?,?,?)
       ON CONFLICT(id) DO NOTHING`,
    )
      .bind(`cred-legacy-${c.order_id}-${c.credential_type}`, c.order_id, c.credential_type, c.ciphertext, "enc-v2", now, now)
      .run();
    credentialsImported++;
  }

  await env.DB.prepare(
    "INSERT INTO audit_events(id,actor_type,actor_id,event_type,entity_type,entity_id,metadata_json,created_at) VALUES(?,?,?,?,?,?,?,?)",
  )
    .bind(
      crypto.randomUUID(),
      "admin",
      null,
      "LEGACY_IMPORT",
      "platform",
      "order_platform",
      JSON.stringify({ users: usersImported, orders: ordersImported, credentials: credentialsImported }),
      now,
    )
    .run();

  return Response.json({
    ok: true,
    users: { imported: usersImported, skipped: usersSkipped },
    orders: { imported: ordersImported, skipped: ordersSkipped, errors: orderErrors.slice(0, 10) },
    credentials: { imported: credentialsImported },
  });
}
