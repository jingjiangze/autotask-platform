/**
 * stage-cloud-31 — §71 tasks/retry-service：重试预算与退避（plan §51/§52/§53）。
 *
 * max_attempts=2（与本地 MAX_RETRY=1 语义一致）；
 * 自动重试仅限临时性错误（§52）；退避 exponential+jitter，上限 15min。
 */

/** §52：允许自动重试的错误分类（与 contracts/error-codes.json auto_retryable 同源）。 */
export const AUTO_RETRYABLE_CODES: ReadonlySet<string> = new Set([
  "NETWORK_TIMEOUT",
  "NETWORK_ERROR",
  "EXECUTOR_CRASH",
  "PROCESS_TIMEOUT",
]);

export function isAutoRetryable(code: string): boolean {
  return AUTO_RETRYABLE_CODES.has(code);
}

const BASE_DELAY_MS = 30 * 1000;
const CAP_MS = 15 * 60 * 1000;

/** §53：attempt 1 → 30s+jitter；attempt 2 → 60s+jitter；上限 15min。 */
export function backoffForAttempt(attemptNo: number, jitterRatio = 0.2): number {
  const n = Math.max(1, attemptNo);
  const base = Math.min(BASE_DELAY_MS * 2 ** (n - 1), CAP_MS);
  const jitter = base * jitterRatio * (Math.random() * 2 - 1);
  return Math.max(1000, Math.round(base + jitter));
}
