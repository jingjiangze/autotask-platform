# MAA 夜间进度看板（公网可查）

> 2026-09-21 交付 · 只读采集 MAA 日志，**绝不触碰模拟器 / MAA 进程**

## 一、访问信息

| 项 | 值 |
|---|---|
| **公网地址** | https://maa.jiangjiangze.icu/ |
| 用户名 | `maa` |
| 密码 | `ABaKOzCZgRBAaOW0`（存放于 `D:\web\maa_status\config.json`，可自行修改） |
| 本机地址 | http://127.0.0.1:8791（仅本机监听，公网入口走 Cloudflare Tunnel） |
| 登录方式 | 表单登录 → Cookie 会话（保持 7 天）；脚本可用 HTTP Basic |
| 服务进程 | 计划任务 **MAA-StatusBoard**（登录自启 + 每 30 分钟自愈检查，单实例锁） |
| 隧道 | 子域 `maa.jiangjiangze.icu` → tunnel `24f92801-…` → `127.0.0.1:8791` |

## 二、页面能看到什么

| 卡片 | 内容 |
|---|---|
| **当前状态** | 正在跑的任务、MAA / MuMu / cloudflared 进程、夜巡最后活动时间、下线前那遍是否已接管 |
| **理智（实测）** | 最新实测理智 + 是否已清空、实测轨迹（首→末）与净消耗、兜底关 `selected`、`selected null` 次数 |
| **健康度** | 肉鸽异常截图张数（12h，≫4 即卡死循环）、MAA/模拟器重启次数、GUI 启动次数、未恢复的异常 |
| **队列进度** | 最近一遍的 9 项任务逐项状态（完成 / 进行中 / 异常 / 待执行） |
| **两遍状态** | 上线那遍 + 下线前那遍的分段记录（05:50 切段，2026-09-21 新增逻辑） |
| **计划任务** | 9 个 MAA-* 任务状态与下次运行时间、**下次自动关机时间** |
| **事件时间线** | 夜巡记录的重启 / 掉线 / 自愈事件 |
| **历史日报** | 最近 7 天报告摘要，点「全文」看完整 markdown |
| 折叠区 | MAA 消耗事件原始行、夜巡日志尾部 |

## 三、数据来源与判据（重要）

| 指标 | 来源 | 说明 |
|---|---|---|
| 理智 | `asst.bak.log` + `asst.log` 的 `analyze_sanity_remain Current Sanity: N` | **最硬判据**；倒序分块扫描（关键行可能在文件 75% 处，尾部 1MB 抓不到） |
| 净消耗 | 上述采样点的首尾差值 | 不用「开始行动 N 次, -X理智」求和 —— MAA 会按轮次重复打印，累加会翻倍（实测 15 行累加 750，真实约 −264） |
| 队列进度 | asst 日志 `TaskChainStart/Completed/Error` 事件流 | 取最后一次 `StartUp` 之后的一段；`OperBox`/`Depot` 归并到 `UserDataUpdate` |
| 兜底关 | `gui.log` 的 `GetFightStage: from [...], selected X` | 只统计近 24h；`selected null` > 0 直接标红 |
| 肉鸽健康度 | `debug\roguelike\*.png`（12h 内） | ≥4 黄、≥8 红 |
| 两遍 / 事件 | `night_report.txt`（夜巡收尾写入） | 01:00–06:22 期间显示的是**上一晚**的报告 |
| 计划任务 | Windows 计划任务 COM 接口 | 直接读真实状态，非推测。**COM 必须按线程初始化**：服务是多线程的，采集器每次请求都自己 `CoInitialize`，否则报「尚未调用 CoInitialize」且因为是降级返回，页面只是少一块数据、不报错 |

## 四、安全设计（暴露前已加固）

- **白名单路由**：只有 `/`、`/api/status`、`/api/report/<日期>`、`/health`、`/login`、`/logout`；无静态目录服务、无任意文件读取（报告按 `YYYY-MM-DD` 正则白名单，路径穿越返回 404）。
- **认证**：全站需登录（除 `/health`）；密码 12 位随机；登录失败 12 次触发 5 分钟封禁。
- **单实例**：Windows 命名互斥体（`Global\MAAStatusBoard.8791`，回退 `Local\`），内核级、进程崩溃自动释放；同时 `allow_reuse_address = False`，让重复启动直接报端口占用而不是在 Windows 上"抢端口"（`SO_REUSEADDR` 在 Windows 允许两个进程绑同一端口）。
  > 旧实现用 `msvcrt` 文件锁，2026-09-21 实测日志里出现过两行「服务启动」且中间无「已有实例在运行」，已替换。
- **网络**：服务仅绑定 `127.0.0.1`，公网仅经 cloudflared 出隧道，**不开放任何入站端口**。
- **隐藏**：`X-Robots-Tag: noindex`、`nosniff`、不暴露 Server 版本。
- 注：Cloudflare 会剥离 `WWW-Authenticate` 头 → 浏览器不会弹原生 Basic 框，故公网走**表单 + Cookie**（Basic 保留给脚本）。

### 可选加固（更强，需手动 3 分钟）
想再加一道 Cloudflare 边缘防线（未验证者连登录页都看不到）：
1. https://one.dash.cloudflare.com/ → 首次设 team name → 选 Free
2. Access → Applications → Add → Self-hosted → 域名填 `maa.jiangjiangze.icu`，Session `24h`
3. Policy：Action `Allow`，规则用 **Emails** 明确填你的邮箱（**不要**用邮箱域名，免费邮箱域≈所有人可进）
4. 默认 One-time PIN 即可
> ⚠️ 加了 Access 后脚本调用会被拦，需要 Service Token 或对特定路径 Bypass。

## 五、运维操作

```bash
# 重启服务（先杀再拉起，走计划任务保证静默）
netstat -ano | grep ":8791.*LISTENING"        # 取 PID
taskkill /F /PID <PID>
# 拉起来（PowerShell 或 python win32com）
Start-ScheduledTask -TaskName MAA-StatusBoard

# 改密码：编辑 config.json 的 password → 重启服务
# 停用：Disable-ScheduledTask -TaskName MAA-StatusBoard  然后杀掉进程
# 自检（12 项，含匿名必须被拦 + 计划任务面板必须有数据）
<venv python> D:\web\maa_status\selftest.py
#   ⚠️ 服务已在跑时，自检会「复用」现有实例（脚本会打印提示），
#      此时测的不是新代码。要验证改动：先杀进程再跑自检。

# 回滚隧道配置（撤销本看板的公网入口）
copy C:\Users\Administrator\.cloudflared\wk_config.yml.bak_20260921_210346_before_maa ^
     C:\Users\Administrator\.cloudflared\wk_config.yml
taskkill /F /IM cloudflared.exe      # 由 health_manager.py 30s 内自动拉起
```

## 六、故障对照

| 症状 | 原因 | 处理 |
|---|---|---|
| 公网 403 / `error code: 1010` | Cloudflare 拦截**无 User-Agent** 的请求（脚本常见） | 请求带正常 UA；浏览器不受影响 |
| 公网打开是登录页 | 未登录或 Cookie 过期（7 天） | 重新登录 |
| 页面数据不刷新 | 服务挂了 | 查计划任务 MAA-StatusBoard 是否 Ready；每 30 分钟会自动拉起 |
| 「两遍状态」只有 1 条 | 05:50 切段逻辑 2026-09-21 才上线，当天之前的历史数据未分段 | 次日起正常 |
| 理智显示「—」 | asst 日志轮转窗口内没有 `Current Sanity` 采样 | 属正常（该行不是每轮都打印） |
| 队列里 Copilot 永远「待执行」 | 未配置作业码（MAA 不支持活动 EX 关自动刷） | 属预期行为 |
| 「计划任务」面板空白或显示 `尚未调用 CoInitialize` | 多线程下 win32com 未按线程初始化 | **已于 2026-09-21 修复**（采集器内 `CoInitialize`）；自检新增该项断言 |
| 任务管理器出现 **两个** `pythonw.exe` 跑同一个 server.py | venv 的 `Scripts\pythonw.exe` 是启动器，会拉起基础解释器作子进程 | **正常**，不是双实例；真正判据看谁在 `LISTENING` 8791 |
| 自检全 PASS 但改动没生效 | 服务已在跑，自检复用了旧实例 | 先杀进程再跑自检（脚本会打印「复用该实例」提示） |

## 七、文件清单

| 文件 | 作用 |
|---|---|
| `collector.py` | 只读采集器（可直接 `python collector.py` 打印 JSON 排查） |
| `server.py` | HTTP 服务 + 页面 + 登录（单文件零依赖） |
| `config.json` | 端口 / 账号 / 密码 |
| `selftest.py` | 本机自检 12 项（含 COM 面板断言、单实例断言） |
| `register_task.py` | 注册 MAA-StatusBoard 计划任务 |
| `logs/` | 服务日志（按天轮转，保留 14 天） |
