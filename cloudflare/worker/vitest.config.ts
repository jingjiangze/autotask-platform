import { defineConfig } from "vitest/config";
import { cloudflareTest } from "@cloudflare/vitest-plugin";

// stage-cloud-03：测试运行在 Workers 运行时内（Miniflare），按计划 §74
// 使用官方 @cloudflare/vitest-plugin（不用已被替代的 vitest-pool-workers）。
export default defineConfig({
  plugins: [
    cloudflareTest({
      main: "src/index.ts",
      wrangler: { configPath: "./wrangler.jsonc" },
    }),
  ],
  test: {
    include: ["tests/**/*.spec.ts"],
  },
});
