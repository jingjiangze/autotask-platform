# PROJECT_FULL_STATE — WorkBuddy 自动任务平台 全量状态档案

> 快照时间：2026-09-14（会话期实测数据）｜ 依据：**当前源码**（D:\web）+ 数据库只读核验 + 进程/计划任务实测 + 日志复核
> 用途：交接给第三方 AI 做第二轮独立审计 / 查缺补漏 / 稳定性评估。**本文档不改动任何代码。**
> 标注体系：
> - **[已确认]** 源码或运行时直接确证
> - **[推断]** 由代码逻辑合理推导，非直接声明
> - **[未知]** 代码无足够信息
> - **[需运行验证]** 静态无法确定，须实际运行确认

---

## 0. 文档说明

- 项目：Windows 单机自动刷课任务平台（学习通/知到），订单式、每单独立子进程、经 Cloudflare Tunnel 公网访问。
- 技术栈快照：Python 3.13.12（venv，实际解释器 `versions\3.13.12.old.36204`）、Flask + Waitress、SQLite 3.53.1（WAL）、requests、Tabler 1.5.1（本地）、cloudflared。
- 规模：order_platform.py 1739 行（含本次会话 P0–P3 全部增强）；19 张订单（全终态）；用户 1 名（admin）。
- 演进：2026-09-11 立项（v4）→ 09-12 P0 → 09-13 P1/P2 + 目录迁移至 D:\web → 09-14 P3 + 本档案。全程改动见根目录 CHANGELOG.md。

## 1. 项目基本信息

| 项 | 值 |
|---|---|
| 根路径 | `D:\web`（2026-09-13 迁入，旧路径已不存在） |
| venv | `C:\Users\Administrator\.workbuddy\binaries\python\envs\default`（PYEXE 硬编码 h10 用于子进程） |
| 入口 | `order_platform.py`（`if __name__ == "__main__"` h1733，Waitress serve h1737，失败回退 dev server h1739） |
| Web | `http://127.0.0.1:8766`（只监听本机回环，未绑 0.0.0.0） |
| 域名 | `order.jiangjiangze.icu`（经 cloudflared） |
| DB | `orders\platform.db`（SQLite 3.53.1，WAL，页面大小 4096） |
| 自启 | 计划任务 `WK_AutoTaskPlatform`（pythonw → health_manager.py） |
| 引擎 | `fuckCourse\`（chaoxing / zhs），业务核心，除 R4 凭据兼容外未改动 |

## 2. 项目目录结构（实测扫描）

```
D:\web
├── order_platform.py               平台主程序 1739 行（h=行号）
├── health_manager.py               看护进程（锁文件单实例+熔断+每日补备份）
├── backup_manager.py               在线备份/校验/轮转/恢复
├── tools_query_courses.py          查课子进程（凭据走环境变量）
├── order_server.py                 v1 弃用保留（端口 8765，JSON 存储）
├── order_platform_v5_backup.py     v5 备份版（⚠ 与线上库同路径，勿运行）
├── order_platform.py.bak_20260914_R4 / tools_query_courses.py.bak_20260914_R4   回滚点
├── ARCHITECTURE_AUDIT.md / RECOMMENDED_ARCHITECTURE.md / CHANGELOG.md /
│   TEST_REPORT.md / README_RUN.md / PROJECT_STATUS.md / PROJECT_FULL_STATE.md
├── orders\
│   ├── platform.db (+ .db-wal / .db-shm 运行时)
│   ├── _query\         查课临时 cookies（housekeeping 1h 清理）
│   └── <32hex>\        每单：log.txt / cookies.json / work\ / tmp\ / home\ / logs\
├── backups\            platform_YYYYMMDD_HHMMSS.db ×7（在线备份轮转）
├── secrets_store\      secret_key.txt / admin_password.txt / 旧口令归档
├── cf\
│   ├── cloudflared.exe / cloudflared.log / cloudflared_run.log / health_manager.log /
│   │   platform_stdout.log / health_manager.lock / 启动平台和隧道.bat /
│   │   deploy_cf.py / run_tunnel_detached.py / deploy_cert.py / cf_state.json /
│   │   *.bak_20260914 / health_manager_v2_lockfile_20260914.py（存档变体）
│   └── browser_profile\（Edge 探针残留 59 项，未参与业务）
├── tools\
│   ├── cleanup_stale.py            游离/僵尸实例清扫（诊断用）
│   ├── stop_platform.ps1 / start_platform.ps1
│   └── __pycache__\
├── tests\   test_crypto / test_health / test_kill_tree / test_restore_drill / test_spawn_timeout
├── fuckCourse\   chaoxing\ zhs\ qr\ welearn\ yuketang\ logs\ config.json(含明文凭据脱敏)
│                 cookies.json / .zhs_cred / cookies_13000000000.json(敏感残留) / run_zhs.py
├── static\ + tabler_pkg\            Tabler 1.5.1 本地 UI（不依赖外网 CDN）
├── zhs_script\ fuckZHS_orig\       上游参考仓库
├── _archive_dev\                    189 个开发残留归档（22MB，含口令推断材料）
├── _hist\ _hikejs\ _onlineweb_js\  历史诊断件（未归档）
└── .workbuddy\memory / __pycache__
```

### 关键文件职责卡（依要求逐字段）

| 文件 | 作用 | 被谁调用 | 调用谁 | 输入 | 输出 | 运行时必须 | 可否删除 | 状态 |
|---|---|---|---|---|---|---|---|---|
| order_platform.py | Web+队列+进程+清理 | 计划任务（经看护）；手动 python | db(thread-safety 每操作新建)、subprocess、backup_manager(housekeeping 内 import) | 无（Web 服务） | HTTP | 必须 | 否 | 运行中 |
| health_manager.py | 平台/隧道看护+补备份 | 计划任务/启动 bat | urlopen /health、tasklist、netstat、taskkill、start_platform(Popen pythonw)、backup_manager | 无 | 日志/进程 | 必须（自愈） | 否（可手动） | 运行中 |
| backup_manager.py | 备份/校验/恢复 | housekeeping（每日）；看护（补位）；手动命令 | sqlite3 backup API | backup/list/restore | 文件/打印 | 否（建议） | 否 | 就绪 |
| tools_query_courses.py | 查课（登录+课程列表） | order_platform.query_courses（HTTP 请求线程内同步 subprocess） | fuckCourse chaoxing.api / zhs.fucker | argv+env(WK_ACCOUNT/WK_PASSWORD) | JSON stdout | 是（功能） | 否 | 就绪 |
| order_server.py | v1 单页（JSON 存储, 8765） | 无（弃用） | - | - | - | 否 | 建议保留 | 弃用 |
| order_platform_v5_backup.py | v5 备份版 | 无（勿运行） | 同库路径 | - | - | 否 | 保留 | 危险共存 |
| cf\启动平台和隧道.bat | 手动/开机经 VBS 启动看护 | 用户/VBS | `start /min python health_manager.py` | - | - | 否（计划任务为主） | 保留 | WK 已修复为 D:\web |

## 3. 技术栈

| 组件 | 版本/形态 | 证据 | 说明 |
|---|---|---|---|
| Python | 3.13.12（venv） | 运行时 traceback 路径 | 解释器目录 `.workbuddy\binaries\python\versions\3.13.12.old.36204` |
| Flask | 随 venv（未锁定版本） | import | 仅用于路由/请求对象，入口已换 Waitress |
| Waitress | 已安装 | h1737 import | threads=16；ImportError 回退 dev server |
| SQLite | 3.53.1（venv 内置） | PRAGMA 实测 | page_size=4096，WAL |
| requests | 随 venv | import | 扫码轮询/登录 |
| Tabler | 1.5.1 本地 vendor | static/vendor | 无外网 CDN |
| cloudflared | 随 cf\cloudflared.exe | tasklist | 独立进程，隧道 |
| fuckCourse 引擎依赖 | 见 fuckCourse\requirements.txt | [需运行验证] | 未逐项核验版本 |

## 4. 系统架构（实测进程级）

```
用户浏览器 ─HTTPS→ Cloudflare Edge ─→ cloudflared(独立进程, cf\
   └→ localhost http 回源 127.0.0.1:8766
order_platform.py (pythonw, 单进程, 多线程)
   ├─ https:// 全部 Flask 路由（Waitress 16 线程池）
   ├─ concurrency_manager 线程(h767) → 维持 N 个 worker 线程(h710) → 每单 1 个 python 子进程(_spawn h620)
   ├─ qr_janitor 线程(h782) / housekeeping 线程(h795)（含每日备份）
   ├─ 每扫码订单 1 个 qr_thread(h494→h1274) / 每运行订单 1 个 RollingLog 线程(h110)
   └─ SQLite(orders\platform.db, WAL) + 订单目录(orders\<id>\) + 静态(static/vendor)
health_manager.py (pythonw, 独立看护进程)
   ├─ 30s 轮询 /health(h56) + cloudflared 存活(h177)
   ├─ 熔断：10min 内平台重启≤3 次(h37-41)
   └─ 每日补备份(h148-166, 调用 backup_manager)
cloudflared.exe（独立，由看护/run_tunnel_detached.py 拉起）
fuckCourse 引擎 python 子进程（venv，低优先级，每单隔离环境）
```

- 进程树深度 ≤2（平台 → 任务子进程；zhs 无课程时还有一次性孙进程 `run_zhs.py list`，先起先收 h684）。
- **无 multiprocessing、无 Job Object、无消息队列、无容器**。

## 5. 模块关系（谁·谁）

| 关系 | 说明 |
|---|---|
| 计划任务 → health_manager | 唯一常驻入口（IgnoreNew；5min 触发；RestartCount=3/10min） |
| health_manager → order_platform.py | start_platform h167（pythonw、DETACHED|CREATE_NEW_PROCESS_GROUP、stdout 落 platform_stdout.log）；/health 检测 |
| health_manager → cloudflared.exe | start_cloudflared h187（剥离代理变量） |
| health_manager → backup_manager | 每日（last_backup 判定）h148-166 |
| order_platform → SQLite | db() h58 每次新建连接；WAL h63 / synchronous=NORMAL h64 / busy_timeout=15000 h65 |
| order_platform → tools_query_courses | query_courses h1012 subprocess.run 同步 180s |
| order_platform → fuckCourse 引擎 | _spawn h620 Popen（venv）；run_chaoxing h663 / run_zhs h680 |
| order_platform → backup_manager | housekeeping h795 内每日 import 调用 |
| order_platform → 静态文件 | Flask 默认 static 路由（static/），Tabler 资源 |
| 引擎 → PushPlus/外部课程平台 | 在 fuckCourse 内部，**未逐行审计** |

## 6. Web 层

- 渲染：单模板 `page()` h899 —— 字符串模板 + `__TITLE__/__NAV__/__BODY__` 替换，f-string 直接拼接用户/服务端数据（**见 §36 XSS 分析**）。
- 前端：Tabler 暗色；内联 JS：下单向导（wizard 3 步）、订单详情 4s 自动刷新（running/waiting_qr）、QR 1.5s 轮询、批量下单。
- **无 session 框架**：登录态为自定义 cookie `wk_token=uid:hmac(sha256, secret, "u"+uid)`（h196-204 校验，h1677-1706 登录/登出），无 `flask.session` 使用。
- CSRF：全站 POST 同源校验 h874-876（Origin/Referer 双 scheme；**无头客户端放行**）——**无 CSRF token，[设计取舍]**。
- Rate limit：内存字典 `_RATE` h308-321；>5000 key 整体清空。
- 静态：Flask 默认 `/static/*`，仅 tabler 资源。

## 7. API / Flask Routes（全量）

| 路由 | 方法 | 功能 | 参数 | 权限 | 数据库操作 | 文件操作 | 后台逻辑 | 行号 |
|---|---|---|---|---|---|---|---|---|
| / | GET | 首页商品橱窗+统计 | - | 公开 | 读 products/orders 计数 | - | - | h977 |
| /buy/<code> | GET/POST | 下单向导+提单 | account/password/courses | 登录 | 插入 orders(enc 密码) | - | - | h1048 |
| /api/courses | POST | 查课程 | platform/account/password/order_id | 登录+限流12/min | 只读 orders(扫码态取 cookie) | 写 _query\*.json（子进程内） | subprocess 180s | h1222 |
| /api/qr_start | POST | 生成知到登录二维码 | product | 登录+限流6/min | 插入 orders(waiting_qr) | - | 启动 qr_thread | h1252 |
| /order/<oid>/set_courses | POST | 扫码后选课提交 | courses | 登录+归属 | 更新 courses/status | - | - | h1280 |
| /query | GET/POST | 访客查单（前8位） | oid | 公开 | 参数化 LIKE 查单 | - | - | h1297 |
| /my | GET | 我的订单 | - | 登录 | 读 | - | - | h1332 |
| /order/<oid> | GET | 订单详情（日志尾 8KB） | - | 登录+归属 | 读 | 读 log.txt 尾 | - | h1355 |
| /qr/<oid> | GET | 二维码图 | - | 登录（P3 加固） | - | 内存 QR_SESSIONS | - | h1407 |
| /qr_status/<oid> | GET | 扫码状态轮询 | - | 登录（P3 加固） | 读 orders | - | - | h1415 |
| /batch | GET/POST | 批量下单（CSV 行） | lines | 登录 | 批量插入(enc) | - | - | h1425 |
| /admin | GET | 管理后台（状态/用户/订单/系统状态） | - | 登录+is_admin | 多表读 | tasklist/disk | - | h1460 |
| /admin/tune | POST | 改运行参数 | 11 项设置 | 登录+is_admin | 写 settings | - | - | h1611 |
| /admin/regcode | POST | 改注册口令 | reg_code | 登录+is_admin | 写 settings | - | - | h1627 |
| /register | GET/POST | 注册（需注册口令） | username/password/reg_code | 限流5/min | 插入 users | - | - | h1638 |
| /login | GET/POST | 登录 | - | 限流10/min | 读 users | - | - | h1677 |
| /logout | GET | 登出 | - | - | - | 删 cookie | - | h1709 |
| /health | GET | 健康检查（非敏感） | - | 公开 | 读 orders 计数 | - | - | h1715 |
| /static/* | GET | Tabler 资源 | - | 公开 | - | 读 static/ | - | Flask 默认 |

- 无文件上传、无文件下载、无任意自定义路径接口。

## 8. 用户系统

- 表 users（6 列）；注册需 `settings.reg_code`（默认 `JJZ-2026`，现为自定义值）h468-474；口令哈希 `hash_pw` h188 = HMAC-SHA256(SECRET, salt"wk"+pw)（**静态盐，[设计取舍]**）。
- SECRET：外置 `secrets_store\secret_key.txt`（首启随机生成 h37-54）；**轮换该文件 = 全部口令哈希失效 + 存量 enc 密码不可解密**（见 §21）。
- admin：is_admin=1；`ensure_admin_password` h258 检测默认 admin123 时强制 16 位随机化并落 `secrets_store\admin_password.txt`（当前口令非默认，未触发）。
- 登录 cookie：wk_token 无过期/无 secure 标志（经 Tunnel 为 HTTPS 回源 http；cookie 相对本域）。
- 无找回/改密接口、无登出鉴权要求。

## 9. 商品 / 查询系统

- 商品表 products（8 列），3 条内置（cx_video/zhs_video/zhs_qr，全部"测试免费"）h180-186。
- 查课：`query_courses` h1012 → `tools_query_courses.py`（subprocess.run，**同步阻塞请求线程最长 180s**）。凭据已改经环境变量 WK_ACCOUNT/WK_PASSWORD（R4，argv 置空）h1020-1025。
- 扫码查课：qr 确认后经 `/api/courses?order_id=` → 读订单目录 cookies.json → zhs_cookie 模式。

## 10. 订单系统（主链路）

下单路径三处：/buy POST（h984-993）、QR 流（/api/qr_start → /order/<id>/set_courses）、/batch（h1425）。均 INSERT orders（password 经 `encrypt_secret` h349）。
订单字段 21 列（见 §19）；`order_dir(oid)` h460 惰性 `makedirs`（任何访问时创建，含 QR 期 cookie 落点）。
任务执行链：worker claim（h692）→ build_order_env（h555，建 work/tmp/home/logs + 指纹/代理/TEMP/HOME）→ run_chaoxing/run_zhs → _spawn（h620）→ RollingLog 线程落盘 → p.wait 20s 分片+心跳 → 超时 180min 树杀 → scan_risk（h604）→ 状态落库 → finally worker_running=0。
**无用户侧取消/重试/删除接口**（v5 备份版有，v4 主程序没有）。[已确认]

## 11. 订单状态机

实际状态集合（代码+数据）：`pending / waiting_qr / running / done / failed / canceled`。
补充语义：`worker_running=1` 承担 claim 标记（无独立 claimed 态）；exit_code 记录 rc（超时=-9）；note/risk_flags 为扩展字段。

| 状态 | 谁改 | 何时 | 条件 | 下一态 | 证据 |
|---|---|---|---|---|---|
| pending | 下单(INSERT) / qr_thread 扫码成功(h494-446) / recover_stale(h296) / worker 重试(h743) | 提交/扫码/恢复/崩溃重试 | - | running(claim) | h990/445/297/743 |
| waiting_qr | api_qr_start INSERT | 下单 | - | canceled(过期/取消 h449/455) / pending(扫码成功 h445) | h1187-1189 |
| running | claim_order | worker 抢占 | status=pending AND worker_running=0 | done/failed/pending(重试) | h692 |
| done | worker | rc=0 | - | 终态 | h735 |
| failed | worker / recover / qr 异常 | rc≠0/恢复超限/扫码异常 | - | 终态 | h748/300/459 |
| canceled | qr_thread | 过期/取消/异常 | - | 终态 | h449/455/459 |

流转图：
```
下单 → pending ⇢(CAS claim)⇢ running → done
                  │            ├→ failed
                  │            └→ pending(attempt<1 的重试)
waiting_qr → pending / canceled
recover_stale_orders(启动): running/waiting_qr → pending(attempt<1) / failed / canceled
```

已排查：
- 状态跳跃：无显式 claimed/timeout/crashed 枚举；统一用 failed+exit_code/note 表达（[设计取舍]）。
- 无出口：waiting_qr → canceled 已闭环；running 运行期崩溃 → **仅下次启动 recover 识别**（运行期无人持续判定）→ **[推断] 运行期进程被杀后，DB 保持 running 直到重启或新 claim 前**。
- DB 约束：status 无 CHECK/ENUM（SQLite 无约束）→ 代码写错状态也能入库（当前数据无异常值）。
- 一致性风险：**DB=running & Process=dead 可长期并存**（见 §35）。

## 12. Worker 系统

- 形态：**线程**（非进程）。创建：`concurrency_manager` h767 每 5s 刷新，维持 `settings.concurrency`（默认 1，当前 4，硬上限 MAX_CONCURRENCY=10 h55）个 worker 线程 h703-706；daemon=True。
- 领取：`claim_order` h692 —— 单事务内 SELECT pending 首行 + `UPDATE orders SET worker_running=1,status='running',attempt+1,... WHERE id=? AND worker_running=0` + rowcount 判定（**CAS，无重复领单**）；busy 异常捕获返回 None 重试。
- 执行：build_order_env → run_* → 状态落库 → finally 置 worker_running=0/hb 清空。
- 空转退避：连续无单 `idle+=1`，sleep 2s（<15 次）→ 5s h651。
- 内存护栏：`free_mem_mb < min_free_mb(500)` 时 sleep 10s 不接单 h645。
- 退出：无显式自退；异常全捕获（h752 外层 except）；线程死亡由 concurrency_manager 重建。
- 异常兜底：safe_set_order h216 3 次退避 + platform_error.log；失败订单写 failed（异常文本去 APP_DIR 路径）。

## 13-14. 进程树与 subprocess / multiprocessing

| 父进程 | 子进程 | 方式 | 超时 | 结束方式 | 证据 |
|---|---|---|---|---|---|
| worker 线程 | venv python（引擎 main.py / run_zhs.py） | Popen（BELOW_NORMAL\|CREATE_NO_WINDOW，stdout=PIPE→RollingLog） | 心跳 20s 分片，订单硬超时=order_timeout_min(180) | 超时→`_kill_tree`(taskkill /F /T + 验证≤5s)；自然退出 join | h620-661 |
| worker 线程（zhs 无课程） | venv python run_zhs.py list（孙进程） | subprocess.run 同步 | 180s | run 返回即收 | h684 |
| HTTP 请求线程 | tools_query_courses.py | subprocess.run 同步 | 180s | 同上 | h1025 |
| health_manager | pythonw order_platform.py | Popen（DETACHED\|CREATE_NEW_PROCESS_GROUP\|NO_WINDOW, stdout→platform_stdout.log） | - | taskkill /F /PID(端口占用者) 重启 h207-212 | h167 |
| health_manager | cloudflared.exe | Popen（同上 detach，剥离代理 env） | - | 看护检测 tasklist 缺失则重启 | h187 |
| 用户/运维 | 计划任务/脚本 | - | - | stop_platform.ps1 强杀 | - |

- multiprocessing：**无**。[已确认]
- shell=True / os.system：**无**。[已确认]
- 任务子进程内部（fuckCourse 引擎）线程并发，不再派生进程（§4）。zhs 的一次性 list 孙进程"先起先收"。
- PID 记录：orders.pid（_spawn h556 写）；进程存活判定 `_pid_alive` h231（OpenProcess SYNCHRONIZE）。

## 15. Windows 进程管理（平台侧）

- 子进程优先级 `BELOW_NORMAL 0x4000`、无窗口 `CREATE_NO_WINDOW 0x08000000` h144-145。
- 树杀 `_kill_tree` h245：taskkill /F /T + 轮询验证退出（10×0.5s）。**无 Job Object**（进程树 ≤2 层，评估后不采用）。
- 另：sweep 脚本 tools\cleanup_stale.py（PowerShell CIM 枚举本项目 python，保留端口持有平台与锁持有看护；看护单进程终止、平台树杀）。
- **环境现象（非代码缺陷）**：本审计会话中，计划任务触发的 pythonw 多次出现"1 线程~4MB、卡在解释器早期"的僵尸；锁文件保证活跃侧唯一，僵尸经清扫/重启回收。[需正式机长期观察]

## 16. 订单隔离环境

`orders\<id>\`（`build_order_env` h555 创建）：work/ tmp/ home/ logs/ + cookies.json + log.txt。

| 项 | 创建 | 使用 | 删除 | 生命周期 |
|---|---|---|---|---|
| cookies.json | 引擎登录后写（zhs 扫码写 h438-443；引擎运行写） | 引擎持 FUCKCOURSE_COOKIES 指引用 | housekeeping 3 天按目录 mtime rmtree | 3 天 |
| log.txt | _spawn 前 tail_keep 预裁 | RollingLog append；详情页读尾 8KB | 同上 | 3 天 |
| work/tmp/home/logs | build_order_env | 引擎工作目录/TEMP/HOME/日志 | 同上 | 3 天 |
| orders\_query\*.json | 查课子进程 Cookie 落点（uuid 名） | tools_query_courses | housekeeping >1h 删 | ≤1h |

- 隔离：每单独立目录 + env(TEMP/HOME/USERPROFILE/TMP + UA 指纹 WK_* + 可选代理) h499-522；`FUCKCOURSE_CONFIG` 全局引擎配置、`FUCKCOURSE_COOKIES` 每单。
- 残留风险：非 housekeeping 时点崩溃 → 目录残留至下次 housekeeping；目录 mtime 判定（3 天）。
- 跨订单误用：文件系统层面无共享（除 FUCKCOURSE_CONFIG 全局 config.json）。

## 17. 文件系统读写点

| 文件 | 写 | 读 | 删 |
|---|---|---|---|
| orders\platform.db | db() 各处 | 各处 | -（随库） |
| orders\<id>\* | 引擎/_spawn/qr | 详情页/housekeeping | rmtree(3d) |
| orders\_query\*.json | 查课 | 查课 | >1h |
| cf\cloudflared.log | cloudflared | - | tail_keep 512KB (h766) |
| cf\health_manager.log | 看护 | 诊断 | 超 512KB 清空重写 |
| cf\platform_stdout.log | 看护拉起平台 | 尸检 | tail_keep 512KB |
| platform_error.log | safe_set_order 兜底 | 诊断 | tail_keep 256KB |
| fuckCourse\logs\* | 引擎 | scan_risk(尾400KB) | 512KB 裁剪 / dated 按 3 天删 |
| secrets_store\* | 平台启动 | 平台 | - |
| backups\*.db | 备份 | 恢复 | 7 份轮转 |

## 18. SQLite 连接与事务

- 连接：`db()` h58 —— 每次操作新建（`with db() as c`）；`timeout=15`、row_factory=Row、PRAGMA journal_mode=WAL / synchronous=NORMAL（2）/ busy_timeout=15000（**代码连接级**）h63-65。**注意：`sqlite3.connect` 默认 timeout=5（busy_timeout=5000），db() 显式设为 15s**。
- **无全局共享连接**、无连接池、无长事务。
- 事务：全部 `with db() as c` 隐式事务（deferred BEGIN→COMMIT）；**无显式 BEGIN IMMEDIATE / ROLLBACK**。
- 实际实测（独立只读连接）：journal_mode=wal、synchronous=2、page_size=4096、**foreign_keys=0**（代码未启用）、索引仅各表主键 autoindex。

## 19. 数据库 Schema（实测 SQLite 3.53.1）

```sql
users(id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL, pw_hash TEXT NOT NULL,
      is_admin INTEGER DEFAULT 0, created_at TEXT DEFAULT (datetime('now','localtime')))
orders(id TEXT PRIMARY KEY, user_id INTEGER, product TEXT DEFAULT '', platform TEXT, account TEXT,
       password TEXT, courses TEXT, status TEXT DEFAULT 'pending', note TEXT DEFAULT '',
       qr_state TEXT DEFAULT '', created_at TEXT, started_at TEXT, finished_at TEXT,
       exit_code INTEGER, worker_running INTEGER DEFAULT 0,
       product, env_profile, risk_flags, speed, pid, attempt, heartbeat_at)  -- ALTER 增列
products(id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT UNIQUE, name TEXT, desc TEXT, price TEXT,
         platform TEXT, enabled INTEGER DEFAULT 1, sort INTEGER DEFAULT 0)
settings(key TEXT PRIMARY KEY, value TEXT)
```
- 21 列（orders）/ 6 / 8 / 2；仅主键索引（sqlite_autoindex_*4）；**无 CHECK/FK/唯一约束（除 username/code/key）**。
- 时间文本 `YYYY-MM-DD HH:MM:SS`（P2 迁移后存量 19 单全部带年份，无残留）。
- 密码 `enc:v1:` 前缀密文（0 明文，实测）。

## 20. 数据库事务清单（重点 SQL）

| 操作 | SQL | 事务 | 证据 |
|---|---|---|---|
| claim（原子领单） | SELECT pending + `UPDATE ... WHERE id=? AND worker_running=0` + rowcount | 单事务（隐式） | h692 |
| 状态写 | `UPDATE orders SET ... WHERE id=?` | 单语句 | set_order h206 |
| 兜底写 | 同上×3 退避 | 单语句 | safe_set_order h216 |
| 下单 | INSERT orders | 单事务 | h990-992 / 1360 / 1187 |
| 设置 | INSERT ON CONFLICT DO UPDATE | 单语句 | set_setting h485 |
| 年份迁移 | 逐行 UPDATE（同连接） | 单事务 | migrate_timestamp_years h390 |
| 密码迁移 | 逐行 UPDATE（每行独立连接） | 每行一事务 | migrate_encrypt_passwords h369 |
| 备份 | sqlite3 backup API（源→目标） | 引擎内 WAL 安全 | backup_manager h37 |
- 无显式 ROLLBACK；无事务内网络/睡眠/subprocess；busy 冲突由 busy_timeout + 重试缓解。

## 21. Cookies / Token / Secret（真实值已隐藏/脱敏）

| 项 | 来源 | 存储 | 使用处 | 是否进日志 | 是否入库 | 是否 HTTP 传递 | 是否 Web 可达 |
|---|---|---|---|---|---|---|---|
| 平台登录 token | 登录时生成 | 浏览器 cookie `wk_token`（uid:sig，sig=hmac(SECRET,"u"+uid)） | current_user h196 | 否 | 否 | 是（Cookie 头） | -（仅本域） |
| SECRET（会话+加密密钥基） | secrets_store\secret_key.txt（首启随机） | 文件 | hash_pw/登录/encrypt/decrypt | 否 | 否 | 否 | 否 |
| 订单密码密文 | 下单时 encrypt_secret | orders.password（enc:v1:） | worker 解密→env 传引擎 | 否 | 是（密文） | 否 | 否 |
| 订单 cookies.json | 引擎/扫码写 | orders\<id>\cookies.json（明文 JSON） | 引擎/扫码查课 | 否 | 否 | 否 | 否（无下载路由） |
| 引擎全局 cookies | fuckCourse\cookies.json + cookies_13000000000.json（手机号为名，敏感残留） | 文件 | 引擎 config use_cookies | 否 | 否 | 否 | 否 |
| 引擎 .zhs_cred | 引擎内部 | 文件 | zhs | 否 | 否 | 否 | 否 |
| 引擎 config.json | 库内 | 文件 | 引擎（chaoxing.common 含明文账号口令，**脱敏**；pushplus.token 空、bark 占位、openai/moonshot key=“sk-”占位） | 否 | 否 | 否 | 否 |
| CF 隧道凭据 | %USERPROFILE%\.cloudflared\{wk_config.yml, cert.pem, 24f…json} | 文件 | cloudflared | 否 | 否 | 否 | 否 |
| admin 口令 | 首次强制随机 | secrets_store\admin_password.txt | 人工 | 否 | 否 | 否 | 否 |
- **密钥轮换影响（关键）**：删除/轮换 secret_key.txt → 全部用户口令哈希失效 + 存量 enc 密码不可解密（订单以认证失败收场）。[已确认，代码语义]
- 加密算法：enc:v1: = 随机 16B nonce + HMAC-SHA256 派生 keystream 异或（非标准密码学方案，**[设计取舍]/需审计**）。

## 22. 日志系统

| 日志 | 位置 | 大小限制 | 周期 | 含 oid/pid | 敏感检测 |
|---|---|---|---|---|---|
| 订单日志 | orders\<id>\log.txt | RollingLog 300KB(h106-141, 可配 log_keep_kb) | 3 天目录删 | 隐含 oid（目录名） | 引擎输出原样落盘，**token 未见，账号名可见**[推断] |
| 引擎 chaoxing.log / zhs_logs\* | fuckCourse\logs | tail_keep 512KB | 保留天数(3) | - | 同引擎输出 |
| loguru 日期轮转 | chaoxing.2026-*.log | 先 512KB 裁剪再按 3 天删（P3） | 3 天 | - | 同 |
| 平台错误 | platform_error.log | 256KB | 保留 | 含 oid | set_order 兜底失败参数（含状态值，无明文密码） |
| 看护 | cf\health_manager.log | ≥512KB 清空 | - | - | 无 |
| 平台崩渍 stdout | cf\platform_stdout.log | 512KB | - | - | Waitress 输出 |
| cloudflared | cf\cloudflared.log | 512KB 裁剪 | - | - | 隧道日志 |
- 格式：订单日志为引擎 print 原样（无统一字段）；看护日志 `YYYY-MM-DD HH:MM:SS msg`；无结构化日志；无 loguru 统一（引擎自用 loguru）。
- 异常文本写入订单 note 时已做 APP_DIR 路径替换（h683）与长度截断（h459/683）。[已确认]

## 23. Timeout 全表

| 类型 | 值 | 文件/函数 | 作用 |
|---|---|---|---|
| HTTP（qr 轮询/登录） | 10s / 15s | order_platform qr_thread h428/431；api_qr_start h1192 | 知到扫码 |
| 查课子进程 | 180s | query_courses h1025；zhs list h684 | 防卡死 |
| 子进程心跳分片 | 20s | _spawn h640 | 轮询 wait |
| 树杀验证 | ≤5s（10×0.5s） | _kill_tree h252-256 | 确认退出 |
| 订单硬超时 | order_timeout_min=180min（min 10） | _spawn h561, 632-636 | 熔断 |
| RollingLog join | 3s/5s | _spawn h586/588 | 收尾 |
| 看护 urlopen /health | 5s | health_manager h60 | 健康检查 |
| 看护轮询 | 30s | h113 | 主循环 |
| 扫描/枚举 | 15-30s | health_manager netstat h68 / cloudflared_running h181 / cleanup_stale | 工具 |
| 心跳写库间隔 | 20s | _spawn h571-574 | 存活佐证 |
| **软/硬双段超时** | **无** | - | [设计取舍]：单层硬超时 |
| 引擎内部请求超时 | [未知] | fuckCourse 内部 | 未审计 |

## 24. Retry

| 位置 | 策略 | 上限 | 证据 |
|---|---|---|---|
| worker | 超时(-9)/负退出码 → 重回 pending，attempt 计数 | MAX_RETRY=1 h214/743 | 参数/认证/业务错误不重试 |
| 看护重启平台 | 失败计数 + 熔断 | 10min 3 次(h39-40) | 超限停止重启转人工 |
| 看护补备份 | 失败 1h 冷却重试 | 每日 | h231-233 |
| safe_set_order | busy 退避 | 3 次 | h216 |
| claim busy | 返回 None 自动重试 | 无限（空转） | h634-635 + worker sleep |
| 引擎章节级重试 | 引擎内部（max_tries=5） | [未知，未审计] | fuckCourse 内部 |
| QR janitor / housekeeping | try 静默 | - | - |

## 25. Exception Handling

| 位置 | 捕获 | 效果 | 是否吞 | 证据 |
|---|---|---|---|---|
| worker 主循环 | except Exception 全捕获 | 写 failed（路径脱敏） | 否（有落库+日志） | h752-760 |
| _spawn 心跳/状态写 | except Exception pass | 心跳失败容忍 | 是（低风险） | h570/575 |
| _kill_tree taskkill | except Exception pass（异常仅记录首层） | - | 部分 | h248-251 |
| RollingLog | except Exception pass | 写日志失败静默 | 是（设计） | h130-131 |
| qr_thread | except Exception pass → canceled | 扫码异常→取消 | 否（有终态） | h457-460 |
| housekeeping | 双重 try 包裹 | 单项失败不中断其它 | 是（容忍） | h724-783 |
| recover_stale / migrate | try 包裹 | 失败打印不崩 | 部分 | h278/369/390 |
| 看护主循环 | 单轮 try+continue（P1 加固） | 单轮异常不死 | 否（记日志） | health_manager h235 |
| 平台主入口 | `_main_` 无外层 try | Waitress 异常→进程退出→看护拉起 | - | h1733 [推断] |
- 结论：**平台进程自身无崩溃吞并**；看护/worker 均有兜底；被吞异常集中于低风险 IO；**订单最终状态理论上总能落库（safe_set_order），除非 DB 完全不可写。**

## 26. Cleanup

| 对象 | 机制 | 周期 | 触发 | 证据 |
|---|---|---|---|---|
| 老订单目录 | rmtree 按 mtime>3 天 | 30min | housekeeping h795 | h728-736 |
| _query | >1h 删 | 30min | 同上 | h738-746 |
| 引擎日志 | tail_keep 512KB | 30min | 同上 | h748-753 |
| dated 日志 | 512KB 裁剪 + 3 天删 | 30min | 同上 | h755-764 |
| cf / 平台 / 错误日志 | 512KB/512KB/256KB | 30min | 同上 | h766-770 |
| 每日备份 | backup 调度 | 30min 判 24h | 同上 | h772-780 |
| QR 会话 | janitor 120s | 2min | qr_janitor h782 | h715-719 |
| 进程（僵尸） | 扫清脚本 | 按需 | 人工 | tools\cleanup_stale.py |
| 程序退出 | **无 atexit / 无优雅清理** | - | - | [已确认] |
| 异常退出 | 同上（无） | - | - | 恢复依赖下次启动 recover |

## 27. Startup（完整时序）

```
Windows 登录/计划任务触发(Logon + 5min) 
 → 计划任务执行 pythonw health_manager.py（单实例，IgnoreNew；RestartCount=3/10min 兜底）
 → 看护：acquire_lock 锁文件(cf\health_manager.lock, O_EXCL+pid/ts) 
 → log 启动 → 循环：/health 检测 → 平台未起 → 冷却内重启 → start_platform(pythonw order_platform.py)
 → 平台模块级：init_db → _ensure_settings → ensure_admin_password → migrate_encrypt_passwords
   → migrate_timestamp_years → recover_stale_orders → 启动 3 个后台线程(concurrency/qr_janitor/housekeeping)
   → Waitress serve(8766, threads=16)
 → 看护另：cloudflared 缺失则拉起(剥离代理)
 → 用户可访问；
```
- 幂等：锁 + IgnoreNew + 端口唯一；重复启动看护自动让位；重复平台由看护 listener_pid 清理。
- 启动失败行为：Waitress 端口被占→启动异常→进程退出→看护再拉起（10min3 次熔断）；DB 损坏→init_db 异常→平台退出→看护重启（循环+熔断）。
- 环境现象：[需运行验证] 僵尸 pythonw 清扫依赖 sweep/重启（见 §15）。

## 28. Shutdown

- 无 Ctrl+C/信号处理、无 atexit（窗口关闭 → 平台进程退出 → 任务子进程孤儿化 → 下次启动 recover 处理）。
- 正式停机路径：`tools\stop_platform.ps1`（停计划任务 → 强杀本项目 python/隧道）或 Stop-ScheduledTask + 手动 taskkill。
- 停机时运行中订单：Process 孤儿 → 下次启动 recover_stale_orders（pid 判活→树杀→attempt<1 重排/failed）。
- **无优雅停机/维护态标记**。[设计取舍]

## 29. Crash Recovery

| 故障 | 恢复 | 证据 | 验证态 |
|---|---|---|---|
| 平台（Flask/Waitress）崩 | 看护 30s 检测 /health，连续 2 次失败+冷却 → taskkill 端口占用者 → 重启 | health_manager h200-222 | 本会话多次实测拉回 [已确认]（正式机 09-13 曾三起自愈） |
| worker 崩 | 线程不死 + finally 清状态 + concurrency_manager 重建 | h752/688/767 | [已确认] |
| 任务子进程崩 | rc≠0 → failed / 自动重试 1 次 | h739-750 | [已确认] |
| cloudflared 崩 | 看护 tasklist 检测 → 冷却拉起 | h177-196 | [已确认]（日志留痕） |
| Windows 重启 | 计划任务 → 看护 → 平台 → recover_stale_orders | - | [已确认代码] |
| 双实例/僵尸（环境现象） | 锁文件保活跃唯一；维生素 sweep/重启 | §15 | [需正式机观察] |
| SQLite busy | claim→None；写→safe_set_order 3 次 | - | [已确认] |

## 30. Cloudflare Tunnel

- binary `cf\cloudflared.exe`（独立进程）；config `%USERPROFILE%\.cloudflared\wk_config.yml`；凭据 cert.pem + 24f….json。
- 启动：看护 start_cloudflared h187（--no-autoupdate，剥离 HTTP(S)/ALL_PROXY，NO_PROXY="*"）或 run_tunnel_detached.py（备用手动）。
- 守护：看护每 30s 检测 tasklist；日志 cf\cloudflared.log（512KB 裁剪）。
- 公网面：仅 Tunnel 出网；Flask 只监听 127.0.0.1（无端口映射）；回源明文 http（本机回环）。
- 故障影响：隧道断 → 外网 502，本机不受影响；看护拉起后恢复。

## 31. 外部 API

| 外部 | 用途 | 认证 | 状态 |
|---|---|---|---|
| 学习通（超星）API | 登录/课程/视频/签到/答题 | 账号密码/cookies（引擎内） | 引擎内部，未逐行审计 |
| 知到 passport.zhihuishu.com | 扫码登录（qr 部分平台侧直调 h426-436） | 二维码 | 平台侧仅二维码获取/轮询/换 token |
| 知到引擎（run_zhs） | 刷课 | cookies/扫码 | 引擎内部 |
| PushPlus | 完成通知 | config zhs.pushplus.token | 当前 enable:false |
| OpenAI / 月之暗面 AI 题库 | 答题（tiku） | config api_key（占位 sk-） | 未启用 |
- 说明：除扫码二维码外，**全部业务网络行为在 fuckCourse 引擎内部**，超出本档案审计范围（属"不触碰"边界）。

## 32. 配置系统

| 配置 | 来源 | 当前值 | 使用处 |
|---|---|---|---|
| concurrency | db settings | 4 | concurrency_manager h772 |
| jobs / speed / min_free_mb / log_keep_kb / log_keep_days / spoof / jitter / verbose / proxy_pool / order_timeout_min / paused | db settings | 2 / 2.0 / 500 / 300 / 3 / 1 / 1 / 0 / '' / 180 / 0 | worker/_spawn/housekeeping/admin |
| reg_code | db settings | 自定义值 | register h1560 |
| last_backup | db settings（平台/看护共用） | 时间戳 | housekeeping/看护备份判定 |
| MAX_CONCURRENCY / MAX_RETRY / DEFAULT_REG_CODE / RISK_PATTERNS / UA_POOL / 限流阈值 | 代码常量 | 10 / 1 / JJZ-2026 / … / 4 条 / … | 各处 |
| WK_FUCKCOURSE_CONFIG / COOKIES / LOG_DIR / HOME / TMP + WK_UA 等 | 平台注入 env | - | 引擎子进程 |
| FUCKCOURSE_CONFIG / COOKIES（tools_query_courses） | env | - | 查课 |
| PYEXE / 端口 / 路径 | 代码硬编码 | venv / 8766 / D:\web | 各处 |
| 计划任务/bat/VBS | 系统级 | 见 §27 | 自启 |
| PushPlus/Bark/AI key | fuckCourse\config.json | enable:false / token 空 / sk- 占位 | 引擎 |

## 33. 定时任务 / 后台线程

| 线程/任务 | 周期 | 说明 |
|---|---|---|
| concurrency_manager | 5s | worker 水位 |
| qr_janitor | 120s | QR 会话清理 |
| housekeeping | 1800s | 清理+裁剪+每日备份 |
| worker×N | 2s→5s | 领单循环 |
| 看护循环 | 30s | 健康/隧道/补备份 |
| 平台心跳写 | 20s/单 | 仅运行中订单 |
| 计划任务触发 | 5min+登录 | 看护兜底重启 |
- 全部为进程内线程（daemon）+ 独立看护进程；无独立定时器框架。

## 34. 并发模型

- 线程池：Waitress 16；worker N(4)；定时 3；瞬时 qr/RollingLog 每单 1 个。
- **共享状态（无显式锁，[推断] 依赖 GIL）**：
  - `QR_SESSIONS` dict（qr_thread 写 / HTTP 读 / janitor 删）——单条 dict 操作原子；无互斥锁。
  - `_RATE` dict（限流；append/清空）——机会性竞态可接受。
  - `_proxy_idx` dict 轮询。
  - `workers` 列表（concurrency_manager 维护）。
- SQLite：每操作新连接；WAL 读不阻塞写；写竞争→busy_timeout+重试（见 §24）。
- 文件：订单专用目录无共享（除全局 config.json 只读）。

## 35. 数据一致性（四状态矩阵）

当前可组合（代码语义）：

| DB | Process | Worker | Filesystem | 是否可能出现 | 说明 |
|---|---|---|---|---|---|
| running | alive | alive | exists | ✅ 正常执行中 | 一致 |
| running | dead | dead(线程被杀前) | exists | ⚠️ 可能长期存在 | 运行期无人探测 → 仅启动恢复识别 |
| pending | - | - | 无目录 | ✅ | - |
| done/failed | - | - | exists ≤3天 | ✅ | 正常 |
| done/failed | - | - | 已删 | ✅ | housekeeping 后 |
| running | alive | dead | exists | ❌（线程死于进程存活不可能） | 平台内线程死会重建 |
| double-platform | 2×running worker 池 | - | - | ⚠️ 环境现象出现过 | CAS 防重复领单，但双池资源与心跳双写 |

- 关键短板：**运行期 heartbeat_at 只写不读**（仅 recover_stale_orders 在启动时借 pid 判活）；DB running + 进程 dead 只能靠重启收敛。[已确认代码]

## 36. 安全现状（现状记录，不改）

| 项 | 状态 | 证据 |
|---|---|---|
| 登录/口令 | 哈希+外置 SECRET+强制随机 admin | §8 |
| CSRF | Origin/Referer 校验、无 token、无头放行 | h874-876；[设计取舍] |
| SQL 注入 | 全部参数化（LIKE 用 ?） | h1227/query |
| XSS | **页面输出未做 HTML 转义（f-string 直插）**：订单 note/用户输入直进 HTML；风险面=引擎输出/异常文本/注册用户名/二维码状态。single-page 无 CSP 头 | h899-899 模板+各页拼接 [推断：部分字段可注入] |
| 路径穿越 | 无用户文件路径接口；detail 仅服务端拼 orders\<id>\log.txt | h1355 [已确认] |
| 任意删/任意命令 | 无；subprocess 参数固定+env 凭据；无 shell=True | §14 |
| 上传/下载 | 无 | §7 |
| 敏感暴露 | orders/backups/secrets 不在 static；无下载路由 | [已确认] |
| 8766 | 仅回环 | [已确认] |
| Tunnel | 唯一出网 | §30 |
| 限流 | 4 入口 | §7 |
| 其它 | cookie 无 Secure/HttpOnly 已设 HttpOnly（set_cookie httponly=True h1606）无 path/domain 限定；SECRET 轮换风险；静态盐 | §8/§21 |
| 已知残留文件 | fuckCourse\cookies_13000000000.json、config.json 明文引擎凭据 | §21 |

## 37. 资源管理

- 实测（会话期）：平台 ~33MB/24 线程；cloudflared ~33MB；看护 ~24MB；每运行订单子进程估 40-80MB（min_free_mb=500 护栏）。
- 护栏：free_mem_mb h71（GlobalMemoryStatusEx）+ min_free_mb 阈值暂停接单。
- 磁盘：无持续采样；管理页按需 disk_usage；backups 7 份；日志全裁剪（§22）。
- **未发现常驻监控机制（无 CPU/内存/磁盘采集器、无图表）**。[已确认]
- 无任务时空闲 CPU≈0（退避后）；SQLite 无意义轮询在 2s→5s 间有控制。[推断-实测基线]

## 38. 当前已知限制（现状清单）

1. 运行期心跳不读 → 卡死订单需重启收敛（§35）。
2. 无用户侧取消/重试/删除（v5 才有）。
3. 单层硬超时；无 soft/hard、无 step timeout。
4. 查课同步阻塞请求线程 180s。
5. 全局无 XSS 转义/无 CSP。
6. enc:v1: 加密非标准密码学方案；静态盐；无 per-user salt。
7. cookie 无 Secure 标志（Tunnel 场景回源 http；公网 https 下 cookie 经 TLS 传输，但首跳无校验）。
8. 无异地备份；备份依赖单机。
9. 多平台实例（env 现象）时双 worker 池资源翻倍（CAS 保证不误领）。
10. v5 备份文件与线上库同路径（勿运行）。
11. 引擎日志直落订单 log（无脱敏层）；引擎 INFO 打印账号名。
12. 计划任务 RestartCount=3/10min + RestartInterval + ExecutionTimeLimit=PT0S（无限）的长期行为未在正式机完整验证。

## 39. 当前无法确认的问题 [未知]

- 引擎内部（fuckCourse）全部细节：题库 provider/通知/AI 密钥实际填写/请求时序/章节重试次数/是否打印 token。
- 各订单目录内引擎自建子文件结构及内容。
- SQLite 当前 .db/.wal 实际体积（本次会话未记录确切字节数）。
- cloudflared wk_config.yml 内容（只确认存在与使用）。
- 引擎对 WK_UA/代理的实际使用方式（跳转链）。
- 正式机长时间（≥2 天）的 CPU/RAM 曲线。

## 40. 需要运行验证的问题 [需运行验证]

1. 正式机 watchdog ≥2 天存活 + 崩溃/自愈序列（本会话环境会定期外部中断长驻进程，无法代表正式机）。
2. 看护"每日补备份"首次实际触发（当前距 24h 未到点；函数本身复用已验证的 backup_manager）。
3. R4 引擎 env 凭据端到端真实下单/报课流程（不触达第三方平台而无法完成）。
4. /qr 全流程（P3 增加登录鉴权后）在正式浏览器验证。
5. 管理页 worker 明细列渲染与实际内存读数。
6. 计划任务双触发（Logon+5min）在正常使用（非审计扰动）下是否产生重复实例或僵尸。
7. Waitress 长稳（内存是否随时间增长）。

## 41. 项目完整状态总结（一句话版）

已在 Windows 单机、零新增常驻/第三方组件前提下，将平台从"单线程 dev server + 无恢复"升级为"Waitress + CAS 原子领取 + 20s 心跳 + 180min 硬超时树杀 + 启动恢复 + 锁文件看护 + 在线备份轮转 + 凭据去命令行 + 管理可观测"的稳定形态；业务引擎零改动；主要未决风险集中在"运行期卡死检测依赖重启收敛"与"正式机长时间行为未观察"两类。

## 42. 状态矩阵（交付第二个 AI 的速查表）

| 系统 | 当前状态 | 证据 | 风险未知项 | 是否需要运行验证 |
|---|---|---|---|---|
| Flask | 运行中 /health OK | §7 h1715 | XSS 转义缺失 | 否 |
| Waitress | 运行中 threads=16 | h1737 | 长稳/内存增长 | **是** |
| SQLite | WAL/NORMAL/5000-15000ms | §18 | 并发写压力上限、页面 4096 无增量 | 是（满负荷） |
| Worker | 4 线程 CAS 领单 | §12 h692 | 心跳无读取方 | 否 |
| Order | 19 全终态 | §1 | 无运行期卡死检测 | 是（注入） |
| subprocess | 树杀+验证 | §14 | zhs list 180s 阻塞 | 否 |
| Cloudflare | 运行中+看护拉起 | §30 | 长稳 | 是 |
| Cleanup | 30min 汇 + 3 天/1h | §26 | 崩溃点残留 | 否 |
| Logging | 全裁剪+脱敏基础 | §22 | 引擎原样输出 | 否 |
| Recovery | 启动恢复+看护自愈 | §29 | 僵尸频率（env） | **是（正式机）** |

## 43. 证据索引（重要结论 → 文件/函数/行号）

- 原子领单：order_platform.py `claim_order` h692-635
- 状态机写点：`set_order` h206 / `safe_set_order` h216 / `recover_stale_orders` h276
- 子进程：`_spawn` h620 / `_kill_tree` h245 / RollingLog h103-141
- worker：h710-765（退避 h651、护栏 h645、重试 h743、finally h688）
- 心跳写：h571-574；**读取方=无（除启动恢复）**
- 硬超时：h561/632-636；$order_timeout_min
- 看护：health_manager.py `acquire_lock` h111 / `main` h198 / 补备份 h148-166 / 熔断 h37-40
- 备份：backup_manager.py `backup_database` h37 / `restore_database` h78 / KEEP=7 h22
- 限流：`rate_limit` h308；登录/注册/查课/扫码引用 h1147/1256/1553/1592
- CSRF：`_csrf_guard` h874 / `same_origin_ok` h323
- 登录 cookie：h1677-1711；校验 h196-204
- 启动链：h785-792（init 段）→ h1733-1739（入口）
- 后台线程：h865-867
- 密码加密：`migrate_encrypt_passwords` h369 / `encrypt_secret` h349 / `decrypt_secret` h358
- WK 环境注入：h499-522（build_order_env）
- 清理：housekeeping h795-783；qr_janitor h782-721
- 时间迁移：`migrate_timestamp_years` h390
- 状态卡 worker 列：admin `_run_row` h1482 / `proc_mem_mb` h413（kernel32.K32GetProcessMemoryInfo）

（本文档结束。未包含任何密码/密钥原文；所有敏感值已脱敏或标注"存在、位置、值隐藏"。）

---
## 增量注记（2026-09-14 第二轮之后）
- 本档案生成后已完成两轮修复（详见 SECOND_ROUND_AUDIT.md / FINAL_ACCEPTANCE.md）：P0×2（运行期 watchdog 收敛、recover kill 校验）、P1×5（XSS 转义 15 处、日志落盘脱敏、限流 IP 策略、restore 在线拦截、明文残留归位）、P2×2（查课并发护栏、tasklist 超时）。
- 相应行号可能偏移（新增 watch/esc/信号量等函数），核心结论不受影响；以 SECOND_ROUND_AUDIT.md 的行号为准。
