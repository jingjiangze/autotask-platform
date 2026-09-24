# FINAL CLOUD ACCEPTANCE — 140 点计划最终验收（§136）

> 验收日期：2026-09-24（R1–R6 补救后第四次修订）｜ 依据：140 点计划 §136 A–G 条件 ｜ 判定词：PASS / FAIL / NOT AVAILABLE（§132）
> 线上入口：**https://autotask.jiangjiangze.icu**（Worker 自定义域；国内直连可达）
> 本次修订背景：合规审计（docs/PLAN_COMPLIANCE_GAP.md）发现"功能等价 ≠ 契约一致"后，
> 执行 R1 协议契约对齐 → R2 模块拆分 → R3 Admin → R4 三路 GitHub 服务 → R5 运维化 → R6 重审。

## 结论

```
Central Control Plane = PASS（A 组无条件项全部闭环）
THREE-PATH PROTOCOL   = PASS（三路真实节点，同一协议契约 v1）
Real-Engine Parity    = Local PASS；Internal/External NOT AVAILABLE（Windows 引擎
                        无法运行于 GitHub ubuntu runner —— 物理约束，非代码缺口，§132 如实标注）
```

## A. Central（全部 LIVE）

| 项 | 判定 | 证据 |
|---|---|---|
| 用户系统 / 登录 / Session / 权限 | PASS | LIVE E2E；vitest auth.spec（过期/吊销 DENY） |
| 订单（创建幂等） | PASS | LIVE E2E；Idempotency-Key 去重 |
| 查询（订单/任务/详情/访客） | PASS | LIVE E2E；order-query/guest-query spec |
| Task / Result / 日志工件 | PASS | LIVE：R2 sha256 + 所有者下载 + 他人 404 + result_json |
| **Admin（§58）** | **PASS（stage-cloud-33）** | /api/v1/admin/*（stats/users/orders/tasks/executors + 启停）+ 管理页；admin-api.spec 含 token 泄漏审计 |

## B. Local Executor（真机 Windows）

| 项 | 判定 |
|---|---|
| Real Executor（协议全链路） | PASS（LIVE） |
| Real Runner（fuckCourse/超星） | **PASS（LIVE：真实登录 + 33 门课实查 + 暂停/恢复实跑）** |
| Real Result / Heartbeat / Lease / Retry / Recovery | PASS（LIVE：含 stale 回收 + 旧租约 DENY） |
| §113 开机自启 | PASS（Task Scheduler ONSTART：exec-local-01 + start_local_executor.cmd） |
| §115 孤儿扫描 | PASS（启动扫描 + 中央 state 查询 + pidfile 终止；python 5/5） |

## C/D. Internal / External Executor（GitHub Actions 服务，LIVE）

| 项 | 判定 | 证据 |
|---|---|---|
| 真实独立节点（非本机模拟） | **PASS** | exec-internal-01 / exec-external-01 运行于 GitHub-hosted runner（outbound-only，每 2h 窗口 + 手动 dispatch；首跑 exit=0 ×2） |
| 协议闭环（pull→执行→complete→工件） | **PASS（LIVE）** | e2e_three_path_demo.py 3/3：health/登录/建单 → 各路 demo.echo → GitHub runner 真实领取执行 → result_json 工件回读校验 |
| Path 隔离（跨路径 DENY） | PASS（LIVE） | 跨路径 claim 403；state 端点跨路径 found:false（实测） |
| Result / Recovery / Fencing | PASS | 与 Local 同一 Agent 代码 + 同一协议契约 |
| Real Runner（Windows 引擎） | **NOT AVAILABLE** | ubuntu runner 不承载 fuckCourse/超星（物理约束；协议同代码已验证） |

## E. Security

| 项 | 判定 | 证据 |
|---|---|---|
| No Git secret | PASS | 树/历史 -S 扫描；deploy-secrets.txt gitignored；.ctl_creds 事件查实为空文件且已 gitignore |
| No plaintext password in D1 | PASS | users.password_scheme=pbkdf2-sha256-v1；orders 无密码列 |
| No plaintext credential | PASS | 云端凭据 enc-v2 AES-GCM；解封仅租约门控 + runtime 内存；Admin 输出无 token_hash |
| No password in logs | PASS | §68 脱敏；真实引擎日志 grep 零凭据 |
| No cross-user / cross-path / stale-lease write / unauthorized artifact | PASS | LIVE 403/404/409 实测 |

## F. Data

| 项 | 判定 | 证据 |
|---|---|---|
| Cloudflare = source of truth | PASS | DO 调度态镜像 D1 |
| Executor 无业务 DB | PASS | 仅内存 + 临时运行目录（任务结束清理；启动孤儿扫描兜底 §115） |
| 三路共享同一中央 Task 模型 | PASS | 同一 Agent 代码 + contracts/ 共享 schema（§73，vitest 契约一致性测试） |

## G. Free-plan 预算

PASS（Workers ~18K/day < 60K；D1 writes ~2.5K/day < 60K；DO ~20K/day < 30K）。
**§12 额度守卫已上线**（stage-cloud-35）：整点 Cron 估算当日 D1 写量，≥70%/≥85% 写 QUOTA_70/QUOTA_85 审计（每自然日幂等）。

## Commit 清单（stage-cloud 分支）

01→26（原链路，见 git log）→ 29/30/30b-d（商品同步/目录正式化/网络容错）→ **31-pre 合规审计 + R1：31（协议契约对齐：contracts/、pull/start/bootstrap/fail/cancel-ack/presign、attempt_id 三元 fencing）→ R2：32（§71/§72 模块拆分补齐）→ R3：33（Admin）→ R4：34（三路 GitHub 服务 + 订单终态白名单修复）→ R5：35（孤儿扫描/自启/额度守卫）**。

测试终态：Worker vitest **92/92** + tsc 零错误；Python unittest **5/5**；
LIVE：主 E2E 14 PASS + 工件 10 PASS + 恢复/并发 8 PASS + 真实引擎 7 PASS + **三路 demo 3/3 PASS**（GitHub 服务）。

## 真实接入过程中发现并修复的缺陷（LIVE 实证价值）

1. `main()` 误调 `runtime.node_heartbeat()` —— Executor 启动即崩。
2. **工件时序**：result_json 晚于 complete 上传被拒 → 修复为工件先传 + 幂等 complete 跳过镜像。
3. **订单终态误翻转**（R4 三路 demo E2E 发现）：非 `*.run` 辅助任务完成曾把订单置为终态，
   导致同订单无法继续入队 —— 修复为白名单（仅 `*.run` 驱动）+ vitest 回归测试。
4. **推送工具重放**：API 推送失败重试会重放全部提交制造远端重复 —— 修复为按远端 tip 对齐增量推送。
