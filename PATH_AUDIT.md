# PATH_AUDIT — 路径硬编码审计（基于真实检索）

> 生成：2026-09-14 ｜ 方法：检索 `order_platform.py / health_manager.py / backup_manager.py / tools_query_courses.py` + BAT/VBS/计划任务

## 1. 代码内路径（Python）

| 文件 | 行号 | 硬编码 | 用途 | 风险 | 建议 |
|---|---|---|---|---|---|
| order_platform.py | 顶部 PYEXE | `C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\python.exe` | 任务/查课子进程解释器 | 迁移机器/venv 移动即断 | 保留（venv 属环境安装路径，非项目路径）；跨机恢复时按 manifest 或环境检测提示 |
| order_platform.py | 各 `os.path.join(APP_DIR,...)` | 均基于 `__file__` | 项目内路径 | 低 | 已合格，可逐步改用 path_manager |
| health_manager.py | PYEXE（同上式） | 同上 | 启动平台/隧道解释器 | 同 | 同上 |
| backup_manager.py / tools_query_courses.py / path_manager.py | 均基于 `__file__` | 无绝对路径 | - | 低 | 合格 |
| crypto_manager.py（新增） | 基于 path_manager | 无绝对路径 | - | 低 | 合格 |

## 2. 系统级/脚本级路径

| 项 | 位置 | 当前值 | 风险 |
|---|---|---|---|
| PYEXE（同上） | order_platform.py / health_manager.py | .workbuddy 绝对 | 机器迁移需改或维护 |
| 计划任务 WK_AutoTaskPlatform | Actions | pythonw.exe "D:\web\health_manager.py" | 指向 D:\web；跨机需改 |
| 启动 bat | D:\web\cf\启动平台和隧道.bat | `set WK=D:\web` + PY | 跨机需改 |
| VBS（Startup） | 自动任务平台.vbs | 调用 D:\web\cf\...bat | 跨机需改 |
| cloudflared 配置 | %USERPROFILE%\.cloudflared\wk_config.yml | 用户目录 | 跨机需复制该目录 |
| 会话期记忆/临时 | D:\web\.workbuddy\memory、_archive_dev 等 | - | 不参与迁移 |

## 3. 结论

- 项目内路径已基本基于 `__file__`（合格），无需大改；`PYEXE` 属 venv 环境路径（保留并文档化）。
- 跨机迁移触点 = 计划任务 / bat / VBS / cloudflared 配置 4 处 + venv（见 MIGRATION_PLAN）。
- 已落地 `path_manager.py` 供新增模块统一取路径；业务文件不强改（兼容优先）。