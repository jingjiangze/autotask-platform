/**
 * stage-cloud-07 — Task Contract（计划 §32/§33/§50/§52/§73）
 *
 * Executor 协议 v1：类型、状态词表、错误码、可重试分类、payload 凭据红线。
 * Python Executor 与 Worker 共用同一契约（contracts/ 侧后续导出 JSON Schema）。
 */

// ---- Execution Path（计划 §60：path 固定规则）----
export const EXECUTION_PATHS = ["local", "internal", "external"] as const;
export type ExecutionPath = (typeof EXECUTION_PATHS)[number];

// ---- Task 状态机状态（计划 §23/§46）----
export const TASK_STATUSES = [
  "queued",
  "leased",
  "running",
  "succeeded",
  "failed",
  "retry_wait",
  "stale_suspected",
  "cancel_requested",
  "canceled",
] as const;
export type TaskStatus = (typeof TASK_STATUSES)[number];

// ---- Attempt 状态（计划 §24）----
export const ATTEMPT_STATUSES = ["running", "succeeded", "failed"] as const;
export type AttemptStatus = (typeof ATTEMPT_STATUSES)[number];

// ---- 错误码（计划 §50）----
export const EXECUTOR_ERROR_CODES = [
  "AUTH_FAILED",
  "TASK_INVALID",
  "PARAM_INVALID",
  "THIRD_PARTY_LOGIN_FAILED",
  "THIRD_PARTY_RATE_LIMIT",
  "CAPTCHA_REQUIRED",
  "NETWORK_TIMEOUT",
  "NETWORK_ERROR",
  "EXECUTOR_CRASH",
  "EXECUTOR_UNAVAILABLE",
  "PROCESS_TIMEOUT",
  "CANCELED",
  "STALE_LEASE",
  "CLOUDFLARE_ERROR",
  "UNKNOWN",
] as const;
export type ExecutorErrorCode = (typeof EXECUTOR_ERROR_CODES)[number];

/** 可自动重试分类（计划 §52）：仅临时性错误；其余一律不自动重试。 */
const RETRYABLE_CODES: ReadonlySet<ExecutorErrorCode> = new Set([
  "NETWORK_TIMEOUT",
  "NETWORK_ERROR",
  "EXECUTOR_CRASH",
  "PROCESS_TIMEOUT",
] as const);

export function isRetryable(code: ExecutorErrorCode): boolean {
  return RETRYABLE_CODES.has(code);
}

// ---- Payload 凭据红线（计划 §33）----
const FORBIDDEN_PAYLOAD_KEYS = [
  "password",
  "passwd",
  "pwd",
  "cookie",
  "cookies",
  "session",
  "token",
  "secret",
  "api_key",
  "apikey",
  "authorization",
] as const;

function keyForbidden(key: string): boolean {
  const k = key.toLowerCase();
  return FORBIDDEN_PAYLOAD_KEYS.some((f) => k === f || k.endsWith(`_${f}`) || k.endsWith(f));
}

function deepScanForbidden(value: unknown, path: string): string[] {
  if (value === null || typeof value !== "object") return [];
  const hits: string[] = [];
  for (const [k, v] of Object.entries(value as Record<string, unknown>)) {
    const p = path ? `${path}.${k}` : k;
    if (keyForbidden(k)) hits.push(p);
    if (typeof v === "object" && v !== null) hits.push(...deepScanForbidden(v, p));
  }
  return hits;
}

// ---- Task Dispatch 消息（计划 §32）----
export interface TaskDispatchMessage {
  protocol: "autotask.executor/v1";
  message_type: "task.dispatch";
  task_id: string;
  order_id: string;
  attempt_id: string;
  attempt_no: number;
  execution_path: ExecutionPath;
  task_type: string;
  lease_id: string;
  lease_expires_at: number;
  issued_at: number;
  trace_id: string;
  required_capabilities: string[];
  payload: Record<string, unknown>;
}

export interface ContractViolation {
  field: string;
  reason: string;
}

export function buildTaskDispatch(
  fields: Omit<TaskDispatchMessage, "protocol" | "message_type">,
): TaskDispatchMessage {
  return { protocol: "autotask.executor/v1", message_type: "task.dispatch", ...fields };
}

/** Dispatch 消息契约校验：结构完整 + payload 无凭据。 */
export function validateTaskDispatch(msg: unknown): ContractViolation[] {
  const v: ContractViolation[] = [];
  if (typeof msg !== "object" || msg === null) {
    return [{ field: "$", reason: "message must be an object" }];
  }
  const m = msg as Record<string, unknown>;
  if (m["protocol"] !== "autotask.executor/v1") {
    v.push({ field: "protocol", reason: "must be autotask.executor/v1" });
  }
  if (m["message_type"] !== "task.dispatch") {
    v.push({ field: "message_type", reason: "must be task.dispatch" });
  }
  for (const f of [
    "task_id",
    "order_id",
    "attempt_id",
    "lease_id",
    "trace_id",
    "task_type",
  ] as const) {
    if (typeof m[f] !== "string" || (m[f] as string).length === 0) {
      v.push({ field: f, reason: "non-empty string required" });
    }
  }
  if (typeof m["attempt_no"] !== "number" || (m["attempt_no"] as number) < 1) {
    v.push({ field: "attempt_no", reason: "integer >= 1 required" });
  }
  if (!EXECUTION_PATHS.includes(m["execution_path"] as ExecutionPath)) {
    v.push({ field: "execution_path", reason: "one of local/internal/external" });
  }
  if (
    typeof m["lease_expires_at"] !== "number" ||
    typeof m["issued_at"] !== "number" ||
    m["lease_expires_at"] <= 0 ||
    m["issued_at"] <= 0
  ) {
    v.push({ field: "lease_expires_at/issued_at", reason: "epoch ms required" });
  }
  if (!Array.isArray(m["required_capabilities"])) {
    v.push({ field: "required_capabilities", reason: "string[] required" });
  }
  const hits = deepScanForbidden(m["payload"], "");
  for (const h of hits) {
    v.push({ field: `payload.${h}`, reason: "credential-like keys are forbidden in payload (plan §33)" });
  }
  return v;
}
