# CHANGELOG.md — 渐进式稳定性改造

## P1 批次（A1/A2/A3）｜ 2026-09-14 17:30
| 项 | 号码 | 修改 | 实测 |
|---|---|---|---|
| A1 看护互斥+回收 | P1-1 | health_manager.py 增加 `clean` / `kill-all` 子命令（经 `run_cleanup` 复用 tools\cleanup_stale.py） | clean EXIT 0 保留链不误杀；kill-all EXIT 0 全清 |
| A1 cleanup 防连坐 | P1-1 | tools\cleanup_stale.py：默认**单进程杀**（原对平台用 `/T` 树杀，会连坐"启动器父→活跃子"）；新增**父链保护**（保留实例祖先一律不杀）；新增 **CS_PROTECT_PID 防自杀**（health_manager clean 把自身 PID 经环境变量传给子进程，规避 Get-CimInstance ParentProcessId 竞态错报导致父进程被误杀的回归） | 修复后 clean 不再把触发父进程误杀；实测 PID 6229→35100→25708 链全程保留 |
| A2 bat 路径 | P1-2 | `cf\启动平台和隧道.bat` 已修正 WK=D:\web（旧版指向已迁移旧目录）；Startup VBS 链路指向修复后 bat | 手动运行 bat：新实例锁让位退出，保持单实例 |
| A3 计划任务 | P1-3 | 核验 `MultipleInstancesPolicy=IgnoreNew` 已生效；**不设 ExecutionTimeLimit（保持 PT0S）**：经 dummy 任务实测，Task Scheduler 时限超时用 Job Object **连坐终止全部子进程**（platform/cloudflared 会被连带杀死），故不设时限；僵尸兜底由 A1 锁 + clean 子命令承担 | 双触发（Boot+5min）配合 IgnoreNew 不并发；实测多源触发后收敛单实例 |
| 实例形态新认知 | — | 本项目 pythonw 以「启动器父(1T 低占用) + 活跃子」成对运行，属 Python 双进程启动特征，非僵尸；clean 只回收真孤儿 | 多轮实测确认 |

**本次实测结论**：锁互斥可靠（重复触发自动让位，errno=13 正确让位）；clean/kill-all 子命令可用；僵尸清理不再误杀健康平台/看护；服务全链路（本机 /health 200 + 线上 Access 302）正常。

## 迁移 ｜ 2026-09-13 18:10
项目整体迁移：`C:\Users\Administrator\WorkBuddy\2026-09-11-10-49-22` → **`D:\web`**（3366 文件/235MB，robocopy 校验一致，DB integrity=ok）。
- 需硬改的配置仅 3 处：cf\启动平台和隧道.bat（WK 路径）、启动文件夹 VBS、计划任务 WK_AutoTaskPlatform（pythonw + D:\web\health_manager.py）
- 代码内路径全部基于脚本自身位置动态生成，无需改动；Python venv 仍在原位（不变）
- 切换时同步了旧平台最后写入的数据（WAL），当前服务进程确认来自 D:\web
- 旧目录改名留档：`2026-09-11-10-49-22_已迁移至D_web`（含迁移前最后一版库，确认运行数日后可删）
- 回滚方法：停计划任务 → 把 bat/VBS/计划任务三处路径改回旧目录 → 重启

## 阶段 P3++ ｜ 2026-09-13 17:55
| 文件 | 修改 |
|---|---|
| order_platform.py | **全站 POST 跨站防护**（before_request 全局 Origin/Referer 校验，放行 http/https 双 scheme 以兼容 Cloudflare Tunnel 的 scheme 改写；无头客户端放行；移除原管理接口的逐路由校验避免重复） |
| health_manager.py | 平台改用 **pythonw.exe** 启动（无控制台，免疫控制台关闭事件——与看护 0xC000013A 同源）；平台 stdout/stderr 落盘 cf/platform_stdout.log（崩溃尸检线索） |
| order_platform.py | housekeeping 追加 platform_stdout.log 裁剪 |

背景：今日平台自发崩溃 3 次（14:39/16:29/17:51，均被看护 1 分钟内自愈，无风暴）。事件日志无崩溃记录、资源充足，时间与工具会话活动吻合，判定为控制台关闭事件所致，pythonw 化为根因对策。
验证：异站 Origin POST 403 ✅ / 同站 Origin 放行 ✅ / 无 Origin（curl 类）放行 ✅ / health 200 ✅ / 进程全 pythonw ✅

## 阶段 P3+ ｜ 2026-09-13 09:15
| 文件 | 修改 |
|---|---|
| order_platform.py | **订单密码加密存储**（纯标准库流式加密 enc:v1: 前缀）：下单/批量下单入口加密入库，worker 取用时解密传引擎，启动时自动迁移存量（幂等） |
| backup_manager.py | 恢复逻辑重设计（backup API 反向覆盖，替代删文件）+ ctypes 删除兜底 |
| 计划任务 | 触发周期 15min→5min；执行程序 python.exe→**pythonw.exe**（消除 0xC000013A 控制台关闭致死问题） |
| tests/ | 新增 test_crypto.py（回环/幂等/存量迁移验证） |

安全说明：加密密钥派生自 secrets_store/secret_key.txt——**删除或轮换该文件将导致存量密码不可解密**（订单会以认证失败收场，需用户重新下单）。旧备份文件（08:23 之前）仍含明文密码，将随 7 份轮转自然淘汰。

兼容性：decrypt_secret 兼容历史明文（无前缀原样返回）；业务行为零变化；无新增依赖。

## 阶段 P3 ｜ 2026-09-13 08:14
| 文件 | 修改 |
|---|---|
| backup_manager.py | **恢复逻辑重设计**：废弃"删 WAL + 复制文件"（WAL 被占用时必失败），改为 SQLite backup API 反向覆盖（在线恢复）；新增 _delete_file ctypes 兜底；restore 前自动备份行为保留 |
| tests/（新增目录） | test_health.py、test_kill_tree.py、test_spawn_timeout.py（10 分钟超时熔断 soak）、test_restore_drill.py（真实恢复演练），全部实测 PASS |
| 其他 | 夜间稳定性巡查：00:19 后 8 小时零事件；自动备份链确认正常（每日一次，轮转 7 份） |

兼容性：restore 现在支持平台运行中执行（仍建议停机后做）；无新增依赖。
回滚：backup_manager 恢复逻辑可逆（旧方案见 git 前文）；tests/ 纯增量。

## 阶段 P2 ｜ 2026-09-13 00:13
| 文件 | 修改 |
|---|---|
| order_platform.py | ① 入口换 Waitress（threads=16，ImportError 自动回退开发服务器）② now_str 及全部时间戳统一为 %Y-%m-%d（跨年可排序可审计）③ 新增 same_origin_ok()：/admin/tune、/admin/regcode 拒绝异站 Origin 的 POST（轻量 CSRF 防护，无头客户端放行）④ 管理后台新增"系统状态"卡（平台进程/SQLite/CF 隧道/内存/磁盘/最近备份/最近心跳）⑤ housekeeping 追加 platform_error.log 裁剪 |
| venv | 新增依赖 waitress（纯 Python WSGI，RAM +约 5MB） |
| 部署 | 计划任务 WK_AutoTaskPlatform 增加"每 15 分钟周期触发"（看护进程自身死亡 ≤15 分钟自动拉起，配合互斥体防重复） |

回滚：删 venv 中 waitress 即自动回退开发服务器；其余改动按函数粒度可逆；时间戳变更只影响增量数据，旧数据显示仍可读。

## 阶段 P0 + 部分 P1 ｜ 2026-09-12

### 修改文件
| 文件 | 修改 |
|---|---|
| order_platform.py | ① orders 新增 pid/attempt/heartbeat_at 列（ALTER 幂等）② claim 增加 attempt 计数与 busy 容错 ③ _spawn 记录 PID + 20s 心跳 + 树杀后验证退出 ④ worker 重试策略（超时/崩溃重试 1 次）+ safe_set_order 兜底 ⑤ 启动恢复 recover_stale_orders() ⑥ /health 接口 ⑦ 登录/注册/查课/扫码轻量限流 ⑧ SECRET 外置 secrets_store/secret_key.txt（值保持兼容） ⑨ 默认管理口令自动重置逻辑（当前已是自定义口令，未触发） ⑩ RollingLog 裁剪句柄重建修复 ⑪ worker 空闲退避 2s→5s ⑫ qr_thread 终态自动转 canceled ⑬ housekeeping 补：loguru 日期轮转日志删除、cloudflared.log 裁剪、每日在线备份 ⑭ 登录页移除默认口令提示、异常文本脱敏路径 |
| backup_manager.py（新增） | SQLite 在线备份/恢复/校验/保留 7 份轮转 |
| health_manager.py（新增） | 轻量看护：平台 /health 检查 + cloudflared 存活检查，幂等（互斥体）+ 冷却 + 10 分钟 3 次熔断防风暴 |
| cf/启动平台和隧道.bat | 改为只启动 health_manager（平台与隧道由看护统一拉起），可重复执行 |
| secrets_store/（新增目录） | secret_key.txt（与旧值兼容）；_admin_pw_旧实验残留.txt（由根目录归档，经校验与当前口令不匹配） |
| backups/（新增目录） | 首份备份 platform_20260912_162851.db（integrity=ok, orders=19） |

### 数据库变化
orders 表新增 3 列（可空默认值，向后兼容，旧代码可继续读写）。

### 配置变化
无新增必填配置；settings 新增 last_backup（自动）。

### 新增依赖
无（全部标准库）。

### 新增常驻进程
health_manager.py 一个（约 10-15MB RAM），作为唯一看护入口。

### 对性能影响
空闲轮询增加退避（长时间无单 2s→5s）；心跳 20s/次/单（微写入）；每日一次备份。其余不变。

### 对稳定性影响
消除"永久 running"三条路径；崩溃可自愈（平台+隧道）；重启风暴有熔断；数据有每日备份。

### 对安全性影响
登录/注册/查课限流；密钥外置；日志裁剪补漏；登录页不再提示默认口令；异常回显脱敏。

### 对现有业务影响
fuckCourse 业务逻辑零改动；下单/扫码/查课/管理流程不变。

### 兼容性说明（重要）
SECRET 外置但**值保持原样**，所有现有口令哈希与登录态不受影响。若将来删除 secrets_store/secret_key.txt 轮换密钥，所有用户口令将失效，管理员需重新设置口令。

### 回滚方法
1. 数据库：用 backups/platform_20260912_162851.db 执行 `python backup_manager.py restore <文件>`（先停平台）。
2. 代码：order_platform.py 修改前无 git，可用 order_platform_v5_backup.py 对照回退，或按本清单逆向删除新增函数与调用（init_db 中 3 个 ALTER 列可保留不影响旧版运行）。
3. 启动链：bat 内容恢复为直接启动平台+cloudflared 即可脱离看护。

## 阶段 1（审计 v2） ｜ 2026-09-14
| 文件 | 修改 |
|---|---|
| ARCHITECTURE_AUDIT.md | 第二版重写：基于 D:\web 当前线上代码（1651 行）+ 运行时实测重新审计；P0 6 项已全部核验完成（备份 7 份/恢复/pid-attempt-heartbeat/兜底写/busy 容错/口令/密码 19-19 加密）；新发现并升级 4 项 P1（A1 双实例+僵尸进程、A2 启动 bat 断链、A3 计划任务双触发、R4 密码命令行残留） |
| RECOMMENDED_ARCHITECTURE.md | 第二版重写：方案取舍逐项复核（Job Object 不采用/Waitress 采用/看护采用需修互斥/计划任务采用需收敛触发）；给出 P1-1..P1-4 处置路径与回滚点 |

实测发现摘要：当前任务列表同时存在 2×health_manager（38384 活跃 + 20600 僵尸）与 2×order_platform（40796 活跃 + 16452 僵尸），僵尸均为 1 线程 ~4MB、未绑端口、存活 12h+；cf\启动平台和隧道.bat 仍指向已删除旧目录（C:\Users\Administrator\WorkBuddy\2026-09-11-10-49-22），VBS 链路断；互斥体经 ctypes.windll.GetLastError() 判定 ERROR_ALREADY_EXISTS 不可靠（未用 use_last_error=True）。
数据库：WAL + synchronous=NORMAL + busy_timeout=15s；19 单全终态（14 done/5 failed），0 明文密码；admin 非默认口令。

## 阶段 P1 ｜ 2026-09-14
| 文件 | 修改 |
|---|---|
| health_manager.py | ① 单实例互斥改锁文件方案（O_EXCL + PID/心跳 + 陈旧接管 TTL100s + 释放清理）；② 主循环单轮异常兜底（看护不可被单轮异常打死）；③ 平台拉起失败记日志；④ **不引入 PowerShell 枚举与启动杀进程**（实测该类行为会触发本环境行为监控导致看护被外部中断；旧 14.5h 稳定实例行为=无 PS/无杀进程） |
| tools/cleanup_stale.py（新增） | 按需清理游离/僵尸本项目实例（保留健康平台+锁持有者）；--kill-all 全清；诊断/修复用 |
| cf/启动平台和隧道.bat | WK 路径修复为 D:\web（旧目录已不存在，原 bat 导致"双击启动"失效） |
| 计划任务 WK_AutoTaskPlatform | 核验 MultipleInstances=IgnoreNew 已生效（无需改动）；动作保持 pythonw + health_manager.py |
| cf/health_manager_v2_lockfile_20260914.py | 存档：含启动 survey（杀僵尸）+PowerShell 枚举的 v2 变体；包装器环境收敛验证通过，计划任务 pythonw 环境反复被外部中断（疑行为监控），建议排除/白名单后评估启用 |

### 数据库变化
无。

### 配置变化
无新增必填配置；计划任务/启动配置同上表。

### 新增依赖
无（全部标准库）。

### 新增常驻进程
无新增（工具脚本为按需执行，非常驻）。

### 对性能影响
锁文件每轮 30s 心跳一次小文件写入（微乎其微）；无新增轮询。

### 对稳定性影响
修复"命名互斥体去重不可靠"与"启动 bat 断链"；长效僵尸回收能力经独立脚本提供；看护主循环增加不死兜底。

### 对安全性影响
无新增攻击面；bat 路径修正减少误入口；清理脚本仅本机可执行。

### 对现有业务影响
fuckCourse 零改动；平台/隧道/下单/扫码/查课流程不变；看护行为与旧版兼容（唯去重机制升级）。

### 回归/验证
- py_compile 通过；/health 200；锁互斥并发让位实测；cleanup_stale 清理实测通过；bat 字节级校验。
- 诚实声明：本工具会话对计划任务触发进程存在外部中断（详见 TEST_REPORT P1 节），僵尸 pythonw 现象与看护代码无关（旧版复现），锁文件保证活跃侧唯一，僵尸由清理脚本/重启回收。

### 回滚方法
1. health_manager.py：还原 cf/health_manager.py.bak_20260914（旧版互斥体看护）。
2. bat：还原 cf/启动平台和隧道.bat.bak_20260914。
3. tools/cleanup_stale.py：删文件即可。

## 待办（下一阶段）
- R4（P1-C）：订单密码改经环境变量传引擎子进程，需用户确认引擎侧（fuckCourse/chaoxing/main.py 参数兼容）口径后实施；boundary 冲突前不对引擎做任何修改。
- P2：备份独立调度（平台停机断更）；worker 明细入系统状态卡；存量时间戳补年份。
- P3：dated 日志大小上限；根目录 _*.log/_*.zip 归档；/qr 鉴权可选。

## 阶段 P1-R4 ｜ 2026-09-14（凭据去命令行化，已确认引擎最小兼容口径）
| 文件 | 修改 |
|---|---|
| order_platform.py | ① run_chaoxing：不再把解密的明文口令放命令行，改注入环境变量 WK_ACCOUNT/WK_PASSWORD（任务子进程 cmdline 不再含 -p 明文）；② query_courses：查课子进程同样改经环境变量传递凭据（argv 置空） |
| fuckCourse/chaoxing/main.py（最小兼容） | init_config 增加凭据回退：命令行 > 环境变量(WK_ACCOUNT/WK_PASSWORD，平台注入) > 配置文件；默认引擎 config 未含签信用途；不涉及任何业务/风控逻辑 |
| tools_query_courses.py | chaoxing/zhs 模式 argv 为空时回读 WK_ACCOUNT/WK_PASSWORD |

### 验证（不触达第三方平台）
- py_compile 三个文件通过；
- 引擎 init_config 单元测试 3 例全过：仅 env→env 生效；CLI 优先于 env；仅配置→config 生效；
- 静态核验：order_platform.py 中不再出现 "-p" 明文传递；
- /health 200（平台以新代码运行，DB/队列正常）。

### 兼容性
- 引擎回退逻辑向后兼容：手动 python main.py -u/-p 仍按 CLI 优先执行；
- 旧备份：*.bak_20260914_R4（order_platform / tools_query_courses / chaoxing main）；
- 回滚：还原上述 .bak 文件并重启平台即可；数据库零变化。

### 说明
- 仅 chaoxing 主任务与查课链路（原明文位置）；zhs 链路本就无口令（扫码/配置）。
- 环境提示：本工具会话对长驻 pythonw 存在周期性外部中断（详见 TEST_REPORT P1 节），正式机经计划任务/开机自启可稳定自愈（历史 14.5h 连续 + 3 次自愈）。

## 阶段 P2 ｜ 2026-09-14
| 文件 | 修改 |
|---|---|
| health_manager.py | **每日补备份**：看护循环内低频检查 settings.last_backup（与平台 housekeeping 共用时间戳防每日双份），间隔>24h 且冷却窗口外时调用 backup_manager（在线备份+校验+7 份轮转）→ 平台停机也备份。失败 1h 冷却重试。纯标准库（sqlite3 只读一次/轮） |
| order_platform.py | ① 管理后台"执行中队列"扩展列：**PID / 内存(MB, proc_mem_mb via kernel32.K32GetProcessMemoryInfo 纯标准库) / 最近心跳 / 运行时长**；② 启动段新增 migrate_timestamp_years()：存量 %m-%d %H:%M:%S→%Y-%m-%d %H:%M:%S（幂等，仅补 2026）；③ 新展示函数 _dur_txt |
| tools/cleanup_stale.py | kill 语义修正：看护实例只单进程终止（避免 /T 连带杀其子进程=平台），平台实例才树杀 |

### 数据库变化
orders 表 created_at/started_at/finished_at 存量 57 项补全年份（+2026-），无年份残留 0；迁移前已备份 platform_20260914_101933.db（integrity=ok, orders=19）可回滚。

### 验证
- py_compile 全部通过；proc_mem_mb 实测返回进程工作集（13.8MB）；_dur_txt 三种输入正确。
- 看护补备份判定实测正确：距上次备份 15.7h<24h 未触发（防双份），下次自动补位在 09-14 18:40+。
- /health 200，平台以新代码运行，DB/队列正常；var（环境侧重复实例现象与代码无关，见 TEST_REPORT P1 节）。

### 回滚
1. 年份迁移：ackup_manager.py restore backups\platform_20260914_101933.db。
2. 备份补位：还原 health_manager.py.bak 前文件；admin 列/迁移：还原 order_platform.py.bak_20260914_R4 前版本（备份于 R4 阶段）。

## 阶段 P3 ｜ 2026-09-14
| 文件 | 修改 |
|---|---|
| order_platform.py | ① R6：loguru 日期轮转日志（chaoxing.*.log 等）在按天删除前先 tail_keep 512KB 裁剪（此前删除前可长到 10MB+）；② /qr/oid、/qr_status/oid 增加登录鉴权（require_login；原 uuid4 低危面收敛） |
| 根目录 | 187 个调试残留文件（_*.txt/_*.log/_*.py/_*.json/_*.html/*.zip/*.tgz，约 22MB，含可能的口令推断材料）归档至 _archive_dev\（不删除，供自查；主程序/文档/回滚备份未受影响） |
| tools/stop_platform.ps1（新增） | 停机：停计划任务 → 结束本项目 python/隧道 |
| tools/start_platform.ps1（新增） | 启动：拉计划任务（看护统一拉起平台+隧道），附 /health 提示 |

### 验证
- py_compile 通过；平台以新代码运行，/health 200；匿名访问 /qr/xxx 返回 302→/login（鉴权生效）✅
- 根目录清理后仅剩主程序/文档/回滚 .bak，目录整洁。

### 回滚
- R6/鉴权：还原 order_platform.py.bak_20260914_R4 前的版本（先按当前 .bak 保留链）；归档文件可由 _archive_dev 移回原位。

## 收尾说明（P0–P3 全阶段）
各阶段回滚点：health_manager（cf\health_manager.py.bak_20260914 及 lock 版演进）、order_platform（.bak_20260914_R4）、迁移（backups\platform_20260914_101933.db）。
剩余开放项（非 P3 范围）：R4 引擎侧 Confirm；v5 备份文件勿运行告警（文档标注）；/query 前缀枚举（uuid4 低危，标注）；模块化按需（已有 backup/health/tools 拆分）。

## 交付 ｜ 2026-09-14 11:30
新增 PROJECT_STATUS.md：全项目状态详单（架构/目录/数据库/生命周期/改造成果/回滚点/未验证项/设计取舍/开放项），供外部审查查缺补漏。

## 交付 ｜ 2026-09-14 12:0x
新增 PROJECT_FULL_STATE.md（45KB，43 节+状态矩阵+证据索引）：全量状态档案（目录/模块/路由/生命周期/状态机/DB/Schema/事务/Worker/进程树/隔离环境/配置/日志/超时重试/异常/清理/启停/恢复/依赖/安全/资源/并发/一致性/隐式行为/未知项/需验证项），供第三方 AI 独立审计。纯只读产出，未改动任何代码。

## 交付 ｜ 2026-09-14（审计轮，无代码改动）
新增 SECOND_ROUND_AUDIT.md：第二轮独立审计报告（按"第二轮审计总指令"：仅审计+报告，至§45结论止）。要点：总体=基本稳定可继续运行；新增确认 P0×2（运行期订单进程死亡无收敛；recover 不校验 kill 结果）与 P1×5（日志 XSS、明文残留、限流 IP 伪造、restore 在线无防护、引擎日志无脱敏）；等待确认后再实施小改动。

## 第二轮修复 ｜ 2026-09-14（按第二轮审计报告确认实施，3 项小改动，可回滚）
| 项 | 修改文件/函数 | 修改内容 | 验证 |
|---|---|---|---|
| P0-1 | order_platform.py 新增 order_watchdog() + 启动线程 | 运行期每 30s 扫描 status='running' AND pid!=0：pid 存活→不干预；pid 死亡→依 attempt 收敛（<1 回 pending / else failed），复用 safe_set_order 兜底 | 单测空转（0 running 只读不写）✅；正常流程验证（页面 200） |
| P0-2 | ecover_stale_orders | 树杀结果校验：_kill_tree 未确认退出（5s 内仍存活）→ 不再盲目回 pending，直接标 failed + note 说明（杜绝旧进程+新进程同单双执行窗口） | 代码级逻辑验证 [运行验证-极端时序] |
| P1-1 | 新增 esc() HTML 转义 helper，接入 15 处输出点 | /query、/my、order_detail（log_tail、note、account、courses、pname、risk_flags、qr_state）、admin（username、account）、my_orders/admin 的 pname/product | esc 单测 4 例 ✅；残留直插扫描清零（管理员可控 product code 除外）✅ |

验证汇总：py_compile ✅；esc 单测 ✅；watchdog 空转 ✅；平台重启后 /health 200、/login 200（转义未破坏渲染）✅。

修改影响：不涉及数据库/状态词汇/进程模型/业务流；无新依赖、无常驻新增（watchdog 为平台内 daemon 线程，30s 一次只读查询）。
回滚：还原 order_platform.py.bak_20260914_Round2。

## 第二轮修复（剩余 P1）｜ 2026-09-14
| 项 | 文件 | 修改 |
|---|---|---|
| P1-2 明文残留 | 文件整理 | uckCourse\cookies_13375472780.json（无代码引用）→ _archive_dev；_admin_pw_verify.txt / _set_admin_pw.py / _verify_admin_pw.py / _test_zhs_cookie.py / _zhs_cookie_test.txt（口令推断材料）→ secrets_store\_archive_dev（更安全位置，同旧先例）；uckCourse\config.json 与 .zhs_cred 为引擎运行时依赖，**保留**（边界） |
| P1-3 限流 IP | order_platform.py ate_limit | IP 信任策略：仅当对端=回环（经本机 cloudflared 回源）才取 CF-Connecting-IP（CF 边缘设置、不可伪造）；其余来源一律用 TCP 对端地址，杜绝伪造头部绕过公网限流 |
| P1-4 restore 在线防护 | backup_manager.py | 新增 _platform_online()（8766 探测）；estore_database 平台在线时**拒绝**（报错提示先停平台）；支持 estore <文件> --force（演练/停机后） |
| P1-5 日志脱敏 | order_platform.py RollingLog._scrub | 订单 log.txt 落盘前正则遮蔽 password/pwd/passwd/token/api_key/secret/authorization <sep> <值> → 字段=<sep>****；仅平台侧写盘，不动引擎；白名单外（speed 等）不误伤 |

验证：py_compile ✅；scrub 6 用例（含保留分隔符、不误伤 speed）✅；restore 在线拦截实测（在线→RuntimeError；--force→放行到文件校验）✅；平台重启 /health 200 /login 200 ✅。
回滚：还原 order_platform.py.bak_20260914_Round2；文件移动可从对应目录移回。

## 第二轮 P2（精选落地）｜ 2026-09-14
| 项 | 文件 | 修改 | 验证 |
|---|---|---|---|
| 查课并发护栏 | order_platform.py | 新增 _QUERY_SLOT = BoundedSemaphore(2)：query_courses 外层加并发限制，忙等 30s 后快速失败（"查询服务繁忙"），原逻辑移至 _query_courses 内层 try/finally release——防止 16 个请求线程全部被 180s 查询占满 | 信号量=2 语义单测 ✅；结构检查 ✅；平台重启 /health 200 ✅ |
| 管理页 tasklist 超时 | order_platform.py | subprocess.run(["tasklist"],…, timeout=10)（原无超时可阻塞请求线程） | 编译/加载 ✅ |

### 评估后保留不改（附理由，诚实记录）
1. FK/CHECK 约束：SQLite 加约束须重建表（orders），当前全部写点已审计合法、无非法数据；收益<风险 → 不改。
2. 备份双实现收敛：housekeeping（进程内）与看护（进程外）两处调度，已共用 last_backup 防双份；收敛收益无法证明、改动需双重启 → 不改（文档标注为已知冗余）。
3. Waitress 请求超时：请求经 Cloudflare（CF 层已有保护），Waitress 默认 channel_timeout=120；显式配置收益不明确且行为需长测 → 不改。
4. 心跳双写（claim/_spawn）：语义不同（启动 vs 周期），非冗余 → 不改。

回滚：还原 order_platform.py.bak_20260914_R2P2。

## 交付 ｜ 2026-09-14（收尾）
新增 FINAL_ACCEPTANCE.md：最终验收清单（在线快照/36 项对照/两轮落地清单/回滚点索引/剩余开放项/正式机验收步骤）；PROJECT_FULL_STATE.md 追加第二轮增量注记。

## Q:\\Web 整理 · 本地基建阶段 ｜ 2026-09-14
新增模块：path_manager.py（逻辑路径统一，基于 __file__）、crypto_manager.py（AES-256-GCM + scrypt KDF + KEK/DEK + Credential Manager + Recovery + 错误分类）；tests/test_crypto_bundle.py（13 项密码学单测 PASS）。
新增文档：ARCHITECTURE_AUDIT_QWEB / PATH_AUDIT / FILE_CLASSIFICATION / CACHE_CLASSIFICATION / CRYPTO_DESIGN / MIGRATION_PLAN（全部基于真实读取）。
状态：Q 盘未挂载（OpenList 缺失）——按用户决策先落地本地基建；sync_manager / SQLite 加密备份 / migration_manager 待 Q 挂载后执行。
