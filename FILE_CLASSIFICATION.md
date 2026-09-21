# FILE_CLASSIFICATION — 文件数据分类（基于真实扫描）

> 分类：A=公开明文同步｜B=私有加密同步｜C=运行时不同步｜D=可再生缓存不同步｜E=恢复必需缓存（加密同步）
> 优先级（§30）：显式加密规则 > 黑名单 > 敏感检测 > 普通同步；无法判断默认按敏感处理。

## A. PUBLIC（明文同步）— 源码/文档/前端

| 项 | 说明 |
|---|---|
| *.py（根/模块） | order_platform、health_manager、backup_manager、tools_query_courses、path_manager、crypto_manager、tests、tools 脚本 |
| 文档 *.md | 现有 9 份 + 本批 6 份（不含含敏感值的报告——SECOND_ROUND_AUDIT 等为工程文档，明文可公开） |
| static\ tabler_pkg\ | Tabler 前端（本地资源） |
| 公开示例配置 | cf\cf_config.example.json 等（无真实密钥） |

## B. PRIVATE_ENCRYPTED（必须同步但加密）

| 项 | 位置 | 处理 |
|---|---|---|
| secrets_store\*（secret_key.txt、admin_password.txt、旧口令归档） | D:\web\secrets_store | 文件级 .enc（注意：**secret_key.txt 与平台登录哈希绑定**，迁移必须同步恢复，否则口令失效） |
| orders\platform.db 及备份 | orders\platform.db（+wal/shm） | **不做实时复制**；一致性 Online Backup → private\backups\*.db.enc |
| orders\<id>\cookies.json（19+ 个） | orders\ | bundle 加密（REQUIRED_CACHE 性质） |
| orders\<id>\home\ 等隐私 | orders\ | 目录 bundle（如包含浏览器状态） |
| cf\browser_profile\（≈140MB，含 Cookies/Trust Tokens） | cf\browser_profile | **目录 bundle 加密**（最高隐私，§35 文件名保护） |
| fuckCourse\config.json / .zhs_cred / cookies.json | fuckCourse\ | 引擎运行时依赖 → 加密 bundle（恢复保持相对路径） |
| _archive_dev\ / secrets_store\_archive_dev\ | - | 含口令材料 → 加密归档 bundle（或明确不同步） |
| PushPlus/Bark/AI key（config 内） | config.json | 随 config 加密 |

## C. RUNTIME（不持久/不同步）

| 项 | 位置 |
|---|---|
| cf\health_manager.lock、*.log（health_manager/platform_stdout/cloudflared）、platform_error.log | cf\、根 |
| orders\_query\（1h 清理） | orders\_query |
| runtime\（若创建） | 新增目录 |
| __pycache__ / *.pyc | 各处 |

## D. REGENERABLE_CACHE（不同步）

| 项 | 说明 |
|---|---|
| __pycache__、编译缓存 | 可再生 |
| static/vendor 之外的可再生资源 | 视审计 |

## E. REQUIRED_CACHE（必须同步 + 加密）

| 项 | 原因 |
|---|---|
| orders\<id>\cookies.json | 删除=重新登录/任务状态丢失 |
| cf\browser_profile 登录态 | 删除=浏览器重新登录 |
| fuckCourse cookies/.zhs_cred | 删除=引擎账号重新登录 |
| secrets_store\secret_key.txt | 删除=平台口令哈希失效+ECP 密文不可解 |

## 关键结论

1. **不得 `cache/**` 一刀切排除**：orders 内 cookie/home、browser_profile 属恢复必需 → 加密同步。
2. 最高隐私 = cf\browser_profile（Cookies 明文）→ 首期加密目标。
3. Q 挂载后，除 public 明文层外，全部上述 B/E 仅以 `*.enc/.db.enc/bundle_*.enc` 出现。