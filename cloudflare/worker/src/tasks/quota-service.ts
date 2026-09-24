/**
 * stage-cloud-35 — §12/§98：免费额度内部预算守卫。
 *
 * Cron 每小时执行一次：估算当日 D1 写量（audit_events + tasks + task_attempts
 * 当日行数），对照 §12 内部预算（60K writes/day）：
 *   - ≥70% → audit QUOTA_70（每自然日至多一次）
 *   - ≥85% → audit QUOTA_85（每自然日至多一次）
 * 只告警不熔断（§12：85% 停新增高频非核心功能是运营决策，非自动行为）。
 */

import type { Env } from "../auth/auth-service";

const DAILY_WRITE_BUDGET = 60_000; // §12 内部预算（非官方上限）
const START_OF_DAY = (): number => {
  const d = new Date();
  d.setUTCHours(0, 0, 0, 0);
  return d.getTime();
};

export async function checkQuota(
  env: Env,
  budgetOverride?: number,
): Promise<{ used: number; ratio: number; level: string }> {
  const budget = budgetOverride ?? DAILY_WRITE_BUDGET;
  const dayStart = START_OF_DAY();
  const row = await env.DB.prepare(
    `SELECT
       (SELECT COUNT(*) FROM audit_events WHERE created_at >= ?1) +
       (SELECT COUNT(*) FROM tasks WHERE updated_at >= ?1) +
       (SELECT COUNT(*) FROM task_attempts WHERE updated_at >= ?1) AS used`,
  )
    .bind(dayStart)
    .first<{ used: number }>();
  const used = row?.used ?? 0;
  const ratio = used / budget;
  let level = "ok";
  if (ratio >= 0.85) level = "QUOTA_85";
  else if (ratio >= 0.7) level = "QUOTA_70";
  if (level !== "ok") {
    // 每自然日至多一条告警（幂等：同日同级别不重复写）
    const seen = await env.DB.prepare(
      "SELECT id FROM audit_events WHERE event_type=? AND created_at >= ? LIMIT 1",
    )
      .bind(level, dayStart)
      .first();
    if (!seen) {
      await env.DB.prepare(
        "INSERT INTO audit_events(id,actor_type,actor_id,event_type,entity_type,entity_id,metadata_json,created_at) VALUES(?,?,?,?,?,?,?,?)",
      )
        .bind(crypto.randomUUID(), "system", null, level, "quota", "d1-writes",
          JSON.stringify({ used, budget, ratio: Number(ratio.toFixed(3)) }),
          Date.now())
        .run();
    }
  }
  return { used, ratio, level };
}
