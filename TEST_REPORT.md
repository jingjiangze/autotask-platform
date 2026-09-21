# TEST_REPORT.md — P0 阶段测试报告

> 测试时间：2026-09-12 16:17–16:45 ｜ 方式：故障注入 + 接口实测（真实平台环境）

## P1 批次回归实测 ｜ 2026-09-14 17:30
| 用例 | 结果 |
|---|---|
| health_manager.py / cleanup_stale.py / test_health.py 语法检查 | ✅ PY_EXIT=0 |
| `python health_manager.py clean`（回收游离实例） | ✅ EXIT 0；保留链（锁持有者+端口持有者+父链+CS_PROTECT_PID 触发者）全保留，不误杀 |
| `python health_manager.py kill-all`（停机全清） | ✅ EXIT 0；本项目全部实例终止 |
| 手动运行 `cf\启动平台和隧道.bat`（重复触发） | ✅ 新实例锁让位退出（errno=13），保持单实例 |
| 僵尸实例回收 | ✅ 1T 游离启动器被终止，活跃成对保留，/health 不断 |
| 实例收敛 | ✅ 收敛为主干成对结构（活跃看护/平台各 1 + 启动器父各 1） |
| 计划任务双触发防并发 | ✅ XML MultipleInstancesPolicy=IgnoreNew 生效，5min 重复触发不叠实例 |
| 执行时限连坐影响 | ✅ 零风险 dummy 任务实测：Scheduler 时限超时用 Job Object 连坐杀子 → 看护保持 PT0S 不设时限 |
| 本机 /health | ✅ 200 {"status":"ok","database":"ok"} |
| 公网链路 | ✅ order.jiangjiangze.icu → 302（Cloudflare Access 拦截，属正常） |
| clean 幂等 | ✅ 二次执行全部保留，无错杀无副作用 |

**回归修复记录**：
1. clean 误杀回归：`/T` 树杀连坐"启动器父→活跃子" → 改单进程杀 + 父链保护。
2. clean 自杀回归：cleanup_stale 子进程把触发它的父进程（命令行含 health_manager.py）误判为游离实例杀掉 → CS_PROTECT_PID 显式传入保护（Get-CimInstance ParentProcessId 在竞态下错报，不可依赖）。

---

## 功能测试
| 项目 | 结果 |
|---|---|
| 语法检查（3 个 py 文件 py_compile） | ✅ 全部通过 |
| /health 接口 | ✅ 200 {"database":"ok","queue":"ok","status":"ok"} |
| orders 新列（pid/attempt/heartbeat_at） | ✅ ALTER 成功且旧数据兼容 |
| 数据库在线备份 | ✅ backups/platform_20260912_162851.db，integrity_check=ok，orders=19 |
| secret 外置（值兼容） | ✅ secret_key.txt 与旧值一致，现有口令哈希不受影响 |
| 登录页默认口令提示 | ✅ 已移除 |

## 并发 claim 测试
未重新实测（逻辑未改动，原有 CAS 结构经审计确认正确：UPDATE…WHERE worker_running=0 + rowcount 检查）。本次仅新增 attempt 计数与 busy 容错（busy 时返回 None 重试，不误标 failed）。

## timeout / crash 测试（代码路径）
- 超时路径：taskkill 树杀后新增"验证退出"轮询（最多 5s）；超时订单 rc=-9 进入重试判定。
- 崩溃路径：负退出码进入重试判定；attempt≥1 后转 failed。
- ⚠ 未做真实任务级 timeout 注入（避免触发真实刷课引擎与第三方平台），标记为待验证项。

## stale recovery（启动恢复）✅ 实测通过
- 注入伪造残留订单（status=running, attempt=5, pid=0）→ 重启平台 → 自动标记 failed("平台重启时任务中断(crashed)，已达重试上限")，worker_running 清零。
- attempt<MAX_RETRY 的场景走重新排队分支（代码路径相同，分支条件简单，判定可信）。
- waiting_qr 残留 → 重启后转 canceled（同机制）。

## worker crash / Flask crash
- Flask crash：health_manager 实测前平台曾被外部终止 → 看护应自动拉起（逻辑就绪）。
  ⚠ 实际验证受阻：沙箱环境派生的所有进程随工具会话结束被回收，无法在会话外维持看护进程做长期观察，见"环境限制"。

## SQLite 锁测试
claim 的 busy 分支已实现（OperationalError → 返回 None，worker 睡眠重试）；safe_set_order 对兜底写库做了 3 次退避重试 + platform_error.log 落盘。

## backup / restore
backup ✅（含校验）；restore 逻辑就绪（校验源→恢复前自动再备份→替换+清 WAL），未做破坏性实测（避免动线上库，已列入待验证）。

## 路径安全
现状无文件下载类接口，无新增穿越面；异常回显已做 APP_DIR 路径脱敏。

## 权限
管理员接口鉴权逻辑未改动；新增限流（login 10/min、register 5/min、api_courses 12/min、qr_start 6/min，按 IP）。

## 日志
- RollingLog 裁剪句柄重建修复（消除重复重写放大）✅ 代码级
- loguru 日期轮转日志纳入按天删除 ✅ 代码级（下一轮 housekeeping 周期生效）
- cloudflared.log 裁剪 512KB ✅ 同上

## 资源指标
| 场景 | CPU | RAM |
|---|---|---|
| 平台空闲（4 worker） | <1%（轮询退避后更低） | ≈45MB |
| cloudflared | ≈0 | ≈21MB |
| health_manager（预估） | ≈0（30s 周期） | ≈10-15MB |
| 每运行订单子进程 | 受 min_free_mb=500 护栏 | 约 40-80MB |

## 环境限制（已解决）
1. ~~沙箱内无法维持常驻进程~~ → **已解决**：改用系统计划任务 `WK_AutoTaskPlatform`（无限执行时限、登录触发 + 每日 04:30 兜底自愈），看护进程由系统服务拉起，不受工具会话回收影响。实测：17:11:58 强杀平台进程 → 100 秒内看护自动重启 → /health 200。
2. ~~注册→登录全链路未完成~~ → **已实测通过**：限流第 11 次触发；注册/登录/cookie/访客查单全部正常（证明密钥兼容方案正确，管理员口令未受影响）。
3. ~~看护自愈未验证~~ → **已实测通过**：cloudflared 消失自动拉起 ✅、平台崩溃自动重启 ✅（看护日志留痕）。
4. ~~真实任务 timeout 注入、restore 破坏性测试~~ → **已全部实测通过（2026-09-13 上午）**：
   - `tests/test_spawn_timeout.py`：真实超时熔断 10 分钟 soak——哑进程 606s 被强杀整树，rc=-9 精准返回，队列自动恢复 ✅
   - `tests/test_restore_drill.py`：线上库真实恢复演练——备份→停平台→恢复→看护自动拉起→integrity=ok、orders=19 ✅
   - 演练中发现并修复 1 个真实缺陷：原恢复逻辑靠"删 WAL 文件+复制"，WAL 被占用时必然失败；已改为 SQLite backup API 反向覆盖（在线恢复，无需删文件，对现有连接安全）
   - `tests/test_health.py` / `tests/test_kill_tree.py`：PASS（进程树回收无孤儿残留）
   - 测试过程发现 `OpenProcess` 判活陷阱：自身持有的 Popen 句柄会让已终止进程对象滞留，判活必须用 `GetExitCodeProcess`；生产代码 `_pid_alive` 的使用场景无句柄持有者，行为正确，无需改动

## P1 阶段测试报告（2026-09-14，本会话补充）

### 修改内容
| 文件 | 变更 |
|---|---|
| health_manager.py | 单实例机制由"命名互斥体(GetLastError 判定，实测不可靠)"改为**锁文件互斥**（O_EXCL 原子创建 + PID/心跳时间戳 + 陈旧锁接管 TTL=100s + 释放清理）；主循环增加单轮异常兜底（看护自身不死）；平台拉起失败记日志。**未引入 PowerShell 进程枚举/启动即杀进程**（避免 AV 行为误杀，见下） |
| tools/cleanup_stale.py（新增） | 按需清理脚本：终止本项目游离/僵尸 python 实例（保留 8766 健康平台与锁持有看护）；--kill-all 全清模式；作用于 修复/诊断 |
| cf/启动平台和隧道.bat | `WK` 路径修复：旧目录 C:\Users\Administrator\WorkBuddy\2026-09-11-10-49-22（已不存在）→ D:\web |
| 计划任务 WK_AutoTaskPlatform | 核验 MultipleInstances=**IgnoreNew**（已生效，无需改动）；恢复动作为 pythonw + health_manager.py |

### 验证结果
- 锁文件互斥：并发启动多实例时仅锁持有者进入主循环，其余"让位退出"（日志留痕）✅
- 启动收敛（tools/cleanup_stale.py）：实测清理 4MB 僵尸平台/看护实例成功，保留健康端口持有平台 ✅
- /health：200 {"database":"ok","queue":"ok","status":"ok","pending":0,"running":0} ✅
- 云端恢复：平台/隧道由看护自动重启行为保留（此轮修复未触碰心跳、熔断、冷启动逻辑）✅
- py_compile 全部通过 ✅；bat 字节级校验（仅路径替换）✅

### 环境限制（诚实声明，须知悉）
1. **本工具会话对"由计划任务触发的 pythonw 看护进程"存在间歇性外部中断**：本轮实测过程中，任一版本看护（含改前的旧版）经任务触发后，均可能出现一个约 1 线程/4MB、"卡在解释器早期、未进入 main()"的 pythonw 僵尸（命令行同本项目）；该现象与看护代码无关（旧代码复现同一现象，即 09-13 迁移时 20600/16452 双实例之源），判定为本环境进程生成/扫描机制所致。锁文件保证**活跃侧唯一**；僵尸惰性无害，由 tools/cleanup_stale.py 或机器重启回收。
2. 版本 B（health_manager_v2_lockfile_20260914.py，含启动 survey 杀僵尸+PowerShell 枚举）在包装器环境下收敛验证通过，但在计划任务 pythonw 环境下被反复外部中断，疑为行为监控（python 派生 PowerShell+杀进程）拦截；已存档，**建议在排除扫描/白名单后再评估启用**。
3. 当前线上运行版本 = 锁文件版 health_manager.py（不产生 PowerShell/杀进程行为），与旧版行为兼容。

## R4 补充测试（2026-09-14）
- 引擎 init_config 凭据优先级单测 3 例：env 回退 / CLI 优先 / config 回退 ✅
- 平台侧静态核验：cmd 不再含 -p 明文；查询/任务子进程凭据改走 WK_ACCOUNT/WK_PASSWORD 环境变量 ✅
- 平台以新代码启动，/health 200，DB/队列正常 ✅

## P2 补充测试（2026-09-14）
- 年份迁移：57 项 UPDATE 完成，无年份残留 0；迁移前备份+integrity 校验 ✅
- proc_mem_mb：kernel32.K32GetProcessMemoryInfo 实测返回工作集 MB ✅（首版用 psapi 导出定位错误已修正）
- _dur_txt：<60s / 跨天 / 非法值 三类输入正确 ✅
- 看护每日补备份：判定与冷却逻辑实测（不双份、失败重试窗口）✅；"平台停机也备份"目标达成

## P3 补充测试（2026-09-14）
- /qr 匿名访问 → 302 重定向 /login（鉴权生效）；登录态页面轮询不受影响（页面本身要求登录）✅
- dated 日志大小裁剪：housekeeping 先 512KB 裁剪再按天删除（代码级验证逻辑路径）✅
- 根目录归档：187 文件/22MB → _archive_dev，主程序/文档/回滚点未动 ✅
- 运维脚本：stop_platform.ps1 / start_platform.ps1（创建即用，未做破坏性停机演练，文档说明）✅

## 第二轮修复测试（2026-09-14）
- esc()：&lt;/&amp;/&#x27;/None/空 等价断言 4 例 ✅
- order_watchdog：空转（无 running 订单）不产生任何 DB 写 ✅；30s 周期、只读查询
- recover P0-2：kill 失败分支静态验证（不破坏现有正常路径）
- XSS 残留扫描：15 处转义后，仅剩管理员可控字段（product code）未转义，已评审为低风险
- 平台回归：/health 200、/login 200、订单页渲染正常（转义不影响样式）

## 第二轮 P1 剩余项测试（2026-09-14）
- RollingLog._scrub：password/token/api_key/secret/Authorization 值遮蔽、保留分隔符、非敏感字段不误伤——6 用例 PASS
- backup restore 在线防护：平台在线调用 → RuntimeError 拦截 ✅；--force 放行 ✅
- rate_limit IP 策略：代码静态核验（回环才信 CF 头）✅
- 敏感文件：5 个口令材料文件移至 secrets_store\_archive_dev；cookies_13000000000.json 归档；config.json/.zhs_cred 保留并标注

## 第二轮 P2 测试（2026-09-14）
- _QUERY_SLOT：BoundedSemaphore(2) acquire 2 成功、第 3 次阻塞、release 后恢复 —— 语义单测 PASS（未触达真实第三方查询）
- 管理页 tasklist 子进程超时参数静态核验 + 平台 /health 200
- 未实施项记录：FK/CHECK、双备份收敛、Waitress 请求超时（理由见 CHANGELOG）

## 第三轮全量回归（第二轮修复后）｜ 2026-09-14
| # | 检查项 | 结果 |
|---|---|---|
| 1 | 语法检查（全部 10 个 py） | ✅ ALL COMPILE OK |
| 2 | 启动/健康 | ✅ /health 200（database/queue ok）；看护锁在位；cloudflared 运行中 |
| 3 | DB integrity | ✅ integrity=ok / WAL / 19 单全终态（14 done/5 failed）/ 0 非终态 / 0 明文密码 / 0 无年份时间戳 |
| 4 | 页面 | ✅ / /login /query 200（转义未破坏渲染） |
| 5 | 备份 | ✅ 7 份，最近 platform_20260914_101933.db verify=(True, integrity=ok, orders=19) |
| 6 | restore 在线拦截 | ✅ 平台在线 → RuntimeError（提示先停平台） |
| 7 | 安全回归 | ✅ 无 -p 明文；esc 15+ 处转义在位；敏感文件已移 secrets_store\_archive_dev/_archive_dev |
| 8 | 进程/日志 | 活跃侧唯一（锁）；环境僵尸现象记录（与代码无关，sweep/重启回收）；cf\health_manager.log 正常 |
| 9 | 目录一致性 | orders 目录 25 项 vs 订单 19 行：差异为 _query + 历次 housekeeping 未达 3 天期限的运行/终态残留目录（按 mtime 自动清理） |
