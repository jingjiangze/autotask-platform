/**
 * stage-cloud-03/05 — Cloudflare 中央控制面
 *
 * /health + /api/v1/auth/*（stage-cloud-05）。
 * 后续 stage 在 router 注册：orders / tasks / /api/executor/v1/*。
 */

import type { Env } from "./auth/auth-service";
import { errorResponse } from "./errors";
import { route } from "./router";
import { PathCoordinator } from "./coordinator/path-coordinator";

export type { Env };
export { PathCoordinator };

function handleHealth(): Response {
  return Response.json({
    ok: true,
    service: "autotask-central",
    time: Date.now(),
  });
}

export default {
  async fetch(request: Request, env: Env, _ctx: ExecutionContext): Promise<Response> {
    const { pathname } = new URL(request.url);

    if (pathname === "/health") {
      if (request.method !== "GET") {
        return errorResponse(405, "METHOD_NOT_ALLOWED");
      }
      return handleHealth();
    }

    const api = await route(request, env);
    if (api) return api;

    return errorResponse(404, "NOT_FOUND");
  },
} satisfies ExportedHandler<Env>;
