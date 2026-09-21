# SECOND_ROUND_AUDIT — 第二轮独立技术审计报告（仅审计阶段）

> 审计日期：2026-09-14 ｜ 依据：当前源码（D:\web, order_platform.py 1739 行等）+ 数据库只读核验 + 运行时/计划任务实测 + 文档仅作线索
> 本报告只到「§45 最终一句话结论」止。**未修改任何代码、未删除/移动任何文件。**
> 标注：[确认] 源码或运行时确证｜[推断] 代码推导｜[未知] 无信息｜[运行验证] 需实际运行
> 对照原则：源码 > 数据库 > 运行时 > 配置 > 文档；文档与源码冲突以源码为准。

---

# 1. 总体结论

**基本稳定，可继续运行。** 证据：当前 19 单全终态（14 done / 5 failed），无 pending/running 残留 [确认-实测]；终态订单 pid/heartbeat 均归零 [确认-实测]；平台 /health 200；备份 7 份最新 09-14 10:19；看护/隧道在线。

同时存在 **2 项应尽快小改的 P0**（运行期一致性闭环缺失、恢复路径 kill 结果未校验）与若干 P1。均可用"一个函数/几行"级别修改解决，无需重构或换栈。**不建议继续扩大使用规模前**先处理 P0（见 §3）。

# 2. 第一轮结论复核（逐项反证）

| # | 第一轮声明 | 第二轮结论 | 证据 | 风险 |
|---|---|---|---|---|
| 1 | 备份可用、7 份轮转、可恢复 | **确认** | backup_manager.py h37/78；backups 7 份；09-13 restore 演练（TEST_REPORT） | 低；restore 在线执行无防护（见 P1-4） |
| 2 | CAS 原子领单、无重复 claim | **确认** | claim_order h692-635：同连接 SELECT+`UPDATE…WHERE worker_running=0`+rowcount；WAL 下写可见最新 [推断] | 低 |
| 3 | pid/attempt/heartbeat 列存在且写入 | **部分解决** | 列存在于 PRAGMA；_spawn 写 pid/hb h556；心跳写 h571-574 | **heartbeat 无读取方、无 stale 判定（见 P0-1）** |
| 4 | 启动恢复 recover_stale_orders | **部分解决** | h276-302 存在 | **不校验 _kill_tree 结果（见 P0-2）；仅启动时收敛，运行期无限期不一致** |
| 5 | 兜底写 safe_set_order | **确认** | h216-229 | 低；DB 完全不可写时只能写 platform_error.log |
| 6 | 四口限流 | **确认（存在缺陷）** | h308-321 | IP 取自 CF-Connecting-IP 可伪造（见 P1-3） |
| 7 | 口令/密码加密 | **确认** | hash_pw h188；enc:v1: h349（0 明文实测） | 静态盐/非标准加密=技术债（P3） |
| 8 | 看护锁文件+熔断 | **确认（含环境现象）** | health_manager h111/37-40 | 僵尸 pythonw 环境现象未根除、依赖清扫/重启 [运行验证] |
| 9 | waiting_qr → canceled 终态 | **确认** | qr_thread h449-459 | 低 |
| 10 | 日志全裁剪 | **确认** | housekeeping + tail_keep 多处 | 低；引擎输出原样落盘（P1-5） |
| 11 | 时间戳补年份 | **确认** | migrate_timestamp_years h390；57 项/0 残留实测 | 低 |
| 12 | /qr 登录鉴权 | **确认** | 实测匿名 302→/login | 低 |
| 13 | 环境"僵尸实例与代码无关"声明 | **确认（现象）** | 本会话 3 版代码同现象复现；正式机历史 14.5h 稳定 | [运行验证] |
| 14 | 文档自评"全部完成" | **需修正** | 见 §3 P0 | 文档不能替代运行期闭环 |

# 3. P0 / P1 / P2 / P3（仅真实问题）

## P0 —— 严重，建议立即小改

### P0-1 运行期订单进程死亡无检测（DB/running + PID/dead 可无限期并存）
- 证据：heartbeat_at 写入位于 `_spawn` h571-574（20s）；**全项目无任何读取 heartbeat_at 或运行期探测 running 订单进程的代码**；`recover_stale_orders` 仅在平台启动 h276 执行。
- 影响：任意时刻 worker 被杀 / 引擎子进程被外部强杀 / 平台进程存活而子进程异常消失 → 订单卡 running，直到下一次平台重启才被收敛；若长期不重启，永久不一致。
- 触发条件：子进程 OOM、防病毒误杀、用户 taskkill、引擎自杀等。
- 是否自动恢复：**否（仅重启时）**。
- 最小修复方向（不在此阶段实施）：housekeeping 或轻量后台检测循环中，对 `status='running'` 行做 `pid 存活` 校验 → 死则按 attempt 规则收敛（复用 h296-301 逻辑）；或 worker 开始任务前/轮询内自带检测。
- 修改范围：1 个函数 + 1 处调度；风险低；可回滚。

### P0-2 `recover_stale_orders` 不校验树杀结果（潜在"旧进程+新进程"双执行窗口）
- 证据：h292-301：
  ```python
  alive = _pid_alive(o["pid"])
  if alive:
      _kill_tree(o["pid"])          # 返回值被忽略；5s 内未确认退出→返回 False
  if (o["attempt"] or 0) < MAX_RETRY:
      ... status="pending" ...
  ```
  `_kill_tree` h245-256 最多验证 10×0.5s；若失败仍继续 pending。
- 影响：极端时序（杀不掉的子进程 + 新 worker claim 同一单）下同一订单被新旧两个进程同时执行——指令 §7 的最高风险方向。当前引擎为线性执行、单层进程，概率低但存在。
- 是否自动恢复：恢复路径本身制造了该窗口（次要）。
- 最小修复方向：kill 未确认 → 等待重试或直接标 failed（不再回 pending）；并对 "pid 已死但 status=running" 在 start 时二次确认。
- 修改范围：`recover_stale_orders` 内 ≤10 行；可回滚。

## P1 —— 明显风险

- **P1-1 XSS（存储型，经日志）**：`order_detail` 将 `log.txt` 尾 8KB 直接嵌入 HTML（h1303-1308），未转义；引擎输出若含 HTML/引号（引擎打印账号名等）可注入脚本；`note`、`username`、二维码状态等页面同样 f-string 直插（h899 模板），无 CSP 头，无转义函数。
- **P1-2 敏感明文残留**：`fuckCourse\config.json`（chaoxing.common 明文账号口令）、`cookies_13000000000.json`（手机号命名的明文 cookie）、`.zhs_cred`、`_archive_dev`（含口令推断材料，无 ACL）。均非 static/Web 可达，但本机共享风险存在。
- **P1-3 限流 IP 可伪造**：`rate_limit` h308-311 取 `CF-Connecting-IP` 头优先；直接访问 127.0.0.1 时可伪造该头绕过限流（公网经 CF 会被重写，风险集中于本机/内网场景）。
- **P1-4 backup restore 在线执行无防护**：`restore_database` h78 会反向覆盖线上库；若 platform 在线写入并发 → 目标库锁冲突（busy_timeout=5s 默认）可能失败或产生半写窗口；README 仅"建议停机"。
- **P1-5 引擎日志无脱敏**：订单 log.txt 记录引擎原样输出（含账号名），3 天清理期内本机可读；无 token 证据但无审计保证。

## P2 —— 技术债

- 查课接口同步阻塞请求线程最长 180s；并发查课可耗尽 Waitress 16 线程 [推断]。
- Waitress 无请求超时；无应用层慢客户端保护。
- 单一硬超时 180min；无 soft/hard 双段、无 step 超时（[设计取舍] 但列为债务）。
- 心跳双写（claim h630 + _spawn h571）；备份两处调度（housekeeping h772 + 看护 h148）虽共用 last_backup 防双份，但双实现维护成本。
- 无 FK/CHECK 约束（status/worker_running/attempt 无 DB 级保障）。
- 管理页 `subprocess.run(["tasklist"])`（h1498）无 timeout → 异常时可能阻塞请求线程。
- cookie 无 Secure/SameSite（Tunnel HTTPS 下传输安全 [确认-TLS]；127.0.0.1 直连 HTTP 会带 cookie，本机低危）。
- `_RATE` >5000 key 整体清空（限流窗口被重置的一次性事件）。
- 无 last_progress/进度字段；无运行期监控采集（有资源实测无持续采集）。

## P3 —— 低

- 静态盐"wk"、enc:v1: 非标准加密（建议保留，不破坏现有订单；新增数据可换方案）。
- note 无长度上限（当前库 max=23 字符 [实测]）。
- 碎片目录（_hist/_hikejs/_onlineweb_js/cf\browser_profile 等）未归档。
- 大量 tail_keep 重复调用可聚合。
- /query 前 8 位前缀枚举（uuid4 低危）。
- v5 备份文件与线上库同路径（勿运行，文档已标注）。

# 4. 最危险的 10 个问题

| # | 问题 | 证据 | 触发 | 影响 | 自动恢复 | 推荐修复 | 范围 | 立即 |
|---|---|---|---|---|---|---|---|---|
| 1 | 运行期 DB=running+PID=dead 无限期 | h571-574 无读方 | 子进程被杀/崩 | 订单卡死、重试语义失效 | 仅重启 | 轻量运行期 pid 校验 | 1 函数 | **是(P0)** |
| 2 | recover 不校验 kill → 理论双执行 | h292-301 | 杀不掉的旧进程 | 同单双执行 | 否 | kill 失败→failed 或重试 | ≤10 行 | **是(P0)** |
| 3 | 引擎输出注入 HTML（XSS） | h1303-1308 | 引擎打印含 HTML | 会话/页面脚本执行 | 否 | html.escape 日志/note | 1 处转义 | 是(P1) |
| 4 | 明文凭据残留 | config.json 等 | 机器共享/备份泄露 | 账号泄露 | 否 | 脱敏/挪 secrets+ACL | 文件级 | 是(P1) |
| 5 | 限流 IP 伪造 | h308 | 本机直连 | 爆破 | 否 | 认证优先+固定 IP 兜底 | 小 | 建议 |
| 6 | restore 在线执行窗口 | h78 | 人误操作 | 数据半写 | 否 | 端口/锁检测 | 小 | 建议 |
| 7 | 查课阻塞请求线程×16 | h1025 | 并发查课 | 页面不可拒服务感 | 否 | 限并发/异步(长期) | 中 | 否 |
| 8 | 无 FK/CHECK | schema | 误写状态 | 状态污染 | 部分 | CHECK(启动迁移) | 中 | 否 |
| 9 | 心跳无进度字段 | - | 假死无法辨识 | 无法区分卡死 | 否 | last_progress | 中 | 否 |
| 10 | 双备份两处实现 | h772+h148 | 维护混淆 | 漏挂 | 有 | 收敛一处 | 小 | 否 |

# 5. 状态机（真实状态 + 标注）

```
pending ──claim(CAS)──▶ running ──rc=0──▶ done           [合法]
   ▲                     │  ├─rc≠0──────▶ failed           [合法]
   │                     │  ├─rc<0 & attempt<1→pending      [合法·retry 1 次]
   │                     │  └─超时(-9)→pending/failed       [合法]
   └──recover_stale(启动)──┘                        [恢复路径]
waiting_qr ─扫码成功▶ pending ──扫码过期/取消/异常▶ canceled  [合法]
运行期: running 无任何运行期退出目标（除 worker 自身异常写 failed h749）
```
- 非法跳转：代码无守卫，SQLite 无 CHECK → **任意状态可被写入**（理论）；当前所有写入点经审查均为合法语义 [确认]。
- 终态覆盖：done/failed 后无代码再写；未来若加"重跑"需防覆盖 [推断-无现存路径]。
- 异常态：**DB=running & PID=dead 可无限期存在（见 P0-1）**；DB=pending & 旧进程仍活（仅启动恢复时序可能，见 P0-2）。

# 6. DB / PID / Worker 一致性矩阵

| DB | PID | Worker | 结论 |
|---|---|---|---|
| running | alive | alive | 正常执行 [确认-设计] |
| running | dead | alive | **可达且无运行期收敛**（worker finally 会写 failed/重排？—— worker 内若 _spawn 返回负码会写 failed，但 **外部强杀子进程后 p.wait 得到 returncode** → 实际会写 failed [推断]；真正危险=子进程"消失"而 wait 悬挂/进程句柄异常时）→ 以重启兜底 [P0-1] |
| running | dead | dead | 平台重启前保持；recover 收敛（正确路径）[确认] |
| pending | alive | alive | 仅启动恢复时序下短暂可能（recover kill 后才置 pending → 理论窗口 [P0-2]） |
| done | alive | dead | 子进程退出但 pid 留存行内（当前库无）→ recover 不处理终态，无害 [确认] |
| failed | alive | dead | 同上（终态不受理）[确认] |

# 7. 进程树（真实：本会话实测 + 代码）

```
health_manager(pythonw) ──(锁文件单例, 30s 轮询)
   ├─ order_platform.py(pythonw, 无控制台, DETACHED|NPG) ← 平台
   │    └─ worker 线程 → venv python.exe（BELOW_NORMAL|NO_WINDOW, stdout=PIPE→RollingLog）
   │        └─（仅 zhs 无课程）一次性孙进程 run_zhs.py list（subprocess.run 先起先收）[确认]
   └─ cloudflared.exe（独立, 服务器进程）
```
- 深度 ≤2（平台→引擎）；孙进程仅限 zhs list 一次性。
- 结构保证 vs 恰好如此：引擎内部为线程并发、不再派生（[推断-未见 Popen/multiprocessing]）；**非 Job Object 保护 = 结构性依赖 taskkill /T**（[确认-logic]）。
- 平台自身由看护 spawn：DETACHED_PROCESS|CREATE_NEW_PROCESS_GROUP|CREATE_NO_WINDOW（health_manager h167-175）。

# 8. SQLite

- [确认] WAL / synchronous=NORMAL(2) / busy_timeout=15000（代码连接级 db() h65；裸连接默认 5000）；page_size=4096；**foreign_keys=0**；无显式 BEGIN IMMEDIATE/ROLLBACK；全部短事务；无网络/sleep/subprocess 入事务。
- claim 原子性 [确认-推演]：SELECT+UPDATE 同连接；UPDATE 的 WHERE 保证最终 CAS。
- 崩溃恢复：WAL 自恢复；restore 在线风险见 P1-4；备份 7 份轮转 [确认]。
- 无约束（FK/CHECK/UNIQUE 除主键）→ 状态自由写入（见 §5）。

# 9. 安全

| 项 | 结论 | 证据 |
|---|---|---|
| 认证 | 口令哈希+外置 SECRET；默认口令强制随机 [确认] | h188/258 |
| 授权/IDOR | /order、set_courses、api_courses 均有归属校验 [确认]；/query 仅公开概要（无账号） | h1206/1284/1358 |
| CSRF | Origin/Referer 校验、无 token、无头放行 [设计取舍] | h874/323 |
| XSS | **存在注入面**（日志/note/用户名直插，无转义、无 CSP） | h1303/899 |
| SQLi | 全参数化 [确认] | h1227 等 |
| 路径穿越/任意文件 | 无用户路径接口；日志按服务端拼订单目录 [确认] | h1355 |
| 上传/下载 | 无 [确认] | §7 路由 |
| 敏感暴露 | orders/backups/secrets 不在 static、无下载路由 [确认] | - |
| Cookie | HttpOnly [确认-h1606]；无 Secure/SameSite（Tunnel HTTPS 传输安全） | §26 |
| 限流 | 4 口；IP 可伪造（P1-3） | h308 |
| Secret | 轮换=全量口令失效+存量密码不可解（设计语义，文档已声明）[确认] | h349-367 |

# 10. 故障验证计划（静态不可确认项 → [运行验证]）

1. 子进程被杀（taskkill 引擎进程）→ worker/_spawn 时序下订单最终态 + pid/hb 清空时机。
2. 假死注入（子进程存活但不输出不产出）→ 180min 硬超时树杀验证。
3. recover kill 失败注入（模拟 taskkill 失败）→ 是否仍回 pending（复现 P0-2）。
4. 双执行注入（旧进程未被杀时强行 claim）→ 是否存在双进程。
5. engine 输出含 HTML 注入 → 浏览器实际执行（复现 P1-1）。
6. 并行 8×/api/courses → Waitress 线程耗尽表现。
7. restore 在线执行 → 锁冲突/半写。
8. 正式机 watchdog 长期稳定性（含 5min 触发+僵尸频率）。

# 11. 本阶段实际完成的修改

**无**（指令约束：审计阶段不修改）。

# 12. 回滚方案

N/A（未修改）。

---

# 四十四、最终必须回答的 12 个问题

1. **是否可能一个订单被两个进程同时执行？** 当前稳态不能（CAS+单恢复路径）[确认]；极端时序下 recover 不校验 kill 结果存在理论窗口（P0-2）。
2. **DB=running 但任务死亡，最长多久收敛？** 运行期不收敛；最长=直至平台重启（无限期）。[确认-代码] ← 需修复。
3. **是否可能出现孤儿进程？** 任务侧不能（树杀+验证）；环境侧出现过僵尸 pythonw（与代码无关，见 §2-13），靠清扫/重启回收。[运行验证]
4. **platform 崩溃后旧订单进程是否继续？** 是——旧引擎子进程会孤儿化继续运行，直到新平台启动时 recover 用 pid 树杀。[确认-代码]
5. **health_manager 是否可能重复拉起 platform？** 锁文件+端口监听者清理；低概率双启动由环境现象体现（僵尸），活跃侧唯一 [确认]。
6. **SQLite 是否存在实际一致性漏洞？** 事务/CAS 正确；无约束与 restore 在线属结构性债务（P1-4/P2）。
7. **backup 是否真的可以恢复？** 09-13 restore 演练通过 [确认]；恢复前会先校验源 + 自动再备份 [确认-h78-94]。
8. **housekeeping 是否可能误删运行订单？** 低——运行中订单硬超时仅 3h < 3 天清理线，且 log.txt 句柄占用使 rmtree 静默失败 [推断]；边界为"长时间无输出"也被 3h 超时兜底 [确认]。
9. **logs/stdout 是否存在阻塞或泄漏？** RollingLog 崩溃后 close 读端 → 子进程写管失败（退出或阻塞至 180min 超时）[推断]；无句柄/连接池泄漏迹象 [确认-代码审查]；`[运行验证] 长跑趋势`。
10. **用户是否可能读取其他用户订单？** 登录路径有归属校验 [确认]；/query 公开概要无敏感字段 [确认]。
11. **当前敏感信息是否存在明显泄露路径？** Web 层无（无下载路由、非 static）；本机明文残留（config.json/cookies_xxx/archive）[确认-文件存在]。
12. **当前最值得做的 3~5 个小改动？** ①运行期 running 订单 pid 存活探测（1 函数）；②recover kill 结果校验（≤10 行）；③HTML 转义（escape(log_tail/note) 一处）；④明文残残留脱敏/挪位；⑤（可选）restore 在线检测。

---

# 四十五、最终一句话结论

> **本项目当前最需要解决的，不是"更高并发"，而是"运行期订单进程死亡的自动收敛与恢复路径的 kill 结果校验"。**

原因：现有 DB/进程一致性只在平台重启时收敛，运行期 heartbeat 无读取方，且恢复时不校验旧进程确已退出，这两个缺口一旦触发，要么订单无限期卡 running，要么在极端时序下让同一订单被新旧两个进程同时执行——且都可用单函数级小改动闭合，无需任何架构变更。
## 附录：开放项终审（2026-09-14 补充）

### 1. 异步查课 —— 不实施
- 证据/理由：指令 §29 明确"当前同步方案在小规模是否仍合理？合理则不改成异步队列"。现 _QUERY_SLOT = BoundedSemaphore(2) 已将最坏情况从 16 请求线程全占收敛为 2 线程+快速失败；改造为后台任务需新增任务状态表/状态机/前端轮询三处（违反"一个函数解决不十个文件"）。
- 条件触发：当查课并发需求显著上升（>2 同时）再评估。

### 2. last_progress / 假死辨识 —— 不实施（依赖引擎）
- 证据/理由：真假死（进程存活但引擎卡住）须引擎上报步骤进度；平台侧无该数据源，而 heartbeat_at 已承担"存活+最近活跃"语义。引擎属"不触碰"边界。
- 长期候选；除非用户明确授权引擎最小上报。

### 3. 运行期监控采集（持久采样）—— 不实施
- 证据/理由：管理页系统状态卡已实时显示进程/SQLite/CF/内存/磁盘/备份/心跳；持久采样引入存储与复杂度，小规模收益低（审计 §37 为现状陈述，非缺陷）。
- 若需历史趋势，建议未来以当日内存采样+管理页小表实现。
