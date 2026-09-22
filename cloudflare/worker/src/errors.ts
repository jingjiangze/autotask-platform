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
