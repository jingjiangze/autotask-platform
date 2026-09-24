# LOCAL_FUNCTIONAL_MAP.md — 现有真实平台功能审计（stage-cloud-01）

> **审计性质**：只读审计，零代码变更。
> **审计日期**：2026-09-22
> **审计对象 HEAD**：工作区检出 `local-data-snapshot-20260922`（含未提交在途改动，未纳入本审计结论）；本分支基于 `feature/cloud-test-orchestration-baseline` @ `097cd9f`
> **主线基线**：`main` @ `bd4df06`（收敛仓库范围并脱敏历史文档手机号）
> **审计方法**：全量通读 `order_platform.py`（1821 行）、`fuckCourse/`（34 个 .py）、`fuckZHS_orig/`、`crypto_manager.py`、`health_manager.py`、`path_manager.py`、`backup_manager.py`、既有 cloud_test 编排层与 docs/ 四文档；SQL/凭据只审代码与 git 追踪清单，不打开数据库二进制。

---

## 1. 系统总览

| 项 | 事实 | 证据 |
|---|---|---|
| Web 框架 | Flask + Waitress（16 线程），监听 `127.0.0.1:8766`，经 Cloudflare Tunnel 对外 | `order_platform.py:29-30, 1816-1822`；`health_manager.py:233-243` |
| 启动序列 | `init_db → _ensure_settings → ensure_admin_password → migrate_encrypt_passwords → migrate_timestamp_years → recover_stale_orders`，随后拉起 4 个守护线程：`order_watchdog / concurrency_manager / qr_janitor / housekeeping` | `order_platform.py:918-927` |
| 进程外看护 | `health_manager.py`（计划任务 `WK_AutoTaskPlatform` 拉起，msvcrt OS 锁单实例）：每 30s 探活 `/health`，失败杀端口进程并以 pythonw 重启（10 分钟内最多 3 次，超限熔断）；同时三层判定 cloudflared 隧道健康 | `health_manager.py:125-166, 347-484` |
| 执行模型 | worker 线程池（1–10，`concurrency_manager` 每 5s 对齐）→ `claim_order()` 原子抢占 → `build_order_env()` 按单隔离环境 → subprocess 启动 fuckCourse 引擎 → `RollingLog` 落日志 → `scan_risk` 风控标记 | `order_platform.py:723-839` |
| 全站 POST 守卫 | `@app.before_request _csrf_guard()`：Origin/Referer 同源校验（无 token CSRF） | `order_platform.py:933-937, 349-361` |

## 2. 核心函数地图

| 符号 | 位置 | 职责 |
|---|---|---|
| `current_user()` | `order_platform.py:213-221` | 从 cookie 取 `wk_token`（`uid:HMAC`），复验签名后查 users 表 |
| `login_user(name, pw)` | `order_platform.py:208-211` | 用户名+`hash_pw` 比对 |
| `hash_pw(pw, salt="wk")` | `order_platform.py:205-206` | `HMAC-SHA256(SECRET, salt+pw)`，全局固定 salt，无逐用户盐 |
| `wk_token` 签发/验证 | `order_platform.py:1777 / 213-221` | 会话 cookie，httponly、**无过期、无服务端吊销能力** |
| `require_login` | `order_platform.py:1030-1038` | 登录装饰器，未登录 302 `/login` |
| `encrypt_secret / decrypt_secret` | `order_platform.py:375-393` | `enc:v1:` 自制 SHA-256 keystream 流密码（16B nonce，无认证标签）；兼容历史明文 |
| `build_order_env(oid, o)` | `order_platform.py:581-625` | 每单隔离 work/tmp/home/logs + `FUCKCOURSE_*` 环境变量 + UA 池随机指纹（561-570）+ 代理池轮询 |
| `run_chaoxing(oid, o, env, work)` | `order_platform.py:694-709` | 组装 `[PYEXE, fuckCourse/chaoxing/main.py, -l, -s, -j, --auto-sign]`，凭据走 `WK_ACCOUNT/WK_PASSWORD` |
| `run_zhs(oid, o, env, work)` | `order_platform.py:711-721` | `run_zhs.py full <ids>`；无课程先 `run_zhs.py list` 枚举 |
| `qr_thread(oid)` | `order_platform.py:520-558` | 每秒轮询 `passport.zhihuishu.com/qrCodeLogin/getLoginQrInfo`；status=1 时用 oncePassword 登录并把 cookies 写 `orders/<oid>/cookies.json` |
| `claim_order()` | `order_platform.py:723-739` | 原子抢占：`UPDATE ... WHERE id=? AND worker_running=0`，rowcount=0 视为抢不到 |
| `worker(wid)` | `order_platform.py:741-796` | 主循环：paused/内存护栏 → claim → build_env → 分发执行 → scan_risk → rc 判定（`MAX_RETRY=1`，:231） |
| `order_watchdog()` | `order_platform.py:798-823` | 每 30s 扫 running 订单，PID 已死按 attempt 收敛到 pending/failed |
| `concurrency_manager()` | `order_platform.py:826-839` | 每 5s 对齐 worker 线程数（上限 `MAX_CONCURRENCY=10`） |
| `scan_risk(oid)` | `order_platform.py:635-648` | 读日志尾部 400KB，按 `RISK_PATTERNS`（:627-633，5 类）匹配落 `risk_flags` |
| `RollingLog`（含 `_scrub`） | `order_platform.py:109-158` | 子进程 stdout 逐行脱敏写盘（password/token/api-key 等正则替换 `****`），超限裁剪 |
| `_spawn(...)` | `order_platform.py:651-692` | `BELOW_NORMAL|CREATE_NO_WINDOW` 启动、PID 落库、心跳写 `heartbeat_at`、超时 `_kill_tree` 返回 -9 |
| `recover_stale_orders()` | `order_platform.py:293-326` | 启动恢复：waiting_qr→canceled；running 按进程存活/attempt 收敛 |

**auto-hand 审计结论**：仓库中**不存在**名为 `auto-hand` 的代码实体（4 轮全库 grep/glob 零命中）。计划书中 "auto-hand" 的真实对应物是 `claim_order()`（原子接单）+ `worker()`（轮询执行）+ `order_watchdog()`（看护恢复）这一组本地编排逻辑。后续 stage-cloud 文档与 Runner 封装应以这组函数为基准，不再引用不存在的 auto-hand。

## 3. SQLite 数据模型（orders/platform.db）

连接工厂 `db()`（:64-74）启用 WAL + `synchronous=NORMAL` + `busy_timeout=15000`。

| 表 | 位置 | 字段要点 |
|---|---|---|
| `users` | :167-172 | `id INTEGER PK, username UNIQUE, pw_hash, is_admin, created_at` |
| `orders` | :173-181 + ALTER :187-194 | `id TEXT PK(uuid4.hex), user_id, product, platform, account, password(密文), courses, status, note, qr_state, created_at, started_at, finished_at, exit_code, worker_running, env_profile, risk_flags, speed, pid, attempt, heartbeat_at` |
| `products` | :182-185 | `id, code UNIQUE, name, desc, price, platform, enabled, sort`；预置 cx_video / zhs_video / zhs_qr（:197-203） |
| `settings` | :496-500 | KV 设置：reg_code / jobs / min_free_mb / log_keep_kb / log_keep_days / speed / concurrency / proxy_pool / spoof / jitter / order_timeout_min / verbose |

## 4. 订单状态机（现状 Ground Truth）

状态全集（BADGE 字典 :939-942）：**pending / running / done / failed / waiting_qr / canceled**；辅列 `qr_state`：`waiting → scanned → confirmed | expired | canceled | error`。

```text
（创建）→ pending            普通下单 :1150-1152 / 批量 :1523-1525
（创建）→ waiting_qr         /api/qr_start :1347-1349
waiting_qr → pending         扫码确认 :543 / 手动选课 :1373-1375
waiting_qr → canceled        QR 过期/取消/异常/重启恢复 :547,552,557,305-306
pending → running            claim_order 原子抢占，attempt+1 :731-734
running → pending            超时/崩溃重试（attempt<MAX_RETRY=1）:776-777, 817-818, 319-320
running → done               rc==0 :766-768
running → failed             非零 rc 超上限 / watchdog / 重启恢复 :779-787, 820-823, 314-323
```

终态：done / failed / canceled；可恢复态：pending / running / waiting_qr。重试语义 `MAX_RETRY=1`（首次 + 最多一次重试）——与 stage-cloud 计划 `max_attempts=2` 一致。

## 5. 认证与加密体系（两套独立机制）

**平台业务（order_platform.py）**：
- `SECRET`：外置 `secrets_store/secret_key.txt`（:41-60），读取失败兜底硬编码 `"wk-platform-local-secret-2026"`（风险，见 §9）。
- 口令哈希：`HMAC-SHA256(SECRET, "wk"+pw)`，固定 salt。
- 会话：`wk_token = uid:HMAC(SECRET,"u"+uid)`，无过期/无吊销/无服务端 session 表。
- 订单凭据：`enc:v1:` 自制流密码（无认证标签）；存量明文由 `migrate_encrypt_passwords`（:395-411）幂等加密。

**独立加密系统（crypto_manager.py，尚未接入业务）**：AES-256-GCM + scrypt(n=2^15) KDF；DEK 存 Windows Credential Manager（Target `WorkBuddy::DEK`）；recovery bundle 跨机恢复。这是未来 `enc:v2:` 的现成实现基础。

## 6. 路由清单（18 条）

| 路由 | 方法 | 鉴权 | 位置 |
|---|---|---|---|
| `/` | GET | 公开 | :1045 |
| `/buy/<code>` | GET/POST | 登录 | :1130 |
| `/api/courses` | POST | 登录+限流 12/min | :1304 |
| `/api/qr_start` | POST | 登录+限流 6/min | :1334 |
| `/order/<oid>/set_courses` | POST | 登录+归属校验 | :1362 |
| **`/query`（访客查单）** | GET/POST | **公开，凭单号前缀（≥6 位）LIKE 查询，无限流** | :1379-1411 |
| `/my` | GET | 登录 | :1414 |
| `/order/<oid>` | GET | 登录+归属校验 | :1437 |
| `/qr/<oid>` | GET | 登录（内存 QR_SESSIONS） | :1489 |
| `/qr_status/<oid>` | GET | 登录（**未校验订单归属**） | :1497 |
| `/batch` | GET/POST | 登录 | :1507 |
| `/admin` | GET | is_admin | :1542 |
| `/admin/tune`、`/admin/regcode` | POST | is_admin | :1694, :1710 |
| `/register` | GET/POST | 公开+限流 5/min+注册口令（默认 `JJZ-2026`） | :1721, :492 |
| `/login` | GET/POST | 公开+限流 10/min | :1760 |
| `/logout` | GET | — | :1792 |
| `/health` | GET | 公开（看护探活） | :1798-1814 |

访客查单无 `guest/order_code/proof/public token` 机制，鉴权仅靠 uuid4.hex 前 8 位前缀匹配；只回显 id 前 8 位/商品/平台/状态/note/时间，不含账密。stage-cloud-17 迁移时应以此真实逻辑为准。

## 7. 执行器体系（fuckCourse）

| 项 | 事实 |
|---|---|
| 引擎入口 | 平台侧 subprocess 直接驱动 `fuckCourse/chaoxing/main.py` 与 `fuckCourse/run_zhs.py`（不是 import `fuckCourse/main.py`——那是交互式菜单启动器，`fuckCourse/main.py:100-156`） |
| 环境变量契约 | 写入方 `order_platform.py:598-612, 705-706`；读取方 `chaoxing/main.py:241-243`、`zhs/fucker.py:112-117`、`chaoxing/api/config.py:6-12`、`zhs/main.py:57,177` 等。变量：`WK_ACCOUNT / WK_PASSWORD / FUCKCOURSE_CONFIG / FUCKCOURSE_COOKIES / FUCKCOURSE_LOG_DIR / WK_UA / WK_PLATFORM / WK_CHUA / WK_LANG` |
| RollingLog / scan_risk | 平台侧组件（:109 / :635），引擎侧无对应物 |
| ZHS QR 流程 | `zhs/fucker.py:220-266`：getLoginQrImg 取码落盘 → 每 0.5s 轮询 getLoginQrInfo → status=1 用 oncePassword 换会话 → cookies 写 `FUCKCOURSE_COOKIES` 的 `root["zhs"]` 键（`zhs/main.py:192-238`）；二维码展示为终端渲染（`zhs/utils.py:13-60`） |
| 依赖 | requests + openai + tiktoken + Pillow + pycryptodome；**无 playwright/selenium**（requirements.txt） |
| fuckZHS_orig | 上游开源 fuckZHS 原始快照（只读存档）；`fuckCourse/zhs` 为演化生产版（指纹环境变量化 + 统一配置/cookies 契约 + 交互选课） |
| 题库 | chaoxing 走 TikuGo provider；zhs 走 OpenAI 兼容接口 |

## 8. 与既有 cloud-test 编排工作的关系（避免重复建设）

上一轮工作在 `feature/cloud-test-orchestration-baseline`（10 提交，自有编号 Commit 01–07 + L0），交付：

1. `docs/LOCAL_FUNCTIONAL_PARITY.md`：**F01–F28 功能基线**，每项带 文件:行号 证据 + 云化方式 + 适配器归属 + 三环境状态矩阵 + G1–G7 缺口登记。**与本审计重合约 80%，引用不重写。**
2. `docs/ADAPTER_CONTRACT.md`：统一契约（同步、DTO frozen dataclass、8 类错误模型、任务 7 态 + QR 6 态状态机、password 永不入 DTO）。
3. `cloud_test/` 适配器层：Local / Synthetic 双侧实现 + 254 个契约用例（conftest 硬护栏禁 import order_platform）；Replay PENDING。
4. `cloud_test_guard.py` egress 双层 guard（内核 nftables + 进程 monkey-patch）+ 真机 CI 验收（`.github/workflows/cloud-test-guard.yml`）。
5. UTF-8 编码层（`cloud_test/encoding.py` 等，Commit L0）。

**stage-cloud-01 增量（本文档补充部分）**：cf/ 部署工具、服务化层（`service_manager.py`）、`crypto_manager.py` 全量 API、fuckCourse 引擎 CLI 接口面、完整路由/状态机/加密审计、auto-hand 结论。

**对 stage-cloud-02（tests/baseline）的输入**：可直接纳入——`tests/cloud_test/` 全套（CI 化无副作用）、`test_access_admin.py`、`test_crypto_bundle.py`、`test_cloud_test_guard.py`；需改造——`test_crypto.py`（直连真实库）；仅人工验收清单——`test_health.py`（依赖 8766 在线）、`test_restore_drill.py`（停平台）、`test_spawn_timeout.py`、`test_kill_tree.py`（真实进程副作用，`run_cloud_test.ps1:94-106` 已明确禁入 CI）。

**文档双份漂移**：根目录 `CLOUD_ORCHESTRATION_BASELINE.md`（中文，Stage-0 只读基线，2026-09-22）与 `docs/CLOUD_ORCHESTRATION_BASELINE.md`（英文，上轮 Commit 02 产出）内容同源但表述不同。**声明：后续 stage-cloud 以 docs/ 版为权威仓库基线，根目录版视为中文参考。**

## 9. 审计发现（仅记录，未改动）

| # | 级别 | 发现 | 位置 |
|---|---|---|---|
| 1 | 高 | `fuckCourse/config.json` 与 `debug_zhs_login.py` 存在明文账号密码 | `fuckCourse/config.json:4-5`、`fuckCourse/debug_zhs_login.py:29` |
| 2 | 高 | 快照分支 `local-data-snapshot-20260922` 含明文秘密（secrets_store/、orders/platform.db、cookies、cf/browser_profile），commit message 注明为仓库所有者明确指示、私库接受；stage-cloud 系列工作与后续任何开源/公开动作必须避开该分支 | `b17f414` |
| 3 | 中 | `/query` 访客查单无限流（全站唯一无 rate_limit 的敏感接口） | `order_platform.py:1379-1411` |
| 4 | 中 | `enc:v1:` 为自制流密码，无完整性校验（AES-GCM 实现已存在但未接入业务） | :375-393 vs `crypto_manager.py:238-263` |
| 5 | 中 | `wk_token` 无过期、无吊销；`hash_pw` 全局固定 salt | :1777, :205-206 |
| 6 | 低 | `/qr_status/<oid>` 不校验订单归属 | :1497 |
| 7 | 低 | `/admin` 明文展示注册口令 | :1613 |
| 8 | 低 | `SECRET` 兜底硬编码 `"wk-platform-local-secret-2026"` | :58 |
| 9 | 信息 | Python 解释器路径硬编码 `C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\python.exe`（Windows 绑定，云化时归入 runtime adapter） | :38、`health_manager.py:27` |
| 10 | 信息 | CI 触发分支仍为旧分支名 `feature/cloud-test-orchestration-baseline`；stage-cloud 分支如需 CI 需后续更新 workflow（属代码变更，不在本 Commit） | `.github/workflows/cloud-test-guard.yml:12-27` |

## 10. 阶段判定（对照 stage-cloud-01..25）

| 判定 | 结论 |
|---|---|
| stage-cloud-01（本审计） | **本次交付**。此前无任何 stage-cloud 提交 |
| stage-cloud-02（tests/baseline） | 未开始。素材已具备（见 §8 输入清单），真实平台行为冻结是最大缺口 |
| stage-cloud-03+ | 未开始。main 仅 2 个 init 提交；`cloudflare/` 目录不存在 |
| 免费复用资产 | egress guard、adapter 契约层、F01–F28 基线、UTF-8 编码层、`crypto_manager` AES-GCM（enc:v2 基础） |

## 11. Live / Code Ready 声明

本 Commit 为纯文档审计，无运行时行为：**AUDIT PASS（静态）**。未运行测试套件（审计轮不改代码不跑破坏性脚本）；tests 现状登记见 §8。真实第三方（学习通/知到）登录与执行能力在本轮未触碰，登记为 **NOT AVAILABLE（本轮未验证）**，留待 stage-cloud-02 基线冻结时按 PASS/FAIL/SKIP/LIVE 逐项登记。
