# RECOMMENDED_ARCHITECTURE.md — 推荐架构与方案取舍（第二版）

> 依据：ARCHITECTURE_AUDIT.md（2026-09-14 第二版，基于 D:\web 线上代码与运行时实测）。
> 原则不变：不换技术栈（SQLite WAL + Worker 线程池 + Waitress/Flask + Cloudflare Tunnel 全部保留）、最小改动、可回滚、低占用。
> 本版核心变化：P0 已全部完成并经实测核验；**新发现并升级 4 项 P1（A1 双实例/僵尸进程、A2 启动 bat 断链、A3 计划任务双触发、R4 密码命令行残留）**，为当前最高优先处置项。

---

## 一、方案取舍总表（独立判断，逐项复核）

| 能力 | 结论 | 原因 |
|---|---|---|
| Windows Job Object | **不采用（维持延后）** | 实测进程树仍仅 2 层（平台 → 任务 python，任务内部线程并发）；`taskkill /F /T` + 退出验证已覆盖树杀，测试 test_kill_tree 通过无孤儿。**本期发现的双实例/僵尸问题与进程树终止无关，是"启动去重"问题，Job Object 解决不了**。触发条件不变：实测 taskkill 杀不干净再独立封装 process_manager |
| Waitress | **采用（已完成）** | 已上线：纯 Python、threads=16、RAM≈33MB 总量含 WSGI 池；ForcedImportError 自动回退开发服务器。无回退必要 |
| Watchdog（health_manager） | **采用（已完成，但需修正缺陷）** | 已实测自愈平台/隧道 3 次、熔断有效。**缺陷 A1**：互斥体去重失效导致可并存双实例；重启只清理"占 8766 端口者"。修复方向：互斥改为 `ctypes.WinDLL('kernel32', use_last_error=True)` + `ctypes.get_last_error()`，或改"锁文件(PID)+端口+互斥三合一"；health_manager 每次启动/重启前回收该项目遗留的游离 pythonw 实例；增加 kill-stale 子命令 |
| NSSM / Windows Service | **延后（维持）** | 计划任务+health_manager 已是单一有效自启链路；先修复 A1/A2/A3，若仍不可靠再整体切换并删除冗余入口 |
| Task Scheduler | **采用（现状），但触发策略必须改** | 当前 LogonTrigger + TimeTrigger(5min) 双触发，且任务默认"若已运行仍启动新实例"，是双实例结构来源（A3）。处置：保留单一触发器（建议只留 LogonTrigger，TimeTrigger 降为低频兜底或去掉），并显式设置运行策略=「若任务已在运行，则不启动新实例」 |
| Flask-Limiter | **不采用（维持）** | 自写限流（login/register/api_courses/qr_start 4 接口）已上线并实测触发，无新依赖 |
| SQLite `RETURNING` | **不采用（维持）** | CAS（UPDATE…WHERE worker_running=0 + rowcount）已正确，换 RETURNING 无收益纯风险 |
| heartbeat | **采用（已完成）** | _spawn 内 20s 级 heartbeat_at 写库；配合 pid 判活做 stale 复证。低写入量，达成 |
| SQLite backup API | **采用（已完成）** | backup_manager：每日 housekeeping 调度 + integrity_check + 保留 7 份 + restore 反向覆盖（已实测演练）。**待办项 R5**：备份仅随平台进程调度，平台停机 >1 天则断更 → 建议把备份独立到计划任务（离线可备份），或由 health_manager 顺带触发 |
| Redis / PostgreSQL / Docker | **不采用（维持）** | 单机个位数并发、19 单规模零收益。执行纪律第 79/80 条反证：复杂度↑、故障点↑、收益≈0 |

## 二、当前直面的 4 项 P1 及处置路径

### A1 —— 双实例/僵尸实例（最高优先）
**事实**：2×health_manager（20600 活/38384 活，→ 实为 38384 活跃 + 20600 僵尸 1 线程 4MB 12h+）与 2×order_platform（40796 活 / 16452 僵尸 1 线程 4MB 未绑端口 12h+）并存；health_manager.log 同一时刻只出现一次"看护进程启动"，僵尸未走到日志，判定其卡在解释器早期启动或退出挂起。
**根因候选**（按可信度排序）：
1. 互斥检测用 `ctypes.windll.kernel32.GetLastError()`——ctypes 默认不保存 last error（须 `WinDLL(use_last_error=True)`+`get_last_error()`），ERROR_ALREADY_EXISTS 判定不可靠 → 去重静默失效；
2. 计划任务双触发器并发触发（A3），同一时刻启动两个 health_manager，其中一个竞争失败后退出挂起/未完成清理；
3. health_manager 重启平台只杀"监听 8766 者"，不占端口的僵尸被叠加保留。
**处置（必须修改，3 件套）**：
1. `health_manager.py`：互斥改为可靠实现（WinDLL use_last_error 或锁文件）；启动时先清理本项目 pythonw 僵尸（枚举 pythonw 命令行含 order_platform.py/health_manager.py 且 PID≠自身 → taskkill /F）；
2. 启动恢复 `recover_stale_orders()` 之外，平台自身启动时不做"自身实例查重"（端口幂等于 Waitress/startup 由看护负责），避免重复职责；
3. 提供 `stop.bat` / `stop_and_clean.bat`：停计划任务 → 杀本项目全部 pythonw/cloudflared → 清理僵尸。
**验证**：修改后重启链路，任务列表中本项目 python 恰 1×health_manager + 1×order_platform(+cloudflared)；跑两次启动脚本无新增实例；强制双触发试验不再产生双实例。

### A2 —— 启动 bat/VBS 断链（必须修改）
`cf\启动平台和隧道.bat` 第 8 行 `set WK=C:\Users\Administrator\WorkBuddy\2026-09-11-10-49-22`（该目录已改名留档、路径不存在）；启动文件夹 `自动任务平台.vbs`（Startup\自动任务平台.vbs，09-13 修改）指向该 bat → 手册"双击 bat 启动"实际无效，登录自启仅有计划任务单点。
**处置**：二选一
- 改 bat：`set WK=D:\web`（一行）+ 同步 README_RUN 手动启动说明；或
- 彻底删除 bat 与 VBS（保留计划任务为唯一入口），README 移除"双击 bat"指引。推荐前者（保留手动兜底入口，一行修复，可回滚）。

### A3 —— 计划任务双触发（必须修改）
触发器现为 LogonTrigger + TimeTrigger(2026-09-13T18:05:10, 每 5 分钟)。双触发可并发启动 → 是 A1 诱因之一。
**处置**：单触发器 + 运行策略：
- 保留 TimeTrigger（每 5 分钟，作为看护自身死亡的兜底自愈，这正是 README 记载的用途）；**将 LogonTrigger 与 TimerTrigger 统一为 TimerTrigger 一个即可满足（开机后 5 分钟内必拉起）**，或保留 Logon 一个；
- 设置任务属性「如果任务正在运行，则：不启动新实例」（放弃"并行启动新实例"）。
- 效果：即使多触发同时到点，也只有一个 health_manager。

### R4 —— 密码仍走命令行（必须修改，P1-C 补做）
`run_chaoxing` 仍构造 `-p <解密后密码>` 命令行参数传引擎（本机任意用户 tasklist 可见明文）。DB 已加密，但进程命令行裸露。
**处置**：改为环境变量传递。最小侵入：`_spawn` 前 `env["WK_ACCOUNT"]/env["WK_PASSWORD"]` 注入，`fuckCourse/chaoxing/main.py` 改为优先读环境变量（引擎侧改动必须最小、且保持参数行为兼容——**与"不触碰引擎"边界冲突时，先与用户确认**；备选：保留参数但至少清理进程列表可见性无法做到的话，评估风险接受，明确记录）。此改动需用户确认引擎侧兼容方案后实施。

## 三、推荐最终架构（渐进态，目标 ≠ 推倒重来）

```
【进程模型】                    【线程模型】（现状即接近目标）
计划任务(单触发,不重复启动)      Waitress 16 请求线程 + worker×N(4) +
 └ health_manager.py(修复互斥)   concurrency_manager + qr_janitor +
    ├ 清理僵尸→拉起/看护平台       housekeeping ≈ 23 常驻线程，保持
    ├ 拉起/看护 cloudflared      RollingLog / qr_thread 按单瞬时，保持
    └ (建议)顺带触发每日备份
worker 线程 → 任务子进程（低优先级）
    └ pid 落库 + 20s 心跳 + 硬超时熔断 + 树杀验证（现状保持）

【订单模型】pending/waiting_qr/claimed(=worker_running)/running/done/failed/canceled
  字段 pid/attempt/heartbeat_at 已就位；timeout/crashed 以 failed+exit_code+note 表达（复用）
【队列模型】不变：SQLite 即队列 + CAS claim（正确）+ worker 2s→5s 退避轮询
【SQLite 模型】不变：WAL + 短事务 + busy_timeout + safe_set_order 兜底
【超时模型】保持单层硬超时 order_timeout_min=180min + 树杀验证；soft/hard 双阶段延后
  （引擎单进程整体执行，双阶段收益低）
【恢复模型】启动 recover_stale_orders（running/waiting_qr → crashed/重排）+ 看护冷却熔断
  （已实现）+ 新增"僵尸进程回收"（A1）
【启动模型】幂等：单一入口（计划任务）→ 看护查重/清僵尸 → 拉起；bat/VBS 路径修复（A2）
【日志模型】现状保留；补 dated 日志大小上限（P3）
【备份模型】每日在线备份+7 份轮转+integrity_check（已实现）；备份驱动独立化（R5，P2）
【安全模型】认证/限流/CSRF/口令/密码加密已就位；补命令行密码传递（R4）；根目录敏感残留清理（P3）
【目录模型】维持现状目录；仅建议归档根目录调试产物（P3）
```

## 四、分阶段实施顺序（每步可回滚，先备份）

| 阶段 | 内容 | 改动面 | 回滚方式 |
|---|---|---|---|
| P1-1 | 修复 health_manager 互斥（use_last_error / 锁文件+清僵尸），A1 | health_manager.py ~20 行 + kill-stale 子命令 | 还原函数/文件版本 |
| P1-2 | 修复 bat WK 路径（A2）+ README 同步 | 1 行 | 改回 |
| P1-3 | 计划任务触发策略统一 + "不启动新实例"（A3） | 计划任务配置（schtasks/PowerShell） | 还原触发器 |
| P1-4 | 密码经环境变量传递（R4，需先确认引擎侧兼容口径） | order_platform._spawn ~5 行 + 引擎 main.py 最小读法 | 还原参数行为 |
| 验证 | 双触发试验 / 僵尸回收 / 手册启动路径实测 / tasklist 断言 | — | — |
| P2 | 备份独立调度（R5）；worker 明细入系统状态卡；存量时间戳补年份 | 小改 | 逐项独立回滚 |
| P3 | dated 日志上限（R6）；根目录归档清理；/qr 鉴权可选；stop.bat；模块化按需 | 渐进 | — |

## 五、明确不做的事

- 不重写 order_platform.py、不搬目录、不换数据库、不换 Flask/Waitress、不引入任何队列/容器/其他 DB；
- 不碰 fuckCourse 任何业务行为（UA/代理/字体解码/请求逻辑原样）；R4 若涉及引擎也仅做"读环境变量"最小兼容、且先经确认；
- 不为 19 行的表建索引；不每天 VACUUM；不加监控基础设施；
- **不同时使用 NSSM + 计划任务 + bat + VBS 多套管理同一进程**——本期实测的双实例即由此类杂糅引发，必须收敛为单一链路（计划任务 → health_manager）。

## 六、剩余风险（诚实清单）

1. A1 僵尸进程的**确切根因**（互斥失效 vs 解释器启动挂起 vs 双触发竞态）未 100% 定论——处置按"去重可靠性 + 僵尸回收 + 触发收敛"三管齐下，可同时覆盖；修复后需双触发试验验证；
2. 引擎日志 INFO 级无条件 print 账号名（S11）——账号名出现在日志中，3 天清理期内可被本机读取；口令未发现，维持低危；彻底脱敏需动引擎（不触碰边界）；
3. R4 密码命令行可见性如果最终确认走引擎参数无法改，需接受并书面化记录；
4. D:\ 磁盘剩余空间本次取证受限（Win32_LogicalDisk/subst 未取到），备份与订单清理的容量前提未实测复核，建议人工确认；
5. order_platform_v5_backup.py 与当前库共用同一 DB 路径，若被误运行会与线上平台并发读写（无互斥）——建议 P3 加固为"检测到 live 端口则拒启"或文件头警示；
6. waiting_qr 中的扫码会话仅在内存（QR_SESSIONS），重启即失效已由恢复逻辑转为 canceled，属预期行为，非缺陷。

---

*下一步：等待确认后按 P1-1 → P1-4 顺序实施，每阶段附报告与回滚点。*