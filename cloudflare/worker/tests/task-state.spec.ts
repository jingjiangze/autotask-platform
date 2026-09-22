/// <reference types="@cloudflare/vitest-plugin/types" />
import { describe, expect, it } from "vitest";
import {
  EXECUTOR_ERROR_CODES,
  buildTaskDispatch,
  isRetryable,
  validateTaskDispatch,
  type ExecutorErrorCode,
} from "../src/types/protocol";
import {
  canTransitionAttempt,
  canTransitionTask,
  isClaimable,
  isTerminalTask,
  transitionTask,
} from "../src/tasks/task-state";

// stage-cloud-07 验收（计划 §81）：
//   非法状态转换（succeeded→running / failed→running / canceled→succeeded）全部 DENY；
//   合法 queued→leased→running→succeeded PASS。

describe("stage-cloud-07 TaskStateMachine", () => {
  it("legal main path queued→leased→running→succeeded PASS", () => {
    expect(transitionTask("queued", "leased").ok).toBe(true);
    expect(transitionTask("leased", "running").ok).toBe(true);
    expect(transitionTask("running", "succeeded").ok).toBe(true);
  });

  it("legal recovery paths PASS (retry_wait→queued, stale_suspected→retry_wait)", () => {
    expect(transitionTask("running", "retry_wait").ok).toBe(true);
    expect(transitionTask("retry_wait", "queued").ok).toBe(true);
    expect(transitionTask("queued", "leased").ok).toBe(true);
    expect(transitionTask("leased", "stale_suspected").ok).toBe(true);
    expect(transitionTask("stale_suspected", "retry_wait").ok).toBe(true);
    expect(transitionTask("running", "cancel_requested").ok).toBe(true);
    expect(transitionTask("cancel_requested", "canceled").ok).toBe(true);
  });

  it("illegal transitions DENY (plan §81 acceptance)", () => {
    expect(transitionTask("succeeded", "running").ok).toBe(false);
    expect(transitionTask("failed", "running").ok).toBe(false);
    expect(transitionTask("canceled", "succeeded").ok).toBe(false);
    // 跳步与逆向
    expect(transitionTask("queued", "running").ok).toBe(false);
    expect(transitionTask("queued", "succeeded").ok).toBe(false);
    expect(transitionTask("running", "leased").ok).toBe(false);
    expect(transitionTask("leased", "queued").ok).toBe(false);
    expect(transitionTask("succeeded", "failed").ok).toBe(false);
    // 终态无出边、no-op
    expect(transitionTask("succeeded", "succeeded").ok).toBe(false);
    expect(transitionTask("canceled", "cancel_requested").ok).toBe(false);
  });

  it("every illegal transition carries a reason; claimable/terminal helpers correct", () => {
    const r = canTransitionTask("failed", "running");
    expect(r.ok).toBe(false);
    expect(r.reason).toContain("failed→running");
    expect(isClaimable("queued")).toBe(true);
    expect(isClaimable("running")).toBe(false);
    expect(isTerminalTask("retry_wait")).toBe(false);
    expect(isTerminalTask("canceled")).toBe(true);
  });

  it("attempt state machine: running→succeeded/failed PASS, no exits from terminal", () => {
    expect(canTransitionAttempt("running", "succeeded").ok).toBe(true);
    expect(canTransitionAttempt("running", "failed").ok).toBe(true);
    expect(canTransitionAttempt("succeeded", "running").ok).toBe(false);
    expect(canTransitionAttempt("failed", "succeeded").ok).toBe(false);
  });
});

describe("stage-cloud-07 ErrorCodes & retry classification", () => {
  it("retryable set matches plan §52 exactly", () => {
    const retryable: ExecutorErrorCode[] = [
      "NETWORK_TIMEOUT",
      "NETWORK_ERROR",
      "EXECUTOR_CRASH",
      "PROCESS_TIMEOUT",
    ];
    const nonRetryable: ExecutorErrorCode[] = [
      "AUTH_FAILED",
      "PARAM_INVALID",
      "CAPTCHA_REQUIRED",
      "THIRD_PARTY_LOGIN_FAILED",
      "CANCELED",
      "THIRD_PARTY_RATE_LIMIT",
      "STALE_LEASE",
      "TASK_INVALID",
      "EXECUTOR_UNAVAILABLE",
      "CLOUDFLARE_ERROR",
      "UNKNOWN",
    ];
    for (const c of retryable) expect(isRetryable(c), c).toBe(true);
    for (const c of nonRetryable) expect(isRetryable(c), c).toBe(false);
    // 词表完整性：分类覆盖全部 15 个错误码
    expect(retryable.length + nonRetryable.length).toBe(EXECUTOR_ERROR_CODES.length);
  });
});

describe("stage-cloud-07 TaskDispatch contract", () => {
  const valid = (): ReturnType<typeof buildTaskDispatch> =>
    buildTaskDispatch({
      task_id: "t1",
      order_id: "o1",
      attempt_id: "a1",
      attempt_no: 1,
      execution_path: "local",
      task_type: "chaoxing.run",
      lease_id: "l1",
      lease_expires_at: Date.now() + 120_000,
      issued_at: Date.now(),
      trace_id: "tr1",
      required_capabilities: ["chaoxing"],
      payload: { courses: "264628209", speed: 2.0 },
    });

  it("valid dispatch passes with zero violations", () => {
    expect(validateTaskDispatch(valid())).toEqual([]);
  });

  it("structure violations detected", () => {
    const bad = valid() as unknown as Record<string, unknown>;
    bad["protocol"] = "autotask.executor/v2";
    delete bad["lease_id"];
    bad["execution_path"] = "moon";
    bad["attempt_no"] = 0;
    const v = validateTaskDispatch(bad);
    const fields = v.map((x) => x.field).join(",");
    expect(fields).toContain("protocol");
    expect(fields).toContain("lease_id");
    expect(fields).toContain("execution_path");
    expect(fields).toContain("attempt_no");
  });

  it("payload credential redline (plan §33): password/cookie/token keys DENY at any depth", () => {
    const leaks = [
      { password: "x" },
      { wk_password: "x" },
      { opts: { COOKIE: "sid=1" } },
      { creds: [{ api_key: "k" }] },
      { zhs_cookie: "abc" },
    ];
    for (const payload of leaks) {
      const msg = { ...valid(), payload };
      const v = validateTaskDispatch(msg);
      expect(v.length, JSON.stringify(payload)).toBeGreaterThan(0);
      expect(v.every((x) => x.reason.includes("forbidden"))).toBe(true);
    }
    // 合法业务参数不受影响
    expect(validateTaskDispatch({ ...valid(), payload: { courses: "1", speed: 2 } })).toEqual([]);
  });

  it("non-object input rejected", () => {
    expect(validateTaskDispatch(null).length).toBeGreaterThan(0);
    expect(validateTaskDispatch("dispatch").length).toBeGreaterThan(0);
  });
});
