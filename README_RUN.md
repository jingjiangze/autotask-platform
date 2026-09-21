# README_RUN.md — 运行手册

## 环境要求
- Windows 10/11，Python venv：`C:\Users\Administrator\.workbuddy\binaries\python\envs\default`
- 项目根目录：**`D:\web`**（2026-09-13 由 WorkBuddy 会话目录迁入；除启动 bat/VBS/计划任务外，代码内路径均基于脚本自身位置动态生成）
- cloudflared 配置：`%USERPROFILE%\.cloudflared\wk_config.yml`

## 首次启动 / 正常启动
**已部署计划任务 `WK_AutoTaskPlatform`（登录自动启动 + 每 5 分钟兜底自愈，pythonw 无窗口运行），无需手动操作。**
手动启动方式（任选）：
- 双击 `D:\web\cf\启动平台和隧道.bat`
- 或 PowerShell：`Start-ScheduledTask -TaskName "WK_AutoTaskPlatform"`

启动链路：计划任务 → health_manager.py（看护，互斥幂等）→ 自动拉起平台(127.0.0.1:8766) + cloudflared 隧道。
- 本机访问：http://127.0.0.1:8766
- 外网访问：https://order.jiangjiangze.icu
- 健康检查：http://127.0.0.1:8766/health （只返回非敏感状态）

## 停止
维护性停机（看护不会把你拉起来的进程当异常重启）：
1. 先停看护：`Stop-ScheduledTask -TaskName "WK_AutoTaskPlatform"`（任务结束会终止看护进程）
2. 再结束平台/隧道残留进程（如 tasklist 中仍有 python.exe / cloudflared.exe 属本项目）
3. 重新启动：`Start-ScheduledTask -TaskName "WK_AutoTaskPlatform"`
   一键停隧道（临时）：`python cf\deploy_cf.py stop`（看护会在 30-60s 内重新拉起，仅适用于快速测试）

## 重启平台
结束平台 python 进程即可——看护进程会自动重启它（冷却 60s）。

## 备份
```
C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\python.exe backup_manager.py backup
C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\python.exe backup_manager.py list
```
平台运行中也可备份（在线备份）。另有每日自动备份（housekeeping 内，保留 7 份，见 backups/）。

## 恢复
1. 停止平台与看护（见"停止"）
2. `python backup_manager.py restore backups\platform_YYYYMMDD_HHMMSS.db`
3. 重新双击启动 bat

## 修复 / 诊断
- 平台没起来？→ 看 `cf\health_manager.log`（看护动作记录）、`platform_error.log`（状态写库失败记录）
- 隧道断了？→ 看 `cf\cloudflared.log`；看护会在 30-60s 内自动拉起
- 订单卡 running？→ 重启平台会自动恢复（stale 扫描：crashed → 重试 1 次 → failed）
- 数据库锁异常误判？→ 已做 busy 容错，若仍出现请查 platform_error.log

## 查看日志
| 日志 | 位置 | 上限 |
|---|---|---|
| 订单日志 | orders/<单号>/log.txt | 300KB（可配） |
| 引擎日志 | fuckCourse/logs/（含日期轮转，按保留天数删除） | 512KB/个 |
| 隧道日志 | cf/cloudflared.log | 512KB |
| 看护日志 | cf/health_manager.log | 512KB |
| 状态写库失败 | platform_error.log | 追加 |

## 查看健康状态 / 系统状态
- /health 接口（程序化）
- 管理后台 → 资源与性能/并发卡片（人工）
- backups/ 目录看最近备份时间

## 修复 / 诊断（P1 增补）
- 出现多个 python/pythonw 本项目实例（含 4MB 僵尸）？→ 执行清理：`python health_manager.py clean` 或 `python tools\cleanup_stale.py`（保留健康平台与看护锁持有者；`python health_manager.py kill-all` 全清停机）。若看护消失，随后 `Start-ScheduledTask -TaskName WK_AutoTaskPlatform`。
- 平台/隧道没起来？→ 看 `cf\health_manager.log`（看护动作）、`cf\platform_stdout.log`（平台崩溃尸检）、`platform_error.log`。
- 手动启动：双击 `D:\web\cf\启动平台和隧道.bat` 或 `Start-ScheduledTask -TaskName "WK_AutoTaskPlatform"`（bat 路径已修正为 D:\web）。
- 看护锁文件：`cf\health_manager.lock`（pid+心跳时间戳；陈旧锁 100s 后可被新实例接管）。OS 排他锁载体 `cf\health_manager.lock.osl`（进程存活期间由 OS 持有，崩溃自动释放；cleanup 不依赖其内容）。
- 实例形态说明：本项目 pythonw 以「启动器父(1T 低占用) + 活跃子」成对运行（见 tools\cleanup_stale.py 注释）；clean/cleanup_stale 只回收"真孤儿"，活跃成对与锁/端口持有者一律保留。

## 安全（R4 已实施）
订单口令不再出现在任务/查课子进程命令行（改经 WK_ACCOUNT/WK_PASSWORD 环境变量，引擎 init_config 回退支持 CLI>env>config）。kill 防护：tasklist 中不再可见明文凭据。

## 运维命令（P3 增补）
- 停机（维护）：powershell -ExecutionPolicy Bypass -File tools\stop_platform.ps1
- 启动：powershell -ExecutionPolicy Bypass -File tools\start_platform.ps1（或 Start-ScheduledTask -TaskName WK_AutoTaskPlatform）
- 清理游离/僵尸实例：python tools\cleanup_stale.py
- 根目录调试残留已归档至 _archive_dev\（不再散落根目录）

## 恢复命令（P1-4 增补）
python backup_manager.py restore backups\platform_YYYYMMDD_HHMMSS.db（平台在线会拒绝；请先 	ools\stop_platform.ps1）
演练/停机后如需在线执行：追加 --force。
