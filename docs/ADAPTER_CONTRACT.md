# ADAPTER_CONTRACT.md — 平台能力适配器契约（Commit 06 定义，Commit 07 实现）

> 目的：让 **同一套业务流程** 在 Local 与 Cloud 两侧跑通，差异只在执行层。
>
> ```
> 同一业务流程 ──┬── Local  → Real Adapter      （Windows / 真实第三方平台）
>                └── Cloud  → Synthetic|Replay   （Linux / 合成数据）
> ```
>
> 本文件定义**接口**。Commit 06 只声明契约（`cloud_test/adapters/*.py` 内仅有 `Protocol` 与 DTO，无任何实现、无网络、无 IO）；Commit 07 才写 Local/Cloud 适配器并跑契约测试。
> 功能编号（F01–F28）引用 `docs/LOCAL_FUNCTIONAL_PARITY.md`。

---

## 1. 通用约定

| 项 | 约定 | 理由 |
|---|---|---|
| 同步/异步 | **全部同步**（`def`，不用 async） | 与现有 Flask + Waitress 线程模型一致，避免两种并发模型混用 |
| 返回方式 | 成功返回 DTO；失败**抛异常**，绝不返回 `(False, "错误串")` | 现有 `query_courses()` 返回 `(ok, res)`，适配器层收敛为异常，边界处再转成旧格式 |
| 错误类型 | 见 §2 统一错误分类，携带 `retryable` | F25 的重试策略必须由类型决定，而不是靠字符串匹配 |
| 无副作用读取 | `get_*` / `list_*` 只读，不得写库、不得改文件 | 便于 UI 轮询与 CI 反复调用 |
| 幂等性 | `create_*` 必须幂等键友好；`submit_order()` 幂等；`execute()` 允许至少一次但结果必须可重放判定 | 队列重投不产生重复业务后果 |
| 命名空间 | Local/Cloud 适配器同名方法、同参数顺序、同 DTO | 契约测试同一套断言跑两侧（F07/F08/F09/F17/F22 五处） |
| 身份标记 | 每个适配器必须暴露 `name: str` 与 `is_synthetic: bool` | CI 可断言"云端不得出现 `is_synthetic=False` 的适配器" |
| 禁止事项 | 适配器内**不得**读取真实凭据文件、不得硬编码真实域名、不得绕过 `cloud_test.runtime.assert_cloud_test_runtime()` | Commit 03/04 的 fail-closed 是硬约束 |
| 依赖 | 仅 Python 标准库 | Commit 05 §24 纪律延续 |

---

## 2. 统一错误模型

| 异常 | `retryable` | 语义 | 本地等价来源（实测） |
|---|---|---|---|
| `AdapterError`（基类） | — | 所有适配器异常 | — |
| `ValidationError` | `False` | 入参非法（缺账号、空课程、平台不支持） | `query_courses` 返回 `参数不足`/`不支持的平台`；`set_courses` 空课程 |
| `AuthError` | `False` | 认证失败 / 登录态失效 / cookies 过期 | `登录失败`、`未查询到课程（登录态失效）` |
| `NotFoundError` | `False` | 资源不存在（订单/二维码/商品） | `订单不存在`、`商品不存在`、`QR 不存在或已过期` |
| `RateLimitedError` | `True` | 限流 / 查询繁忙 | `操作过于频繁，请稍后再试`、`查询服务繁忙（并发受限）` |
| `TransientError` | `True` | 超时 / 崩溃 / 网络抖动 | `_spawn()` 返回 `-9`（超时）、负退出码（崩溃）、`max retries`/`timed out` |
| `PermanentError` | `False` | 业务拒绝（引擎正常返回的非零码） | `worker()` 中 `rc > 0` 的分支（**不重试**，F25 语义） |
| `AdapterContractError` | `False` | 契约被破坏（适配器实现不合规，例如云端出现非合成适配器） | 无（新增，用于守住契约本身） |

**映射铁律**：`retryable=True` 只能出现在 `TransientError` / `RateLimitedError`。`worker()` 现有策略（超时或崩溃才重试、业务错误不重试）必须能由本表**无损推导**。

---

## 3. 统一任务状态（Commit 15 落库，本 Commit 只冻结语义）

| 统一状态 | 含义 | 兼容的本地状态（`orders.status`） | UI 呈现 |
|---|---|---|---|
| `PENDING` | 待执行 / 待扫码后待执行 | `pending`、`waiting_qr`（扫码门未过） | 排队中 |
| `CLAIMED` | 已被某 Runner 抢占（尚未真正开始） | 无（新增；本地用 `worker_running=1`+`running` 近似） | 执行中 |
| `RUNNING` | 执行中 | `running` | 执行中 |
| `DONE` | 成功 | `done` | 已完成 |
| `FAILED` | 终态失败 | `failed` | 失败 |
| `RETRY_WAIT` | 可重试等待（新增；本地直接回 `pending`） | `pending`（本地无独立态） | 排队中（可标注重试） |
| `CANCELED` | 用户取消 / 二维码过期 | `canceled` | 已取消 |

`waiting_qr` 与 `qr_state`（waiting/scanned/confirmed/expired/canceled/error）**保持原样**，作为 PENDING 的子状态并存，确保现有前端零改动（v3 §31）。

---

## 4. 数据契约（DTO）

全部为 `@dataclass(frozen=True)`，字段名与真实业务字段对齐（`docs/LOCAL_FUNCTIONAL_PARITY.md` §2）。

| DTO | 字段 | 对应本地 |
|---|---|---|
| `Product` | `code, name, desc, price, platform, enabled, sort` | `products` 表 |
| `Course` | `id, name, kind` | `tools_query_courses.py` 输出 `{id,name,kind}`；`kind ∈ {知到课, 共享课}` |
| `SessionUser` | `id, username, is_admin` | `users` 表 |
| `QrSession` | `oid, state, created_at` | `QR_SESSIONS[oid]` |
| `OrderRecord` | `id, user_id, product, platform, account, courses, status, note, qr_state, created_at, started_at, finished_at, exit_code, risk_flags, attempt, runner_id, lease_id` | `orders` 表（新增 `runner_id`/`lease_id`；**不新增 password 字段的云端存储**） |
| `ExecutionResult` | `exit_code, status, risk_flags, log_ref` | `_spawn()` 返回码 + `scan_risk()` + `log.txt` |
| `TaskLogEntry` | `timestamp, task_id, runner_id, attempt, event, message` | `log.txt` 的时序化结构（F16） |
| `HealthStatus` | `status, database, queue, pending, running, extra` | `/health` 现有 5 字段（F21） |

**隐私红线**：`OrderRecord` 中 `account` 在云端允许保留脱敏占位（如 `syn-user-01`）；**`password` 永不出现在 DTO 中**——本地密码只在 `LocalExecutionAdapter` 内部解密使用，云端执行适配器不接受密码参数。

---

## 5. 适配器接口定义

### 5.1 `StorageAdapter`（`cloud_test/adapters/storage.py`）— 覆盖 F01/F05/F06/F10–F16/F27

| 方法 | 签名 | 说明 |
|---|---|---|
| `get_products` | `() -> list[Product]` | 仅 `enabled=1`，按 `sort` |
| `create_order` | `(user_id: int, product: str, platform: str, account: str, secret_ref: str, courses: str) -> OrderRecord` | 幂等键由调用方给出；密码只传 `secret_ref`（不透明引用） |
| `get_order` | `(oid: str) -> OrderRecord` | 不存在 → `NotFoundError` |
| `list_orders` | `(user_id: int \| None, limit: int, offset: int) -> list[OrderRecord]` | `user_id=None` 表示全量（仅管理员/控制面） |
| `find_order_by_prefix` | `(prefix: str) -> OrderRecord \| None` | **F14 访客查单**语义（前 6–8 位） |
| `update_order` | `(oid: str, **fields) -> OrderRecord` | 终态重复写必须无害（本地 `safe_set_order` 语义 `:233`） |
| `submit_order` | `(oid: str, courses: str) -> OrderRecord` | 等价 `set_courses` + 转 `PENDING`（F10/F11） |
| `get_setting` / `set_setting` | `(key, default)` / `(key, value)` | 云端白名单化（F19） |

### 5.2 `AuthAdapter`（`auth.py`）— 覆盖 F02/F03/F04

| 方法 | 签名 | 说明 |
|---|---|---|
| `register` | `(reg_code: str, username: str, password: str) -> SessionUser` | 口令错 → `ValidationError`；重名 → `ValidationError` |
| `login` | `(username: str, password: str) -> SessionUser` | 失败 → `AuthError` |
| `session_user` | `(session_token: str) -> SessionUser \| None` | 无会话返回 `None`（不抛错，本地 `current_user()` 同语义 `:213`） |
| `logout` | `(session_token: str) -> None` | 幂等 |
| `hash_password` | `(password: str) -> str` | 本地为 `hmac(secret, salt+pw)` `:205`；云端独立实现，**两套哈希不互认** |

### 5.3 `CourseAdapter`（`course.py`）— 覆盖 F07（`TEST ADAPTER`）

| 方法 | 签名 | 说明 |
|---|---|---|
| `get_courses` | `(platform: str, account: str = "", secret_ref: str = "", cookie_ref: str = "") -> list[Course]` | 与本地 `query_courses(platform, account, password, cookie_path)` `:1082` 参数一一对应；空列表视为合法（但本地把它当错误 `未查询到课程` → 契约规定：**空列表返回 `[]`，由调用方决定提示**，本地适配器负责把旧错误串转成 `AuthError`/`ValidationError`） |
| `name` / `is_synthetic` | 属性 | 云端必须 `is_synthetic=True` |

平台取值（冻结）：`chaoxing` | `zhs` | `zhs_cookie`（扫码后，见 F07 三模式）。

### 5.4 `QrAdapter`（`qr.py`）— 覆盖 F08/F09（`TEST ADAPTER`）

| 方法 | 签名 | 说明 |
|---|---|---|
| `create_session` | `(oid: str) -> QrSession` | 云端生成合成 PNG（本地生成，不触网） |
| `qr_png` | `(oid: str) -> bytes` | 对应 `GET /qr/<oid>` `:1490`；不存在 → `NotFoundError` |
| `get_status` | `(oid: str) -> str` | 返回 `waiting`/`scanned`/`confirmed`/`expired`/`canceled`/`error`；订单不存在 → `unknown`（**保持本地 `/qr_status` 语义 `:1496`**） |
| `wait_confirmed` | `(oid: str, timeout_s: float) -> bool` | 本地为后台线程 `qr_thread()` `:520`；云端允许轮询实现 |
| `cleanup` | `() -> int` | 对应 `qr_janitor()` 600s TTL `:841` |

**状态机（冻结，必须逐字对齐）**：`waiting → scanned → confirmed`；终态 `expired` / `canceled` / `error`；`confirmed` 后订单从 `waiting_qr` 转 `PENDING`。

### 5.5 `ExecutionAdapter`（`execution.py`）— 覆盖 F22/F23/F17（`TEST ADAPTER`）

| 方法 | 签名 | 说明 |
|---|---|---|
| `prepare` | `(order: OrderRecord) -> ExecutionContext` | 本地等价 `build_order_env()` `:581`；云端只返回合成上下文，无代理/无指纹参数 |
| `execute` | `(ctx: ExecutionContext) -> ExecutionResult` | 本地等价 `run_chaoxing`/`run_zhs` `:694/:711` + `_spawn` `:651` |
| `cancel` | `(ctx: ExecutionContext) -> None` | 本地等价 `_kill_tree()` `:262` |
| `scan_risk` | `(log_text: str) -> str` | 本地 `scan_risk()` `:635` 的 5 类关键词语义（captcha/forbidden/risk_ctrl/login_fail/network）**不得改变分类名** |

`ExecutionContext` 必须提供：`order_id`、`attempt`、`heartbeat()`（本地 20s 心跳 `:674`）、`log(event, message)`、`progress(done, total)`。
`ExecutionResult.exit_code` 语义（冻结）：`0` 成功；`-9` 超时（可重试）；`<0` 崩溃（可重试）；`>0` 业务错误（**不重试**）。

### 5.6 `LogSink`（在 `storage.py` 内声明）— 覆盖 F16/F17

| 方法 | 签名 | 说明 |
|---|---|---|
| `append` | `(entry: TaskLogEntry) -> None` | 对应 `RollingLog` `:109`；云端替换 `log.txt` 文件 |
| `read_tail` | `(task_id: str, max_bytes: int = 8000) -> str` | **默认 8000 字节与本地一致** `:1466` |
| `list_entries` | `(task_id: str, limit: int) -> list[TaskLogEntry]` | 云端结构化读取 |

---

## 6. 契约测试计划（Commit 07 执行）

`tests/cloud_test/test_adapter_contract.py` 用 `pytest.mark.parametrize` 对 **Local 与 Cloud 两侧适配器跑同一套断言**：

1. **输入格式**：同参数调用不因实现不同而需要额外参数。
2. **输出格式**：DTO 字段集合完全一致（`dataclasses.fields` 比对）。
3. **错误模型**：同一错误场景（登录失败/不存在/限流/超时/业务错误）在两侧抛出**同一异常类型**且 `retryable` 一致。
4. **状态语义**：QR 六态、订单七态、`exit_code` 四分类在两侧一致。
5. **安全断言**：云端适配器 `is_synthetic is True`；`cloud_test/adapters/**` 中不得出现任何真实平台域名（由 Commit 03 的审计脚本复查）。
6. **失败即 fail-closed**：云端适配器在 `assert_cloud_test_runtime()` 失败时必须拒绝工作。

---

## 7. 边界声明

- 本契约**不含** Runner / Scheduler / Approval / Codespaces / Cloudflare 接口——它们分别在 Commit 17/18/22/23/20 定义（v3 §61 顺序）。
- `Health`（`HealthProbe`）与 `Admin`（统计聚合）**不是适配器**：它们是服务层，读 `StorageAdapter` + Runner 状态，接口在 Commit 19/20 定义。
- 本文件与 `LOCAL_FUNCTIONAL_PARITY.md` 是后续所有 Commit 的**引用基准**；两者如有冲突，以功能基线 F 表为准并立即修正本文件。
