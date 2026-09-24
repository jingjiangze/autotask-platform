# 服务化注册 与 线上迁移部署方案

> 承接文档：《`ONLINE_MIGRATION_AUDIT.md`》（审计与迁移规划，14 章）
> 本文是**执行篇**：记录 Windows 服务化的实施与验证结果，并给出迁移到线上运行的完整方案。
> 编写时间：2026-09-22 00:50（本地时间）　主机：`SC-202608131737`（Windows，管理员 `Administrator`）

---

## 0. 执行摘要

| 事项 | 状态 | 说明 |
|---|---|---|
| Windows 服务注册 | **已完成并实测通过** | 两个服务均 `RUNNING` + 延迟自启 |
| 开机自动启动 | **已配置并验证机制** | `START_TYPE = AUTO_START (DELAYED)`，已实测冷启动 |
| 崩溃自愈（替代自研看护） | **已实测通过** | 强杀进程后约 5 秒被 SCM 自动拉起 |
| 旧计划任务退役 | **已完成** | `WK_AutoTaskPlatform` 计划任务已禁用，自研 `health_manager` 已下线 |
| 迁移到线上运行 | **方案已就绪，未执行** | 卡在审计结论里的 **G1 合规决策门**，需你拍板后才能落地 |

**一句话结论**：服务化部分我做完了，而且把「进程崩了自动拉活」「停机不留孤儿进程」这两件原来靠自研看护脚本硬撑的事，换成了操作系统原生能力，实测有效。至于「迁到线上」，技术步骤我已经写全（第 3~6 章），但**它不该由我替你决定是否开跑**——原因见第 4.1 节。

---

## 1. 需要注册为服务的程序与启动方式

### 1.1 服务清单

| # | 服务名称 | 显示名 | 注册的映像（binPath） | 启动类型 | 运行账户 |
|---|---|---|---|---|---|
| 1 | `WKAutoTaskPlatform` | WK 自动任务平台 (AutoTask Platform) | `"C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\python.exe" -u "D:\web\service_platform.py"` | 延迟自动启动 | `LocalSystem` |
| 2 | `WKCloudflared` | WK Cloudflare Tunnel | `"D:\web\cf\cloudflared.exe" tunnel --config "C:\Users\Administrator\.cloudflared\wk_config.yml" --no-autoupdate --metrics 127.0.0.1:20241 run wk-platform` | 延迟自动启动 | `LocalSystem` |

两者都是 `SERVICE_WIN32_OWN_PROCESS`（独占进程型服务，各自一个进程，互不干扰）。

### 1.2 为什么注册 `service_platform.py` 而不是 `order_platform.py`

`order_platform.py` 是业务主体（Flask + Waitress，监听 `127.0.0.1:8766`）。但它**不能直接注册为服务**，因为它是一段「启动即裸跑」的脚本：

- 模块级就执行 `init_db()` / 迁移 / `recover_stale_orders()`，并在 import 时直接拉起 4 个后台守护线程；
- 结尾是 `app.run(...)` 这类前台阻塞调用，没有与 **SCM（服务控制管理器）握手**的能力。

Windows 服务要求进程在启动后 **30 秒内**向 SCM 报告 `RUNNING`，否则报错 1053 并杀掉进程。裸脚本无法满足，会被 SCM 判死。

所以我新增了 **`D:\web\service_platform.py`** 作为**薄托管层**，职责只有三件事：

1. **完成 SCM 握手**——用 pywin32 的 `servicemanager` 自己实现，**不依赖** `pythonservice.exe`、也不引入 NSSM / WinSW 等第三方包装器；
2. **托管 HTTP 生命周期**——`import order_platform` 后用它导出的 `app` 对象，通过 Waitress 的 `create_server()` 编程式起服务；
3. **有序停机**——先关 Waitress（停收新连接、排空在途请求），再树杀仍在运行的任务引擎子进程，最后退出。

关键点：**HTTP 业务逻辑一行都没复制**，`service_platform.py` 里没有路由、没有业务代码，它只是宿主。这保证了迁移时业务代码仍然是单一维护点。

> `import order_platform` 会完整复现原先 `pythonw order_platform.py` 的运行语义（同样的建表、迁移、启动恢复、4 个后台线程），因此服务化**不改变任何业务行为**。

### 1.3 启动方式与自启机制

- **启动类型**：`SERVICE_AUTO_START` + **延迟自动启动**（`SERVICE_CONFIG_DELAYED_AUTO_START_INFO = True`）。
  选「延迟」而不是普通自启，是为了避开开机登录初期的资源争抢高峰——延迟自启会在系统进入稳定状态后才拉起，且**不依赖任何用户登录**，真正做到无人值守。
- **启动账户**：`LocalSystem`。已验证 `LocalSystem` 对以下路径有完整读取权限：
  - `C:\Users\Administrator\.cloudflared\`（隧道配置目录，SYSTEM 有 `(F)`）
  - `...\24f92801-...json`（隧道凭据文件，SYSTEM 有 `(F)`）
  - `D:\web\cf\cloudflared.exe`（SYSTEM 有 `(F)`）
- **失败恢复策略**（SCM 原生看护）：
  - 第 1 次失败 → **5 秒**后重启
  - 第 2 次失败 → **30 秒**后重启
  - 第 3 次及以后 → **60 秒**后重启
  - 计数重置周期：**86400 秒**（24 小时）
  - `FAILURE_ACTIONS_ON_NONCRASH_FAILURES = TRUE` ← **这个开关很关键**。默认是 `FALSE`，意味着只有「崩溃（非零退出码）」才会触发重启；如果 Python 进程以 `0` 码意外结束，服务会**静默停摆**而 SCM 不管。我把它置为 `TRUE`，任何非主动停止的退出都会被拉活。

### 1.4 有序停机（避免孤儿进程）

`SvcStop()` 收到停止请求后：

```
ReportServiceStatus(SERVICE_STOP_PENDING, waitHint=30000)   # 先告知 SCM 需要时间
  → SetEvent(hWaitStop)                                      # 放行主线程
主线程：server.close() → join(timeout=10)                    # 关闭 Waitress，排空在途请求
     → 树杀 DB 中 status='running' 的订单子进程（best-effort）
     → 退出
```

**故意不修改订单状态**：子进程被清掉后，下一次启动时 `order_platform.recover_stale_orders()` 会按既有的 attempt 规则统一收敛，保持**单一收敛入口**，避免两处逻辑打架。

`SvcShutdown()` 复用了同一条路径，因此**系统关机**时也会走有序停机，不会被硬杀。

### 1.5 日志

服务进程没有控制台，`print` 输出会丢失。因此 `service_platform.py` 在进入主循环前把 `stdout/stderr` 重定向到带轮转的日志文件：

- 路径：`D:\web\cf\platform_service.log`
- 轮转：超过 2 MB 时保留尾部一半（避免无限增长）
- 保留 `order_platform` 原有的所有 `print`，用于事后尸检

cloudflared 作为服务运行时由 SCM 托管，其日志依赖 Windows 事件日志；隧道健康度建议直接查 metrics 端点（见 6.1）。

---

## 2. 实施记录与验证证据

### 2.1 变更清单

| 文件 | 性质 | 说明 |
|---|---|---|
| `D:\web\service_platform.py` | **新增** | 服务宿主（SCM 握手 + Waitress 托管 + 有序停机 + 日志重定向） |
| `D:\web\tools\service_manager.py` | **新增** | 服务生命周期管理 CLI（`status/install/uninstall/start/stop/restart/verify/console`） |
| `D:\web\tools\_rollback\WK_AutoTaskPlatform.task.xml` | 新增 | 原计划任务定义备份（回滚用） |
| `D:\web\tools\_rollback\procs_before_service.json` | 新增 | 切换前进程快照 |
| `D:\web\tools\_rollback\old_tunnel_processes.txt` | 新增 | 旧隧道进程命令行存档 |
| 计划任务 `WK_AutoTaskPlatform` | **禁用** | `schtasks /Change /DISABLE`，保留定义以便回滚 |

**没有修改任何业务逻辑**，`order_platform.py` 未被触碰。

### 2.2 排错记录：binPath 被双重引号包裹

首次用 `sc.exe create ... binPath= "..."` 创建服务后，服务启动即静默失败（`WIN32_EXIT_CODE = 1067`，进程终止），日志文件根本没被创建。

根因：`sc create` 要求把**带引号的可执行路径**当成一个整体 argv 元素传入，而 Python `subprocess` 的 `list2cmdline` 会**再次加引号并转义内部引号**，最终 sc.exe 存下的是：

```
""C:\...\python.exe" -u "D:\web\service_platform.py""
```

`CreateProcess` 解析这串时，开头的 `""` 会先开再闭一个引号段，产生一个空 token，可执行文件路径解析失败 → 服务无法启动。

修复：**改用 pywin32 的 `CreateService` / `ChangeServiceConfig` Win32 API 直接传字符串**，全程不经过任何 shell 转义环节。修正后的 `BINARY_PATH_NAME` 已确认为：

```
"C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\python.exe" -u "D:\web\service_platform.py"
```

同一处坑也修好了隧道服务的 binPath。**这是一个通用教训：在 Windows 上注册服务时，凡是 binPath 含引号，不要走 `subprocess` + `sc.exe`。**

### 2.3 验证结果（实测）

**`python tools/service_manager.py verify` 输出：**

```
[PASS] 服务 WKAutoTaskPlatform: 存在=True 状态=running 启动类型=delayed-auto
[PASS] 服务 WKCloudflared:   存在=True 状态=running 启动类型=delayed-auto
[PASS] 监听 127.0.0.1:8766
[PASS] 本地 /health → 200 {"database":"ok","pending":0,"queue":"ok","running":0,"status":"ok"}
[PASS] 隧道边缘连接 http://127.0.0.1:20241/ready → 200
[INFO] 隧道 HA 边缘连接数 = 2（正常为 2）
[PASS] 公网 https://order.jiangjiangze.icu/health → 302：Cloudflare Access 登录拦截生效
[PASS] 旁证 https://maa.jiangjiangze.icu/ → 200（同隧道另一域名端到端贯通）
结论: PASS（核心项全部通过）
```

**① 冷启动测试**（模拟开机自启）：`sc stop` 后 `sc start`，服务从 `STOPPED` → `START_PENDING` → `RUNNING`，新进程 PID 拉起，`/health` 恢复 200。

**② 有序停机测试**：日志完整记录停机链路，8766 端口释放，**Session 0 无任何孤儿 python 进程**：
```
[00:45:40] 收到停止请求，开始有序停机…
[00:45:40] 正在关闭 Waitress（停止接收新连接，排空在途请求）…
[00:45:40] Waitress 已关闭
[00:45:40] 正在清理仍在运行的任务引擎子进程…
[00:45:40] 子进程清理完成，处理 0 个运行中订单
[00:45:40] 有序停机结束
[00:45:40] WKAutoTaskPlatform 已退出
```

**③ 崩溃自愈实测**（决定性验证）：直接 `taskkill /F` 强杀服务进程 `13544` → 8766 立即无监听 → **约 5 秒后 SCM 自动拉起新进程 `13592`** → `/health` 恢复 200。日志留有对应启动记录。

**④ 无残留确认**：当前机器上其余 `pythonw.exe` 经查全部属于其他项目（工学云打卡 `AutoMoGuDingCheckIn`、`D:\web\maa_status\server.py`、MAA 夜巡 `night_watch.py`），**自研 `health_manager` 已无任何残留进程**。

### 2.4 本次服务化修复的审计问题

审计报告 §5.4 指出的 **A1（看护进程与派生平台形成僵尸进程对）** 与 **A2（锁文件 + 三层自研守护模型脆弱）** 在本轮被**结构性消除**：

| 维度 | 旧模型（三层自研） | 新模型（SCM 原生） |
|---|---|---|
| 守护层级 | 计划任务 → `health_manager` 看护 → 派生平台 | SCM 直接托管服务进程 |
| 存活判定 | 锁文件 + 心跳 | 内核级进程句柄 |
| 崩溃恢复 | 看护脚本轮询后重启 | SCM 失败恢复（5s/30s/60s） |
| 停机清理 | 依赖看护脚本自杀，易留孤儿 | 有序停机 + 树杀子进程，实测零孤儿 |
| 层级数 | 3 层，2 个进程对 | 1 层，1 个进程 |

### 2.5 使用方式

```bash
# 需管理员权限
python tools/service_manager.py status         # 查看状态与健康
python tools/service_manager.py verify         # 端到端验证
python tools/service_manager.py start|stop|restart [platform|cloudflared|all]
python tools/service_manager.py install        # 创建/更新并启动
python tools/service_manager.py uninstall      # 停止并删除（回滚用）
python tools/service_manager.py console        # 前台调试运行，Ctrl+C 退出
```

---

## 3. 迁移范围（迁什么、不迁什么）

### 3.1 必须迁移：应用与引擎

| 类别 | 具体内容 | 体积 | 说明 |
|---|---|---|---|
| 平台代码 | `order_platform.py`、`service_platform.py`、`tools/`、`tests/` | ~170 KB | 主体 |
| 任务引擎 | `fuckCourse/`（`fucker` 模块 + 课程脚本） | 2.9 MB | 业务核心 |
| 打卡/脚本引擎 | `zhs_script/` | 24 MB | 视线上是否启用该功能决定 |
| 前端资源 | `static/`、`tabler_pkg/` | 26 MB | Tabler 主题静态包 |
| 数据库 schema | 由 `init_db()` 自动建表 | — | **不迁数据文件**，只迁 schema 定义 |

### 3.2 绝对不迁移：密钥、隐私、运行态

| 内容 | 路径 | 原因 |
|---|---|---|
| 会话密钥 | `secrets_store/secret_key.txt` | 密钥；且**必须在新环境重新生成**以轮换 |
| 账号配置 | `fuckCourse/config.json` | 含真实账号口令 |
| 用户与订单数据 | `orders/platform.db` | **含第三方用户手机号等个人信息（PIPL 管辖）** |
| 历史备份 | `backups/`、`_hist/`、`_archive_dev/` | 历史垃圾 |
| 浏览器指纹库 | `cf/browser_profile/` | 含登录态 cookie，属凭据 |
| 运行态标记 | `portal/`、`maa_status/`、`*.lock*` | 本机运行态，线上重新生成 |
| 二进制 | `cf/cloudflared.exe` | 平台相关，各环境自行下载 |

> `.gitignore` 已按上述边界配置完毕（第二轮 GitHub 上传时做过全量凭据扫描，确认无泄漏）。

### 3.3 依赖清单与跨平台问题

项目**没有 `requirements.txt`**（这是个需要补的债）。按实际 import 与已装版本，依赖为：

```
Flask==3.1.3
waitress==3.0.2
requests==2.34.2
beautifulsoup4==4.15.0
lxml==6.1.1
pillow==12.3.0
psutil==7.2.2
urllib3==2.7.0
Werkzeug==3.1.8
Jinja2==3.1.6
MarkupSafe==3.0.3
itsdangerous==2.2.0
click==8.4.2

# 仅 Windows 需要，Linux 目标环境必须排除：
pywin32==312
```

**⚠️ 关键跨平台障碍：`pywin32`。**
`service_platform.py` 依赖 `servicemanager` / `win32service` / `win32event`（全部来自 pywin32），**在 Linux 上不存在**。
我已在代码里做了**降级保护**：pywin32 缺失时 import 失败会被捕获（`_PYWIN32_OK = False`），此时文件仍可被 import 做静态检查，只是不能以服务方式运行。

> 这意味着：迁到 Linux 时，**`service_platform.py` 整个文件应当被 systemd unit 取代**（见 5.4），不要试图移植。

### 3.4 环境绑定项（迁到新环境必须改）

| 位置 | 现状 | 迁移动作 |
|---|---|---|
| `order_platform.py:33` | `PYEXE` 硬编码为 `C:\Users\Administrator\.workbuddy\...\python.exe` | **改为 `sys.executable`**（它拉子进程跑任务引擎，硬编码路径在新环境必崩） |
| `order_platform.py:30` | `DB_PATH = orders/platform.db`（SQLite + WAL） | 改为从环境变量读，或切 PostgreSQL（见 4.3） |
| `order_platform.py:36` | `_SECRET_FILE = secrets_store/secret_key.txt` | 新环境重新生成；生产建议改从环境变量注入 |
| `service_platform.py:44-46` | `HOST/PORT = 127.0.0.1:8766` | 线上由反向代理前置，保持仅监听回环 |
| `order_platform.py:56` | `MAX_CONCURRENCY = 10` | 按新环境 CPU 核数/内存调整 |

> `PYEXE` 的硬编码在**同机服务化场景下是可接受的**（本轮不改），但它是迁移的**头号地雷**。

---

## 4. 目标线上环境

### 4.1 ⚠️ 决策门 G1：先合规，再谈上线

审计报告 §14 已明确指出：本系统的**核心功能存在合规与法律风险**（涉及《刑法》285/286 条的"提供侵入、非法控制计算机信息系统程序、工具"风险，以及《个人信息保护法》对第三方用户数据的管辖）。**这是非技术问题，我无法用代码解决，也不应由我替你决定。**

因此**线上迁移在这里分叉**，请先确认你属于哪种情形：

| 选项 | 含义 | 对迁移的影响 |
|---|---|---|
| **G1-A** 仅内部自用 | 只保留 Cloudflare Access 后的私密访问，不对外提供 | ✅ 可立即执行迁移（推荐） |
| **G1-B** 停止运营 | 关停下线 | ❌ 迁移无意义，应走退役流程 |
| **G1-C** 对外经营 | 向第三方用户提供服务 | ⚠️ **须先取得法律意见并完成合规整改**，否则迁移=把风险规模化 |

**我的建议**：在 G1 明确之前，**维持现状**——即「服务化在本机跑 + Cloudflare Access 挡在最前面」。这个姿态是当前风险最低的「线上」形态，且本轮服务化已经把它做扎实了。

### 4.2 现状复盘：它其实"已经在线上"了

这点必须说清楚，否则容易做无用功：

- 平台**已于本机对外发布**：`https://order.jiangjiangze.icu`（Cloudflare Tunnel）
- **前置了 Cloudflare Access（Zero Trust）**：未认证访客在 Cloudflare 边缘就被 302 到 `yuehuibu5561.cloudflareaccess.com` 登录页，**请求根本到不了本机** —— 这是有效的访问控制，不是装饰
- 隧道同时承载 `maa.jiangjiangze.icu`（→ `127.0.0.1:8791`，MAA 状态页）
- 现有实测数据量（只读查库）：**4 个账户**（1 管理员 + 3 普通用户）、**19 笔订单**（14 完成 / 5 失败）

所以「迁移到线上」的真实含义**不是从无到有上线**，而是二选一：

- **(a) 就近强化**：继续在本机跑，把它做成一个稳定、可自愈、开机自启的服务 —— **← 本轮已完成**
- **(b) 异地迁移**：把应用搬到独立的云主机/容器，脱离这台个人电脑 —— 需要在 G1 明确后进行

### 4.3 三种目标环境对比（供 (b) 选型）

| 方案 | 形态 | 优势 | 代价 | 适用 |
|---|---|---|---|---|
| **方案 A：本机为主**（现状） | Windows + SCM 服务 + Cloudflare Tunnel + Access | 零新增成本；已投产；隐私数据不出本机 | 依赖这台电脑在线；家庭宽带 SLA；升级/重启会中断 | **G1-A** |
| **方案 B：单台云主机**（推荐用于迁移） | 1× Linux VM（2C4G）+ Docker Compose + Cloudflare Tunnel + Access | 免开入站端口；SQLite 可继续用；迁移成本最低；可平滑切到方案 C | 月费；需做数据脱敏迁移 | G1-A / 轻度 G1-C |
| **方案 C：容器化拆分**（审计推荐的终态） | PostgreSQL + Redis + 平台容器 + worker 容器 | 多 worker 水平扩展；订单队列可靠；可观测性好 | 工程量大；需改造 SQLite → PG、线程池 → 独立 worker | **G1-C 且已被批准** |

**选型建议**：
- 若选 **G1-A** → 保持方案 A（本轮已完成），不折腾。
- 若要做**异地迁移**（脱离个人电脑）→ 走 **方案 B**，它是"迁移成本 / 收益"比最高的一档，且是通往方案 C 的必经台阶。
- **方案 C 只在 G1-C 获批后才值得投入**，否则是给一个可能要被关停的系统做过度工程。

> 关于 **SQLite vs PostgreSQL**：当前 `SQLite + WAL` 在**单机单写者**场景下完全够用（本系统就是这种负载）。审计把它列为"缺陷"要辩证看——真正需要 PG 的触发条件是**多实例并发写**，而那只有当你要上方案 C 时才发生。不建议为了"显得专业"提前切库。

---

## 5. 完整迁移与部署步骤（以方案 B 为目标）

> 前置：G1 已明确，且确定为「异地迁移」。以下步骤按顺序执行，每步都有验证点。

### Phase A：代码可移植性整改（**在源机做**）

| 步骤 | 动作 | 验证 |
|---|---|---|
| A1 | `order_platform.py:33` 的 `PYEXE` 改为 `sys.executable` | 本机重启服务后，派单仍能正常拉起任务引擎子进程 |
| A2 | 把 `DB_PATH` / `_SECRET_FILE` / `HOST` / `PORT` / `MAX_CONCURRENCY` 改为读环境变量（带默认值） | 用不同环境变量启动两次，行为随配置变化 |
| A3 | 生成 `requirements.txt`（见 3.3，**排除 pywin32**） | `pip install -r requirements.txt` 在纯净 venv 中成功 |
| A4 | 补 `.env.example`（只放占位值） | 确认 `.env` 在 `.gitignore` 中 |

### Phase B：镜像化

| 步骤 | 动作 | 验证 |
|---|---|---|
| B1 | 写 `Dockerfile`：`python:3.13-slim` + `pip install -r requirements.txt` + 拷贝应用（**排除 3.2 全部路径**） | `docker build` 成功 |
| B2 | 写 `docker-compose.yml`：`platform` 服务，挂载 `orders/` 为 volume，`restart: unless-stopped` | `docker compose up -d` 后容器 `healthy` |
| B3 | 加 `HEALTHCHECK`：`curl -f http://127.0.0.1:8766/health` | `docker inspect` 显示 `healthy` |
| B4 | 不用 `service_platform.py`，改用入口 `CMD ["python","-m","waitress","--host=127.0.0.1","--port=8766","order_platform:app"]` 或 gunicorn | 容器内 `curl /health` 返回 200 |

### Phase C：数据迁移（**唯一涉及隐私的环节，需脱敏**）

| 步骤 | 动作 | 验证 |
|---|---|---|
| C1 | 导出**仅 schema**：`sqlite3 orders/platform.db .schema > schema.sql` | 新环境执行后表结构一致 |
| C2 | 若必须带用户数据 → **先去标识化**（手机号→哈希、用户名→随机 ID），并在迁移记录中登记处理范围 | 抽样比对确认无原始 PII |
| C3 | 若不需历史数据 → **只迁 schema，用户重新注册**（最干净，PIPL 风险最低） | 新环境 `platform.db` 由 `init_db()` 自建 |
| C4 | 新环境生成全新 `secrets_store/secret_key.txt` | 确认与源机内容不同 |

> **强烈建议 C3**：既然只有 3 个第三方用户，与其承担迁移 PII 的合规风险，不如让用户重新注册。迁移的应该是**系统**，不是**个人信息**。

### Phase D：线上部署

#### 外部依赖清单（线上环境需放行出网）

| 依赖 | 域名 | 用途 |
|---|---|---|
| 智慧树（任务目标站） | `passport.zhihuishu.com`、`hike.zhihuishu.com`、`hike-examstu.zhihuishu.com`、`studyh5.zhihuishu.com`、`studyservice-api.zhihuishu.com`、`onlineservice-api.zhihuishu.com` | 任务引擎目标 |
| Cloudflare | `*.cloudflareaccess.com`、`*.argotunnel.com` | 隧道与访问控制 |
| PushPlus | 数据表 `users.pushplus` 字段存在，但**代码中无任何调用**（僵尸字段） | 无 |

| 步骤 | 动作 | 验证 |
|---|---|---|
| D1 | 云主机上装 cloudflared，用**新建的隧道凭据**（不要复用本机凭据文件） | `cloudflared tunnel run <name>` 连通 |
| D2 | 配置 systemd unit 托管平台 + cloudflared（对标本机 SCM 职责）：`Restart=always`、`RestartSec=5`、`WantedBy=multi-user.target` | `systemctl enable --now` 后 `systemctl status` = `active (running)` |
| D3 | 配置 ingress：`order.<新域名>` → `http://127.0.0.1:8766` | 本机 `curl -H "Host: ..." http://127.0.0.1:8766/health` |
| D4 | **必须重建 Cloudflare Access 应用**，把新域名纳入保护 | 未认证访问新域名应被 302 到 Access 登录页 |
| D5 | 开防火墙：**只出不进**，不暴露任何入站端口 | 外部端口扫描无开放端口 |
| D6 | DNS 切换：把 `order.jiangjiangze.icu` 指到新隧道（灰度或直接切） | 公网 `curl` 返回 302（Access 拦截）= 正常 |

### Phase E：并行运行与切流

| 步骤 | 动作 | 验证 |
|---|---|---|
| E1 | 新环境跑通、旧环境保持在线（**双活一段时间**） | 两端 `/health` 均 200 |
| E2 | 观察期（建议 ≥ 3 天）：无 5xx、任务成功率与旧环境持平 | 比对两端日志 |
| E3 | 确认稳定后，**才**停用本机服务：`python tools/service_manager.py uninstall` | 本机 8766 无监听 |

### Phase F：回滚方案

| 层级 | 回滚动作 |
|---|---|
| 服务配置 | `python tools/service_manager.py uninstall`（删服务），再用 `tools/_rollback/WK_AutoTaskPlatform.task.xml` 恢复计划任务：`schtasks /Create /XML <file> /TN "WK_AutoTaskPlatform"` |
| 手动兜底 | `python tools/service_manager.py console`（前台跑，看实时输出） |
| 隧道 | 用 `tools/_rollback/old_tunnel_processes.txt` 中记录的原命令行直接手工启动 cloudflared |
| 线上切流 | DNS 切回本机隧道 |

---

## 6. 验证方式与验收标准

### 6.1 服务化验收（**本轮已全部通过**）

```bash
cd /d/web
python tools/service_manager.py verify
```

| # | 验收项 | 判据 | 实测 |
|---|---|---|---|
| 1 | 服务存在且运行 | 两个服务 `STATE = RUNNING` | ✅ |
| 2 | 开机自启 | `START_TYPE = AUTO_START (DELAYED)` | ✅ |
| 3 | 运行账户 | `LocalSystem` | ✅ |
| 4 | 失败自愈 | `FAILURE_ACTIONS` = 重启 5s/30s/60s，`..._ON_NONCRASH_FAILURES = TRUE` | ✅ |
| 5 | 冷启动 | 从 `STOPPED` 可 `start` 到 `RUNNING` 且 `/health` 200 | ✅ |
| 6 | 端口监听 | `127.0.0.1:8766` LISTENING | ✅ |
| 7 | 应用健康 | `/health` → `{"status":"ok"}` | ✅ |
| 8 | 隧道连边 | `http://127.0.0.1:20241/ready` → 200 | ✅ |
| 9 | 隧道 HA | `cloudflared_tunnel_ha_connections = 2` | ✅ |
| 10 | 访问控制 | 公网未认证 → 302 到 Cloudflare Access | ✅ |
| 11 | 端到端贯通 | 同隧道域名 `maa.jiangjiangze.icu` → 200 | ✅ |
| 12 | 无孤儿进程 | 停机后 Session 0 无残留 python | ✅ |
| 13 | 崩溃恢复耗时 | 强杀后 ≤ 10 秒恢复 | ✅ ~5 秒 |

**日常巡检一条命令**：`python tools/service_manager.py status`

### 6.2 迁移验收（**待执行**，方案 B）

| # | 验收项 | 判据 |
|---|---|---|
| 1 | 依赖可装 | 纯净环境 `pip install -r requirements.txt` 无错（无 pywin32） |
| 2 | 容器健康 | `docker compose ps` 显示 `healthy` |
| 3 | 开机自启 | 重启云主机后 `systemctl is-active` = `active`，`/health` 自动 200 |
| 4 | 崩溃自愈 | `kill -9` 主进程后由 systemd 自动拉起（对标本机 5 秒指标） |
| 5 | 无 PII 泄漏 | 新环境 `grep -rE "1[3-9][0-9]{9}"` 无真实手机号 |
| 6 | 无密钥泄漏 | 新环境密钥与源机**不同**；仓库中无密钥 |
| 7 | 访问控制 | 未认证访问新域名被 302 到 Access |
| 8 | 无入站端口 | 外部扫描无开放端口（仅出站建连） |
| 9 | 任务可跑通 | 新建一单，任务引擎子进程正常派生并完成 |
| 10 | 并发不超限 | 并发派单不超过 `MAX_CONCURRENCY` |
| 11 | 灰度可用 | DNS 切回本机后 < 5 分钟内恢复服务 |

### 6.3 已知遗留风险

| # | 风险 | 影响 | 建议 |
|---|---|---|---|
| R1 | **合规风险未消解**（G1 未决） | 若对外经营可能触及刑责 | **上线前必须解决，优先级最高** |
| R2 | `PYEXE` 硬编码 | 迁移后任务引擎无法拉起 | Phase A1 修掉 |
| R3 | 无 `requirements.txt` | 新环境依赖不可复现 | Phase A3 补 |
| R4 | SQLite 单写者 | 多实例部署会锁冲突 | 上方案 C 时切 PostgreSQL |
| R5 | 无结构化日志/请求 ID | 线上排障困难 | 迁移时引入 JSON 日志 |
| R6 | 依赖本机在线（方案 A） | 家宽/关机/重启致中断 | 若要 SLA，走方案 B |
| R7 | `maa.jiangjiangze.icu` 无 Access 保护（返回 200） | 该域名对公网开放 | 属另一项目；建议同法加固 |

---

## 附录 A：文件与路径速查

| 用途 | 路径 |
|---|---|
| 服务宿主 | `D:\web\service_platform.py` |
| 服务管理 CLI | `D:\web\tools\service_manager.py` |
| 服务日志 | `D:\web\cf\platform_service.log` |
| 业务主体 | `D:\web\order_platform.py` |
| 数据库 | `D:\web\orders\platform.db` |
| 会话密钥 | `D:\web\secrets_store\secret_key.txt` |
| 隧道配置 | `C:\Users\Administrator\.cloudflared\wk_config.yml` |
| 隧道凭据 | `C:\Users\Administrator\.cloudflared\24f92801-....json` |
| 回滚备份 | `D:\web\tools\_rollback\` |
| 审计报告 | `D:\web\ONLINE_MIGRATION_AUDIT.md` / `.html` |

## 附录 B：常用命令

```bash
# 服务状态 / 验证
python tools/service_manager.py status
python tools/service_manager.py verify

# 重载服务（改完代码后）
python tools/service_manager.py restart platform

# 前台调试（看实时输出，Ctrl+C 退出）
python tools/service_manager.py console

# 查看服务日志
tail -40 D:/web/cf/platform_service.log

# 隧道健康
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:20241/ready
```
