import { readD1Migrations } from "@cloudflare/vitest-plugin";

// 在 Node 上下文读取 D1 迁移并注入 Workers 运行时测试环境。
// 不能在测试文件内直接 import readD1Migrations —— 会把 miniflare/chalk
// 拖进 workerd bundle 导致 node:process 解析失败（vitest-plugin known-issues）。

declare module "vitest" {
  interface ProvidedContext {
    d1Migrations: { name: string; queries: string[] }[];
  }
}

export default async function ({
  provide,
}: {
  provide: (key: string, value: unknown) => void;
}): Promise<void> {
  const migrations = await readD1Migrations("./migrations");
  provide(
    "d1Migrations",
    migrations.map((m) => ({ name: m.name, queries: m.queries })),
  );
}
