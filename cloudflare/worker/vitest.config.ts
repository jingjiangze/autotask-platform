import { defineConfig } from "vitest/config";
import { cloudflareTest } from "@cloudflare/vitest-plugin";

// stage-cloud-03：测试运行在 Workers 运行时内（Miniflare），按计划 §74
// 使用官方 @cloudflare/vitest-plugin（不用已被替代的 vitest-pool-workers）。
export default defineConfig({
  plugins: [
    cloudflareTest({
      main: "src/index.ts",
      wrangler: { configPath: "./wrangler.jsonc" },
      // stage-cloud-06/08：测试专用绑定（生产通过 wrangler secret 注入）
      miniflare: {
        bindings: {
          LEGACY_HMAC_SECRET: "test-legacy-secret",
          EXECUTOR_BOOTSTRAP_TOKEN: "test-bootstrap-token",
          // stage-cloud-11：32 字节测试密钥（base64）
          CREDENTIAL_KEY: btoa(
            String.fromCharCode(...crypto.getRandomValues(new Uint8Array(32))),
          ),
        },
      },
    }),
  ],
  test: {
    include: ["tests/**/*.spec.ts"],
    globalSetup: ["./tests/global-setup.ts"],
  },
});
