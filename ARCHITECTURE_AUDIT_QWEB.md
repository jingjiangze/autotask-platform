# ARCHITECTURE_AUDIT_QWEB — Q:\\Web 迁移整理 · 第一阶段审计

> 生成：2026-09-14 （基于真实读取，非假设）｜ 范围：本地 D:\\web + Q 目标层现状
> 关键事实：**Q: 盘当前不可见（OpenList 未挂载/不存在）**；本地主副本 = `D:\web`，平台运行中（/health 200）；
> 指令所称"已整体移动到 Q:\\Web"与现况不符，已向用户确认：先落地本地基建，Q 挂载后再做同步/迁移验证。

## 1. 权威源与运行位置

| 项 | 状态 |
|---|---|
| 本地主副本 | D:\\web 存在，平台、看护、隧道均在运行（8766 /health ok） |
| Q:\\Web | 不存在（Q: 未挂载）→ 目标层待挂载后建立 |
| 权威源 | 本地 D:\\web（开发+日常运行） |
| Q 定位 | 公开项目副本 + 加密私有数据仓库 + 迁移/恢复来源（单向 LOCAL→Q） |

## 2. 本地架构快照（已核验，详见 PROJECT_FULL_STATE.md）

- 单进程 Flask(Waitress:8766) + Worker 线程池 + 每单独立 python 子进程；SQLite WAL；看护 health_manager（锁文件）；cloudflared 隧道。
- 两轮加固已落地（P0/P1/P2，见 SECOND_ROUND_AUDIT.md）：运行期 order_watchdog、recover kill 校验、esc 转义、日志脱敏、并发护栏等。

## 3. Q 层设计约束（指令 §4–§9、§48、§62-63）

- 物理结构以兼容现有代码为主，不强制搬迁；引入 `path_manager.py` 统一逻辑路径（已落地）。
- 单向 LOCAL → Q；Q 不反向合并；Q 断开不影响本地业务（同步器 state=degraded 自动重试）。
- 公开仅限 `Q:\Web\public`（OpenList 公开 root），private/encrypted/backups/runtime 一律不公开。
- 私有数据：AES-256-GCM bundle；数据库：一致性备份后加密 .enc；主密钥/Recovery 凭据绝不入 Q。

## 4. 现状与目标差异表

| 差异 | 现状 | 目标（Q 挂载后） |
|---|---|---|
| 公开层 | 静态/等混在项目内 | public/ 单层公开 |
| 私有数据 | 明文散落（orders/cf profile/secrets） | private/encrypted/*.enc（bundle） |
| 数据库落 Q | 无 | private/backups/*.db.enc（一致性备份加密） |
| 同步 | 无 | sync_manager LOCAL→Q（防抖/单任务/重试/降级） |
| 跨机恢复 | 无 | manifest + Recovery + 加密 bundle + 本机 Credential Manager |
| 主密钥 | 平台 SECRET 在 secrets_store | 与平台 SECRET 独立；DEK 在 CM；Recovery 离线 |

## 5. 风险（审计阶段记录）

1. Q 缺失期间不得创建"假同步"；待挂载后先做目标存在性/连通性探测。
2. 迁移整理禁止删除现有业务文件；所有动作=新增模块+复制+验证。
3. browser_profile（cf\\browser_profile 140MB 级、含 Cookies/Trust Tokens）为最高隐私目录 → 目录 bundle 加密迁移。
4. orders/<id>/cookies.json 明文登录态 → 加密 bundle（含 REQUIRED_CACHE 性质）。
5. `config.json`(.zhs_cred) 为引擎运行时依赖 → 加密恢复需保持相对路径不变。

## 6. 结论

本地基建（path_manager + crypto_manager + 分类/设计文档）先落地；Q 挂载后执行"目标探测 → 首批加密 bundle → 一致性备份加密 → 单向同步"；再确认真实迁移与跨机恢复。