# CLOUDFLARE FREE QUOTA — 配额审计（stage-cloud-21）

> 额度来源：Cloudflare 官方 Workers Free / D1 / R2 / DO / KV / Queues 定价页（2026-09 检索，计划书 §127 引用）。
> 本项目内部预算（计划 §12）非官方上限；达 70% 告警、85% 停新增高频非核心功能。

## 1. 官方 Free 额度 vs 本项目设计目标 vs 当前估算

| 资源 | 官方 Free | 设计目标 | 当前估算（3 Executor 稳态） | 风险 |
|---|---|---|---|---|
| Workers requests | 100,000/day | < 60,000/day | ~18,000/day（idle 15s pull×3 + 用户流量） | 低 |
| Workers CPU | 10ms/inv | 轻 IO 编排 | 认证 pbkdf2(500 iter)≈1-2ms；DO 转发 <1ms | 低（禁高成本 KDF，§17） |
| D1 reads | 5,000,000/day | < 2,500,000/day | ~60,000/day（索引查询，无全表扫描） | 低 |
| D1 writes | 100,000/day | < 60,000/day | ~2,500/day（仅状态机迁移点写入，心跳不写 D1 §54） | 低 |
| D1 storage | 5GB total | < 1GB | < 50MB | 低 |
| DO requests | 100,000/day | < 30,000/day | ~20,000/day（每 claim/ack/hb/complete 经 DO） | 中：heartbeat 高频需观察 |
| DO duration | 13,000 GB-s/day | 短请求 | < 500 GB-s（无长驻 WebSocket） | 低 |
| R2 storage | 10GB-month | < 5GB | 0（R2 未激活，10042） | 待开通 |
| R2 Class A | 1,000,000/mo | — | 0 | 待开通 |
| KV | 100K reads/1K writes/day | 不使用（§9） | 0 | 无 |
| Queues | 10,000 ops/day | 不使用（第一版 §10） | 0 | 无 |
| Cron | 5 triggers | 1（*/1 会话清理） | 1 | 低 |

## 2. 关键设计决策（免费额度最大化而非服务数量最大化 §126）

1. **心跳走 DO 不走 D1**（§42/§54）：3 Executor × 每 30s 心跳全部由 PathCoordinator DO 内存/storage 承接，D1 只记注册/上线/离线/任务终态。
2. **Executor pull 15s idle / 5s busy**（§101）：不引入 Queues。
3. **Session 由 D1 管理**，Executor 状态由 DO 管理——KV 无真实需求不使用。
4. **日志/工件走 R2**，D1 只存 metadata（sha256/size/object key）。
5. **密码哈希 pbkdf2 迭代数 500**（Workers 10ms CPU 预算内实测通过，§17 禁未经 benchmark 的高成本 KDF）。

## 3. 已观测数据点（LIVE 部署后）

- 冒烟 + E2E（约 30 次请求）后 D1 用量正常，无异常扫描（核心查询均走 idx_* 索引，迁移含 EXPLAIN 验证）。
- Cron */1 每天产生 1,440 次触发，每次 1 条 DELETE——计入 Workers requests 预算（~1.4K/day，可接受）。

## 4. 告警与行动

| 阈值 | 动作 |
|---|---|
| 任一资源达内部预算 70% | 记录告警（audit_events + 文档更新） |
| 达 85% | 停止新增高频非核心功能 |
| Workers 超预算 | 先降 pull 频率 → 再引入 Queues → 最后考虑 Paid（§101，不要先买服务） |
