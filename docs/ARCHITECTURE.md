# ARCHITECTURE — 最终架构（stage-cloud 最终交付 §122）

```
                         ┌────────────────────────────────┐
                         │   Cloudflare Central Platform  │
                         │  autotask-central.workers.dev  │
                         ├────────────────────────────────┤
                         │ Worker API（§38/§55 全表）       │
                         │  /health  /api/v1/auth/*       │
                         │  /api/v1/products|orders|tasks │
                         │  /api/executor/v1/*（pull 协议）│
                         ├──────────┬──────────┬──────────┤
                         │ D1       │ DO       │ Secrets  │
                         │ 唯一业务  │ PathCoord│ BOOTSTRAP│
                         │ 真相 11表│ ×3 path  │ CRED_KEY │
                         ├──────────┴──────────┴──────────┤
                         │ Cron */1（会话清理，1/5）        │
                         │ R2 工件桶（待账户激活 10042）     │
                         └───────────────┬────────────────┘
                                   outbound-only HTTPS
                 ┌───────────────────┼───────────────────┐
                 ▼                   ▼                   ▼
          ┌────────────┐      ┌────────────┐      ┌────────────┐
          │   Local    │      │  Internal  │      │  External  │
          │  Executor  │      │  Executor  │      │  Executor  │
          │ exec-local │      │ exec-internal │   │ exec-external│
          └─────┬──────┘      └─────┬──────┘      └─────┬──────┘
                │  同一 Agent 代码（cloudflare/executor/）│
                │  EXECUTION_PATH=local|internal|external│
                └─────────┬──────────┴──────────┬────────┘
                          ▼                     ▼
              REAL EXECUTION（fuckCourse / 超星 / ZHS / QR）
                          │
                          ▼
                Result / Status / Artifacts → Cloudflare
```

## 数据流（§123）

```
User → Cloudflare → D1 Order → D1 Task（queued）
     → DO enqueue → Executor claim（leased，attempt_no+1，D1 镜像）
     → ack（running）→ heartbeat（租约续期 120s）
     → credentials（租约门控解封 enc-v2，仅内存）
     → Real Runner → complete（succeeded/failed/retry_wait）
     → D1 镜像终态（tasks/attempts/orders）→ Cron 清理
     → User 查询（D1，唯一入口 §111）
```

## 隐私数据流（§124）

```
第三方密码 → 本地 enc:v1（存量）→ 迁移工具本机解密 → 云 enc-v2 AES-256-GCM 入 D1
          → Executor 凭 task_id+lease_id 调 /credentials
          → Worker 三元认证 + DO verify-lease → D1 解密 → TLS → Executor 内存
          → subprocess env → 任务完成 → 清理
绝不经：GitHub / Queue / HTML / URL / 日志 / D1 明文 / R2 明文
```

## 组件分工（§125）

| 组件 | 职责 |
| --- | --- |
| Worker | API / 认证 / 编排 / D1 状态镜像 / Cron |
| D1 | 唯一业务真相（11 表 + 11 索引，migration 0001/0002） |
| R2 | 日志 / 截图 / QR / Cookie 密文（待激活） |
| DO PathCoordinator ×3 | 队列 / 租约 / 心跳 / stale 恢复 / retry 预算 |
| Cron | 过期会话清理（*/1） |
| Secrets | EXECUTOR_BOOTSTRAP_TOKEN / CREDENTIAL_KEY |
| Queues / KV | 第一版不使用（§10/§9） |

## 安全边界

- Executor 三元认证：token + executor_id + execution_path（每节点独立 token，仅 hash 入库）。
- 凭据红线：dispatch payload 深度扫描禁凭据形 key（§33）；解封仅凭活跃租约；CREDENTIAL_RELEASED 审计。
- Fencing：旧租约 ack/heartbeat/complete 全拒绝（LEASE_INVALID）；幂等 complete（§49/§53）。
- 越权：跨用户 404 不暴露存在性；Executor 跨路径 DENY。
- 错误契约：统一 {ok:false,error:{code,message,request_id}}（§116），无 stack/SQL/Secret。
