/**
 * stage-cloud-03/05 — Cloudflare 中央控制面
 *
 * /health + /api/v1/auth/*（stage-cloud-05）。
 * 后续 stage 在 router 注册：orders / tasks / /api/executor/v1/*。
 */

import type { Env } from "./auth/auth-service";
import { errorResponse } from "./errors";
import { route } from "./router";
import { checkQuota } from "./tasks/quota-service";
import { PathCoordinator } from "./coordination/path-coordinator";
import { APP_HTML } from "./web/app-html";

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
  // stage-cloud-24：Cron */1 —— 过期会话清理 + 终态残留回收（§87，占用 1/5 Free Cron）
  async scheduled(_controller: ScheduledController, env: Env, ctx: ExecutionContext): Promise<void> {
    ctx.waitUntil(
      env.DB.prepare("DELETE FROM auth_sessions WHERE expires_at < ? OR revoked_at IS NOT NULL AND revoked_at < ?")
        .bind(Date.now() - 24 * 3600 * 1000, Date.now() - 24 * 3600 * 1000)
        .run(),
    );
    // stage-cloud-35 — §12：额度守卫每小时整点跑一次（cron 本身每分钟）
    if (_controller.cron === "0 * * * *") {
      ctx.waitUntil(
        checkQuota(env).catch(() => undefined),
      );
    }
  },

  async fetch(request: Request, env: Env, _ctx: ExecutionContext): Promise<Response> {
    const url = new URL(request.url);
    const { pathname } = url;

    // stage-cloud-38b — 站点收敛：order.jiangjiangze.icu 是唯一用户入口。
    // autotask（执行器/admin API 基地址）与 chaxun 的浏览器 UI 访问 → 302 到 order；
    // /api/*、/health、非 HTML 请求（执行器/脚本/E2E）原样处理，不受影响。
    const host = url.hostname;
    if (
      host !== "order.jiangjiangze.icu" &&
      request.method === "GET" &&
      !pathname.startsWith("/api/") &&
      pathname !== "/health" &&
      (request.headers.get("Accept") ?? "").includes("text/html")
    ) {
      return Response.redirect("https://order.jiangjiangze.icu/", 302);
    }

    if (pathname === "/health") {
      if (request.method !== "GET") {
        return errorResponse(405, "METHOD_NOT_ALLOWED");
      }
      return handleHealth();
    }

    const api = await route(request, env);
    if (api) return api;

    // stage-cloud-27：Worker 托管前端 —— 非 /api 的 GET（含 / 与无扩展名路径）回 SPA，
    // 使 Executor 回调与浏览器共用同一域；API 与 /health 不受影响。
    if (request.method === "GET" && !pathname.startsWith("/api/") && !pathname.includes(".")) {
      return new Response(APP_HTML, {
        headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-cache" },
      });
    }

    return errorResponse(404, "NOT_FOUND");
  },
} satisfies ExportedHandler<Env>;
