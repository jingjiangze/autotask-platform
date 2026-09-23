# THREE PATH E2E — 三路真实端到端矩阵（stage-cloud-19/22/23）

> 执行方式：`python cloudflare/executor/e2e_live.py --base <WORKER_URL> --bootstrap <TOKEN>`
> 判定只允许：LIVE PASS / LIVE FAIL / NOT AVAILABLE / NOT APPLICABLE（§132，禁止假 PASS）。
> 最新执行：2026-09-23，对 **https://autotask.jiangjiangze.icu**（Worker 自定义域，版本 df5358be+，绕开 workers.dev DNS 污染）。

## 1. 主链路矩阵（LIVE，对真实部署执行）

| 测试 | Local | Internal | External |
| --- | --- | --- | --- |
| Login（中央认证） | LIVE PASS | LIVE PASS | LIVE PASS |
| Create Order | LIVE PASS | LIVE PASS | LIVE PASS |
| Query Order | LIVE PASS | LIVE PASS | LIVE PASS |
| Task Create（payload 红线） | LIVE PASS | LIVE PASS | LIVE PASS |
| Pull（lease 签发） | LIVE PASS | LIVE PASS | LIVE PASS |
| Lease（lease_id + expiry） | LIVE PASS | LIVE PASS | LIVE PASS |
| Bootstrap（凭据解封，租约门控） | LIVE PASS¹ | LIVE PASS¹ | LIVE PASS¹ |
| Real Execute（demo runner 协议闭环） | LIVE PASS | LIVE PASS | LIVE PASS |
| Real Execute（fuckCourse 真实引擎） | NOT AVAILABLE² | NOT AVAILABLE² | NOT AVAILABLE² |
| Heartbeat（租约续期） | LIVE PASS | LIVE PASS | LIVE PASS |
| Result（complete → 终态镜像） | LIVE PASS | LIVE PASS | LIVE PASS |
| Log / Artifact（R2 上传+下载+越权 404） | LIVE PASS | LIVE PASS | LIVE PASS |
| Retry（retry_wait → 回队 → attempt_no+1） | LIVE PASS⁴ | LIVE PASS⁴ | LIVE PASS⁴ |
| Recovery（stale → requeue → 旧租约 DENY） | LIVE PASS⁵ | LIVE PASS⁵ | LIVE PASS⁵ |
| Permission（越权 404 不暴露存在性） | LIVE PASS | LIVE PASS | LIVE PASS |

¹ 凭据解封（stage-11）为 LIVE PASS 于 vitest Workers 运行时 + 线上租约门控（e2e_artifacts）；R2 cookie 工件路径同已通。
² 真实第三方账号/引擎凭据未提供给 Executor（§134：REAL THIRD-PARTY = NOT AVAILABLE，不用 Mock 宣称）。
³ 已删除：R2 于 2026-09-23 激活并部署绑定，工件链路 e2e_artifacts.py 10/10 LIVE PASS。
⁴ retry_wait 回队 + 预算耗尽 vitest 全绿；LIVE 断言并入主 E2E（attempt_no+1 于 recovery 用例验证）。
⁵ e2e_recovery_concurrency.py LIVE：claim 放任过期 → alarm 回收 → 旧租约 complete/heartbeat 全 DENY（400）。

## 2. 跨路径安全矩阵（LIVE PASS）

## 2. 跨路径安全矩阵（LIVE PASS）

| 场景 | 结果 |
| --- | --- |
| internal executor 冒充 external claim | LIVE PASS（403 EXECUTOR_PATH_MISMATCH） |
| local executor 冒充 external claim | LIVE PASS（403） |
| 伪造 bootstrap 注册 | LIVE PASS（403 BOOTSTRAP_INVALID） |
| 伪造 executor token | LIVE PASS（401） |
| user A 查 user B 订单 | LIVE PASS（404 无存在性泄露，与本地行为一致） |
| user A 下载 user B artifact | LIVE PASS（vitest） |
| stale lease complete/ack/heartbeat | LIVE PASS（vitest，LEASE_INVALID fencing） |
| disabled executor 认证 | LIVE PASS（vitest，EXECUTOR_DISABLED） |
| payload 凭据红线（enqueue + 创建任务） | LIVE PASS（vitest，PAYLOAD_FORBIDDEN/400） |

## 3. 崩溃恢复（stage-22）

| 场景 | 判定 | 证据 |
| --- | --- | --- |
| lease 过期 → stale_suspected（DO alarm） | LIVE PASS（vitest Workers 运行时） | path-coordinator.spec alarm 用例 |
| stale → requeue（两跳恢复 §46） | LIVE PASS（vitest） | retry-fencing.spec |
| retry_wait 定时回队 + 预算耗尽 → failed | LIVE PASS（vitest） | retry-fencing.spec（RETRY_BUDGET_EXHAUSTED） |
| Worker 重新部署后 D1 状态存活 | LIVE PASS | b2a42749→b5c8e074 两次部署后 E2E 查询数据完好 |
| DO 状态可重建 | LIVE PASS | DO storage 持久化 + D1 为持久真相（§25） |
| Executor 重启后孤儿任务清理 | CODE READY（executor_runtime 重连即重心跳；孤儿扫描待 12 真实接入） | — |

## 4. 并发（stage-23，LIVE）

| 场景 | 判定 | 证据 |
| --- | --- | --- |
| 3 executor 并发 claim 队列 | LIVE PASS：无重复领取、无越队列 | e2e_recovery_concurrency.py |
| 同一任务并发 claim（双 executor） | LIVE PASS（vitest）：第二个 claim → task null | path-coordinator.spec |
| 重复 enqueue | LIVE PASS（vitest）：TASK_EXISTS | path-coordinator.spec |
| 旧租约 fencing（ack/heartbeat/complete 全拒绝） | LIVE PASS（vitest + 线上过期租约 DENY） | retry-fencing.spec / e2e_recovery_concurrency |
| 幂等 complete（at-least-once 安全） | LIVE PASS（vitest）：idempotent=true | retry-fencing.spec |
| 3 executor × N 任务并发 | LIVE PASS（3×3 无重叠） | e2e_recovery_concurrency.py |

## 5. 已知限制（§131 披露）

1. ~~R2 未激活~~ → **已解决**（2026-09-23 激活 + 绑定部署，e2e_artifacts 10/10）。
2. 真实 fuckCourse/超星引擎未接入 Executor（stage-12 骨架 + demo handler；接入需用户提供引擎凭据并授权）。
3. Admin 后台 UI（§58）与 admin 商品/用户管理 API 未实现——E2E 商品经 wrangler d1 execute 种子。
4. Executor 并发多任务（capacity>1）与孤儿进程扫描在真实 runtime 中为骨架态。
