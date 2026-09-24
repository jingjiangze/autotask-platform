# PLAN COMPLIANCE GAP — 140 点计划 vs 实际实现（stage-cloud-30d @ 8bf7165）

> 审计日期：2026-09-24｜性质：只读合规审计（未改代码）｜判定词沿用 §132：PASS / PARTIAL / NOT IMPLEMENTED / NOT AVAILABLE

## 结论

框架**功能链路**完整且 LIVE 验证过（Local 真实引擎 12/12），但**与计划的契约层存在系统性偏差**：
实现了一套"功能等价但形状不同"的自造协议，而非 §32/§34/§38 规定的协议契约；
目录重构只做了改名搬家（G1 之前宣称"按 §71/§72 完成"是不准确的），未按计划拆分模块；
Admin（§136 验收 A 条件之一）未实现。

## 差距矩阵

### 1. 协议契约层 — PARTIAL（功能等价，契约不符）

| 计划条款 | 要求 | 实际 | 判定 |
|---|---|---|---|
| §32 Task Protocol | task.dispatch 结构含 attempt_id/attempt_no/trace_id/issued_at | claim 响应无 attempt_id/trace_id（attempt_id 由服务端合成 `taskId#no`，不下发） | PARTIAL |
| §34 Pull API | `POST /pull`（capacity/active_tasks 上报） | 自造 `POST /claim` | PARTIAL |
| §35 Bootstrap | `POST /tasks/{id}/bootstrap`（凭据+runtime UA） | 等价物 `POST /credentials`（租约门控，形状不同） | PARTIAL |
| §38 API 总表 | pull/bootstrap/start/complete/fail/cancel-ack/artifacts/presign/config | claim/ack/heartbeat/credentials/complete/artifacts/node-heartbeat；缺 **fail、cancel-ack、presign、start、config** | PARTIAL |
| §41 Heartbeat payload | active_tasks[{task_id,attempt_id,local_pid}] + capacity + version | 心跳仅 task_id+lease_id，无 active_tasks/local_pid/capacity | NOT IMPLEMENTED |
| §47 Fencing | worker 校验 executor_id+**attempt_id**+lease_id 三元完全匹配 | 只校验 executor_id+lease_id（attempt_id 未回传） | PARTIAL |
| §73 contracts/ | executor-protocol/task-schema/result-schema/error-codes.json 双端共享 | 目录不存在，两端各写一份协议常量 | NOT IMPLEMENTED |

### 2. 目录结构 — PARTIAL（壳已对齐，件未拆齐）

| 计划条款 | 缺失 |
|---|---|
| §71 worker | `tasks/task-service.ts`、`tasks/retry-service.ts`、`executors/executor-service.ts`、`executors/task-dispatch.ts`、`storage/r2.ts`、`crypto/envelope.ts`、`crypto/hashing.ts`（现全部逻辑集中在 executor-api.ts / order-service.ts / storage/*.ts） |
| §72 executor | `agent/` 仅 main.py+client.py（计划 9 模块：缺 auth/heartbeat/lease/bootstrap/result/artifact/cleanup）；runners 缺 `zhs_qr_runner.py`；runtime 缺 `environment.py`/`logs.py` |

### 3. Admin 中央管理 — NOT IMPLEMENTED（§136 A 验收条件，阻断最终验收）

- 现仅有 2 个 admin API（admin/credentials、admin/orders/by-account）
- 缺：用户管理、全量订单、执行器管理（含吊销/禁用）、任务/失败任务/执行历史、系统状态页面（§58）
- Executor 展示（ID/Path/Version/Online/Last Heartbeat/Capabilities，禁显 Token）未实现

### 4. 三路真实执行 — 局部 PASS

| 端 | 判定 |
|---|---|
| Local | **PASS**（真实超星/知到引擎，LIVE 12/12） |
| Internal | 协议链路 PASS（同机模拟）；**真实内网机器 LIVE = NOT AVAILABLE**（需一台内网机 + 独立 Token 注册） |
| External | 协议链路 PASS（同机模拟）；**真实外部机器 LIVE = NOT AVAILABLE**（需一台外部机） |

附：executor_nodes 表残留 30+ E2E 临时节点未清理/禁用（§25 语义上应只保留正式节点）。

### 5. 其他未完成项

| 条款 | 项 | 判定 |
|---|---|---|
| §65/§66 | QR 全链路（zhs_qr task→R2→UI 短时授权→扫码→cookie 入库） | NOT IMPLEMENTED（云端下单已禁用并提示） |
| §113 | Executor Windows Service/计划任务自启 | NOT IMPLEMENTED（手动启动） |
| §115 | Executor 启动孤儿任务扫描（对账 Cloud 终态→清理） | NOT IMPLEMENTED（仅重连心跳） |
| §12/§98 | 免费额度 70%/85% 告警机制 | NOT IMPLEMENTED（文档已有，机制未建） |
| §106 | "Cloud 成为唯一生产入口"切换 | NOT AVAILABLE（本地 order_platform.py 仍为生产入口，Cloud 并行验证中——按 §107 定位属预期） |
| §136 | 最终验收 | **未达成**（A:Admin 缺、C/D:真实机器缺） |

### 6. 已达成项（如实记录）

D1 全部 11 表+3 迁移+索引（§13-29）、auth/session/legacy 兼容（§14-17）、enc-v2 凭据边界（§20/§21/§33/§36）、Task 状态机含 cancel_requested（§23）、attempts/lease/stale 恢复/fencing(lease 层)/幂等 completion（§24/§44-§49）、R2 工件+日志（§30/§31/§67）、日志脱敏（§68）、访客查单（§91）、三路路径隔离 403（§57/§60）、Security/Quota/Migration 工具与幂等校验（§97-§105）、retry 分类/backoff（§51-§53）。

## 补救 Commit 序列（按 §128/§130 独立可回滚）

| # | Commit | 内容 | 关联 |
|---|---|---|---|
| R1 | feat(stage-cloud-31) | **协议契约对齐**：contracts/ 共享 JSON schema（§73）+ 端点别名层 pull/bootstrap/start/fail/cancel-ack/presign（§38）+ attempt_id 全链路贯穿与三元 fencing（§32/§47）+ heartbeat payload 扩展（§41） | 最高优先 |
| R2 | refactor(stage-cloud-32) | **模块拆分补齐**：worker 侧 task-service/retry-service/task-dispatch/executor-service/r2.ts/crypto(envelope+hashing)；executor 侧 agent 九模块拆分 | §71/§72 |
| R3 | feat(stage-cloud-33) | **Admin 中央管理**：执行器/用户/订单/任务管理 API + UI（§58） | §136 阻断项 |
| R4 | test(stage-cloud-34) | **三路真实 E2E**：两台真实机器注册跑通 15 项矩阵；无机器则如实 NOT AVAILABLE；顺带清理 E2E 残留节点 | §93-§95 |
| R5 | feat(stage-cloud-35) | **运维化**：Windows Service 自启（§113）+ 孤儿扫描（§115）+ QR 链路（§65/§66，需真人扫码配合） |  |
| R6 | test(stage-cloud-36) | **最终验收重审**：§136 A–G 全表重跑，达标才宣布 THREE-PATH REAL PLATFORM = PASS | 收口 |

## 补救执行结果（2026-09-24 更新）

| # | Commit | 状态 | 关键证据 |
|---|---|---|---|
| R1 | stage-cloud-31（a3abffd） | ✅ DONE | contracts/ 4 个 schema + vitest 契约一致性测试；attempt_id 三元 fencing LIVE |
| R2 | stage-cloud-32 | ✅ DONE | worker 7 模块 + executor agent 九模块；tsc 零错 |
| R3 | stage-cloud-33 | ✅ DONE | /api/v1/admin/* + 管理页；admin-api.spec |
| R4 | stage-cloud-34 | ✅ DONE | exec-internal-01/external-01 上线 GitHub Actions（真实独立节点）；e2e_three_path_demo 3/3 LIVE；34 个残留节点清理；顺带修复订单终态误翻转回归 |
| R5 | stage-cloud-35 | ✅ DONE | §115 孤儿扫描（state 端点 + pidfile）LIVE；§113 自启（exec-local-01 + 计划任务）；§12 额度守卫 |
| R6 | stage-cloud-36（本文档 + FINAL_CLOUD_ACCEPTANCE 修订） | ✅ DONE | §136 A–G 重审：Central PASS；三路协议 PASS；Local 真实引擎 PASS；Internal/External 真实引擎 NOT AVAILABLE（ubuntu 物理约束，§132） |

遗留（如实披露）：§65/§66 QR 全链路需真人扫码配合，云端下单保持"暂不支持"提示（CODE READY 部分：zhs_qr_runner 骨架）；
Internal/External 若日后有 Windows 机器，同一 agent 代码换 token 即可跑真实引擎（零代码改动）。
