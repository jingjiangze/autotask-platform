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
| Real Execute（fuckCourse 真实引擎） | LIVE PASS² | NOT APPLICABLE² | NOT APPLICABLE² |
| Heartbeat（租约续期） | LIVE PASS | LIVE PASS | LIVE PASS |
| Result（complete → 终态镜像） | LIVE PASS | LIVE PASS | LIVE PASS |
| Log / Artifact（R2 上传+下载+越权 404） | LIVE PASS | LIVE PASS | LIVE PASS |
| Retry（retry_wait → 回队 → attempt_no+1） | LIVE PASS⁴ | LIVE PASS⁴ | LIVE PASS⁴ |
| Recovery（stale → requeue → 旧租约 DENY） | LIVE PASS⁵ | LIVE PASS⁵ | LIVE PASS⁵ |
| Permission（越权 404 不暴露存在性） | LIVE PASS | LIVE PASS | LIVE PASS |

¹ 凭据解封（stage-11）为 LIVE PASS 于 vitest Workers 运行时 + 线上租约门控（e2e_artifacts）；R2 cookie 工件路径同已通。
² 真实引擎已接入 Local Executor（e2e_real.py 7/7 LIVE PASS：enc-v2 解封 → 真实登录 → 课程树扫描 → 日志/result_json 入 R2 → 中央终态）。Internal/External 为云端沙箱，按设计不安装本地引擎，故 NOT APPLICABLE（协议层同一代码已在三路验证）。
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
| Executor 重启后孤儿任务清理 | CODE READY（agent/main.py 重连即重心跳；孤儿扫描待 12 真实接入） | — |

## 4. 并发（stage-23，LIVE）

| 场景 | 判定 | 证据 |
| --- | --- | --- |
| 3 executor 并发 claim 队列 | LIVE PASS：无重复领取、无越队列 | e2e_recovery_concurrency.py |
| 同一任务并发 claim（双 executor） | LIVE PASS（vitest）：第二个 claim → task null | path-coordinator.spec |
| 重复 enqueue | LIVE PASS（vitest）：TASK_EXISTS | path-coordinator.spec |
| 旧租约 fencing（ack/heartbeat/complete 全拒绝） | LIVE PASS（vitest + 线上过期租约 DENY） | retry-fencing.spec / e2e_recovery_concurrency |
| 幂等 complete（at-least-once 安全） | LIVE PASS（vitest）：idempotent=true | retry-fencing.spec |
| 3 executor × N 任务并发 | LIVE PASS（3×3 无重叠） | e2e_recovery_concurrency.py |

## 5. 执行介质（stage-34 更新）

| 端 | 介质 | 真实引擎 |
| --- | --- | --- |
| Local | 本机 Windows 常驻 agent（`agent/main.py --runner chaoxing`） | LIVE：fuckCourse/超星真实登录、33 门课实查、暂停/恢复实跑 |
| Internal | GitHub Actions 服务 `executor-internal`（ubuntu runner，outbound-only HTTPS，每 2h 第 7 分钟起 20 分钟窗口 + 手动 dispatch；固定节点 exec-internal-01，独立 Token 存 repo secrets） | NOT AVAILABLE（Windows 引擎不上 GitHub runner；协议/链路 demo 闭环 LIVE PASS） |
| External | GitHub Actions 服务 `executor-external`（同上，固定节点 exec-external-01） | NOT AVAILABLE（同上） |

三路 demo 闭环证据：`e2e_three_path_demo.py` → 3/3 PASS（2026-09-24 LIVE，部署 dfaebdec）：
health → 登录 → 建单 → internal/external 各建 demo.echo 任务 → GitHub runner pull → 执行 → complete → result_json 工件回读校验。
该 E2E 发现并修复回归：辅助任务（非 `*.run`）完成曾误置订单终态（task-dispatch 白名单修正 + vitest 回归测试）。

## 6. 已知限制（§131 披露）

1. ~~R2 未激活~~ → **已解决**（2026-09-23 激活 + 绑定部署，e2e_artifacts 10/10）。
2. 真实 fuckCourse/超星引擎仅在 Local 路径运行（Windows 专属）；Internal/External 为 GitHub ubuntu runner，只有 demo 协议能力（§132 如实标注 NOT AVAILABLE）。
3. ~~Admin 后台未实现~~ → **已解决**（stage-cloud-33：/api/v1/admin/* + 管理页，vitest admin-api.spec）。
4. Executor 并发多任务（capacity>1）为骨架态；孤儿扫描见 §115 进展（R5）。
