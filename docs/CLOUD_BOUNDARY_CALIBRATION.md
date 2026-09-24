# CLOUD_BOUNDARY_CALIBRATION.md — 基于真实代码的 Cloudflare 边界校准（stage-cloud-01b）

> **目的**：在编写任何 Cloudflare Worker / Executor 代码之前，用仓库真实代码逐项校准
> "中央平台 vs Executor" 边界，修正计划书中与代码事实不符的假设。
> **依据**：`order_platform.py`（stage-cloud 分支已提交版，743452a）、`fuckCourse/` 全量、
> `crypto_manager.py`、`tests/baseline/`（stage-cloud-02）实测行为。
> **原则**：每条判定都有 文件:行号 或可复现测试作为证据；与计划书冲突处显式标注"修正"。

---

## 1. 会话与认证边界

| # | 真实代码事实 | 证据 | 边界判定 |
|---|---|---|---|
| 1.1 | `wk_token = uid:HMAC(SECRET,"u"+uid)`，httponly，**无过期、无服务端状态、登出仅清 cookie** | `order_platform.py` login 路由（:1759 起）、`current_user`；baseline `test_register_login_logout_wk_token` PASS | Cloud 改用 opaque session + D1（计划 §15 成立）。Local 兼容层必须继续接受 wk_token——它是 Local Ground Truth，不为 Cloud 改动 |
| 1.2 | 登录失败返回 **200 + 错误页**（非 302/401） | baseline 实测：`用户名或密码错误` 页 | 中央平台错误契约（统一 JSON error）与本地 HTML 行为不同属预期；迁移时按平台 API 设计，不照搬 HTML 状态码 |
| 1.3 | **legacy 口令哈希 = HMAC-SHA256(SECRET, "wk"+pw)，SECRET 是本地文件** `secrets_store/secret_key.txt` | `hash_pw`（:205 区域）、`_load_secret`（:43-60） | **修正计划 §17**：legacy-hmac-v1 的验证依赖本地 SECRET，Cloudflare Worker **无法**实现 legacy 验证。legacy 用户导入必须在**本地迁移工具**内完成验证并重哈希为云端 scheme（或云端只存重哈希结果）。`password_scheme` 字段设计保留 |
| 1.4 | `enc:v1:` 密钥 = `sha256(SECRET+"wk-enc")`，同样本地派生 | `encrypt_secret/decrypt_secret`（:375-393） | **修正计划 §21/102**：enc:v1 解密只能发生在本地迁移工具（`tools/migrate_local_to_cloud.py` 在本机运行时解密 v1 → 重加密 v2）。云端 D1/R2 只存在 enc:v2（AES-256-GCM，`crypto_manager.py` 已有现成实现可改造） |
| 1.5 | 管理员引导：检测默认 admin123 → 生成随机口令写 `secrets_store/admin_password.txt` | `ensure_admin_password`（:275-291）；baseline `test_admin_bootstrap_password_and_tune` PASS（默认口令已轮换、新口令可登录） | Cloud admin 引导另行设计（bootstrap token / 一次性口令），不复用本地文件机制 |
| 1.6 | rate_limit 为**进程内存态**；仅回环来源信任 `CF-Connecting-IP` | `rate_limit`（:330-350）；baseline 实测注册 5 次/分钟触发「操作过于频繁」 | Cloud 用 DO rate-limiter（计划一致）。**安全警示**：回环可伪造 CF 头——云端 Worker 必须只信 Cloudflare 真实注入的 header，不接受客户端伪造路径 |

## 2. 任务与执行边界

| # | 真实代码事实 | 证据 | 边界判定 |
|---|---|---|---|
| 2.1 | `claim_order`：`UPDATE ... WHERE id=? AND worker_running=0` + `attempt=COALESCE+1`，rowcount=0 视为抢不到 | `claim_order`（:723-739 区域） | 原子 claim 语义与计划 Lease 一致；D1+DO 原子 claim 按此语义实现，attempt_no 对应 |
| 2.2 | **auto-hand 不存在**；"自动接单"= claim_order + worker + order_watchdog | stage-cloud-01 审计（4 轮全库搜索零命中） | **修正计划书**：Runner 封装基准改为这三个函数；文档不再引用 auto-hand |
| 2.3 | 重试：`MAX_RETRY=1`；`rc==-9 或 rc<0` 才可重试，正 rc（参数/认证/业务错误）直接 failed | worker（:789-800 区域）；baseline `test_worker_claim_execute_classify` PASS（rc 正值 → attempt 停在 1） | 与计划 §51/52 一致；Error Code 映射时保持"正退出码不重试"语义 |
| 2.4 | `paused` / `min_free_mb` / `concurrency`（settings 表）控制 worker | worker（:746）；baseline 实测 paused=1 时 worker 空转 | `paused` → Cloud 侧 executor drain 开关（DO）；`min_free_mb` 内存护栏是本地资源语义，不迁移；`concurrency` → executor capacity |
| 2.5 | `build_order_env`：每单隔离 work/tmp/home/logs + UA 池随机指纹 + 代理池轮询 | `build_order_env`（:581-625 区域） | 全部属 **Executor EnvProvider** 边界；Cloud 不感知 UA/代理细节 |
| 2.6 | PYEXE 硬编码本机 venv 路径 | `order_platform.py:38` | Executor 本地配置项（EXECUTOR_PYTHON），不入协议 |
| 2.7 | 模块级 import 副作用：init_db/迁移/recover_stale_orders + 4 个守护线程 | 文件尾部初始化块；baseline conftest 因此采用"隔离 APP_DIR + 子进程"方案 | Executor Agent 保留"启动即自恢复"语义（对应计划 §115 orphan 清理），但业务状态来自 Cloud，本地仅临时运行时 |

## 3. 数据与隐私边界

| # | 真实代码事实 | 证据 | 边界判定 |
|---|---|---|---|
| 3.1 | 订单**账号明文入库**，密码 enc:v1 密文入库 | baseline `test_buy_creates_order_with_encrypted_password` PASS（密文断言） | 迁移时 account 明文迁 D1（低敏），password 走 v1→v2 重加密；订单列表/详情永不回显密码（baseline 已断言详情页无明文/密文） |
| 3.2 | 访客查单 = 单号前 8 位能力凭据，回显前 8 位/商品/平台/状态/note/时间，**无账号无密码**；另有「复制完整单号」按钮 | `/query`（:1402-1436）；baseline `test_guest_query_limited_fields` PASS | 中央查单 API（计划 §17/91）按此语义迁移；**补强**：现状 `/query` 无限流，中央版必须加 rate limit |
| 3.3 | 订单越权：详情页 200+「订单不存在」；set_courses 返回 ok=False | baseline `test_ownership_denied_for_other_user` PASS | 中央平台统一为 404/JSON error（错误契约），语义等价即可 |
| 3.4 | RollingLog._scrub 覆盖 password/pwd/passwd/token/api_key/secret/authorization 形态 | `RollingLog`（:109-158 区域） | SecretScrubber 的起点正则清单；云化后 Executor 端上传前先 scrub，Worker 端不再二次脱敏 |
| 3.5 | scan_risk 5 类风控关键词，结果落 `risk_flags` | `scan_risk`（:635-648 区域）、RISK_PATTERNS（:627-633） | 风控判定保留在 Executor，结果作为 result payload 上报 |
| 3.6 | settings 表混合"业务配置"（reg_code）与"本地运维"（log_keep/proxy_pool/spoof/jitter） | `_ensure_settings`（:494-501） | 拆分：reg_code/商品/限流→D1；proxy_pool/spoof/jitter/log_keep→Executor 本地 config；paused→DO |

## 4. QR 边界

| # | 真实代码事实 | 边界判定 |
|---|---|---|
| 4.1 | 平台侧 `qr_thread`：进程内存 `QR_SESSIONS` + 每秒轮询 passport.zhihuishu.com，status=1 用 oncePassword 换 cookies 写 `orders/<oid>/cookies.json`（:520-558 区域） | 云化后 QR task 归 **Executor**（计划 §65 成立）：Executor 生成/展示/确认 QR，中央只存 qr_state；Worker 不与第三方保持长连接 |
| 4.2 | 引擎侧另有同步 QR 流程（`zhs/fucker.py:220-266`），cookies 写 `FUCKCOURSE_COOKIES` 的 `root["zhs"]` 键 | 两套 QR 共存是现状事实；封装 ZhsQrRunner 时以引擎流程为执行体、平台流程为状态参考，不合并重写 |
| 4.3 | 重启后 waiting_qr 一律 canceled + qr_state=expired | baseline `test_restart_recovery_paths` PASS——中央平台须保留同语义：Executor 掉线后 QR 会话视为失效 |

## 5. 对计划书的修正汇总

| 计划书假设 | 真实代码事实 | 修正动作 |
|---|---|---|
| auto-hand 是现有真实执行器之一 | 不存在；对应物 claim_order/worker/order_watchdog | Runner 封装与 E2E 文档改用真实函数名 |
| Cloud 可实现 legacy-hmac-v1 验证 | legacy 哈希密钥是本地 SECRET 文件 | legacy 验证/解密收敛到本地迁移工具（Commit 25 范围） |
| enc:v1 由 Cloud 解密 | enc:v1 密钥本地派生 | 同上；云端只有 enc:v2 |
| 访客查单可能有 order_code/proof code 机制 | 仅单号前缀匹配 | 中央查单按前缀语义实现并补限流 |
| 三路 E2E 需要 auto-hand/fuckCourse 双执行器 | 引擎只有 fuckCourse 一族 | Runner 清单：ChaoxingRunner / ZhsRunner / ZhsQrRunner |

## 6. 证据链

- 静态：stage-cloud-01 `LOCAL_FUNCTIONAL_MAP.md`（全量审计）
- 动态：stage-cloud-02 `tests/baseline/`（12 用例，隔离 APP_DIR 子进程运行真实平台，见 `tests/baseline/BASELINE_REPORT.md`）
