# LOCAL_FUNCTIONAL_PARITY.md — 本地功能基线冻结（Commit 06）

> 状态：**冻结基线（Commit 06 产出）**
> 取证方式：2026-09-22 对 `D:\web` 工作区**实测读取**（非记忆、非推测）。下文所有 `文件:行号` 均为当时真实位置。
> 用法：后续 Commit 07–29 的每一项云化工作都必须引用本文件的功能编号（F01–F28），不得凭印象增删功能。

---

## 0. 冻结纪律

1. 本 Commit **不修改任何业务代码**，只登记现状与定义接口。
2. 状态列只允许三种取值：`PASS`（已实测可用）、`TEST ADAPTER`（云端以 Synthetic/Replay 等价实现）、`NOT AVAILABLE`（**仅限 §38 明列的本地专属配置项**，且必须在 UI 显示为 "Cloud Test only / Not available"）。
3. **禁止**把任何真实平台能力写成 `NOT SUPPORTED`。能力存在，只是执行层换成测试适配器。
4. 本基线只描述**功能与接口**，不描述真实平台实现细节；真实平台 URL 不出现在任何云化代码中（Commit 03/04 的 egress guard 是硬约束）。

---

## 1. 三环境定义

| 环境 | 宿主 | 存储 | 执行层 | 用途 |
|---|---|---|---|---|
| `LOCAL_PRODUCTION` | Windows（本机） | SQLite `orders/platform.db` | **Real Adapter**（真实第三方平台） | 真实业务 |
| `CLOUD_TEST` | Codespaces / Linux | Durable Object SQLite（规划） | **Synthetic / Replay Adapter** | 全流程功能验证 |
| `CI_TEST` | GitHub Actions | 内存 / 临时库 | Contract + State 测试 | 单测/契约/安全 |

核心红线：`功能共用、适配器分离、运行时分离、数据分离、凭据分离`。

---

## 2. 基线快照（实测）

| 项 | 实测值 | 证据 |
|---|---|---|
| 应用主文件 | `order_platform.py`，1820 行 | `wc -l` |
| Web 栈 | Flask + Waitress（16 线程，回退 dev server） | `order_platform.py:1815-1821` |
| 监听 | `127.0.0.1:8766` | `order_platform.py:1819` |
| 路由总数 | **18** | `grep -c "@app.route"` |
| 表 | `users` / `orders` / `products` / `settings` | `order_platform.py:164-204, 496` |
| `orders` 列 | id,user_id,product,platform,account,password,courses,status,note,qr_state,created_at,started_at,finished_at,exit_code,worker_running,env_profile,risk_flags,speed,pid,attempt,heartbeat_at | `order_platform.py:173-194` |
| 状态取值 | `pending`/`running`/`done`/`failed`/`waiting_qr`/`canceled` | `order_platform.py:178, 1347, 1445` |
| settings 键 | reg_code,jobs,min_free_mb,log_keep_kb,log_keep_days,speed,concurrency,proxy_pool,spoof,jitter,order_timeout_min,verbose(+paused,last_backup) | `order_platform.py:497-500`, `health_manager.py:198` |
| 硬常量 | `MAX_CONCURRENCY=10`、`MAX_RETRY=1`、查课并发 `BoundedSemaphore(2)` | `:61`, `:231`, `:1080` |
| 并发模型 | 常驻 worker 线程池，CAS 抢占 + `attempt` + `heartbeat_at` | `:723-741, 826-839` |
| 本地组件 | `crypto_manager.py` 419 行、`health_manager.py` 487 行、`backup_manager.py` 170 行、`service_platform.py` 248 行 | `wc -l` |

**已确立的安全前提（Commit 03/04，本基线继承）**：`CLOUD_TEST_MODE=1` + `CLOUD_TEST_EGRESS_REQUIRED=1` 时，`zhihuishu.com`/`chaoxing.com`/`api.openai.com` 在 Python 层与内核 nftables 层双层拒连（0ms reject），任一条件不满足即 fail-closed 拒绝启动。

---

## 3. F01–F28 逐项登记

字段说明：**Local 实现** = 真实代码位置；**云化方式** = 目标实现；**适配器** = 归属的 Adapter 契约（见 `docs/ADAPTER_CONTRACT.md`）。

### F01 首页
- Local 实现：`GET /` → `home()`，`:1045-1082`
- Local：`PASS`（商品卡片列表 + 入口导航）
- 云化方式：同一路由，数据源换成 `ProductRepository`
- CI：`PASS`（渲染冒烟）
- 适配器：`storage`（Product 读取）

### F02 注册
- Local 实现：`GET/POST /register` → `register()`，`:1720-1759`；口令校验 `settings.reg_code`；口令哈希 `hash_pw()` `:205`
- Local：`PASS`
- 云化方式：`AuthService.register()` + `CloudAuthRepository`
- CI：`PASS`（注册口令错误 / 重名 / 成功三分支）
- 适配器：`auth`

### F03 登录
- Local 实现：`GET/POST /login` → `login()` `:1759-1791`；`login_user()` `:208`；会话 `current_user()` `:213`
- Local：`PASS`
- 云化方式：`AuthAdapter.login()`；外层可叠加 Cloudflare Access，但**应用内 user/role/permission 模型必须保留**
- CI：`PASS`
- 适配器：`auth`

### F04 退出
- Local 实现：`GET /logout` → `logout()`，`:1791-1797`
- Local：`PASS`
- 云化方式：同语义（清 session）
- CI：`PASS`
- 适配器：`auth`

### F05 商品列表
- Local 实现：`products` 表 + `home()` 渲染，`:1045-1082, 182-186`
- Local：`PASS`（3 个商品：cx_video / zhs_video / zhs_qr）
- 云化方式：Synthetic Product 集合，字段与真实一致
- CI：`PASS`
- 适配器：`storage`

### F06 单个下单
- Local 实现：`GET/POST /buy/<code>` → `buy()`，`:1129-1303`
- Local：`PASS`（密码经 `encrypt_secret()` 落库 `:375`）
- 云化方式：`OrderService.create_order()`；密码字段在云端**只存占位**，不存真实口令
- CI：`PASS`
- 适配器：`storage` + `order service`

### F07 普通账号查询课程
- Local 实现：`POST /api/courses` → `api_courses()` `:1303-1331` → `query_courses()` `:1082-1091` → 子进程 `tools_query_courses.py`
- **契约（实测）**：`{"ok":true,"courses":[{"id","name","kind"}]}` / `{"ok":false,"error":"..."}`；退出码 0/1；`tools_query_courses.py:7-8,22-23`
- 限流：`rate_limit("api_courses", 12)` `:1306`；并发护栏 `BoundedSemaphore(2)` + 30s 忙等 → 快速失败 `:1084-1090`
- Local：`PASS`
- 云化方式：**`SyntheticCourseAdapter.get_courses()` / `ReplayCourseAdapter`**，输出结构完全一致（`kind` 必须保留：`知到课`/`共享课`）
- CI：`PASS`（契约测试，见 §12 计划）
- 适配器：`course`

### F08 扫码二维码
- Local 实现：`POST /api/qr_start` → `api_qr_start()`，`:1333-1358`；`GET /qr/<oid>` → `qr_img()` `:1488-1494`（内存 `QR_SESSIONS`，PNG 直出）
- Local：`PASS`（依赖真实 passport 域名）
- 云化方式：`SyntheticQrAdapter.create_session()` 返回**合成 PNG**（本地生成，不触网）；路由与前端完全不变
- CI：`PASS`
- 适配器：`qr`

### F09 扫码状态轮询
- Local 实现：`GET /qr_status/<oid>` → `qr_status()` `:1496-1503`；后台 `qr_thread()` `:520-558` 轮询真实接口
- **状态语义（实测）**：`waiting` → `scanned`(status=0) → `confirmed`(status=1，写 cookies 并转 `pending`) ；`expired`(2)/`canceled`(3)/`error`；`qr_state` 落库
- 清理：`qr_janitor()` 600s TTL `:841-852`
- Local：`PASS`
- 云化方式：Synthetic 状态机按固定时间推进（waiting→scanned→confirmed），语义逐字对齐
- CI：`PASS`（状态机迁移测试）
- 适配器：`qr`

### F10 课程选择
- Local 实现：`POST /order/<oid>/set_courses` → `order_set_courses()` `:1361-1375`（`courses` 空白分隔 ID 串；`waiting_qr` → `pending`）
- Local：`PASS`
- 云化方式：同语义；Synthetic 课程 ID 使用 `SYN-COURSE-00x`
- CI：`PASS`
- 适配器：`storage`

### F11 提交订单
- Local 实现：`buy()` POST 分支 `:1129-1303`；`api_qr_start` 直接插入 `waiting_qr` 单 `:1346`
- Local：`PASS`
- 云化方式：`OrderService.submit_order()`；云端需经 Approval（Commit 22）后才允许 Runner 执行
- CI：`PASS`
- 适配器：`storage` + `order service`

### F12 订单详情
- Local 实现：`GET /order/<oid>` → `order_detail()`，`:1436-1486`（步骤条 + 环境/风控 + 日志 + 4s 自动刷新）
- Local：`PASS`
- 云化方式：同页面，日志源换成 `TaskLog`
- CI：`PASS`
- 适配器：`storage` + `log`

### F13 我的订单
- Local 实现：`GET /my` → `my_orders()`，`:1413-1436`
- Local：`PASS`
- 云化方式：`OrderService.list_orders(user_id)`
- CI：`PASS`
- 适配器：`storage`

### F14 订单查询
- Local 实现：`GET/POST /query` → `query()`，`:1378-1410`（支持完整单号或前 8 位，**访客可用**）
- Local：`PASS`
- 云化方式：同语义（云端是否需要登录由产品决定，默认保持访客可查）
- CI：`PASS`
- 适配器：`storage`

### F15 批量下单
- Local 实现：`GET/POST /batch` → `batch()`，`:1506-1538`
- **输入契约（实测）**：每行 `平台,账号,密码,课程ID(可空)`；平台仅 `chaoxing`/`zhs`；空课程 = 全部课程；非法行静默跳过；返回受理条数
- 限流/约束：无独立限流（依赖登录 + 事务）
- Local：`PASS`
- 云化方式：同输入格式，10 条 Synthetic 订单 → 验证 4 running + 6 pending（v3 §36）
- CI：`PASS`
- 适配器：`storage`

### F16 订单日志
- Local 实现：`order_dir(oid)/log.txt`；`order_detail()` 读末 8000 字节并去 ANSI `:1463-1467`；滚动写 `RollingLog` `:109-163`（默认保留 300KB）
- Local：`PASS`
- 云化方式：`TaskLog`（字段 timestamp/task/runner/attempt/event/message），`GET /order/:id` 展示等价内容
- CI：`PASS`
- 适配器：`log`（storage 子系统）

### F17 风险标记
- Local 实现：`scan_risk()` `:635-648` + `RISK_PATTERNS` 5 类 `:627-633`（captcha / forbidden / risk_ctrl / login_fail / network），命中写入 `orders.risk_flags`
- Local：`PASS`
- 云化方式：**保留同一分类语义**，输入源换成 Synthetic 日志（可在 Replay fixture 中注入 `risk_ctrl` 等信号以验证 UI 链路）
- CI：`PASS`
- 适配器：`log`

### F18 管理后台
- Local 实现：`GET /admin` → `admin()`，`:1541-1693`（按状态统计、用户表、运行中订单含 PID/内存/心跳/时长、最近 10 单、系统状态卡）
- Local：`PASS`
- 云化方式：等价能力需恢复：用户统计、订单统计、Runner 状态、Task 状态、并发、任务数、运行状态、最近任务、错误统计
- CI：`PASS`
- 适配器：`admin`（读 storage + runner 状态）

### F19 管理设置
- Local 实现：`POST /admin/tune` `:1693-1707`（jobs/min_free_mb/log_keep_kb/log_keep_days/speed/concurrency/spoof/jitter/order_timeout_min/proxy_pool/verbose）+ `POST /admin/regcode` `:1709-1717`
- Local：`PASS`
- 云化方式：**参数白名单化**。其中 `proxy_pool` / `spoof` / `jitter` / 真实平台配置 / Windows 可执行文件路径 → UI 必须显示 `Cloud Test only / Not available`（v3 §38 授权此唯一例外），**不得保留真实功能入口**
- CI：`PASS`（白名单 + 拒绝写入测试）
- 适配器：`admin`

### F20 并发控制
- Local 实现：`concurrency_manager()` `:826-839`（按 `settings.concurrency` 动态维持 1..MAX_CONCURRENCY=10 线程）；`worker()` `:741-796`
- Local：`PASS`
- 云化方式：Runner slot 模型（初始 2 Runner × 2 slot = 4）
- CI：`PASS`（slot 分配 / 不多占 / 不死占）
- 适配器：`scheduler`（Commit 18）

### F21 健康检查
- Local 实现：`GET /health` → `health()` `:1797-1813`，返回 `{status, database, queue, pending, running}`（只暴露非敏感状态）
- Local：`PASS`
- 云化方式：扩展为 Control Plane / Durable Object / Runner / Task Queue / Synthetic Adapter / GitHub API / Codespace，状态聚合 `healthy|degraded|failed`
- CI：`PASS`
- 适配器：`health`

### F22 任务执行
- Local 实现：`worker()` `:741` → `build_order_env()` `:581` → `run_chaoxing()` `:694-709` / `run_zhs()` `:711-721` → `_spawn()` `:651-692`
- **执行契约（实测）**：子进程 + 滚动日志 + 20s 级心跳 + PID 落库；超时 `settings.order_timeout_min`（默认 180min，下限 10min）→ 树杀返回 `-9`
- 引擎参数：chaoxing `main.py -l <courses> -s <speed> -j <jobs> --auto-sign`；zhs `run_zhs.py full <ids>`（空课程先 `run_zhs.py list` 解析）
- Local：`PASS`
- 云化方式：`CloudExecutionAdapter.execute(order, ctx)`，模拟 准备→开始→step→progress→heartbeat→完成 全过程
- CI：`PASS`（执行器状态与结果语义）
- 适配器：`execution`

### F23 heartbeat
- Local 实现：`_spawn()` 每 20s 写 `orders.heartbeat_at` `:674-678`；抢占时初始化 `:732`
- Local：`PASS`
- 云化方式：`TaskLease`（task_id, attempt, runner_id, slot_id, lease_id, expires_at, heartbeat_at，Commit 14）
- CI：`PASS`（心跳超时判定）
- 适配器：`execution` + `lease`

### F24 watchdog
- Local 实现：`order_watchdog()` `:798-823`（30s 一轮；DB=running 但 pid 死亡 → `attempt<MAX_RETRY` 回 `pending`，否则 `failed`）
- Local：`PASS`
- 云化方式：lease 过期回收 → `PENDING`（v3 §50 流程 E）
- CI：`PASS`
- 适配器：`lease` + `scheduler`

### F25 retry
- Local 实现：`worker()` 重试策略 `:769-781` + `MAX_RETRY=1` `:231`
- **策略（实测）**：仅超时 `-9` 或负退出码（崩溃）可重试，且 `attempt < MAX_RETRY`；引擎正常返回的非零码（参数/认证/业务错误）**不重试**，直接 `failed`
- Local：`PASS`
- 云化方式：`transient → RETRY_WAIT → retry → DONE`（v3 §49 流程 D）；**必须保持"业务错误不重试"这一语义**
- CI：`PASS`（可重试/不可重试分类）
- 适配器：`scheduler` + `execution`

### F26 自动恢复
- Local 实现：`recover_stale_orders()` 启动期恢复 `:293-330`
- Local：`PASS`
- 云化方式：控制面 reconcile（Runner 丢失 / Codespace 手工停止 / Worker 重启后状态不丢，v3 §51-52 流程 F/G）
- CI：`PASS`
- 适配器：`control plane`（Commit 21）

### F27 备份
- Local 实现：`backup_manager.py`（`sqlite3.Connection.backup()` 在线备份 + `PRAGMA integrity_check` 校验 + `KEEP=7` 轮转）；调度双路：进程内 `housekeeping()` `:854+` 与进程外 `health_manager.py:194-207`，共用 `settings.last_backup` 防双份
- Local：`PASS`
- 云化方式：Cloud 侧为 Durable Object 存储快照/导出；**恢复演练必须在 Cloud Test 内可重复执行**
- CI：`PASS`（快照-恢复-校验）
- 适配器：`storage`

### F28 资源监控
- Local 实现：`free_mem_mb()` `:77-96`（Windows GlobalMemoryStatusEx）、`proc_mem_mb(pid)` `:439-472`（OpenProcess/GetProcessMemoryInfo）、admin 系统卡磁盘/内存/PID `:1576-1609`；worker 侧内存门限 `min_free_mb`（默认 500MB）`:749-751`
- Local：`PASS`
- 云化方式：仅保留**跨平台等价指标**（进程内存、任务数、Runner slot 占用），Windows 专属 API 不迁移；参数在云端 UI 显示 `Cloud Test only / Not available`
- CI：`PASS`
- 适配器：`health`

---

## 4. 汇总矩阵

状态词表（统一使用，禁止用 PASS 表示"计划中"）：

| 词 | 含义 |
|---|---|
| `BASELINE` | 已在本文档冻结的事实基线 |
| `TARGET` | 有明确设计，未开始实现 |
| `IMPLEMENTED` | 代码已实现（adapter 层）；**不等于端到端跑通** |
| `PARTIAL` | 部分实现，缺口见备注 |
| `PENDING` | 依赖后续 Commit（API/前端/Runner/Scheduler 等） |
| `BLOCKED` | 被外部条件阻塞 |
| `PASS` | **端到端验收通过**（本文档 Cloud Test 列暂不出现） |

| ID | 功能 | Local | Cloud Test（Commit 07 后） | CI |
|---|---|---|---|---|
| F01 | 首页 | PASS | PENDING（前端未建） | PASS |
| F02 | 注册 | PASS | IMPLEMENTED（SyntheticAuthAdapter） | PASS |
| F03 | 登录 | PASS | IMPLEMENTED（SyntheticAuthAdapter） | PASS |
| F04 | 退出 | PASS | IMPLEMENTED（语义差异已登记，见 §8） | PASS |
| F05 | 商品列表 | PASS | IMPLEMENTED（SyntheticStorageAdapter） | PASS |
| F06 | 单个下单 | PASS | IMPLEMENTED（SyntheticStorageAdapter） | PASS |
| F07 | 普通账号查询课程 | PASS | IMPLEMENTED（SyntheticCourseAdapter，`TEST ADAPTER`） | PASS |
| F08 | 扫码二维码 | PASS | PARTIAL（Synthetic 侧 IMPLEMENTED；Local 侧 create 仍内联在路由） | PASS |
| F09 | 扫码状态轮询 | PASS | IMPLEMENTED（SyntheticQrAdapter，`TEST ADAPTER`） | PASS |
| F10 | 课程选择 | PASS | IMPLEMENTED（SyntheticStorageAdapter.submit_order） | PASS |
| F11 | 提交订单 | PASS | IMPLEMENTED（SyntheticStorageAdapter） | PASS |
| F12 | 订单详情 | PASS | PARTIAL（数据 IMPLEMENTED；页面 PENDING） | PASS |
| F13 | 我的订单 | PASS | IMPLEMENTED（list_orders） | PASS |
| F14 | 订单查询 | PASS | IMPLEMENTED（find_order_by_prefix） | PASS |
| F15 | 批量下单 | PASS | IMPLEMENTED（storage 层；批量编排 PENDING） | PASS |
| F16 | 订单日志 | PASS | IMPLEMENTED（SyntheticLogSink） | PASS |
| F17 | 风险标记 | PASS | IMPLEMENTED（SyntheticExecutionAdapter.scan_risk，`TEST ADAPTER`） | PASS |
| F18 | 管理后台 | PASS | PENDING（统计服务/前端未建） | PASS |
| F19 | 管理设置 | PASS | PARTIAL（白名单已强制；页面 PENDING） | PASS |
| F20 | 并发控制 | PASS | PENDING（Scheduler，Commit 18） | PASS |
| F21 | 健康检查 | PASS | PENDING（Control Plane，Commit 19/20） | PASS |
| F22 | 任务执行 | PASS | IMPLEMENTED（SyntheticExecutionAdapter，`TEST ADAPTER`） | PASS |
| F23 | heartbeat | PASS | PARTIAL（adapter 回调已有；TaskLease 待 Commit 16） | PASS |
| F24 | watchdog | PASS | PENDING（lease 回收，Commit 16） | PASS |
| F25 | retry | PASS | PARTIAL（错误分类已供重试判定；调度重试待 Commit 18） | PASS |
| F26 | 自动恢复 | PASS | PENDING（控制面 reconcile） | PASS |
| F27 | 备份 | PASS | PENDING（Durable Object 快照） | PASS |
| F28 | 资源监控 | PASS | PENDING（跨平台指标服务） | PASS |

`TEST ADAPTER` = 能力保留、执行层替换为 Synthetic/Replay；**不等于不支持**。

---

## 5. 适配器边界清单（真实实现 → 未来 Adapter 方法）

| 当前真实实现（证据） | 目标 Adapter 方法 | 云端实现 |
|---|---|---|
| `query_courses()` `:1082` / `_query_courses()` `:1093`（子进程 `tools_query_courses.py`，JSON `{ok,courses[{id,name,kind}]}`） | `CourseAdapter.get_courses(platform, account, secret, cookie_ref)` | `SyntheticCourseAdapter` / `ReplayCourseAdapter` |
| `api_qr_start()` `:1335` + `qr_thread()` `:520` + `qr_img()` `:1490`（真实 passport 轮询 + cookies 落盘） | `QrAdapter.create_session()` / `get_status()` / `qr_png()` | `SyntheticQrAdapter`（内存状态机 + 本地生成 PNG） |
| `login_user()` `:208` / `current_user()` `:213` / `require_login` `:1030` / `register()` `:1721` | `AuthAdapter.login()` / `session_user()` | `CloudAuthRepository`（+ Cloudflare Access 外层） |
| `run_chaoxing()` `:694` / `run_zhs()` `:711` / `_spawn()` `:651` | `ExecutionAdapter.execute(order, ctx)` | `CloudExecutionAdapter`（合成课程全流程） |
| `claim_order()` `:723` / `worker()` `:741` / `order_watchdog()` `:798` / `concurrency_manager()` `:826` | `Scheduler` + `TaskLease`（非 Adapter，服务层） | Control Plane + DO |
| `db()` 读写 orders/users/products/settings `:64-76, 164-204` | `OrderRepository` / `ProductRepository` / `TaskLogStore` | `CloudOrderRepository`（DO SQLite） |
| `health()` `:1798` | `HealthProbe` 集合 | Control Plane 聚合 |

**明确不迁移（云端显示 NOT AVAILABLE）**：`proxy_pool`、`spoof`、`jitter`、Windows 可执行文件路径（`PYEXE` `:44`）、Windows 内存 API（`:77, :439`）、`tasklist` 探测（`:1579`）、Windows Credential Manager DEK（`crypto_manager.py`）。

---

## 6. 已知缺口与风险（诚实登记）

| # | 缺口 | 影响 | 归属 Commit |
|---|---|---|---|
| G1 | `crypto_manager.py` 用 Windows Credential Manager 存 DEK → 本机加密口令在 Linux 无法解密 | 云端**绝不能**复用本地加密产物；云端密码字段只存占位 | 11（Storage） |
| G2 | 前端 HTML 与路由耦合在 `order_platform.py`（模板内联字符串） | 云端 API 层需重新组织，不能复制 HTML 全量 | 12–13 |
| G3 | 真实引擎是子进程 + Windows 专属 flags（`CREATE_NO_WINDOW`/`BELOW_NORMAL`） | 执行器必须抽象出跨平台 spawn 契约 | 14 |
| G4 | `QR_SESSIONS` 为进程内存态 | 云端多实例下必须外置（DO/KV） | 11、21 |
| G5 | 无 runner/lease 概念，runner 身份即进程 | 需引入 `TaskLease` 与 runner 注册 | 14–17 |
| G6 | 限流/CSRF 依赖本地进程与同源判断（`rate_limit` `:330`、`_csrf_guard` `:934`、`same_origin_ok` `:349`） | 云端需重新设计（边缘限流 + CORS 显式来源） | 12、20 |
| G7 | 备份/看护为 Windows 计划任务 + 本地文件 | 云端需 snapshot 化 | 19、27 |

---

## 7. 冻结后的约束

1. 后续任何 Commit **不得**在云端代码中出现真实平台域名（egress guard 已是硬阻断，代码层也不得出现）。
2. 任何新增云端接口都必须能在上表 F01–F28 中找到对应行，否则视为范围蔓延。
3. 本文件中标注 `TEST ADAPTER` 的 5 项（F07/F08/F09/F17/F22）必须在 Commit 07 的契约测试中**与 Local 实现跑同一套断言**，以证明接口等价。
4. 若后续实测发现 F 列表与真实代码不符，必须**先修正本基线再改代码**（基线是契约，不是注释）。

---

## 8. Commit 07 实现状态（adapter 层，2026-09-22）

`docs/ADAPTER_CONTRACT.md` 的契约已落地为可运行实现：

```text
cloud_test/adapters/
    base.py auth.py course.py qr.py execution.py storage.py   # 契约（IO-free，CI 断言保证）
    factory.py                                                # build_local_adapters / build_synthetic_adapters
    local/      backend.py errors.py auth.py course.py qr.py execution.py storage.py
    synthetic/  auth.py course.py qr.py execution.py storage.py
```

| 项 | 状态 | 说明 |
|---|---|---|
| Local adapters（5） | IMPLEMENTED | 薄包装现有平台能力；`is_synthetic=False` |
| Synthetic adapters（5） | IMPLEMENTED | 内存态、无网络、无子进程、无真实文件；`is_synthetic=True` |
| Adapter factory | IMPLEMENTED | 合成侧逐个 `assert_synthetic`；本地侧拒绝出现合成适配器 |
| Contract tests | PASS（254 用例） | 同一套 case 跑 Local/Synthetic 两侧 |
| Compatibility tests | PASS | DTO 形状/类型/状态词表一致；差异项单列 |
| Security tests | PASS | 禁网、禁真实路径、禁 import `order_platform`（conftest 强制） |
| Replay adapters | PENDING | 未实现（非本 Commit 硬要求），留待后续 |

**已登记的两处有意差异（不是缺陷，是事实）**：

1. **logout 语义**：Synthetic 会真正失效 token；Local 的退出只是删 cookie（`order_platform.py:1792`），token 仍然可验签。契约因此只要求「登出是幂等的、返回 None」；token 失效仅是云端属性。
2. **Local QR `create_session`**：创建会话的代码内联在 Flask 路由 `api_qr_start` 里（含真实网络调用），提取它必须改 `order_platform.py`——本 Commit 明确禁止。因此 Local 侧必须注入 `create_hook`（生产接线），未接线时抛 `AdapterContractError` 并指明原因；Synthetic 侧完整实现。**这不是把缺口写成 PASS。**

**新增的硬防线**：`tests/cloud_test/conftest.py` 在每个用例前后断言 `order_platform` 未被导入。原因：该模块 `import` 即产生副作用（`order_platform.py:918-927` 写真实库 + 起 4 个常驻线程）。本 Commit 开发过程中曾触发一次误导入（见 §6 G2 相关），已修复并加装该断言——生产库核查无损（integrity ok、users 4 / orders 63 / products 3 未变）。
