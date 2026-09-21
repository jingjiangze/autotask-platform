# 刷课系统全量审计与线上迁移方案

> **审计对象**：`D:\web`（Windows 单机自动刷课任务平台 + fuckCourse 引擎）
> **审计时间**：2026-09-21 20:57 ~ 21:20（实测）
> **审计性质**：只读审计。**未修改、未删除、未移动任何代码与数据文件。**
> **方法**：静态源码复核 + 数据库只读核验 + 运行时进程/端口/日志/计划任务实测 + 既有档案（PROJECT_FULL_STATE 等）交叉验证
> **标注体系**：`[实测]` 本次运行时直接确证 ｜ `[源码]` 源码确证 ｜ `[档案]` 引自既有文档 ｜ `[推断]` 逻辑推导 ｜ `[需确认]` 须人工或运行验证
> **对照优先级**：源码 ＞ 数据库 ＞ 运行时 ＞ 配置 ＞ 文档

---

## 0. 结论先行（TL;DR）

### 0.1 一句话结论

> **这套系统在工程上已经相当成熟（两轮独立审计 + P0~P3 修复闭环），技术上"搬到云上"是一次中等难度的可移植化改造；但它真正的阻断点不在技术，而在于它当前已经是一个面向公网、已有第三方注册用户的"代刷课"服务——这一步跨过了合规红线，任何迁移方案都必须先解决"能不能做"，再讨论"怎么搬"。**

### 0.2 五个最重要的判断

| # | 判断 | 结论 | 依据 |
|---|---|---|---|
| 1 | 系统是否"本地部署"？ | **否。它已经在公网运行。** 经 Cloudflare Tunnel 暴露为 `https://order.jiangjiangze.icu`，且已有 3 个非管理员注册用户 | `[实测]` cf_state.json / health_manager.log / users 表 |
| 2 | 技术上能否迁移上云？ | 能，但**不是平移**：约 35% 的能力（进程自愈、进程树杀、扫码会话、订单目录）与 Windows 强绑定，必须重写 | `[源码]` health_manager / _spawn / _kill_tree |
| 3 | 最大风险是什么？ | **合规与法律风险，且不可用技术手段消除。** 业务本身（代刷课、代考试答题）违反平台协议与教育主管部门规定；对外提供服务可能触及《刑法》285/286 条 | 见 §7.1 |
| 4 | 当前是否安全？ | 无重大 Web 层漏洞（SQLi/XSS/越权/路径穿越均已封堵），但**凭据明文残留严重**、**无 CSP/安全头**、**注册口令为弱口令**，作为公网服务防护厚度不足 | `[实测]` config.json / 无 after_request / reg_code |
| 5 | 交付节奏建议 | **先做 Stage 0 合规决策闸门（5 天）**，未通过则迁移方案不启动；通过后按 6 周路线图执行 | 见 §9 / §11 |

---

## 1. 系统画像

| 维度 | 现状 |
|---|---|
| 系统定位 | 订单式自动刷课任务平台。用户在 Web 端下单（学习通/知到），平台按单派生隔离子进程调用自动化引擎完成刷课 |
| 承载形态 | **Windows 单机**（本人家用 PC，D 盘 448G / 剩余 321G） |
| 项目根 | `D:\web`（2026-09-13 由会话目录迁入） |
| 平台代码量 | `order_platform.py` 1811 行；`health_manager.py` 487；`backup_manager.py` 170；`tools_query_courses.py` 102；`order_server.py` 305（v1 弃用）——合计 **2875 行** |
| 引擎代码量 | `fuckCourse\` **10300 行** Python，覆盖 学习通(chaoxing) / 知到(zhs) / WE Learn / 雨课堂 四平台 |
| 技术栈 | Python 3.13.12 + Flask 3.1.3 + Waitress 3.0.2(threads=16) + SQLite(WAL) + Tabler 1.5.1(本地 vendor) + cloudflared 2026.9.1 |
| 对外入口 | `http://127.0.0.1:8766`（仅回环）+ `https://order.jiangjiangze.icu`（Cloudflare Tunnel） |
| 数据规模 | 订单 **19** 条（14 done / 5 failed，全终态，时间跨度 2026-09-11 ~ 09-12）；用户 **4** 个；商品 3 个；DB 约 49KB `[实测]` |
| 资源占用 | 平台 32.1MB / 看护 17.5MB / 隧道 19.8MB / 启动器 1.1MB ≈ **70.5MB 常驻** `[实测]` |
| 自启方式 | Windows 计划任务 `WK_AutoTaskPlatform`（登录触发 + 5min 兜底） |
| 演进史 | 2026-09-11 立项 v4 → 09-12 P0 → 09-13 P1/P2 + 迁移 D:\web → 09-14 P3 + 两轮独立审计 + P0×2/P1×5/P2×2 修复闭环 |

### 1.1 功能模块清单（12 个）

| # | 模块 | 职责 | 关键实现 |
|---|---|---|---|
| M1 | 商品橱窗 | 首页商品卡片 + 平台统计 | `/` → products/orders 计数 |
| M2 | 下单向导 | 3 步：登录授权 → 查课勾选 → 提交 | `/buy/<code>` |
| M3 | 查课服务 | 代用户登录并拉取课程列表 | `/api/courses` → 子进程 `tools_query_courses.py`（180s 同步阻塞） |
| M4 | 扫码登录 | 知到二维码出码/轮询/换 token | `/api/qr_start` + `qr_thread` + `/qr/<oid>` + `/qr_status/<oid>` |
| M5 | 订单中心 | 我的订单 / 详情（日志尾 8KB）/ 访客查单 | `/my` `/order/<oid>` `/query` |
| M6 | 批量下单 | CSV 行批量建单 | `/batch` |
| M7 | 管理后台 | 状态看板 / 用户 / 订单 / 系统状态 / 11 项运行参数 / 注册口令 | `/admin` `/admin/tune` `/admin/regcode` |
| M8 | 用户系统 | 注册（注册口令）/ 登录 / 登出 | `/register` `/login` `/logout`；cookie `wk_token` = `uid:hmac(sha256,SECRET,"u"+uid)` |
| M9 | 任务执行 | worker 池 → CAS 领单 → 隔离环境 → 子进程 → 心跳 → 硬超时树杀 → 风控扫描 | `claim_order` / `build_order_env` / `_spawn` / `scan_risk` |
| M10 | 凭据与加密 | 订单密码入库加密；SECRET 外置 | `crypto_manager.py` `enc:v1:` 格式 |
| M11 | 运维自愈 | 看护进程 / 在线备份 / 清理 / QR janitor / 运行期 watchdog | `health_manager.py` / `backup_manager.py` / `housekeeping` / `qr_janitor` / `order_watchdog` |
| M12 | 风控对抗 | UA 池指纹伪装 / 代理池 / 倍速抖动 / 风控信号采集 | `build_order_env`（4 套 UA）+ `scan_risk`（captcha/403/risk_ctrl/login_fail/network） |

---

## 2. 技术架构

### 2.1 运行形态与进程链路（2026-09-21 实测）

```
Windows 计划任务 WK_AutoTaskPlatform（登录 + 每 5min 兜底）
   │
   └─ pythonw health_manager.py            PID 40060   17.5 MB   ← 看护（持有 cf\health_manager.lock: pid=40060）
        ├─ cloudflared.exe                  PID  3172   19.8 MB   ← 隧道（--metrics 127.0.0.1:20241）
        │    └─ 出站 QUIC ──▶ Cloudflare Edge ──▶ https://order.jiangjiangze.icu
        └─ pythonw order_platform.py （启动器父）PID 41028   1.1 MB
             └─ pythonw order_platform.py（实际服务）PID 46808  32.1 MB   ← LISTEN 127.0.0.1:8766
                  ├─ Waitress WSGI 线程池 ×16
                  ├─ concurrency_manager  → worker 线程 ×N（settings.concurrency=4）
                  ├─ qr_janitor（120s）/ housekeeping（1800s）/ order_watchdog（30s）
                  └─ worker → venv python.exe 子进程（BELOW_NORMAL | CREATE_NO_WINDOW）
                       └─ fuckCourse 引擎（chaoxing / zhs）
```

**同时发现一对空转残留进程** `[实测]`：

```
pythonw PID 29516 (1.5MB) ──▶ pythonw PID 31740 (0.8MB)   ← 既未持锁、也未持端口，纯空转
```

这与既有档案记录的"pythonw 以「启动器父 + 活跃子」成对出现，偶发残留对"现象完全一致 `[档案，本次复现]`。消耗可忽略（2.3MB），但属于需要纳入迁移方案的"本机进程模型不可靠"证据。

### 2.2 分层与模块依赖

```
                  ┌──────────────── 浏览器（Tabler 暗色单页模板）─────────────┐
                  ▼                                                        │
   ┌──────────────────────── order_platform.py（Flask 1811 行）──────────────┐
   │  HTTP 层   @app.route ×18（UI 渲染由 page() 字符串模板拼接）             │
   │  业务层    claim_order / build_order_env / scan_risk / recover_stale    │
   │  存储层    db() 每次新建连接（WAL / busy_timeout=15000）                 │
   │  进程层    _spawn / _kill_tree / _pid_alive（Windows 专有）             │
   └───┬───────────────┬────────────────┬───────────────┬──────────────────┘
       │               │                │               │
   平台 DB         订单目录         tools_query      引擎子进程
   orders\          orders\<id>\    _courses.py      fuckCourse\
   platform.db      {log,cookies,   （查课，180s）     （刷课，10300 行）
                     work,tmp,home}
       ▲               ▲
       │               │
   backup_manager  housekeeping（3 天 rmtree / 1h 清 _query）
   health_manager（30s 轮询 /health + 隧道存活 + 每日补备份）
```

**关键耦合点**：`health_manager` 是唯一常驻入口，`order_platform` 由它 `Popen` 拉起——这构成"进程自愈"能力，但也意味着**迁移时必须换成 systemd/supervisor/容器编排**，不能直接搬运。

### 2.3 Web / API 全量路由（18 条，源码实测）

| 路由 | 方法 | 功能 | 权限 | 备注 |
|---|---|---|---|---|
| `/` | GET | 首页商品橱窗 | 公开 | — |
| `/buy/<code>` | GET/POST | 下单向导 | 登录 | 密码入库前加密 |
| `/api/courses` | POST | 查课 | 登录 + 限流 12/min | 子进程阻塞最长 180s，`BoundedSemaphore(2)` 护栏 |
| `/api/qr_start` | POST | 生成知到二维码 | 登录 + 限流 6/min | 建 waiting_qr 订单 |
| `/order/<oid>/set_courses` | POST | 扫码后选课提交 | 登录 + 归属校验 | — |
| `/query` | GET/POST | 访客查单（单号前 8 位） | 公开 | 仅返回概要，无账号字段 |
| `/my` | GET | 我的订单 | 登录 | — |
| `/order/<oid>` | GET | 订单详情（读日志尾 8KB） | 登录 + 归属校验 | 已做 HTML 转义 |
| `/qr/<oid>` | GET | 二维码图 | 登录 | P3 加固 |
| `/qr_status/<oid>` | GET | 扫码状态轮询 | 登录 | 前端 1.5s 轮询 |
| `/batch` | GET/POST | 批量下单 | 登录 | — |
| `/admin` | GET | 管理后台 | 登录 + is_admin | 含 tasklist 调用（已加超时） |
| `/admin/tune` | POST | 改 11 项运行参数 | 登录 + is_admin | — |
| `/admin/regcode` | POST | 改注册口令 | 登录 + is_admin | — |
| `/register` | GET/POST | 注册 | 限流 5/min + 注册口令 | 口令当前为 `【注册口令·已脱敏】`（弱） |
| `/login` | GET/POST | 登录 | 限流 10/min | — |
| `/logout` | GET | 登出 | — | — |
| `/health` | GET | 健康检查（非敏感） | 公开 | 实测 `{"status":"ok","database":"ok","pending":0,"running":0}` `[实测]` |

**无**文件上传、无文件下载、无用户可控路径接口。

### 2.4 任务执行链（核心链路）

```
worker 线程循环
 └─ claim_order()   ← 单事务 CAS：SELECT pending 首行 + UPDATE ... WHERE worker_running=0 + rowcount 判定
     └─ build_order_env()  → orders\<id>\{work,tmp,home,logs} + HOME/TEMP/USERPROFILE 重定向
     │                     + WK_UA 指纹（4 套 UA 池随机）+ 可选代理 + 倍速抖动
     └─ run_chaoxing() / run_zhs()
     │   └─ _spawn()  → Popen(venv python, BELOW_NORMAL|CREATE_NO_WINDOW, stdout→RollingLog 线程)
     │       ├─ 20s 心跳写库（heartbeat_at）
     │       ├─ 硬超时 180min → _kill_tree(taskkill /F /T + 5s 轮询验证)
     │       └─ scan_risk()  正则扫日志尾 → risk_flags(captcha/403/risk_ctrl/login_fail/network)
     └─ set_order() → 状态落库 → finally 清 worker_running
```

**状态机**：`pending ⇢(CAS claim)⇢ running → done | failed`；`waiting_qr → pending | canceled`；启动时 `recover_stale_orders()` 收敛遗留态；运行期 `order_watchdog()`（30s）收敛"DB=running 但 pid 已死"。**前两轮审计的 P0-1/P0-2 均已修复落地** `[源码: order_watchdog h788]`

---

## 3. 数据存储

### 3.1 SQLite Schema（本次只读实测）

```
users    (id, username UNIQUE, pw_hash, is_admin, created_at, pushplus)          ← 6 列（pushplus 为 v5 遗留列）
orders   (id PK, user_id, platform, account, password, courses, status, note,
          qr_state, created_at, started_at, finished_at, exit_code, worker_running,
          product, speed, env_profile, risk_flags, pid, attempt, heartbeat_at)   ← 21 列
products (id, code UNIQUE, name, desc, price, platform, enabled, sort)            ← 8 列
settings (key PK, value)                                                          ← 2 列
```

| 属性 | 实测值 | 迁移影响 |
|---|---|---|
| journal_mode | `wal` | 云上 PostgreSQL 需替换并发模型 |
| foreign_keys | `0`（代码未启用） | 迁移时可顺带补 FK |
| page_size | `4096` | 无影响 |
| 索引 | 仅 4 个主键 autoindex | 迁移后需为 `orders.status` / `user_id` / `created_at` 补索引 |
| 约束 | 除 username/code/key 唯一外，**无 CHECK / FK / ENUM** | status 可被写入任意值 `[源码]` |
| 加密 | `orders.password` 全部 `enc:v1:` 前缀，**0 条明文** `[实测]` | 迁移需保留解密能力或全量重加密 |
| 明文密码 | 0 条 `[实测]` | ✅ |
| 库体积 | 49KB + WAL 138KB `[实测]` | 极小，可整库导出 |
| 备份 | `backups\platform_YYYYMMDD_HHMMSS.db` × **7 份**，`[实测]` 最近 `platform_20260921_184257.db`（每日 18:41~18:42 自动生成，说明 housekeeping 正常运行） | 迁移后改对象存储快照 + 保留策略 |

### 3.2 文件系统数据（非 DB 状态）

| 路径 | 内容 | 体积 `[实测]` | 生命周期 | 迁移处置 |
|---|---|---|---|---|
| `orders\<id>\` | 每单 log.txt / cookies.json / work / tmp / home / logs | 233KB 总量 | housekeeping **3 天** rmtree | → 对象存储 / 临时卷 |
| `orders\_query\` | 查课临时 cookie | 极小 | **1 小时** | → 内存/Redis |
| `backups\` | 7 份 DB 快照 | 340KB | 滚动 7 份 | → 对象存储版本化 |
| `secrets_store\` | `secret_key.txt`(29B) / `recovery_key.txt` / `recovery_key.bundle.json` / `_admin_pw_旧实验残留.txt` / `_archive_dev\` | 26KB | 永久 | → KMS / Secrets Manager |
| `cf\` | cloudflared.exe(55MB) / 日志 / browser_profile / 大量开发残留 | **140MB** | 混合 | 大部分不迁移 |
| `fuckCourse\` | 引擎源码 + `config.json`(明文凭据) + `cookies.json`(含 token) + `.zhs_cred` | 2.9MB | — | config 改为纯环境变量注入 |
| `_archive_dev\` | 185 个开发残留（含 `_cf_login*.txt` / `_cx_login*.txt` 等登录过程记录） | **23MB** | 永久 | **不迁移**，且应清理 |
| `_hist\` | **`hist_chrome.db` 8.8MB + `hist_edge.db` 256KB**（浏览器历史数据库） | 8.7MB | 永久 | **不迁移**，属隐私数据 |
| `tabler_pkg\` | Tabler 包：22MB | 22MB | — | 仅需 `static\vendor` 3.8MB |
| `static\` | Tabler vendor 资源（已本地化，无外网 CDN） | 3.8MB | — | → CDN 或静态托管 |

---

## 4. 运行环境与外部依赖

### 4.1 本机环境绑定项（**迁移阻断点**）`[源码实测]`

| 绑定项 | 位置 | 迁移处置 |
|---|---|---|
| `PYEXE` 绝对路径 | `order_platform.py:33` = `C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\python.exe` | 改为 `os.environ["WK_PYTHON"]` 或容器内 `sys.executable` |
| 端口 8766 硬编码 | `h1809` / `h1811` | 改为环境变量 |
| 路径 `D:\web` | `cf\启动平台和隧道.bat` / `tools\*.ps1` | 改为相对脚本位置解析 |
| `DETACHED_PROCESS \| CREATE_NEW_PROCESS_GROUP \| CREATE_NO_WINDOW` | `health_manager.py:167` | Windows 专有 → systemd/docker |
| `BELOW_NORMAL_PRIORITY_CLASS` | `_spawn` | Linux 用 `nice` / cgroup 权重 |
| `taskkill /F /T` 树杀 | `_kill_tree` | Linux 用进程组 `killpg` / cgroup |
| `OpenProcess(SYNCHRONIZE)` 判活 | `_pid_alive` | Linux 用 `/proc/<pid>` |
| Windows 计划任务 | `WK_AutoTaskPlatform` | cron / systemd timer / K8s CronJob |
| 锁文件 + 单实例 | `cf\health_manager.lock` | Redis 分布式锁 / K8s Lease |
| 内存护栏 `GlobalMemoryStatusEx` | `free_mem_mb` | cgroup memory limit / 容器 requests-limits |
| 二维码会话内存字典 `QR_SESSIONS` | 进程内 | **多副本后会失效 → 必须迁 Redis** |
| 限流内存字典 `_RATE` | 进程内 | 多副本后失效 → **必须迁 Redis** |

### 4.2 外部服务依赖矩阵

| 外部服务 | 用途 | 认证方式 | 当前状态 | 风险 |
|---|---|---|---|---|
| 学习通（超星）API | 登录 / 课程 / 视频 / 签到 / 章节答题 | 账号密码 + cookies | 引擎内调用 `[档案:实测刷完]` | **核心法律风险源** |
| 知到（智慧树）passport / hike / onlineservice | 扫码登录 / 课件 / 弹题 / AI 考试 | 二维码 + cookies | 引擎内调用 | 同上 |
| 第三方题库 TikuGo | 章节检测自动答题 | 内置 | `submit=true, cover_rate=0.5` | 同上 |
| 知到官方 AI / 月之暗面 / OpenAI | AI 课考试作答 | API Key | **占位 key（3 字符），未启用** | 低 |
| PushPlus / Bark | 完成通知 | token | **均 `enable:false`**（Bark token 占位 29 字符） | 低 |
| ddddocr | 本地验证码 OCR | 本地模型 | 已安装 1.6.1 | 低 |
| **Cloudflare Tunnel + DNS** | 公网入口 | cert.pem + tunnel 凭据 | 运行中，tunnel `24f92801-…` | **迁移需重配或改直连** |
| 域名 `jiangjiangze.icu` | 入口域名 | Cloudflare 托管 | 在线 | 国内云需 ICP 备案 |
| Windows 计划任务 | 自启 | 系统级 | 已配置 | 迁移替换 |

---

## 5. 风险评估

### 5.1 【P0 · 最高】合规与法律风险 —— 技术手段无法消除

> 这是本次审计的最重要结论。请在阅读技术方案前先读完本节。

| # | 风险 | 说明 | 严重度 |
|---|---|---|---|
| C1 | **服务性质违规** | 系统对"学习通/知到"课程视频进行自动化播放、进度伪造，并对章节测验/考试自动答题。这直接违反两平台的用户协议，且属于教育主管部门明令禁止的"刷课"行为 | 🔴 极高 |
| C2 | **向第三方提供服务 = 性质升级** | `users` 表已有 `chatgpt`、`user2@example.com`、`wcnb` 三个非管理员账号（注册于 09-14 / 09-20）`[实测]`，且 `/register` 当前开放（口令 `【注册口令·已脱敏】`）。**性质已从"自己用"变为"对外提供工具/服务"**，法律风险量级显著跃升 | 🔴 极高 |
| C3 | **可能触及刑事责任** | 对外提供专门用于侵入/非法控制计算机信息系统的程序工具，或非法获取计算机信息系统数据、破坏计算机信息系统功能，可能对应《刑法》第 285 条、第 285 条之三、第 286 条。**"上线公网 + 对外可注册 + 有实际用户"是构罪评价中极为不利的情节** | 🔴 极高 |
| C4 | **个人信息保护（PIPL）** | 平台收集并存储学生的**平台账号 + 密码**（`orders.account` / `orders.password`）、课程清单、手机号（`13000000000` 出现在 `config.json` / `.zhs_cred` 明文中）。无隐私政策、无用户授权书、无数据处理者告知、无最小化措施。一旦发生泄露，处罚与民事责任直接落在运营者身上 | 🔴 极高 |
| C5 | **数据跨境** | Cloudflare 作为前置/CDN，流量与日志经境外节点；学生个人信息存在跨境传输环节，未做安全评估 | 🟠 高 |
| C6 | **学术诚信** | 自动答题 / 自动考试（`zhs.ai` 已武装）已被集成。若平台方追溯，涉及的是学生的学业处分与学校的学术不端认定 | 🟠 高 |
| C7 | **Cloudflare 服务条款** | 将此类用途用于 CF Tunnel/网络可能触发 CF 的 AUP 违规，账号有被停风险 | 🟡 中 |
| C8 | **无经营资质** | 当前"测试免费"，一旦转为收费，将叠加无照经营 / 非法经营风险 | 🟡 中 |

**必须由决策人明确选择的三条路径（三选一，无法回避）：**

| 路径 | 内容 | 技术方案 |
|---|---|---|
| **A · 内部自用化（推荐，风险最低）** | 关闭 `/register`、清退第三方账号、撤下公网入口（仅保留本机/内网访问或加 Cloudflare Access 白名单），定位为"个人学习辅助工具" | 迁移方案可大幅简化，甚至**无需上云** |
| **B · 全面停止对外服务** | 停止平台运行，保留引擎作为个人研究/学习用途 | 不需要迁移方案 |
| **C · 继续对外经营** | 若坚持走这条路，**必须**先完成：合规法律意见、ICP 备案、等保测评、隐私政策与用户授权、数据处理协议、实名与责任划分 | 才轮到本方案 §10~§14 的技术迁移 |

> ⚠️ **审计建议**：在 Stage 0 闸门未明确选择之前，不应启动任何迁移实施工作。技术迁移会让系统更难撤回、更容易被追溯。

### 5.2 安全风险

| # | 风险 | 等级 | 证据 | 迁移时是否修复 |
|---|---|---|---|---|
| S1 | **凭据明文残留多处** | 🟠 高 | `[实测]` `fuckCourse\config.json` 含明文账号 `13000000000` + 12 位明文密码；`.zhs_cred`(25B) 含手机号；`cookies.json`(6.7KB) 含 token；`secrets_store\_admin_pw_旧实验残留.txt`；`_archive_dev` 185 个含登录过程的文本 | **必须** |
| S2 | **浏览器历史数据库留存** | 🟠 高 | `[实测]` `_hist\hist_chrome.db` 8.8MB / `hist_edge.db` 256KB —— 与业务无关的隐私数据 | **必须**（不迁移 + 清理） |
| S3 | 无 CSP / X-Frame-Options / HSTS 等安全响应头 | 🟠 高 | `[源码]` 无 `after_request` 钩子（仅 `set_cookie` 一处） | **必须** |
| S4 | 注册口令为弱口令 | 🟠 高 | `[实测]` `settings.reg_code = 【注册口令·已脱敏】` | **必须** |
| S5 | cookie 无 `Secure` / `SameSite` | 🟡 中 | `[源码: h1766]` 仅 `httponly=True` | **必须** |
| S6 | 无 CSRF Token（仅 Origin/Referer 校验，无头客户端放行） | 🟡 中 | `[源码] _csrf_guard` / `same_origin_ok` | 建议 |
| S7 | 加密为非标准方案 | 🟡 中 | `[源码] enc:v1:` = 随机 nonce + HMAC-SHA256 派生 keystream 异或；口令哈希使用**静态盐 `"wk"`**；无 per-user salt | 建议（新数据） |
| S8 | SECRET 轮换 = 全库口令失效 + 存量密码不可解密 | 🟡 中 | `[源码]` `secret_key.txt` 为唯一密钥基 | 迁移时用 KMS 托管 + 双密钥过渡 |
| S9 | 无 MFA / 无登录失败锁定 / 无审计日志 | 🟡 中 | `[源码]` 仅内存限流 | 建议 |
| S10 | 限流与扫码会话为**进程内**状态 | 🟡 中 | `_RATE` / `QR_SESSIONS` 均为 dict | **必须**（多副本） |
| S11 | v1 弃用服务 `order_server.py`(8765) 与 v5 备份仍与线上库同路径 | 🟢 低 | `[实测]` 文件存在 | 建议清理 |
| S12 | 无优雅停机 / 无 `atexit` | 🟢 低 | `[源码]` | 建议 |

**已确认修复（本轮复核通过，无需重做）**：SQL 注入（全参数化）、存储型 XSS（`esc()` 16 处）、越权/IDOR（归属校验）、路径穿越（无用户路径接口）、数据库下载（无下载路由）、运行期卡死（`order_watchdog` 30s）、恢复路径 kill 校验。

### 5.3 性能与容量风险

| # | 风险 | 等级 | 说明 |
|---|---|---|---|
| P1 | **本机家宽 + 家用 PC 单点** | 🟠 高 | 无冗余；断电/休眠/网络抖动 = 全站不可用；上传带宽受家宽限制 |
| P2 | 查课接口同步阻塞最长 180s | 🟡 中 | 已加 `BoundedSemaphore(2)`，但仍是同步模型 |
| P3 | SQLite 单写者模型 | 🟡 中 | 高并发写依赖 `busy_timeout=15000` 缓解；并发上限低 |
| P4 | 每单一个 Python 进程 | 🟡 中 | 引擎冷启动开销大（依赖 ddddocr/onnx 等）；4 并发 ≈ 160~320MB |
| P5 | 无连接池、无索引（status/user_id/created_at） | 🟡 中 | 数据量小暂无感，云上需补 |
| P6 | 无异地备份 | 🟡 中 | 备份与主库同机同盘 |
| P7 | Waitress 长稳（内存是否增长）未验证 | 🟡 中 | `[档案:需正式机观察]` |

### 5.4 可用性与运维风险

| # | 风险 | 等级 | 说明 |
|---|---|---|---|
| A1 | **本机进程模型不可靠（已复现）** | 🟠 高 | `[实测]` 当前存在一对空转残留 pythonw（PID 29516/31740）；档案记载多次出现 4MB 僵尸 |
| A2 | 隧道 metrics 端口曾冲突 | 🟡 中 | `[实测]` `cloudflared.log`：`bind 127.0.0.1:20241 failed` → 曾出现重复 cloudflared 实例；最近重启 2026-09-21 04:14:36 |
| A3 | 无结构化日志 / 无统一 traceId | 🟡 中 | 订单日志为引擎原样 print 输出 |
| A4 | 审计/操作留痕缺失 | 🟡 中 | 管理后台改参数无记录 |
| A5 | 引擎日志含账号名，无脱敏层 | 🟡 中 | 3 天清理期内本机可读 |
| A6 | 无监控告警（仅 /health 被动探测） | 🟡 中 | 无 CPU/磁盘/队列长度告警 |

### 5.5 风险总览矩阵

| 风险域 | 数量 | 最高等级 | 迁移能否消除 |
|---|---|---|---|
| 合规与法律 | 8 | 🔴 极高 | ❌ **不能**，只能靠决策规避 |
| 安全 | 12 | 🟠 高 | ✅ 能（列入迁移验收项） |
| 性能容量 | 7 | 🟠 高 | ⚠️ 部分（上云解决单点，架构需改造） |
| 可用性运维 | 6 | 🟠 高 | ✅ 能（替换为云原生机型） |

---

## 6. 可迁移性评估（逐模块）

| 模块 | 现行实现 | 云上目标 | 迁移难度 | 是否阻断 |
|---|---|---|---|---|
| M1 商品橱窗 / M2 下单向导 / M5 订单中心 / M6 批量下单 | Flask + 字符串模板 | 原样迁移（补安全头 + 模板引擎化） | ⭐ 低 | 否 |
| M7 管理后台 | Flask + `tasklist` 调用 | 迁移；`tasklist` → 容器 API / cAdvisor | ⭐⭐ 中 | 否 |
| M8 用户系统 | 自研 cookie + HMAC | 迁移（补 MFA / 审计 / 锁定） | ⭐⭐ 中 | 否 |
| M3 查课服务 | 同步 subprocess 180s | 改异步任务队列 | ⭐⭐⭐ 中高 | 否 |
| M4 扫码登录 | `QR_SESSIONS` 内存 dict | **必须**迁 Redis | ⭐⭐⭐ 中高 | **是**（多副本前提） |
| M9 任务执行 | worker 线程 + Windows 子进程树杀 | **重写**：消息队列 + 容器化 Job / K8s Job | ⭐⭐⭐⭐ 高 | **是** |
| M10 凭据加密 | 自研 `enc:v1:` + 文件 SECRET | 迁移 + KMS + 信封加密 + 密钥轮换 | ⭐⭐⭐ 中高 | 否 |
| M11 运维自愈 | `health_manager` 进程看护 | **整段替换**为 systemd/K8s + 探针 + 告警 | ⭐⭐⭐⭐ 高 | **是** |
| M12 风控对抗（UA/代理/抖动） | 环境变量注入 | 可迁移，但**合规上应重新评估是否保留** | ⭐⭐ 中 | 合规阻断 |
| 引擎 `fuckCourse` | 本地 Python 10300 行，CLI 契约 | 打成容器镜像，CLI 契约可保持 | ⭐⭐⭐ 中高 | **合规阻断** |
| 数据存储 | SQLite + 本地目录 | PostgreSQL/MySQL + 对象存储 + Redis | ⭐⭐⭐ 中高 | 否 |
| 备份恢复 | 本地文件 + `sqlite3` backup API | 托管备份 + 对象存储版本化 + 异地 | ⭐⭐ 中 | 否 |
| 公网入口 | Cloudflare Tunnel | 直连 ALB + WAF，或保留 Tunnel 指向云主机 | ⭐⭐ 中 | 否 |

**汇总**：可直接迁移 ≈ 45%，需改造迁移 ≈ 40%，**因合规不应迁移 ≈ 15%（引擎业务本体 + 风控对抗）。**

---

## 7. 前置决策闸门（Stage 0）

> **以下 5 项必须由决策人明确回答，未决则迁移方案不启动。**

| # | 决策项 | 选项 | 影响 |
|---|---|---|---|
| G1 | **服务性质定位** | A 内部自用 / B 停止 / C 对外经营 | 决定是否还需要云迁移。**A 与 C 的方案复杂度相差 5 倍以上** |
| G2 | 是否保留公网入口 | 关闭 / 保留 + Cloudflare Access 白名单 / 完全开放 | 决定安全投入 |
| G3 | 目标用户与规模 | 仅本人 / 小圈子（<10 人）/ >100 人 | 决定实例规格与架构档位 |
| G4 | 是否收费 | 免费 / 收费 | 收费则叠加经营资质与税务问题 |
| G5 | 数据保留与删除策略 | 保留天数 / 是否留存学生密码 | **建议：不再留存明文可解密码，改一次性凭据** |

---

## 8. 目标线上架构（三档方案）

### 方案 A · 保守最小化（仅当 G1 选 A「内部自用」）

```
本机/内网 ──▶ Cloudflare Access（身份白名单）
                  └──▶ Cloudflare Tunnel ──▶ 本机 127.0.0.1:8766
```
- **不做云迁移**。仅加固：关闭 `/register`、清退第三方用户、清弱口令、补安全头。
- 工作量：**1~2 人日**。不满足"全面线上运行"诉求，但风险最低。

### 方案 B · 标准云托管（推荐，适用于 G1 选 A/C 且规模 ≤ 50 人）

```
                    ┌──────────── CDN / WAF ───────────┐
   用户 ──HTTPS──▶  │  Cloudflare（保留）或 云厂商 ALB  │
                    └───────────────┬──────────────────┘
                                    ▼
                     ┌───────────────────────────────┐
                     │  应用层 · 2 副本（容器）        │
                     │  Flask + Waitress / Gunicorn   │
                     │  无状态：会话/限流/QR → Redis   │
                     └───────┬───────────────┬───────┘
                             │               │
                   ┌─────────▼─────┐  ┌──────▼──────────┐
                   │ 托管 PostgreSQL│  │  Redis（会话/   │
                   │ （主库 + 备份） │  │  限流/QR/队列）  │
                   └───────────────┘  └──────┬──────────┘
                                             │ 任务投递
                                    ┌────────▼──────────┐
                                    │ Worker 池（按需伸缩）│
                                    │ 每单 1 容器 Job    │
                                    │ 挂载临时卷 + 引擎镜像│
                                    └────────┬──────────┘
                                             ▼
                                  对象存储（订单日志 / 备份）
                                  密钥管理 KMS（SECRET / 凭据）
                                  监控告警 + 结构化日志
```
- 关键改造：状态外置（Redis）、DB 换 PostgreSQL、worker 容器化、KMS 托管密钥、对象存储承接订单产物、监控告警。

### 方案 C · 全 Serverless（仅在规模波动极端时）

- 应用层 → 函数计算 / 容器实例；DB → Serverless PG；队列 → 云消息队列；任务 → 按需容器任务。
- 优点：免运维、弹性。缺点：**引擎镜像冷启动 5~15s（含 onnx/ddddocr 依赖）对短任务极不经济**，且队列与容器编排复杂度反而更高。**不推荐**。

**推荐：方案 B。**

---

## 9. 迁移路线图

> 时间锚点：**2026-09-21（D0）**。总工期约 **6 周**。每阶段均设"通过则前进、不通过则回退"闸门。

### Stage 0 · 合规与目标决策闸门 ｜ D1~D5（09-22 ~ 09-26）

| 任务 | 产出 | 验收标准 |
|---|---|---|
| 完成 §7 的 G1~G5 决策 | 《迁移目标确认书》 | 5 项决策全部书面确认，签字 |
| 若选 C：取得法律意见；确认是否需要 ICP 备案 / 等保 | 《合规评估意见书》/ 备案受理回执 | 明确"可以对外提供服务"的书面依据 |
| 冻结当前状态 | 全量快照（代码 + DB + secrets 清单 + 配置） | 快照可独立还原；SHA256 校验通过 |

**闸门**：未取得"可以对外提供服务"的书面依据 → **不进入 Stage 1**。

### Stage 1 · 代码可移植化改造 ｜ D6~D19（09-27 ~ 10-10）

| 任务 | 产出 | 验收标准 |
|---|---|---|
| 抽离硬编码（PYEXE / 8766 / D:\web）→ 环境变量 + 配置校验启动即失败 | `config.py` + `.env.example` | `grep -r "D:\\\\web\|8766"` 在业务代码中 0 命中 |
| 状态外置：`QR_SESSIONS` / `_RATE` → Redis；会话令牌统一 | Redis 适配层 | 起 2 个应用副本，扫码/限流行为正确 |
| 进程模型抽象：`_spawn` / `_kill_tree` / `_pid_alive` → 平台无关接口（Linux 用进程组；容器用 K8s Job） | `runtime/` 抽象层 | 同一套业务代码在 Windows 与 Linux 均通过单测 |
| `health_manager` → systemd/K8s 探针 + 存活/就绪/启动探针 | 部署清单 | 杀进程后 30s 内自动恢复，行为与本地看护等价 |
| 查课改异步（任务表 + 轮询接口） | 异步任务链路 | 并发 8 路查课，P95 < 3s，无线程耗尽 |
| 补安全头（CSP / X-Frame-Options / HSTS / Referrer-Policy）+ cookie `Secure`/`SameSite` | `after_request` 钩子 | `curl -I` 校验全部头存在 |
| 补索引（status / user_id / created_at）+ CHECK 约束迁移脚本 | Alembic 迁移 | 迁移在临时库上可正向/反向执行 |
| 凭据治理：移除全部明文残留；引擎 config 改纯环境变量注入 | 清理清单 + 代码改动 | 全盘扫描 0 明文凭据 |

**闸门**：单元测试 + 集成测试全绿；`grep` 无硬编码残留；Linux 下可启动。

### Stage 2 · 数据层迁移准备 ｜ D20~D26（10-11 ~ 10-17）

| 任务 | 产出 | 验收标准 |
|---|---|---|
| SQLite → PostgreSQL/MySQL 转换脚本（含 `enc:v1:` 密文原样搬迁） | 迁移脚本 + 反向脚本 | 19 单 / 4 用户 / 3 商品 / 14 设置**全字段逐行比对一致** |
| 密钥迁移到 KMS；建立双密钥过渡（老 SECRET 可解存量，新数据用新方案） | KMS 配置 + 解密回退逻辑 | 存量订单密码可解密；新订单使用新方案 |
| 订单目录产物 → 对象存储（日志/ cookies） | 存储适配层 | 详情页读取日志行为不变 |
| 备份策略改造（托管备份 + 对象存储版本化 + 异地副本） | 备份方案文档 | 完成一次"恢复到全新环境"演练，RTO < 30min |

**闸门**：**一次完整恢复演练通过**（在全新空环境从备份恢复并跑通下单→执行→完成）。

### Stage 3 · 云上环境搭建与部署 ｜ D27~D40（10-18 ~ 10-31）

| 任务 | 产出 | 验收标准 |
|---|---|---|
| 采购云资源、网络规划（VPC/安全组，DB 与 Redis 不暴露公网） | 资源清单 + 拓扑图 | 安全组仅放行 443/80；DB 仅内网可达 |
| 引擎容器镜像构建（含 onnx/ddddocr 依赖） | Dockerfile + 镜像 | 镜像冷启动 < 10s；离线环境可运行 |
| 应用层部署 2 副本 + Worker 池 + 队列 | 部署清单 | 滚动发布零中断 |
| 入口配置（保留 Tunnel 指向云主机 或 切换 ALB + WAF + 证书） | 入口清单 | HTTPS 正常；证书自动续期 |
| 接入监控告警（CPU/内存/队列长度/失败率/隧道状态） | 告警规则 | 人为制造故障 3 分钟内收到告警 |
| 结构化日志 + traceId 贯穿 | 日志方案 | 一条请求可跨应用/worker 全链路检索 |

**闸门**：生产就绪评审通过（健康检查、优雅停机、限流、WAF、备份、告警、回滚全部就绪）。

### Stage 4 · 灰度与验收 ｜ D41~D47（11-01 ~ 11-07）

| 任务 | 产出 | 验收标准 |
|---|---|---|
| 双跑：本机与云上并行，影子流量比对 | 比对报告 | 键路径响应一致性 ≥ 99% |
| 灰度：10% → 50% → 100% 用户切换 | 灰度记录 | 每档观察 ≥ 24h，错误率无上升 |
| 全量功能回归 | 回归报告 | 见 §12 验收清单全项通过 |
| 压测 | 压测报告 | 见 §12 性能指标 |

**闸门**：验收清单 100% 通过 → 进入 Stage 5；任一 P0/P1 未过 → 回滚。

### Stage 5 · 本机下线与归档 ｜ D48~D52（11-08 ~ 11-12）

| 任务 | 产出 | 验收标准 |
|---|---|---|
| 双跑观察 7 天后关闭本机平台与隧道 | 下线记录 | 云上无异常 |
| 数据与代码归档（含密钥托管） | 归档包 | 可复现 |
| 清理本机敏感残留（`_hist` 浏览器历史、`_archive_dev` 登录日志、明文凭据） | 清理记录 | 全盘 0 明文凭据 / 0 隐私数据 |
| 计划任务 / VBS 自启移除 | 系统清单 | 无残留自启项 |

---

## 10. 资源需求

### 10.1 云资源（方案 B，≤50 人规模）

| 资源 | 规格 | 数量 | 说明 |
|---|---|---|---|
| 应用实例 | 2 vCPU / 4 GB | ×2（多可用区） | 无状态，可弹性 |
| Worker | 2 vCPU / 4 GB | ×2 常驻 + 按需 | 每单 1 容器，可按队列伸缩 |
| 托管数据库 | PostgreSQL 2 vCPU / 4 GB / 100 GB SSD | ×1 | 开启自动备份 + 只读副本（可选） |
| Redis | 1 GB 基础版 | ×1 | 会话 / 限流 / QR / 队列 |
| 对象存储 | 50 GB + 版本化 | ×1 | 订单日志、备份 |
| 密钥管理 | KMS | ×1 | SECRET / 凭据信封加密 |
| 入口 | Cloudflare（保留）或 ALB + WAF | ×1 | 证书自动续期 |
| 监控 | 云监控 + 日志服务 | ×1 | 告警 |
| 域名/备案 | 已有 `jiangjiangze.icu` | — | 国内云需 **ICP 备案（10~20 工作日）** |

**成本量级**：约 **¥150 ~ ¥400 / 月**（国内轻量云口径）；若选境外节点另计，但需重新评估数据跨境与合规（见 C5）。

### 10.2 人力

| 角色 | 投入 | 阶段 |
|---|---|---|
| 全栈开发 | 全程主责 | Stage 1~5 |
| 运维/SRE | 兼职 | Stage 3~5 |
| 法务/合规顾问 | 一次性 | Stage 0（**必要条件**） |
| 测试 | 兼职 | Stage 1 / 4 |

---

## 11. 时间节点总表

| 阶段 | 起止 | 里程碑 | 关键交付 |
|---|---|---|---|
| Stage 0 合规决策 | 09-22 ~ 09-26 | **M0 目标确认书** | 5 项决策 + 法律意见 + 全量快照 |
| Stage 1 可移植化 | 09-27 ~ 10-10 | **M1 Linux 可运行** | 无硬编码 + 状态外置 + 安全头 + 迁移脚本 |
| Stage 2 数据迁移 | 10-11 ~ 10-17 | **M2 恢复演练通过** | 数据一致 + KMS + 备份异地 |
| Stage 3 云上部署 | 10-18 ~ 10-31 | **M3 生产就绪评审** | 部署完成 + 监控告警 + WAF |
| Stage 4 灰度验收 | 11-01 ~ 11-07 | **M4 全量切流** | 灰度记录 + 回归 + 压测 |
| Stage 5 下线归档 | 11-08 ~ 11-12 | **M5 本机下线** | 归档 + 敏感数据清理 |

---

## 12. 验收标准

### 12.1 功能验收（逐项）

| # | 验收项 | 通过标准 |
|---|---|---|
| F1 | 健康检查 | `GET /health` 返回 200 且 `database=ok` |
| F2 | 注册 / 登录 / 登出 | 弱口令拒绝；口令错误限流生效；cookie 具备 HttpOnly + Secure + SameSite |
| F3 | 下单向导 3 步 | 学习通账密 / 知到账密 / 知到扫码三条路径均可完成建单 |
| F4 | 查课 | chaoxing / zhs / zhs_cookie 三模式均返回正确课程列表 |
| F5 | 任务执行 | 订单 `pending → running → done`，`exit_code=0` |
| F6 | 订单详情 | 日志尾 8KB 正确渲染且**已转义**（注入 `<script>` 不执行） |
| F7 | 访客查单 | 单号前 8 位可查到概要，**不泄露账号等敏感字段** |
| F8 | 批量下单 | CSV 批量建单，全部入库 |
| F9 | 管理后台 | 状态看板 / 用户管理 / 11 项参数 / 注册口令均可操作 |
| F10 | 越权防护 | 用户 A 访问用户 B 的订单 → 拒绝（IDOR 拦截） |
| F11 | 备份与恢复 | 从备份恢复到全新环境并可正常服务 |

### 12.2 非功能验收

| 维度 | 指标 |
|---|---|
| 可用性 | 30 天可用性 ≥ 99.5%；单实例故障自动恢复 < 60s |
| 性能 | `/health` P95 < 100ms；页面 P95 < 800ms；查课异步接口 P95 < 3s |
| 并发 | 4 路订单并发稳定运行 ≥ 8h，无内存增长趋势（RSS 漂移 < 10%） |
| 安全 | 安全响应头齐全；全盘 **0 明文凭据**；**0 隐私残留**；`/register` 按 G1 决策处置 |
| 数据 | 迁移前后逐行比对 100% 一致；备份 RPO ≤ 24h、RTO ≤ 30min |
| 可观测 | 100% 请求带 traceId；P0 故障 3 分钟内告警触达 |
| 合规 | Stage 0 结论已落地（备案 / 隐私政策 / 用户授权 齐备，或入口已关闭） |

### 12.3 回滚验收

| 场景 | 要求 |
|---|---|
| 灰度回滚 | 5 分钟内切回上一版本，数据无丢失 |
| 全量回滚 | 30 分钟内切回本机运行（Stage 4 期间本机保持热备） |
| 数据回滚 | 具备从 T-1 备份恢复的能力，演练已验证 |

---

## 13. 本次未实施事项（明确边界）

1. **未修改任何代码**（`order_platform.py` / `health_manager.py` / 引擎均未触碰）。
2. **未删除、未移动任何文件**（含 `_archive_dev` / `_hist` / 明文凭据残留 —— 仅记录，未处置）。
3. **未停止或重启任何服务**（审计期间平台 / 隧道 / 看护保持运行）。
4. **未执行任何数据写入**（DB 全程只读方式访问）。
5. **未测试第三方平台**（未触达学习通/知到，未产生任何真实业务请求）。

---

## 14. 建议的下一步

| 优先级 | 动作 | 说明 |
|---|---|---|
| 🔴 立刻 | 答复 §7 的 G1（服务性质定位） | 这是唯一的真正前置条件 |
| 🔴 立刻 | 关闭 `/register` 或更换为强口令 | 当前 `【注册口令·已脱敏】` 可被轻易猜到，任何人可注册使用 |
| 🟠 本周 | 清退/确认 3 个第三方账号（`chatgpt` / `user2@example.com` / `wcnb`）的处置 | 关系到"是否对外提供过服务"的事实认定 |
| 🟠 本周 | 处置明文凭据与隐私残留 | `config.json` / `.zhs_cred` / `_hist` / `_archive_dev` |
| 🟡 两周内 | 清理空转残留进程对 + 排查隧道日志中的 metrics 端口冲突 | 本机稳定性 |
| 🟡 两周内 | 补安全响应头（即使不上云也应做） | 1~2 小时工作量，收益高 |

---

## 附录 A · 证据索引（重要结论 → 文件/行号/实测值）

| 结论 | 证据 |
|---|---|
| 系统正在运行 | `[实测]` `LISTEN 127.0.0.1:8766` (PID 46808)；`/health` = `{"status":"ok","database":"ok","pending":0,"running":0}` |
| 进程链路 | `[实测]` 计划任务 → health_manager(40060, 17.5MB) → cloudflared(3172, 19.8MB) + order_platform(41028 启动器 1.1MB → 46808 服务 32.1MB) |
| 空转残留进程对 | `[实测]` pythonw PID 29516(1.5MB) → PID 31740(0.8MB)，未持锁未持端口 |
| 已在公网 | `[实测]` `cf_state.json` = `{tunnel_id: 24f92801-…, domain: order.jiangjiangze.icu, port: 8766}`；`~/.cloudflared/wk_config.yml` ingress → `http://127.0.0.1:8766` |
| 隧道健康 | `[实测]` `health_manager.log` 2026-09-21 20:52:46 `[隧道状态] healthy=True(metrics/ready=200) 连续失败=0`；`[隧道探测] order.jiangjiangze.icu=HTTP 302`；最近重启 `2026-09-21 04:14:36` |
| 隧道 metrics 端口曾冲突 | `[实测]` `cloudflared.log`：`ERR Error opening metrics server listener ... bind 127.0.0.1:20241: Only one usage of each socket address` |
| 数据现状 | `[实测]` orders 19 条（14 done / 5 failed，全终态）、users 4、products 3、settings 14；`orders.password` 0 条明文 |
| 已有第三方用户 | `[实测]` users：`chatgpt`(09-14)、`user2@example.com`(09-14)、`wcnb`(09-20) |
| 备份在跑 | `[实测]` `backups\` 7 份，2026-09-15 ~ 09-21 每日 18:41~18:42 |
| 明文凭据残留 | `[实测]` `fuckCourse\config.json`：`chaoxing.common.username = 13000000000`、`password = <12 chars 明文>`；`.zhs_cred` 25B 含手机号；`cookies.json` 6.7KB 含 token |
| 隐私数据残留 | `[实测]` `_hist\hist_chrome.db` 8.8MB、`hist_edge.db` 256KB（浏览器历史） |
| 弱注册口令 | `[实测]` `settings.reg_code = 【注册口令·已脱敏】` |
| 无安全响应头 | `[源码]` 全局无 `after_request` / CSP 设置 |
| cookie 缺 Secure/SameSite | `[源码] order_platform.py` `resp.set_cookie("wk_token", …, httponly=True)` |
| 硬编码 PYEXE | `[源码] order_platform.py:33` |
| 硬编码端口 | `[源码] order_platform.py` `serve(app, host="127.0.0.1", port=8766, threads=16)` |
| 运行期看护已存在 | `[源码] order_platform.py` `order_watchdog()` h788，30s 轮询收敛"DB=running 且 pid 已死" |
| XSS 已修 | `[源码]` `esc()` 定义 + 16 处调用 |
| 技术栈版本 | `[实测]` Python 3.13.12 / Flask 3.1.3 / Waitress 3.0.2 / requests 2.34.2 / loguru 0.7.3 / ddddocr 1.6.1 / Pillow 12.3.0 / openai 3.13.0 |

## 附录 B · 关键配置速查（当前生效值）

| 配置 | 值 | 说明 |
|---|---|---|
| `concurrency` | 4 | worker 池大小（硬上限 10） |
| `jobs` | 2 | 引擎并发 |
| `speed` | 2.0 | 播放倍速 |
| `order_timeout_min` | 180 | 单订单硬超时 |
| `min_free_mb` | 500 | 内存护栏阈值 |
| `log_keep_kb` / `log_keep_days` | 300 / 3 | 日志控制 |
| `spoof` / `jitter` | 1 / 1 | 指纹伪装 / 倍速抖动 |
| `proxy_pool` | （空） | 代理池未启用 |
| `reg_code` | `【注册口令·已脱敏】` | ⚠️ 弱口令 |
| `paused` | 0 | 队列未暂停 |
| SECRET | `secrets_store\secret_key.txt`（29B） | ⚠️ 轮换 = 全库口令失效 + 存量密码不可解 |
| admin 口令 | `secrets_store\admin_password.txt` | 首启强制随机化（非默认） |

---

*本报告仅审计与规划，不含任何代码改动。所有敏感值均已脱敏或标注"存在、位置、值隐藏"。*
*报告生成：2026-09-21 ｜ 审计人：WorkBuddy 全栈开发专家*
