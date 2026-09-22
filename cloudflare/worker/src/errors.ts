/**
 * 统一错误契约（计划 §116）：
 *   { ok: false, error: { code, message, request_id? } }
 * 禁止返回 stack trace / SQL / 本地路径 / Secret。
 */

export const ERROR_CODES = {
  NOT_FOUND: "Not found",
  METHOD_NOT_ALLOWED: "Method not allowed",
  AUTH_REQUIRED: "Authentication required",
  INVALID_CREDENTIALS: "Invalid username or password",
  USERNAME_TAKEN: "Username already registered",
  VALIDATION_FAILED: "Request validation failed",
  // stage-cloud-08：Executor 注册 / 认证（§39/§40/§82）
  BOOTSTRAP_DISABLED: "Executor bootstrap registration is disabled",
  BOOTSTRAP_INVALID: "Invalid bootstrap token",
  EXECUTOR_EXISTS: "Executor id already registered",
  EXECUTOR_AUTH_FAILED: "Executor authentication failed",
  EXECUTOR_DISABLED: "Executor is disabled",
  EXECUTOR_PATH_MISMATCH: "Executor is not allowed on this execution path",
  // stage-cloud-11：凭据边界
  FORBIDDEN: "Insufficient permissions",
  LEASE_INVALID: "Unknown task or invalid lease",
  CREDENTIAL_DECRYPT_FAILED: "Credential decryption failed",
} as const;

export type ErrorCode = keyof typeof ERROR_CODES;

export function errorResponse(
  status: number,
  code: ErrorCode,
  requestId?: string,
): Response {
  return Response.json(
    { ok: false, error: { code, message: ERROR_CODES[code], request_id: requestId } },
    { status },
  );
}
