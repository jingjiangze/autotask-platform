# 项目封盘说明（Sealed 2026-09-25）

> 本项目自 2026-09-22 起从本地 Flask+SQLite 平台渐进迁移至 Cloudflare Workers 云端架构，
> 于 2026-09-25 封盘。此后默认冻结功能开发，仅接受缺陷修复。

## 线上终态

| 项 | 状态 |
|---|---|
| 用户站点 | **https://order.jiangjiangze.icu**（唯一入口，Cloudflare Access 双重登录门） |
| 执行器 API | **https://executor.jiangjiangze.icu**（无 Access，机器流量专用） |
| autotask / chaxun 域名 | 已退役：任何请求返回 **410 DOMAIN_RETIRED** |
| Worker 部署版本 | `fa773cf7`（stage-cloud-42） |
| 数据库 | D1 `autotask-central`（中央真相）+ R2 `autotask-artifacts`（工件） |

## 本地终态（只保留执行链路）

- 计划任务 `AutotaskLocalExecutor`：本机执行器（exec-local-01，能力 chaoxing/zhs）
- 执行依赖：`D:\web\fuckCourse\`（刷课引擎）、`D:\web\tools_query_courses.py`（查课）
- 历史数据归档：`D:\web\orders\platform.db`（154 单已迁移云端，账号已回填）
- 旧本地平台（order_platform.py / health_manager / 相关隧道）已停用

## 功能清单（云端已实现）

1. Worker 托管前端 SPA（商品橱窗 / 下单向导 / 我的订单 / 访客查单 / 管理后台）
2. 凭据 enc-v2 加密托管，执行期租约解封（payload 永不带凭据）
3. 三路执行器调度（local/internal/external，Durable Objects 排队）
4. 超星视频刷取（chaoxing.run）+ 知到密码刷取（zhs.run）
5. **知到扫码刷取全链路**（zhs_qr.run：取码→qr_png 工件→App 扫码→cookies→查课→勾课执行）
6. 订单控制（暂停/恢复/优先级）、工件上传下载（owner 授权）、审计事件
7. 历史订单 154 条迁移完毕，账号信息已回填

## 质量门禁

- vitest 96/96（含内联前端脚本语法门禁：提取 APP_HTML 全部 `<script>` 编译校验）
- 部署前必须过 `npm run check`（tsc）+ `npm test`

## 关键约定（维护者必读）

- 前端 JS 藏在 `src/web/app-html.ts` 模板字符串里 —— **tsc 不检查它**，改动后务必本地浏览器冒烟
- 执行器基地址是 `executor.jiangjiangze.icu`（不是已退役的 autotask）
- 域名 `api.jiangjiangze.icu` 被另一个无关应用占用，勿动
- 部署：`cd cloudflare/worker && npx wrangler deploy`；推送走 `tools/push_via_api.py`

## 封盘时的提交链

38b 单站点收敛+商品加载可见化 → 39 域名退役+executor 域 → 40 浏览器兼容 →
41 前端三重真因修复+语法门禁 → 42 账号回填+知到扫码链路。
