/**
 * stage-cloud-07 — TaskStateMachine（计划 §23/§46）
 *
 * 所有任务状态转换必须经由本机；任何绕过（直接 UPDATE tasks.status）
 * 都视为契约违规。终态无出边。
 */

import type { AttemptStatus, TaskStatus } from "../types/protocol";

// 计划 §23 + §46（stale_suspected 恢复路径）
const TASK_TRANSITIONS: Readonly<Record<TaskStatus, readonly TaskStatus[]>> = {
  queued: ["leased", "canceled"],
  leased: ["running", "stale_suspected", "canceled"],
  running: ["succeeded", "failed", "retry_wait", "cancel_requested"],
  retry_wait: ["queued", "canceled"],
  stale_suspected: ["retry_wait", "canceled"],
  cancel_requested: ["canceled"],
  succeeded: [],
  failed: [],
  canceled: [],
};

const ATTEMPT_TRANSITIONS: Readonly<Record<AttemptStatus, readonly AttemptStatus[]>> = {
  running: ["succeeded", "failed"],
  succeeded: [],
  failed: [],
};

export interface TransitionResult {
  ok: boolean;
  reason?: string;
}

function checkTransition(
  table: Readonly<Record<string, readonly string[]>>,
  from: string,
  to: string,
): TransitionResult {
  if (from === to) {
    return { ok: false, reason: `no-op transition ${from}→${to}` };
  }
  const allowed = table[from];
  if (!allowed) {
    return { ok: false, reason: `unknown status: ${from}` };
  }
  if (!allowed.includes(to)) {
    return { ok: false, reason: `illegal transition ${from}→${to}` };
  }
  return { ok: true };
}

export function canTransitionTask(from: TaskStatus, to: TaskStatus): TransitionResult {
  return checkTransition(TASK_TRANSITIONS, from, to);
}

export function transitionTask(from: TaskStatus, to: TaskStatus): TransitionResult {
  return canTransitionTask(from, to);
}

export function canTransitionAttempt(from: AttemptStatus, to: AttemptStatus): TransitionResult {
  return checkTransition(ATTEMPT_TRANSITIONS, from, to);
}

/** 给定当前状态，判断是否允许执行新的 lease claim（计划 §44/§46）。
 *  只有 queued（及重试回队的 queued）可被 claim。 */
export function isClaimable(status: TaskStatus): boolean {
  return status === "queued";
}

/** 终态判断（重试等待不是终态）。 */
export function isTerminalTask(status: TaskStatus): boolean {
  return status === "succeeded" || status === "failed" || status === "canceled";
}
