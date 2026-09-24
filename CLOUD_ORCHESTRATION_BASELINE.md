# CLOUD_ORCHESTRATION_BASELINE.md

> Stage 0 只读基线 · 依据《autotask-platform 全面改造方案》§44/§58 生成
> 生成时间：2026-09-22 11:00 本地时间　·　主机 SC-202608131737　·　仓库 `jingjiangze/autotask-platform` @ `main`
> 本阶段**未修改任何业务代码**，未创建任何云资源，未提升任何 Token 权限。

---

## 0. 边界声明（先读这个）

本 baseline 及后续 Stage 1+ **只覆盖**：控制面 / 调度 / Runner 生命周期 / 可移植 runtime / 测试基建 / CI。

**不覆盖、不迁移、不优化**（方案 §20）：

```text
fuckCourse 的真实平台交互逻辑
  UA 伪装 · 代理池 · 倍速抖动 · 学习通/知到真实端点请求
```

**真实平台任务继续只在本机跑**（Windows 服务 `WKAutoTaskPlatform`）；云端只跑合成数据。
若后续要求云端执行真实平台任务、或解除 `CLOUD_TEST_MODE` 的端点阻断 → **停止并回到 G1 决策门**。

---

## 1. Git 只读基线（§58 第一步）

| 项 | 值 |
|---|---|
| 分支 / HEAD | `main` @ `bd4df06`（`bd4df06 chore: 收敛仓库范围并脱敏历史文档手机号`） |
| 与远端关系 | `git pull --ff-only` = **Already up to date**，本地 == `origin/main` |
| 总提交数 | 2（`88fe85a` init → `bd4df06` 脱敏收敛） |
| 工作区 | 干净，仅 5 个**未跟踪**文件（上一轮服务化产物）：`service_platform.py`、`tools/service_manager.py`、`tools/_rollback/`、`SERVICE_AND_ONLINE_DEPLOY.{md,html}` |

> 这些未跟踪文件**暂不提交**。它们属于"本机 Windows 服务化"主题，与云端改造是两条线；建议后续单独一个 `chore(local): add windows service host` 提交，或保持本地不入库（推荐后者，见 §9）。

---

## 2. 仓库结构与关键模块

```text
static/               651 文件  前端静态资源（Tabler）
fuckCourse/            39 文件  任务引擎（chaoxing/zhs/yuketang/welearn）
cf/                    19 文件  Cloudflare 部署/CDP/隧道运维工具（Windows 向）
tests/                  7 文件  test_crypto / test_health / test_kill_tree / test_restore_drill / test_spawn_timeout / test_access_admin / test_crypto_bundle
tools/                  3 文件
order_platform.py    1811 行   业务主体（Flask + worker 池）
health_manager.py     487 行   旧看护（已被 Windows SCM 服务替代，Linux 侧由 systemd 取代）
crypto_manager.py     419 行   加密 bundle / Windows Credential Manager
order_server.py       305 行
backup_manager.py     170 行
path_manager.py        53 行   统一路径取值（已具备，可复用为 runtime/paths.py 底座）
tools_query_courses.py 102 行
```

**不存在**（Stage 1+ 需新建）：`.github/`、`.devcontainer/`、`cloudflare/`、`runtime/`、`cloud/`、`Dockerfile`、根级 `requirements.txt`

---

## 3. 当前并行模型（§44 必填）

```text
concurrency_manager()  单进程线程池，每 5s 补足
   want = clamp(设置 concurrency, 1, MAX_CONCURRENCY=10)，默认 4
        ↓
worker(wid)  循环
   paused? → sleep(3)
   free_mem_mb() < min_free_mb(500) → sleep(10)      ← 全局内存闸
   claim_order()  SQLite CAS
        ↓
build_order_env(oid)  生成 per-order env / work / profile（任务隔离已有雏形）
        ↓
_spawn(cmd, env, cwd, log)   subprocess，creationflags=BELOW_NORMAL|CREATE_NO_WINDOW
   超时 → 树杀 → rc=-9
        ↓
scan_risk(oid) → done / failed / retry(MAX_RETRY=1，仅 rc==-9 或 rc<0 可重试)
```

**CAS 语义**（`claim_order()` L713）：

```sql
SELECT * FROM orders WHERE status='pending' AND worker_running=0 ORDER BY created_at LIMIT 1;
UPDATE orders SET worker_running=1, status='running', attempt=COALESCE(attempt,0)+1, heartbeat_at=?
 WHERE id=? AND worker_running=0;      -- rowcount==0 → 别的 worker 抢到了，本轮放弃
```

多 worker 并发安全，DB 忙(`OperationalError`)视为抢不到，不误伤订单。

**存活收敛**：`order_watchdog()` 每 30s 检查 `running` 订单的 `pid`；死亡 → attempt<MAX_RETRY 重新排队，否则 failed。`recover_stale_orders()` 负责启动收敛。**心跳字段 `heartbeat_at` 已存在**，方案 §25 的 Runner heartbeat 可以直接对接，不用加列。

**结论：现有 `claim_order` CAS + `attempt` + `heartbeat_at` 三件套与目标架构的 Task Lease 概念一一对应，Stage 3 的 Scheduler 抽象可以做薄 adapter，不需要重写状态机。**（与方案 §1 的判断一致）

---

## 4. Windows 绑定清单（Linux 迁移阻力 · §44 必填）

全部位于**已跟踪文件**内，逐处列行号：

### order_platform.py（唯一需要移植的运行时文件）

| # | 行号 | 内容 | Linux 替换 |
|---|---|---|---|
| 1 | **L33** | `PYEXE = r"C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\python.exe"`，被 L693/705/710/1085 引用 | `sys.executable` |
| 2 | L75-90 | `ctypes.windll.kernel32.GlobalMemoryStatusEx`（`free_mem_mb()` 全局内存闸） | `/proc/meminfo` 或 `psutil.virtual_memory()` |
| 3 | L157 + L646 | `CREATE_NO_WINDOW=0x08000000`、`creationflags=BELOW_NORMAL\|CREATE_NO_WINDOW` | `start_new_session=True`（+ 可选 `os.nice`） |
| 4 | L243-252 | `_pid_alive()` = `OpenProcess(SYNCHRONIZE)` | `os.kill(pid,0)` 或 `Path(/proc/pid).exists()` |
| 5 | L257-261 | `_kill_tree()` = `taskkill /F /T /PID` | `os.killpg(os.getpgid(pid), SIGKILL)`（配合 `start_new_session`） |
| 6 | L437-460 | `GetProcessMemoryInfo`（单进程内存） | `/proc/{pid}/status` VmRSS 或 `psutil.Process().memory_info()` |
| 7 | L1569 | `tasklist`（风控扫描用进程枚举） | `psutil.process_iter()` |
| 8 | （缺） | 无进程组/会话管理 | 需引入 `start_new_session` 才能让 `killpg` 生效 |

### 其他模块

| 文件 | 绑定 | 处置 |
|---|---|---|
| `health_manager.py` (487行) | pywin32 + ctypes + taskkill | **不移植**。Windows 侧已被 SCM 服务替代；Linux 侧由 systemd 单元取代，整个文件退役 |
| `crypto_manager.py` (419行) | **L98-114 `win32cred`**（Windows Credential Manager 存 DEK，`CRED_PERSIST_LOCAL_MACHINE`） | ⚠ **硬阻断**，见下 |
| `backup_manager.py` (170行) | L72-79 `DeleteFileW` | `os.remove()` |
| `cf/` 19 个文件 | taskkill/tasklist/硬编码盘符/creationflags/`.bat` | Windows 运维工具，云端不需要，不移植 |

### ⚠ 硬阻断：加密层与平台绑定

`crypto_manager.py` 把 **DEK 存在 Windows Credential Manager**（本机绑定），这意味着：

```text
本机加密的 bundle / 加密备份，在 Linux 上无法解密 —— 不是"改改路径"能解决的。
```

处置（与方案 §33 一致，天然自洽）：

```text
1. 云端禁止出现本机加密产物（private/encrypted、加密备份）—— 不复制即可绕开
2. 云端 secret 供给走独立通道：Cloudflare Worker Secret / 环境变量（方案 §10）
3. crypto_manager 保留 Windows 专用，不进 Linux 镜像；runtime/paths.py 不依赖它
```

### 引擎依赖（`fuckCourse/requirements.txt`，跨平台性评估）

```text
requests urllib3 pyaes pycryptodome beautifulsoup4 lxml loguru fonttools Pillow httpx   ← Linux 均有 wheel，无障碍
ddddocr        ← 依赖 onnxruntime，Linux 有 wheel，可装
openai tiktoken ← ⚠ 答题走 LLM API
```

**`openai`/`tiktoken` 是云端的一个隐性坑**：若不 mock，每个测试单都会发起真实 LLM 调用（需要真实 API key + 烧额度 + 走外网）。**云测试模式必须同时阻断 openai 端点并注入 mock adapter**，见 §8。

---

## 5. 目标并行模型（§44 必填，引自方案 §2/§3）

```text
Cloudflare Pages/Worker(永久在线) + Access + WAF
        ↓
Durable Object AutotaskControl（唯一控制状态源，SQLite-backed）
  requests / runners / tasks / leases / audit_events / usage_snapshots
        ↓
GitHub Codespaces lifecycle API（START/STOP）
        ↓
Runner Pool：默认 2 Runner × 2 slots = 4 并发，无任务时全 STOPPED
        ↓
Task Lease（runner 启动即发 lease；heartbeat/complete 均验 lease）
        ↓
CLOUD_TEST_MODE=1 的测试执行（合成数据 + mock adapter）
        ↓
Auto Shutdown（idle ≥ 3min → STOP）
```

**状态机**：

```text
请求：REQUESTED → APPROVED → QUEUED → ASSIGNED → RUNNING → DONE/FAILED/CANCELED
Runner：STOPPED → STARTING → READY → BUSY → DRAINING → STOPPING → (STOPPED | ERROR)
```

**CI 并行**（与运行时完全分离）：GitHub Actions matrix（`max-parallel: 2`，python 3.12/3.13 × unit/runtime/security），不做长时间后台任务。

---

## 6. Runner 生命周期（§44 必填）

| 阶段 | 动作 | 依据 |
|---|---|---|
| STOPPED | 默认态。零 core-hours 消耗 | §2/§23 |
| STARTING | `POST /user/codespaces/{name}/start`，重试 ≤2 后转 ERROR | §21/§39 |
| READY | runner_agent 调 `POST /internal/register` 上报 | §13 |
| BUSY/DRAINING | 按 slot 计数；DRAINING = 不再接新任务 | §2 |
| STOPPING | `idle_since` 满足 `current_tasks=0 ∧ queue=0 ∧ idle≥3min` | §22 |
| STOPPED | `POST .../stop`；前端仍在线 | §23 |
| ERROR | reconcile 修正；heartbeat 连续 3 次丢失 → SUSPECTED → reconcile 定夺 | §24/§25/§39 |

**兜底**：GitHub 侧 inactivity timeout 设 15~30 分钟（§4），作为控制面停机逻辑失效后的第二道闸。

---

## 7. Cloudflare 控制面现状（§44 必填 · 实测）

> 探测方式：复用项目自带 `cf/access_admin.py` 的凭据抽象（Windows Credential Manager），**只发 GET**。

| 项 | 实测结果 | 结论 |
|---|---|---|
| Token 有效性 | `/user/tokens/verify` = **active**，来源 `credential_manager` | 可用 |
| Zone | `jiangjiangze.icu`（1 个） | 域名就绪 |
| **Access 应用** | **4 个已存在**：`OrderPlatform`、`OpenList`、`dingyue - Cloudflare Workers` ×2 | **Access 层已具备**，只需为新增控制面域名加一条应用 |
| **Workers scripts** | **0 个**（端点可读，权限 OK） | 控制面**从零开始**，与预期一致 |
| Pages / KV / R2 / Durable Objects | **全部 `Authentication error`(code 10000)** | ⚠ **现有 Token 缺这四类 scope** |

**权限缺口（进入 Stage 5 前必须解决）**：现有 CF Token 只覆盖 Access + Zone + Workers 读取。
需要**新建一个 Cloudflare API Token**，权限：

```text
Workers Scripts          : Edit
Durable Objects          : Edit
Workers KV Storage       : Edit
R2                       : Edit
Cloudflare Pages         : Edit
Access: Apps and Policies: Edit
Zone → DNS               : Edit（Pages 自定义域 / 控制面域名）
Account → Workers Tail/Logs: Read（排障用，可选）
```

**工具链**：`wrangler` 未安装（可用 `npx wrangler`，node v22.22.2 已就位）；`cloudflare/` 目录待建。

---

## 8. GitHub API 权限现状（§44 必填 · 实测）

> 探测方式：读取 Windows 凭据管理器中的 `git:https://github.com` 凭据（`gho_` classic PAT，scopes `gist, repo, workflow`）。

| 端点 | 实测 | 结论 |
|---|---|---|
| `GET /user` | **200**，login = `jingjiangze` | 认证 OK |
| `GET /repos/…/codespaces/machines` | **200** | `basicLinux32gb`(2C/8G/32G)、`standardLinux32gb`(4C/16G/32G) → **Codespaces 已启用且 2C 机型可用** |
| `GET /user/codespaces` | **403** "Must have admin rights to Repository" | ⚠ 现有 Token **无 `codespaces` scope**，无法列出/启停 Codespace |
| `GET /repos/…/actions/workflows` | 200，`[]` | CI 待建，与预期一致 |

**权限缺口**：需**新建 fine-grained PAT**（方案 §10 的要求，也是我实测确认的硬缺口）：

```text
Repository access : 仅 jingjiangze/autotask-platform
Permissions       :
  Codespaces lifecycle admin : Read/Write   ← START/STOP Codespace
  Contents                   : Read         ← Runner 拉代码（只读）
其余一律不授。不勾 admin/org 权限。
```

**Token 保存位置**：只进 **Cloudflare Worker Secret**；不进前端 / Git / Codespace / KV / DO / 日志（§10）。Runner Agent 只持 `RUNNER_SHARED_SECRET`，**不持有** GitHub lifecycle token（§13）✓。

---

## 9. 免费额度预算（§44 必填）

> ⚠ 实测限制：现有 GitHub Token 无 billing/admin scope，**Actions 分钟数与 Codespaces 用量无法通过 API 查询**，只能按常数预算 + 自建用量估算器（方案 §40 的 `usage_snapshots`）。

| 资源 | 免费额度 | 本项目预算推演 |
|---|---|---|
| **Codespaces** | 120 core-hours/月 + 15 GB-月存储（个人 Free） | 2C 机器 1 小时 = 2 core-hours；**双 2C Runner 并行 = 4 core-hours/小时 → 满额只够约 30 小时**；单 Runner 约 60 小时。→ "默认全 STOPPED + 人工审批 + 3min idle 停机"**不是锦上添花，是额度能不能活过一个月的前提** |
| **Actions** | 2000 min/月（私库） | 单次全量 CI（2 python × 3 suite，max-parallel=2）≈ 6 job × 3-8 min；每月可支撑 **~40-80 次全量 CI**，充足。但**不要**把 CI 当后台执行器 |
| **Workers** | 100k req/day，10ms CPU/req，5 Cron/account | 控制面用 1 个 Cron（reconcile/5min），远低于阈值 |
| **Durable Objects** | 100k req/day，13k GB-s/day（SQLite-backed，Free 可用） | Runner heartbeat 30s × 2 Runner = 5760 req/day/Runner，可控 |
| **Pages** | 静态请求免费不限量；**500 builds/月** | 前端改动别触发频繁构建，靠 Actions 只在 `cloudflare/` 变更时构建 |
| **R2** | 10 GB-月，1M Class A/月，10M Class B/月，egress 免费 | 只存加密备份 + 测试报告 |

**额度熔断**（照搬方案 §40）：`<80%` 正常 / `80~90%` 告警 / `90~100%` 仅人工批准 / `≥100%` 禁止新 Runner（已运行的不杀，避免任务中断）。

**Codespace 存储注意**：15 GB-月很紧。2 个 Runner 各挂 32 GB 盘 ≠ 免费 15 GB——存储按"已创建的 Codespace 存活时间"计。**建议默认只建 2 个 Runner 且常置 STOPPED**（STOPPED 状态仍计存储费，但不计计算费）；若额度吃紧，改为"用完即删、`cloud-test` 分支 + `.devcontainer` 可随时重建"（方案 §27 关 Prebuild、§55 重建测试正是为此）。

---

## 10. Stage 1 前的阻塞项清单（需要用户操作）

| # | 阻塞项 | 谁来做 | 影响 |
|---|---|---|---|
| 1 | 新建 **Cloudflare API Token**（§7 的 7 项权限） | 用户在 CF Dashboard 创建 | 无则 Worker/DO/KV/R2/Pages 全部无法部署 |
| 2 | 新建 **GitHub fine-grained PAT**（`Codespaces lifecycle admin: RW` + `Contents: R`，仅限本仓库） | 用户在 GitHub Settings 创建 | 无则无法列出/启停 Codespace（已实测 403） |
| 3 | Codespace **inactivity timeout** 设 15-30 分钟 | 用户在 GitHub Codespaces 设置 | 停机逻辑失效时的兜底 |
| 4 | （可选）确认 Codespace 默认机型选 `basicLinux32gb`（2C） | 用户 | 4C 机型 core-hours 消耗翻倍 |

---

## 11. `CLOUD_TEST_MODE` 必须做成 fail-closed（我的硬性建议）

方案 §19 把 cloud-test-mode 描述为"强制 proxy_pool=off / spoof=off / jitter=off / 真实生产 endpoint=blocked"。
**若这只是一个引擎自己读的配置开关，它形同虚设**——引擎里硬编码着 `zhihuishu.com` / `chaoxing.com` 端点，开关关掉它照样连。

因此进入 Commit 03（`feat(cloud-test): add cloud test mode`）时，阻断必须落在**网络层**，而不是配置层：

```text
runner_agent 在 slot 子进程的入口注入一个 egress guard（sitecustomize / entrypoint wrapper）：
  白名单：控制面 URL（心跳/claim/complete）、PyPI（装依赖）、GitHub
  黑名单硬编码：zhihuishu.com 及其全部子域、chaoxing.com 及其全部子域、
               api.openai.com（LLM 答题）
  命中黑名单 → 连接直接被拒（Connection refused 级别），并写入 audit_events
```

**可测的验收**（对应方案 §56 安全项 "no production endpoint in cloud-test mode"）：
在 cloud-test-mode 下**故意**发起一次对 `hike.zhihuishu.com` 的连接，断言其被拒、且 `audit_events` 留痕。测不过就不算 Commit 03 完成。

同时：引擎的 `openai/tiktoken` 调用必须以 **mock adapter** 注入，否则测试单会发起真实 LLM 请求（烧额度 + 需要真实 key）。

---

## 12. 风险与遗留

| # | 风险 | 等级 | 处置 |
|---|---|---|---|
| 1 | Codespace 存储 15 GB-月 偏紧（STOPPED 也计费） | 中 | 默认只建 2 Runner；必要时改"用完即删 + 可重建" |
| 2 | 120 core-hours/月 上限低，双 Runner 满载只够 ~30h | 中 | 依赖按需启停 + idle 停机；usage_snapshots 自计量 |
| 3 | `crypto_manager` 平台绑定，加密产物不可跨平台 | 低（云端用合成数据，天然不涉密） | 云端走独立 secret 通道；Windows 加密产物不上云 |
| 4 | 现有两个 Token 权限缺口（CF 4 类 / GH codespaces） | **阻塞** | §10，需用户创建 |
| 5 | 引擎 LLM 依赖（openai/tiktoken）在云端需 mock | 中 | §11，mock adapter 进 Commit 03 |
| 6 | 现有 `order_platform.py` 1811 行单文件，Stage 3 抽 Scheduler 有回归风险 | 中 | 按方案 §47 只加 adapter 不删旧实现，tests/ 7 个现有测试作回归护栏 |

---

## 13. 结论与下一步

**结论**：方案可行，且现有代码的三件套（`claim_order` CAS / `attempt` / `heartbeat_at`）与目标架构高度同构，迁移阻力集中在 **§4 的 8 处 Windows 绑定 + 加密层平台绑定**，而非业务模型。Cloudflare 侧 Access 已就绪、控制面需从零建；GitHub 侧 Codespaces 可用但 Token 权限不足。

**下一步（按方案 Commit 路线）**：

```text
Commit 01  chore: finalize cloud-test privacy boundary   ← .gitignore 补 cloud/cloudflare 产物边界
Commit 02  docs: add cloud orchestration baseline        ← 本文件入库
Commit 03  feat(cloud-test): add cloud test mode          ← 含 §11 的 fail-closed egress guard
   …
```

**Stage 1 开工前需要你提供**：§10 的两个新 Token（Cloudflare + GitHub fine-grained PAT）。
在拿到它们之前，我可以先做 **Commit 01 / 02 / 03**（这三步都不需要云凭据，纯本地代码 + 入库），Stage 4 之后的 Runner 真实启停才被阻塞。
