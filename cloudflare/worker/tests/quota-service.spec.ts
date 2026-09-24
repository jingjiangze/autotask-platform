/// <reference types="@cloudflare/vitest-plugin/types" />
import { describe, expect, it } from "vitest";
import { applyMigrations, DB } from "./helpers";
import { checkQuota } from "../src/tasks/quota-service";

// stage-cloud-35 — §12：额度守卫（70%/85% 两级告警，幂等）。
describe("§12 quota guard", () => {
  it("低用量 → ok 且不写审计", async () => {
    await applyMigrations();
    const r = await checkQuota({ DB } as never, 100);
    expect(r.level).toBe("ok");
    const a = await DB.prepare("SELECT COUNT(*) AS n FROM audit_events WHERE event_type LIKE 'QUOTA%'").first<{ n: number }>();
    expect(a?.n).toBe(0);
  });

  it("≥85% → QUOTA_85 审计一次，重复调用不重复写（幂等）", async () => {
    await applyMigrations();
    const dayStart = new Date(); dayStart.setUTCHours(0, 0, 0, 0);
    await DB.prepare("INSERT INTO audit_events(id,actor_type,actor_id,event_type,entity_type,entity_id,created_at) VALUES('q-1','user',NULL,'USER_LOGIN','user',NULL,?)")
      .bind(dayStart.getTime() + 1000).run();
    const r = await checkQuota({ DB } as never, 1); // used=1 → ratio=1 → ≥85%
    expect(r.level).toBe("QUOTA_85");
    const r2 = await checkQuota({ DB } as never, 1);
    expect(r2.level).toBe("QUOTA_85");
    const a = await DB.prepare("SELECT COUNT(*) AS n FROM audit_events WHERE event_type='QUOTA_85'").first<{ n: number }>();
    expect(a?.n).toBe(1);
  });
});
