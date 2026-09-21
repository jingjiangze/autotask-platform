# PROJECT_STATUS.md — WorkBuddy 自动任务平台 · 全项目状态详单

> 生成：2026-09-14 ｜ 用途：发给外部审查（GPT 等）做查缺补漏 ｜ 依据：源码通读 + 运行时实测 + 数据库只读核验
> 约定：**[已验证]** = 本会话实测/只读核验过；**[未验证]** = 代码级就绪但未在正式机端到端确认；**[设计取舍]** = 有意为之。
> 定位：这是"事实 + 证据 + 缺口"清单，不包含主观美化。**任何"✅"都对应具体证据或回滚点**。

---

## 0. 一句话概览

Windows 单机、Flask(Waitress)+SQLite(WAL)+Worker 线程池+每单独立子进程+Cloudflare Tunnel 的自动化刷课任务平台；订单级隔离，fuckCourse 为业务引擎（除 R4 最小凭据兼容外零改动）。已按"渐进稳定性改造"完成 P0–P3 全部计划项。

## 1. 部署环境

| 项 | 值 | 说明 |
|---|---|---|
| 项目根 | `D:\web` | 2026-09-13 自 `C:\Users\Administrator\WorkBuddy\2026-09-11-10-49-22` 迁入（该旧路径目前已不存在，留档目录已删） |
| Python 解释器 | `C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\python(.w).exe` | 实际解释器 3.13.12（traceback 显示 versions\3.13.12.old.36204）字面 |
| SQLite | WAL / synchronous=NORMAL / busy_timeout=15000 | PRAGMA 只读核验 [已验证] |
| Flask | Waitress threads=16 @`127.0.0.1:8766` | 不监听 0.0.0.0；ImportError 自动回退 dev server [已验证：代码] |
| 隧道 | `cf\cloudflared.exe` → `%USERPROFILE%\.cloudflared\wk_config.yml` | 域名 order.jiangjiangze.icu |
| 自启 | 计划任务 `WK_AutoTaskPlatform` | pythonw + health_manager.py；Logon + Time(5min/P3650D) 双触发；IgnoreNew；RestartCount=3/10min；ExecutionTimeLimit=PT0S [已验证：dump] |
| 引擎 | `fuckCourse\`（chaoxing/zhs） | 业务核心，指令边界"不触碰" |

## 2. 系统链路与进程模型（实测）

```
用户浏览器 → Cloudflare Edge → cloudflared(独立进程) → 127.0.0.1:8766
order_platform.py (pythonw, Waitress 16 线程)
 ├─ concurrency_manager → worker×N（settings.concurrency=4，硬上限 10）
 ├─ qr_janitor / housekeeping / （看护为独立进程）
 └─ worker → _spawn 每单 1 个 venv python 子进程（低优先级+CREATE_NO_WINDOW）
      └─ 引擎内部线程并发（-j jobs），不再派生更深的进程
health_manager.py（独立看护，锁文件单实例）→ 拉平台 / 拉 cloudflared / 每日补备份
```

**进程树层级 ≤ 2**（平台 → 任务子进程），因此**未采用 Windows Job Object**（`taskkill /F /T`+退出验证已覆盖，tests/test_kill_tree 通过）。

## 3. 目录结构全清单（2026-09-14 归档后实测）

```
D:\web
├── order_platform.py          85.7KB  主程序（P0–P3 全增强，1651→约1690行）
├── health_manager.py          10.7KB  看护（锁文件+熔断+补备份）
├── backup_manager.py          4.4KB   在线备份/校验/7份轮转/恢复
├── tools_query_courses.py     4.2KB   查课子进程（凭据走 env）
├── order_server.py            13.2KB  v1 弃用保留（端口8765，JSON存）
├── order_platform_v5_backup.py 50.5KB v5 备份（与线上库同路径，勿运行）
├── *.bak_20260914_R4          order_platform / tools_query_courses 回滚点
├── 文档：ARCHITECTURE_AUDIT / RECOMMENDED_ARCHITECTURE / CHANGELOG /
│        TEST_REPORT / README_RUN / PROJECT_STATUS
├── orders\
│   ├── platform.db (+-wal/-shm)  线上库
│   ├── _query\                   查课临时 cookies（1h 清理）
│   └── <uuid>\ 每单目录：log.txt / cookies.json / work / tmp / home / logs （3 天清理）
├── backups\                      platform_YYYYMMDD_HHMMSS.db ×7（最近 09-14 10:19）
├── secrets_store\                secret_key.txt / admin_password.txt / 旧口令归档
├── cf\
│   ├── cloudflared.exe / cloudflared.log / cloudflared_run.log
│   ├── health_manager.log / platform_stdout.log / health_manager.lock
│   ├── 启动平台和隧道.bat（WK 已修复=D:\web）
│   ├── deploy_cf.py / run_tunnel_detached.py / deploy_cert.py
│   ├── *.bak_20260914 / health_manager_v2_lockfile_20260914.py（存档变体）
│   └── browser_profile\   （Edge 探针残留 59 项；未审慎使用，可归档）
├── tools\
│   ├── cleanup_stale.py          游离/僵尸清扫（看护单进程杀、平台树杀）
│   ├── stop_platform.ps1 / start_platform.ps1
│   └── __pycache__\
├── tests\   test_crypto / test_health / test_kill_tree / test_restore_drill / test_spawn_timeout
├── fuckCourse\  chaoxing/ zhs/ logs/ qr/ welearn/ yuketang/ config.json(明文凭据)/
│                cookies.json / .zhs_cred / cookies_13000000000.json(敏感残留P3) / main.py(已R4 patch)
├── static\ + tabler_pkg\   Tabler 1.5.1 本地 UI
├── zhs_script\ fuckZHS_orig\  上游参考仓库
├── _archive_dev\   189 个开发残留归档（22MB，含口令推断材料，可还原）
├── .workbuddy\memory、_hist\、_hikejs\、_onlineweb_js\   （历史诊断件，未归入归档）
└── __pycache__\
```

## 4. 数据库设计（只读核验）

```sql
users(id PK AI, username UNIQUE, pw_hash, is_admin, created_at)
orders(id TEXT PK, user_id, product, platform, account, password, courses,
       status, note, qr_state, created_at, started_at, finished_at,
       exit_code, worker_running,
       product(重名列兼容), env_profile, risk_flags, speed,   -- 早期增列
       pid, attempt, heartbeat_at)                             -- P0 增列
products(id PK AI, code UNIQUE, name, desc, price, platform, enabled, sort)
settings(key PK, value)
```

- 当前数据：**19 单（14 done / 5 failed，0 pending/running）**；用户仅 1 个（admin，非默认口令）[已验证]
- 密码：全部 `enc:v1:` 前缀（0 明文）[已验证]；加密为纯标准库流式（HMAC-SHA256 派生 keystream + 随机 nonce），密钥源自 secret_key.txt；**轮换/删除 secret_key.txt 会使存量密码不可解密（可预期，需用户重新下单）**
- 时间戳：已全部 `%Y-%m-%d %H:%M:%S`（P2 迁移 57 项，残留 0）[已验证]；迁移年份固定 2026（数据均为 2026-09 创建）
- 索引：仅主键（19 行规模不建，写入成本>收益，设计取舍）
- 状态词汇：pending/waiting_qr/running/done/failed/canceled（复用，timeout/crashed 用 failed+exit_code+note 表达）

## 5. 订单/任务/文件生命周期

```
下单(密码加密入库) → pending → worker CAS claim(attempt+1, 记pid/heartbeat) → running
  → _spawn(低优先级子进程+RollingLog+20s心跳+硬超时180min)
  → rc=0 → done ；rc≠0 → failed ；rc<0且attempt<1 → 自动重试1次
waiting_qr → 扫码成功→pending ；过期/取消→canceled
平台重启 → recover_stale_orders：running 判 pid → 树杀 → attempt<1?pending:failed
```
- 文件：订单目录 3 天 rmtree；_query 1h；订单 log 300KB 裁剪；引擎 logurur 日期文件 512KB 裁剪+按天删；cf 日志 512KB；备份 7 份轮转；QR 会话 janitor 120s。

## 6. 改造历程与回滚点（P0–P3 全部落地）

| 阶段 | 改动 | 回滚点 |
|---|---|---|
| P0 | orders 增列 pid/attempt/heartbeat_at；CAS claim+attempt；_spawn 记 pid+心跳+树杀验证；safe_set_order 兜底；recover_stale_orders；/health；四口限流；SECRET 外置；默认口令随机化；RollingLog 句柄修复；空闲退避；waiting_qr 终态；housekeeping 补日志/备份/cloudflared | [已验证：功能现库核验] |
| P1 | A1：看护单实例 互斥体→**锁文件**（O_EXCL+pid/ts+TTL100s 陈旧接管+释放）+ 看护主循环不死兜底；A2：bat WK 路径修复为 D:\web；A3：IgnoreNew 核验；僵尸清扫脚本 tools\cleanup_stale.py；R4：引擎/平台/查课三处凭据改 env（WK_ACCOUNT/WK_PASSWORD，CLI>env>config） | health_manager.bak_20260914、*.bak_20260914_R4 |
| P2 | 看护**每日补备份**（共用 last_backup 防双份，停机也备份）；管理页"执行中队列"加 PID/内存(proc_mem_mb via kernel32.K32GetProcessMemoryInfo)/最近心跳/运行时长；存量时间补年份 | backup restore platform_20260914_101933.db |
| P3 | dated 日志 512KB 上限；/qr、/qr_status 登录鉴权；根目录 187 文件归档 _archive_dev；stop/start ps1 脚本 | 各 .bak / _archive_dev 可还原 |

## 7. 安全现状

- 口令哈希 HMAC-SHA256(secret+静态盐"wk")；SECRET 外置文件（非硬编码）
- CSRF：全站 POST 同源校验（Origin/Referer，双 scheme 兼容 CF，无头客户端放行）——**无 token，属设计取舍**
- 限流：login 10/min、register 5/min、api_courses 12/min、qr_start 6/min（内存计数，>5000 key 清空）
- 敏感文件：secrets_store 单独目录；根目录残留已归档
- 明文残留点（有意保留/标注）：fuckCourse\config.json 引擎凭据、cookies_13000000000.json、.zhs_cred、cf 隧道凭据在 %USERPROFILE%\.cloudflared
- 已知低危：/query 前缀 LIKE 枚举（uuid4）；引擎 INFO 级无条件 print 账号名（3 天清理期）

## 8. 当前运行状态快照（2026-09-14 11:3x 实测）

| 组件 | 状态 |
|---|---|
| 平台 /health | ✅ {"database":"ok","queue":"ok","pending":0,"running":0,"status":"ok"} |
| 看护 | ✅ 锁文件在位（pid+ts），30s 轮询 |
| cloudflared | ✅ 运行中 |
| backups | ✅ 7 份，最近 09-14 10:19 |
| 订单 | 19 单全终态 |

## 9. 已知问题与缺口（供查缺补漏重点）

### A. 已处理（证据+回滚齐备，见 §6/§11）
P0–P3 全部项；回滚点一一对应。

### B. 环境限制（重要，勿误判为代码缺陷）
1. **本工具会话会周期性外部中断长驻 pythonw 进程**（多次实测；旧代码在相同环境亦复现；06-13 正式机历史稳定运行 14.5h + 平台崩溃 3 次自愈）。因此"看护长期存活/正式机回归"未在本会话完成端到端观察。
2. 计划任务触发时可能产生一个"早启动僵尸 pythonw"（约 1线程/4MB，未进 main()）——**锁文件保证活跃侧唯一**；僵尸惰性无害，由 `tools\cleanup_stale.py` 或机器重启回收。**正式机建议长期观察后再决定是否采纳 cf\health_manager_v2_lockfile_20260914.py（启动自动清僵尸变体，需排除行为监控）**。

### C. 未验证项（[未验证]，需正式机确认）
1. R4 引擎侧变更端到端任务回归（本会话不触达第三方平台）——引擎 init_config 三例单测已过，平台静态核验无 `-p` 明文。
2. 看护"每日补备份"完整触发链路（当前距上次备份 <24h 未到点；代码路径与 housekeeping 调用的 backup_manager.backup_database 相同，备份功能本身已验证）。
3. 管理页 worker 明细列在正式机 UI 实际展示。
4. Waitress 替换后的长期稳定性（本会话平台持续切换）。

### D. 设计取舍（已有意为之）
- 无 Job Object（进程树≤2 层）；无 Redis/队列/容器；SQLite 即队列+CAS；
- 单层硬超时 180min（无 soft/hard 双段，引擎整体执行收益低）；
- 状态词汇复用 done/failed（不新增 timeout/crashed 列）；
- 无 token CSRF（Origin/Referer+无头放行）；
- 口令静态盐"wk"、unlimited 限流字典上限清空策略；
- 不建索引/不每日 VACUUM；
- 报错形式：异常文本 APP_DIR 脱敏后写入订单 note（长度截断）。

### E. 开放项（建议继续跟踪）
1. **正式机回归清单**：下单/扫码/查课/管理页全流程 + watchdog ≥2 天观察 + 僵尸现象频率记录。
2. order_platform_v5_backup.py 与线上库同路径——加"检测 8766 监听则拒启"（当前仅文档标注）。
3. 异地备份（可选）。
4. 引擎 INFO 日志账号名脱敏（触碰引擎边界，需确认）。
5. /query 前缀枚举加固（可选）。

## 10. 命令速查

```
启动:   powershell -ExecutionPolicy Bypass -File tools\start_platform.ps1
        （等价 Start-ScheduledTask -TaskName WK_AutoTaskPlatform）
停机:   tools\stop_platform.ps1
清扫:   python tools\cleanup_stale.py           （--kill-all 全清慎用）
备份:   python backup_manager.py backup|list
恢复:   python backup_manager.py restore backups\platform_YYYYMMDD_HHMMSS.db
健康:   http://127.0.0.1:8766/health
日志:   cf\health_manager.log / cf\platform_stdout.log / platform_error.log
```

## 11. 回滚点索引（全部可操作）

| 目标 | 回滚方式 |
|---|---|
| 年份迁移 | `backup_manager.py restore backups\platform_20260914_101933.db` |
| 平台 P2/P3 | 还原 order_platform.py.bak_20260914_R4 后重启 |
| 看护 | 还原 cf\health_manager.py.bak_20260914 + 启动任务 |
| bat | 还原 cf\启动平台和隧道.bat.bak_20260914 |
| R4（引擎） | 还原 fuckCourse\chaoxing\main.py.bak_20260914_R4 与 tools_query_courses 同名备份 |
| 归档 | _archive_dev\ 内文件移回原位 |

## 12. 给审查者的必读入口

1. 架构全貌与决策：ARCHITECTURE_AUDIT.md（第二版）/ RECOMMENDED_ARCHITECTURE.md（第二版）
2. 全程变更日志：CHANGELOG.md（含每阶段原因与回滚）
3. 测试证据：TEST_REPORT.md（P0–P3 各阶段）
4. 运维手册：README_RUN.md