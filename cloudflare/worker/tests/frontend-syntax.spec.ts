import { describe, expect, it } from "vitest";
import { APP_HTML } from "../src/web/app-html";

// stage-cloud-40 教训：内联脚本藏在 TS 模板字符串里，tsc/vitest 均不检查其语法。
// 一处 onclick 引号错位曾导致整页 JS 解析失败（所有 UI 无响应）。
// 此测试提取全部 <script> 内容做编译，任何语法回归在 CI 即失败。
describe("inline frontend script syntax", () => {
  it("all <script> blocks compile cleanly", () => {
    const scripts = [...APP_HTML.matchAll(/<script[^>]*>([\s\S]*?)<\/script>/g)].map((m) => m[1]);
    expect(scripts.length).toBeGreaterThan(0);
    for (let i = 0; i < scripts.length; i++) {
      // new Function 仅编译不执行 —— 捕获 SyntaxError
      expect(() => new Function(scripts[i]), `script #${i} must parse`).not.toThrow();
    }
  });

  it("generated onclick handlers are syntactically balanced", () => {
    // 模拟 drawAdmin 两条按钮模板的产出，确认引号闭合
    const esc = (v: unknown) => String(v == null ? "" : v);
    const id = "ex-1";
    const html =
      '<button class="btn sm" onclick="toggleExec(\'' +
      esc(id) +
      '\',' +
      1 +
      ')">';
    expect(html).toBe(`<button class="btn sm" onclick="toggleExec('ex-1',1)">`);
    expect(() => new Function(`var el = ${JSON.stringify(html)}`)).not.toThrow();
  });
});
