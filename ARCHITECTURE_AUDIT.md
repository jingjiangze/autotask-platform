# ARCHITECTURE_AUDIT.md — WorkBuddy 自动任务平台 · 架构审计报告（第二版，基于当前线上代码）

> 审计日期：2026-09-14 06:50 ｜ 审计对象：`D:\web`（平台迁移后的正式运行目录）
> 审计方式：通读当前源码（order_platform.py 1651 行 / backup_manager / health_manager / tools_query_courses / cf 脚本）+ 只读查询线上数据库 + 检查运行中进程与计划任务 + 日志复核。**本阶段未修改任何业务代码。**
> 运行时实测：平台 pythonw PID 40796 监听 127.0.0.1:8766（Waitress，RAM≈33MB），cloudflared PID 39668（RAM≈33MB），看护 health_manager 运行中，计划任务 `WK_AutoTaskPlatform` 状态 Running。
> ⚠ 关键实测发现：**当前同时存在 2 个 health_manager（20600/38384）与 2 个 order_platform（16452/40796），其中各有一个 1 线程 ~4MB 的僵尸实例已存活 12 小时以上**（详见 §三 6.9/§十五 A1）。
> 上一版审计（2026-09-12，基于 `C:\Users\Administrator\WorkBuddy\...`）已过时，本版替代之。

---

## 目录

1. 当前系统架构
2. 组件职责
3. 进程关系（含 6.1–6.17 逐项回答）
4. 线程关系
5. 数据库结构
6. 订单生命周期
7. 任务生命周期
8. 文件生命周期
9. 启动流程
10. 停止流程
11. 异常流程
12. 恢复流程
13. 资源占用点
14. 性能瓶颈
15. 稳定性风险
16. 数据丢失风险
17. 安全风险
18. Windows 部署风险
19. Cloudflare 暴露面
20. SQLite 风险
21. 问题分级汇总（P0–P3）与"必须修改/建议修改/暂时不要动"

---

## 一、当前系统架构

```
用户浏览器
   ↓ HTTPS
Cloudflare Edge（order.jiangjiangze.icu）
   ↓
cloudflared.exe（39668，独立进程，由看护拉起）
   ↓ http://127.0.0.1:8766
order_platform.py（pythonw 40796，Waitress threads=16，单进程）
   ├─ Waitress 请求线程池（16）
   ├─ concurrency_manager 线程 → 4 个 worker 线程（settings.concurrency=4）
   ├─ qr_janitor / housekeeping 线程
   ├─ 每个扫码订单 1 个 qr_thread（瞬时）
   └─ worker 线程经 _spawn：
        subprocess.Popen（低优先级 + CREATE_NO_WINDOW，Python venv，引擎）
             └─ 引擎内部 thread 并发（-j jobs），不再派生进程
             └─（zhs 无课程ID时）先 subprocess.run(run_zhs.py list) 孙进程，先起先收
   ↓
故障自愈：health_manager.py（计划任务拉起）→ 平台 /health + cloudflared 存活检查
```

**存储**：SQLite（`orders/platform.db`，WAL，synchronous=NORMAL，busy_timeout=15000），
表：`users / orders / products / settings`。每订单一个目录 `orders/<uuid>/`（cookies.json、log.txt、work/tmp/home/logs）。

### 上一版"已做对"项复核（保持不动）
- 每单独立子进程 + 独立环境（目录/cookies/TEMP/HOME/UA/代理），隔离思路正确 ✅
- `claim_order()` 的 `UPDATE ... WHERE worker_running=0` + rowcount 检查构成 CAS，无重复 claim ✅
- SQLite 全部短事务（`with db() as c`），无长事务、无事务内网络/睡眠 ✅
- 每次操作新建连接，无全局共享连接 ✅
- RollingLog 流式落盘 + 裁剪（且已在 P0 修复句柄重建问题）✅
- 任务子进程低于正常优先级 + 内存护栏（min_free_mb）✅
- housekeeping 清理 3 天前订单目录、1 小时前 _query ✅
- 无限 retry 不存在（MAX_RETRY=1，仅 timeout/负退出码可重试一次）✅

## 二、组件职责（当前）

| 组件 | 职责 | 状态 |
|---|---|---|
| order_platform.py（1651 行） | HTTP 前端 + 订单库 + worker 池 + 子进程管理 + 扫码登录 + 清理 + 备份调度 + /health | 运行中（Waitress，PID 40796） |
| health_manager.py | 唯一看护：平台 /health 检测 + cloudflared 存活检测；互斥体单实例（**有缺陷，见 A1**）；冷却 + 10 分钟 3 次熔断 | 运行中（出现双实例） |
| backup_manager.py | SQLite 在线备份/校验/保留 7 份/反向覆盖恢复 | 正常（housekeeping 每日调度 + 手动命令） |
| tools_query_courses.py | 查课子进程（180s 超时，stdout JSON） | 正常 |
| order_server.py（v1） | 弃用，不删除 | 仅保留 |
| order_platform_v5_backup.py | v5 备份版本（与当前库共享 DB 路径，**勿运行**） | 不修改 |
| fuckCourse/ | 刷课引擎（chaoxing / zhs），业务核心 | **不触碰** |
| zhs_script/ fuckZHS_orig/ tabler_pkg/ | 上游参考/打包中间件 | 保留物 |
| cf/ | cloudflared.exe + deploy_cf.py + run_tunnel_detached.py + **已过期的启动 bat（见 A2）** | 隧道运行中 |
| secrets_store/ | secret_key.txt（SECRET 外置）+ _admin_pw_旧实验残留.txt | 正常 |
| backups/ | 7 份备份（最近 09-13 18:40:50） | 正常 |
| 自启 | 计划任务 WK_AutoTaskPlatform（pythonw，LogonTrigger + TimeTrigger 5min，见 A3）；VBS→bat 链路已断（见 A2） | 部分有效 |

## 三、进程关系（6.1–6.17 逐项回答）

**6.1 一个订单实际包含哪些进程？**
```
pythonw.exe order_platform.py（常驻）          ← 全部 worker 均为其线程
  └─ worker 线程
      └─ python.exe（venv）任务子进程（_spawn，低优先级，每单 1 个）
          └─（仅 zhs 无课程ID时）临时 run_zhs.py list 孙进程，先起先收
任务子进程内部为线程并发（-j jobs），不再派生更深进程。查课另有独立同步子进程链路（HTTP 线程 → subprocess.run(tools_query_courses.py)，最长 180s）。
```

**6.2 谁创建这些进程？** worker 线程经 `_spawn()`/`subprocess.run()` 创建任务/查课进程；平台由 health_manager `start_platform()`（pythonw）创建；cloudflared 由 health_manager `start_cloudflared()` 或 run_tunnel_detached.py 创建；health_manager 由计划任务（及理论上开机 VBS→bat，当前断链）创建。

**6.3 谁负责记录 PID？** ✅ 已补齐：orders.pid（_spawn 时写库）、heartbeat_at（20s 级）。cloudflared/health_manager 无 PID 文件（靠 tasklist/端口/互斥体识别）——可复核但当前可用。

**6.4 谁负责结束进程？** ① `_spawn` 超时 → `_kill_tree()`（taskkill /F /T + **验证退出**，最多等 5s，P0 已修复）；② 任务自然退出；③ health_manager 重启平台前 `taskkill /F /PID`(监听 8766 者)；④ `deploy_cf.py stop` 杀 cloudflared。

**6.5 谁负责等待进程真正退出？** `_spawn` 的 `p.wait(timeout=20)` 分片 + `_kill_tree` 验证循环。✅

**6.6 谁负责清理进程资源？** RollingLog 关闭 stdout 句柄；worker `finally` 置 `worker_running=0`；订单目录 3 天后 rmtree。无 Job Object（进程树仅 2 层，无需；理由见 RECOMMENDED）。**缺口：已僵死的 pythonw 实例无人回收（A1）。**

**6.7 worker 崩溃以后发生什么？** worker 为 `while True` + 全捕获，基本不死。兜底写库失败有 `safe_set_order`（3 次退避 + platform_error.log），busy 不再误标 failed。✅ P0-D 已修复。

**6.8 task process 崩溃以后发生什么？** returncode≠0 → failed；负退出码/超时(=-9)且 attempt<1 → 自动重试 1 次 → 再失败 failed。✅

**6.9 Windows 重启以后发生什么？** 计划任务 LogonTrigger 拉起 health_manager → health_manager 拉起平台 → 平台启动调用 `recover_stale_orders()`：running 单先判 PID（活→树杀）再按 attempt 重回 pending 或 failed；waiting_qr → canceled。✅ 已实现。
⚠️ **但启动去重失效**：实测迁移时刻之后至今（>12h）同时存在 2×health_manager + 2×order_platform，其中一个平台僵尸（16452，1 线程 4MB，未绑端口）、一个看护僵尸（20600，1 线程 4MB）。僵尸没有被启动流程回收，也没有被看护清理。

**6.10 Flask 崩溃以后发生什么？** health_manager 每 30s 探测 /health，连续 2 次失败 + 冷却 60s 后 `listener_pid(8766)`→taskkill→重新 start_platform；10 分钟内 3 次触发熔断转人工。实测 09-13 三次自发崩溃（14:39/16:29/17:51-54）均 1 分钟内自愈、无风暴。✅
⚠️ `listener_pid` 只杀**占着 8766 端口**的实例：若僵尸不占端口，会被原样保留并叠加上新实例——这正解释/放大了 A1。

**6.11 Cloudflare Tunnel 崩溃以后发生什么？** health_manager 检查 cloudflared.exe 是否在 tasklist，消失则冷却后拉起。实测有效（日志多条"cloudflared 不在运行，已重新拉起"）。✅

**6.12 SQLite 遇到锁竞争以后发生什么？** WAL + busy_timeout=15s；claim 的 OperationalError → 返回 None（worker 空转重试，不误伤订单）；`safe_set_order` 对写失败退避 3 次。✅

**6.13 当前是否存在永久 running？** **不存在**（当前库 19 单全部终态：14 done / 5 failed，0 pending/running）。且三条永久卡死路径均已封堵（启动恢复 + 兜底写 + pid 判活）。

**6.14 当前是否可能重复 claim？** **不会。** CAS 结构保留（§一），SQLite 写锁保证原子。

**6.15 当前是否可能出现孤儿进程？** 任务子进程侧：不可（kill /T + 验证）。**平台/看护自身侧：存在，且正有实录**——两个 1 线程 4MB 僵尸 pythonw 常驻 12h+（A1）。

**6.16 当前是否可能出现无限 retry？** **不会**（MAX_RETRY=1，仅 timeout/负退出码重试；参数/认证/业务错误直接 failed）。

**6.17 日志/tmp/orders 是否无限增长？**
| 对象 | 机制 | 结论 |
|---|---|---|
| orders/<uuid>/ | 3 天按目录 mtime rmtree | ✅ 可控 |
| orders/_query/ | 1 小时清理 | ✅ 可控 |
| 订单 log.txt | RollingLog 裁剪（300KB 可配） | ✅ 可控 |
| fuckCourse/logs/chaoxing.log、zhs_logs/* | housekeeping tail_keep 512KB | ✅ 可控 |
| **fuckCourse/logs/chaoxing.2026-09-11_14-39-20_677701.log（loguru 日期轮转）** | **仅"按 3 天 mtime 删除"，无大小上限**；当前 9.99MB，将于 09-14 18:31 housekeeping 周期删除 | ⚠ 删除前可长到 ~10MB+，需给 dated 文件也加大小裁剪（P3） |
| cf/cloudflared.log | housekeeping tail_keep 512KB | ✅（P3+ 已修） |
| cf/platform_stdout.log / platform_error.log | tail_keep 512KB / 256KB | ✅ |
| cf/health_manager.log | 自身写入前超 512KB 即清 | ✅ |
| backups/ | 保留 7 份 | ✅ |
| platform.db / WAL | 正常 checkpoint | ✅ |
| QR_SESSIONS | qr_janitor 120s 清理 | ✅ |
| **根目录 _*.log/_*.txt/_*.py 调试残留 + 4 个 zip/tgz（合计 >20MB）** | **无清理** | ❌ P3 需归档 |
| **fuckCourse/cookies_13375472780.json（明文 cookie，文件名为手机号）** | **无清理** | ❌ P3 敏感残留 |

## 四、线程关系

常驻线程（空闲实测 %）：Waitress 16 请求线程 + concurrency_manager + qr_janitor + housekeeping + 4 worker ≈ 23 线程（对应 PID 40796 的 24 线程计数）。
瞬时线程：每运行订单 1 个 RollingLog；每扫码中订单 1 个 qr_thread。
worker 空闲行为：`get_setting('paused')`+`free_mem_mb()`+`claim_order()` 每轮新开 SQLite 连接，空闲退避 2s→5s。4 worker ≈ 每秒 1~4 次连接/只读查询，CPU 影响忽略不计。**符合"最少后台并发"原则，无需精简**；Waitress 16 请求线程为 WSGI 池常驻（RAM ≈33MB 总进程已含），不可省。

## 五、数据库结构

```sql
users(id, username UNIQUE, pw_hash, is_admin, created_at)
orders(id TEXT PK, user_id, product, platform, account, password, courses,
       status, note, qr_state, created_at, started_at, finished_at,
       exit_code, worker_running,
       product, env_profile, risk_flags, speed,          -- P0 前已有增列
       pid, attempt, heartbeat_at)                        -- P0 增列 ✅
products(id, code UNIQUE, name, desc, price, platform, enabled, sort)
settings(key PK, value)
```

- 状态词汇：`pending / waiting_qr / running / done / failed / canceled`（done≈success，复用不改名；timeout/crashed 用 failed+note/exit_code 表达——遵循"复用而非重建"原则）。
- **密码加密 ✅**：全部 19 单 `enc:v1:` 前缀，0 明文（只读核验通过）；密钥派生自 secrets_store/secret_key.txt。
- 时间格式：**新写时间已为 `%Y-%m-%d %H:%M:%S`，19 条存量行为旧 `%m-%d %H:%M:%S`无年份** → 跨格式 `ORDER BY created_at` 排序语义失真（`0…` < `2…`），当前恰好无混排无影响，但属数据一致性隐患（P3）。
- 索引：仅主键。19 行规模**不建** status+created_at 索引（写入成本>收益，维持判断）。

## 六、订单生命周期（当前）

```
pending ──claim(CAS, attempt+1, 记 pid/heartbeat)──▶ running ──rc=0──▶ done
   ▲                            │ ├──rc≠0(参数/认证/业务)──▶ failed
   │                            │ ├──rc<0 且 attempt<1 ──▶ pending(自动重试1次)
   │                            │ └──超时(taskkill /T+验证) ──▶ rc=-9 同上重试
   │                            └── 平台重启 ──recover_stale: pid?树杀:→attempt<1?pending:failed
waiting_qr ─扫码成功──▶ pending（重新排队）
waiting_qr ─过期/取消/异常──▶ canceled（qr_thread 已写终态 ✅）
```
**任何订单不再永久 running。** ✅（P0 目标达成）

## 七、任务生命周期

下单(密码加密入库) → pending → worker CAS claim → build_order_env（独立目录/cookies/TEMP/HOME/UA/代理）→ env_profile 落库 → _spawn（低优先级子进程 + RollingLog + 20s 心跳 + 硬超时熔断）→ scan_risk（尾 400KB）→ done/failed（含环境画像 note）→ finally worker_running=0。查课为独立同步子进程链路。心跳只作存活佐证（pid 判活优先），非高频进度写入。

## 八、文件生命周期

见 6.17 表。补充：`orders/<id>/cookies.json` 含平台登录态（随目录 3 天删除）；fuckCourse 内部 `config.json`（**含引擎自身明文账号口令**，属引擎既有配置文件，按边界不触碰但需知悉风险）、`.zhs_cred`、`cookies_13375472780.json` 属**敏感残留**（P3）；根目录大量调试产物（P3）。

## 九、启动流程（当前）

```
开机 → 计划任务 WK_AutoTaskPlatform（pythonw，LogonTrigger + TimeTrigger/5min）
  └→ health_manager.py（互斥体单实例【有缺陷】）
       ├→ 幂等拉起 order_platform.py（pythonw + platform_stdout.log）→ init_db/ensure_settings/
       │   ensure_admin_password/migrate_encrypt_passwords/recover_stale_orders → Waitress(8766)
       ├→ 幂等拉起 cloudflared.exe（剥离代理变量，--no-autoupdate)
       └→ 30s 循环看护（平台 /health + cloudflared tasklist + 熔断防风暴）
```
**当前缺陷**（详见 §十五）：
- A2：`cf\启动平台和隧道.bat` 内 `WK` 仍指向**已不存在的旧目录** `C:\Users\Administrator\WorkBuddy\2026-09-11-10-49-22`（该目录已改名留档）；启动文件夹 `自动任务平台.vbs` 指向该 bat → **手册推荐的"双击 bat"启动实际失效**；登录自启实际只依赖计划任务。
- A1：互斥体未阻止重复实例 → 双 health_manager/双 platform（1 僵尸）并存。
- A3：计划任务含双触发器且未配置"若已在运行则不启动新实例" → 并发双触发的结构性来源。

## 十、停止流程（当前）

无独立 stop 脚本。官方流程（README_RUN）：`Stop-ScheduledTask` → 结束看护 → 结束平台/隧道残留。**运行中订单停机**会孤儿化任务 → 下次启动 recover_stale_orders 接管（可恢复）。评价：可用但无"优雅停机/标记维护态"；以当前单机规模可接受（P3 可加 stop.bat）。

## 十一、异常流程

- worker 全捕获 → 尽力 failed（safe_set_order 兜底 3 次 → platform_error.log）；异常文本 APP_DIR 脱敏。✅
- taskkill 结果验证（_kill_tree 循环等退出）。✅
- RollingLog 全捕获静默（正常设计）。
- `migrate_encrypt_passwords` / `recover_stale_orders` / housekeeping / qr_thread 均有 try 包裹，异常不致死平台。✅
- 查课子进程异常 → JSON error 返回前端。✅

## 十二、恢复流程（当前）

| 故障 | 恢复机制 | 状态 |
|---|---|---|
| Windows 重启 | 计划任务+health_manager 拉起 → recover_stale_orders 扫描 | ✅ |
| Flask 崩溃 | health_manager /health 检测→冷却重启→熔断防风暴 | ✅ 实测 3 次自愈 |
| worker 崩溃 | 线程不死身+finally 清 worker_running | ✅ |
| task 崩溃/超时 | rc 判定→重试 1 次或 failed | ✅ |
| cloudflared 崩溃 | health_manager 拉起（冷却） | ✅ 实测 |
| **僵尸/重复实例** | **无**——只清理"占端口者"，不清理"无主/僵死"实例 | ❌ A1 |
| SQLite 忙 | claim busy→None 重试；写 busy→safe_set_order 重试 | ✅ |

## 十三、资源占用点（实测 2026-09-14）

| 项目 | 数值 | 评价 |
|---|---|---|
| 平台进程（Waitress，PID 40796） | RAM≈33MB / 24 线程 | 良好（含 16 WSGI 线程池） |
| cloudflared（39668） | RAM≈33MB / 15 线程 | 正常 |
| health_manager（38384，活跃） | RAM≈24MB / 3 线程 | 正常 |
| **僵尸 pythonw ×2（20600/16452）** | 各 1 线程 / ~4MB / 存活 12h+ | ❌ 泄漏性常驻（A1） |
| 每运行订单子进程 | 引擎+loguru ≈40–80MB | 受 min_free_mb=500 护栏 |
| 空闲 CPU | worker 退避 2s→5s + 少量只读查询 | <1%，可接受 |
| 磁盘 | orders≈3.6MB、fuckCourse/logs≈10.5MB（其中 10MB 为 09-14 18:31 将删除的 dated log） | 总体可控 |

## 十四、性能瓶颈

1. 查课接口同步阻塞 Flask 请求线程最长 180s（小规模可接受，维持）。
2. worker 空闲轮询（2s→5s 退避已存在，尚未到"事件驱动"，单机规模不值得引入队列）。
3. Waitress threads=16 为固定池（与旧 threaded=Flask 同量级，无回退必要）。
4. order_platform.py 单文件 1651 行——可维护性瓶颈（已拆出 backup/health 两个模块，其余保持）。
其余链路在当前规模**无瓶颈**。

## 十五、稳定性风险（汇总）

| 编号 | 风险 | 等级 | 处置 |
|---|---|---|---|
| **A1** | **启动去重失效 + 僵尸实例无回收**：实测 2×health_manager+2×order_platform 并存 12h+（各一僵尸，均未绑端口、1 线程 ~4MB）。互斥体用 `ctypes.windll.GetLastError()` 判定 ERROR_ALREADY_EXISTS——未经 `use_last_error=True` 的 GetLastError 不可靠（经典 ctypes 陷阱），重复触发时去重静默失效；health_manager 重启只杀"占 8766 端口者"，不杀无主僵死实例；恢复逻辑也只处理 orders 表内订单，不回收游离 pythonw。若僵尸平台日后完成初始化，会形成**第二个 worker 池**（CAS 防重复 claim，但资源与排障双倍） | **P1（本版最高优先）** | 必须修改：修正互斥（WinDLL use_last_error 或改为锁文件+PID+端口三合一）；startup 与 health_manager 增加"回收本项目遗留 pythonw 僵尸"逻辑；提供 stop/kill-stale 命令 |
| R2 | VBS→bat 自启链路断链（旧路径），仅计划任务单点生效 | P1 | 建议修改：修复 bat 的 WK 路径或删除误导入口（见 A2） |
| R3 | 计划任务双触发（Logon+5min）可并发双启动 | P1 | 建议修改：触发策略统一为单一触发器并设置"若已在运行则不启动新实例"（见 A3） |
| R4 | 密码仍经**命令行参数**传给引擎子进程（`python main.py -u … -p …`，本机任何用户 tasklist 可见） | P1 | 建议修改：P1-C（RECOMMENDED 已列，未实施）改经环境变量 |
| R5 | 备份只在平台进程内 housekeeping 调度：平台停机 >1 天则备份中断 | P2 | 建议修改：备份改由计划任务/看护独立驱动（离线时也能备份） |
| R6 | loguru dated 日志删除前最大可至 ~10MB | P3 | 建议修改：dated 文件也加 tail_keep 上限 |
| R7 | waiting_qr/扫码会话异常残留 | ✅ 已解决（qr_thread 终态+canceled+janitor 清理） | 维持 |
| R8 | 曾有平台自发崩溃（09-13 三起，判为控制台关闭事件） | ✅ 已用 pythonw+看护自愈解决，最近 12h 零事件 | 维持观察 |

## 十六、数据丢失风险

D1 备份：每日在线备份 + integrity_check + 保留 7 份（backups/ 现 7 份，最近 09-13 18:40）✅；restore 走 SQLite backup API 反向覆盖（已实测演练）✅。
D2 残留明文：DB 内密码已全加密 ✅；但 **fuckCourse/config.json 存量明文引擎凭据**（不触碰区）、fuckCourse/cookies_13375472780.json 明文 cookie（P3 清理）仍存在。
D3 时间戳格式混合（无年份旧行）→ 审计/排序失真（P3）。
D4 cloudflared 凭据：`%USERPROFILE%\.cloudflared\{cert.pem, 24f92801….json, wk_config.yml}` 含隧道账号凭据——在用户目录而非项目目录，泄露面=本账户；维持。

## 十七、安全风险

| # | 风险 | 处置状态 |
|---|---|---|
| S1 | 管理口令 | ✅ 只读核验：admin 口令**非默认 admin123**；启动时若为默认会强制随机化并落 secrets_store/admin_password.txt |
| S2 | 登录/注册/查课/扫码无限流 | ✅ 已做轻量限流（login 10/min、register 5/min、api_courses 12/min、qr_start 6/min，按 IP） |
| S3 | /api/courses 资源耗尽 | ✅ 上限流覆盖 |
| S4 | 订单密码明文入库 | ✅ 已加密（enc:v1: 全量）；**残留：进程命令行仍明文传密码（R4，P1）** |
| S5 | SECRET 硬编码 | ✅ 已外置 secrets_store/secret_key.txt（首启随机生成）；口令哈希仍用静态盐 "wk"（维持，单机可接受） |
| S6 | CSRF | ✅ 全站 POST 同源校验（Origin/Referer，双 scheme 兼容 CF；无头客户端放行） |
| S7 | 口令材料残留 | 根目录 _admin_pw_verify.txt 等调试文件仍含口令推断材料（P3 清理）；旧 _admin_pw.txt 已归档进 secrets_store（经校验与现口令不一致） |
| S8 | 异常文本回显 | ✅ APP_DIR 脱敏；QR 异常截断 120 字符 |
| S9 | /qr /qr_status 无鉴权 | uuid4 不可枚举，维持（P3 可选补归属校验） |
| S10 | 路径穿越 | 现状**零文件下载/自定义路径接口**；/query 为参数化 LIKE；查课 cookie_path 服务端拼 UUID。维持零穿越面（新增下载功能必须 normalize+resolve+根目录校验） |
| S11 | 引擎日志泄露 | 默认 logLevel=INFO：zhs/fucker.py 有**无条件 print 账号名**（Username: xxx）→ 账号出现在订单/引擎日志（3 天即清，低危）；debug 级 headers/cookies 默认抑制。口令未见 INFO 输出（抽样），维持"残余低危"标注，不触碰引擎 |

## 十八、Windows 部署风险

W1 自启链路：计划任务正常（实测 Running / pythonw / 无窗口）；**VBS→bat 断链（A2）**为唯一失配点。
W2 幂等性：**未真正达成（A1/A3）**——重复实例实测存在。
W3 路径硬编码：`PYEXE` 与计划任务引用 `C:\Users\Administrator\.workbuddy\...`（venv 原位未迁，快照一致）；bat 的 `WK` 旧路径失配（A2）。
W4 管理权限：以 Administrator 运行，secrets/orders 无 ACL 隔离——单用户机可接受，维持。
W5 双管理风险：当前管理机制=计划任务(触发) + health_manager(自启/看护)。VBS/bat 为陈旧冗余入口，须统一或删除，否则未来仍可能出现"重启风暴/重复启动"（与上一版"单一自启链路"原则一致）。

## 十九、Cloudflare 暴露面

仅经 Tunnel 出网（无路由器端口映射、Flask 仅监听 127.0.0.1:8766）✅。回源为明文 http（本机回环，可接受）。cloudflared 配置/凭据位于用户目录（非项目内）。看护已覆盖掉线拉起 ✅。剩余暴露面集中于公网可打的应用层：S2/S3 已限流、S1 已改口令，总体收敛。

## 二十、SQLite 风险

T1 busy 误标 failed：✅ 已消除（claim busy→None；写 busy→safe_set_order 重试）。
T2 无备份：✅ 已消除（每日在线备份+7 份轮转+恢复演练）。
T3 无 stale 配合字段：✅ 已增 pid/heartbeat/attempt 并接入恢复逻辑。
T4 时间文本无年份：P3 数据一致性（混合格式），不影响功能。
T5 WAL/SHM 与 db 同目录：不受 orders 清理逻辑影响（仅删目录不删文件），✅ 安全。
T6 备份只在平台进程内调度 → 停机断更（R5，P2）。

---

## 二十一、问题分级汇总（P0–P3）与处置判定

### P0 —— 必须立即处理
**当前 P0 清单已全部清零**。上一版 6 项 P0（备份/启动恢复/pid-heartbeat-attempt/兜底写/busy 误标/管理口令/retry）经本版只读核验与运行时实测均已完成：
- 备份：backups/ 7 份，最近 09-13 18:40 ✅
- 恢复：recover_stale_orders 在启动链路 ✅
- pid/attempt/heartbeat_at：列存在、字段写全 ✅
- 兜底写 + busy 容错：safe_set_order + claim busy→None ✅
- admin 口令：非默认 ✅；密码：19/19 加密 ✅
- retry：MAX_RETRY=1 有上限 ✅
- 永久 running：实测 0 单 ✅

### P1 —— 高优先级（本版重点）
| 编号 | 问题 | 判定 |
|---|---|---|
| A1 | **双实例/僵尸实例并存（互斥去重失效 + 无僵尸回收）** | **必须修改**（启动幂等核心） |
| A2 | **cf\启动平台和隧道.bat 指向已删除旧目录，VBS 链断，手册"双击 bat"启动失效** | **必须修改**（改路径或删除，保留计划任务单链路） |
| A3 | 计划任务双触发器 + 未配置"已运行则不新启动" → 双启动结构性来源 | **必须修改** |
| R4 | 订单密码仍以命令行参数传引擎子进程（tasklist 可见明文） | **必须修改**（P1-C 补做：改环境变量传递） |

### P2 —— 中优先级
| 编号 | 问题 | 判定 |
|---|---|---|
| R5 | 备份仅随平台进程 housekeeping，平台停机断更 | 建议修改（备份挪到计划任务独立调度，或 health_manager 顺带触发） |
| — | 系统状态卡已具备，可补充 worker 明细（worker_id/order/pid/内存） | 建议修改（worker 异常可观察） |
| — | 时间列年份迁移（存量 19 行补年份） | 建议修改（一次性 UPDATE，可回滚） |

### P3 —— 低优先级
- loguru dated 日志加大小上限（R6）
- 根目录 `_*.log/_*.txt/_*.py` 与 4 个 zip/tgz 归档清理
- `fuckCourse/cookies_13375472780.json`、根目录口令推断类调试文件归档
- /qr 鉴权补齐（可选）；/query 前缀枚举保护（可选）
- stop.bat/优雅停机；模块化（process_manager 等按需）
- order_platform_v5_backup.py 与当前库同路径提示（勿运行）

### 明确"暂时不要动"
fuckCourse 引擎全部逻辑（含 WK_UA/代理/字体解码/业务请求，即使 config.json 明文凭据属既有设计）；order_server.py 与 v5 备份；SQLite/WAL 技术栈；Tabler 前端结构；现有状态词汇（done/failed 复用）；现有健康/备份机制方向（只修正缺陷不重做）；不引入 Redis/PostgreSQL/Docker/Job Object/Flask-Limiter。

---

*审计人：WorkBuddy ｜ 下一份交付物：RECOMMENDED_ARCHITECTURE.md（本版更新：方案取舍维持 + A1/A2/A3/R4 处置路径）*