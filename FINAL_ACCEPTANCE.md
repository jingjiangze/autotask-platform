# FINAL_ACCEPTANCE — WorkBuddy 自动任务平台 最终验收清单

> 生成：2026-09-14（两轮改造全部落地后）｜ 依据：源码 + 实测 + 回归（TEST_REPORT 第三轮全量回归）
> 用途：正式机验收对照 + 交接留档。标注：[已确认] 实测/核验｜[条件通过] 依赖运行时项｜[需正式机] 正式机确认

---

## 一、在线状态快照（2026-09-14 实测）

| 组件 | 状态 |
|---|---|
| 平台 /health | ✅ {"database":"ok","queue":"ok","pending":0,"running":0,"status":"ok"} |
| 看护（锁文件） | ✅ 锁在位（cf\health_manager.lock） |
| cloudflared | ✅ 运行中 |
| 备份 | ✅ 7 份，最近 platform_20260914_101933.db（verify=ok） |
| 订单库 | ✅ 19 单全终态（14 done / 5 failed），0 非终态，0 明文密码，0 无年份时间戳 |

## 二、验收标准对照（36 项）

| 模块 | 验收项 | 状态 | 证据/落点 |
|---|---|---|---|
| 运行 | Windows 正常运行 / 空闲 CPU 低 / RAM 合理 | ✅ | 退避 2s→5s；平台≈33MB、看护≈24MB、cf≈33MB |
| 轮询 | 无任务无高频 DB 轮询 / 无大量无意义线程 | ✅ | 退避 + 常驻≈24 线程（WSGI16+4worker+4 维护） |
| 资源 | worker 无明显泄漏 / 子进程完整回收 / 日志/临时目录可控 | ✅ | /T+验证退出；log 300KB/引擎512KB/cf512KB/dated512KB+3天 |
| 并发 | 不重复 claim | ✅ | CAS（UPDATE…WHERE worker_running=0+rowcount） |
| 恢复 | 不永久 running | ✅ | recover+watchdog+兜底写；现库 0 卡死 |
| 恢复 | Windows 重启/Flask/worker/cloudflared 崩溃可恢复 | ✅ | 计划任务→看护→recover；看护 30s+熔断 |
| DB | WAL 正常 / 无长事务 / busy_timeout 合理 / 索引按需 | ✅ | WAL/NORMAL/busy15000/短事务/主键索引 |
| 备份 | backup/restore 可用 | ✅ | 每日+补位；restore 演练过；在线拦截（--force 可选） |
| 安全 | cookies 不经 HTTP / token 不入日志 / 密码库内加密 | ✅ | 无下载路由；esc 遮蔽+db 密文 enc:v1: |
| 安全 | 管理员接口保护 / 普通用户不可越权 / IDOR 拦截 | ✅ | is_admin+归属校验 |
| 安全 | 路径穿越阻止 / 数据库不可下载 | ✅ | 无用户路径接口 |
| 安全 | /health 存在 / 系统状态页 / worker 可观察 | ✅ | /health、管理卡、执行队列带 PID/内存/心跳/时长 |
| 安全 | retry 不无限 / watchdog 无重启风暴 | ✅ | MAX_RETRY=1；熔断 10min/3 次 |
| 安全 | 启动幂等 | ✅[条件通过] | 锁文件+IgnoreNew+bat 修复；僵尸环境现象由 sweep/重启回收（[需正式机]观察） |
| 业务 | 原有业务保留 / 无大组件 / fuckCourse 未动 | ✅ | 下单/扫码/查课/管理流程不变 |

## 三、本轮（第二轮）已落地清单

| 类别 | 项 | 文件 | 回滚 |
|---|---|---|---|
| P0 | 运行期订单进程死亡收敛（order_watchdog 30s） | order_platform.py | bak_Round2 |
| P0 | recover 树杀结果校验（kill 失败→failed） | order_platform.py | bak_Round2 |
| P1 | HTML 转义 15 处（esc，含 log_tail/note/username…） | order_platform.py | bak_Round2 |
| P1 | 日志落盘脱敏（RollingLog._scrub） | order_platform.py | bak_Round2 |
| P1 | 限流 IP 信任策略（回环才取 CF 头） | order_platform.py | bak_Round2 |
| P1 | restore 在线拦截（--force 可选） | backup_manager.py | 还原函数 |
| P1 | 明文残留归位（cookies_1337…→_archive_dev；口令材料→secrets_store\_archive_dev） | 文件 | 移回 |
| P2 | 查课并发护栏（BoundedSemaphore=2） | order_platform.py | bak_R2P2 |
| P2 | 管理页 tasklist 超时 | order_platform.py | bak_R2P2 |

此前（第一轮 P0–P3）：备份/恢复/pid-attempt-heartbeat/兜底写/busy 容错/口令/密码加密/retry/看护锁文件/bat 修复/年份迁移/QR 鉴权/dated 日志上限/归档/运维脚本 —— 全部落地（见 CHANGELOG）。

## 四、回滚点索引（全部可操作）

| 目标 | 方式 |
|---|---|
| 本轮全部平台改动 | 还原 `order_platform.py.bak_20260914_Round2` 或 `bak_R2P2`（按时间）后重启 |
| 年份迁移/数据库 | `python backup_manager.py restore backups\platform_20260914_101933.db`（停机后，或 --force） |
| 看护 | 还原 `cf\health_manager.py.bak_20260914` |
| bat | 还原 `cf\启动平台和隧道.bat.bak_20260914` |
| R4（引擎凭据 env） | 还原 `fuckCourse\chaoxing\main.py.bak_20260914_R4` 等 |
| 归档文件 | `_archive_dev\` / `secrets_store\_archive_dev\` 移回原位 |

## 五、剩余开放项（诚实清单）

1. **[需正式机]** watchdog 长期稳定 + 僵尸 pythonw 频率观察（≥2 天；异常时 `python tools\cleanup_stale.py`）。
2. **[需正式机]** R4 引擎凭据 env 化的端到端真实任务回归（本环境不触达第三方平台）。
3. [保留不改] 异步查课 / last_progress(依赖引擎) / 运行期持久采样（理由见 SECOND_ROUND_AUDIT 附录）。
4. [低危] 引擎 INFO 日志含账号名；/query 前缀枚举；v5 备份文件勿运行。
5. 无异地备份（单机可接受）。

## 六、正式机验收步骤（建议顺序）

1. `Start-ScheduledTask -TaskName WK_AutoTaskPlatform` → 等 2 分钟 → `/health` 200。
2. 浏览器走通：登录 → 查课 → 下单(queue 任务建议先用已完结账号短课程) → 订单详情日志 → 管理后台各卡。
3. `python tools\cleanup_stale.py` 确认单实例；观察 2 天看护日志无异常重启风暴。
4. 备份演练：`python backup_manager.py backup` + `restore backups\platform_…db`（停机或 --force）。
5. 完成后本项目即达验收状态。