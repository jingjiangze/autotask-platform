/**
 * stage-cloud-03 — Cloudflare 中央控制面 bootstrap
 *
 * 本阶段只提供 GET /health 与统一错误契约骨架（计划 §77/§116）。
 * 后续 stage 在 router 上注册：/api/v1/*（用户）与 /api/executor/v1/*（执行器）。
 *
 * 错误契约（计划 §116）：所有 API 错误统一
 *   { ok: false, error: { code, message, request_id? } }
 * 禁止返回 stack trace / SQL / 本地路径 / Secret。
 */

export interface Env {
  // stage-cloud-04 起加入 DB: D1Database; stage-cloud-09 加入
  // PATH_COORDINATOR: DurableObjectNamespace; stage-cloud-14 加入 R2 绑定。
}

export const ERROR_CODES = {
  NOT_FOUND: 'Not found',
  METHOD_NOT_ALLOWED: 'Method not allowed',
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

function handleHealth(): Response {
  return Response.json({
    ok: true,
    service: 'autotask-central',
    stage: 'stage-cloud-03',
    time: Date.now(),
  });
}

export default {
  async fetch(request: Request, _env: Env, _ctx: ExecutionContext): Promise<Response> {
    const { pathname } = new URL(request.url);

    if (pathname === '/health') {
      if (request.method !== 'GET') {
        return errorResponse(405, 'METHOD_NOT_ALLOWED');
      }
      return handleHealth();
    }

    return errorResponse(404, 'NOT_FOUND');
  },
} satisfies ExportedHandler<Env>;
