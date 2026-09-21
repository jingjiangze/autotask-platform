# PROJECT_PLAN_AUDIT — 「最终完整工程执行指令 V2」规划审计

> 审计：2026-09-14 ｜ 方法：只读探测（本地/Q:\Web/OpenList 5244/公网端点）+ 与已落地模块对照
> 范围：审计规划本身（前提/阶段/风险/顺序/边界），**不执行任何修改**。

## 1. 实测事实（本审计只读快照）

| 项 | 实测 | 与 V2 文档关系 |
|---|---|---|
| OpenList 本地 | http://127.0.0.1:5244 → **HTTP 200** | ✅ 与 §2 一致 |
| Q:\Web 映射盘 | **Q: 已挂载（本会话先前探测为缺失，现已存在）** | ⚠️ 前提已恢复 |
| 公网 | pan.jiangjiangze.icu → 200；order.jiangjiangze.icu/health → 200 | ✅ 与 §2/§58 一致 |
| 本地平台 | 127.0.0.1:8766 /health 200，DB 正常 | ✅ 权威源在线 |
| Q:\Web | **3131 文件 / 698 目录 / 203.4MB**；时间戳 15:05–15:36（最新镜像） | ⚠️ 并非"88% 残缺"，而是"新结构未建" |
| Q 关键路径 | order_platform.py/orders\platform.db/cf\cloudflared.exe/secret_key.txt 均存在；**manifest.json/public/private 不存在** | ⚠️ 目标结构未建立 |
| Q 缺失（对照本地） | **path_manager.py、crypto_manager.py 不在 Q**（本会话新增模块未同步） | ⚠️ 迁移未含最新 |
| Q 明文敏感 | **orders\platform.db、secrets_store\secret_key.txt、orders\*\cookies.json 等以明文存在于 Q 副本** | ❌ 违反规划 §27/§14 |

## 2. 规划前提核对结论

1. **"迁移 88% 后报错"：现状更像"已完成一次整体复制、尚未进入目标结构与加密"，而非单纯的复制残缺**；失败的"88%"具体位置未定位（缺差量清单）→ 需执行 MIGRATION_STATUS（V2 §6-7）才能定性。
2. Q: 盘此前不可见，现已挂载 —— 此前阶段"先落地本地基建"的决策仍然成立且方向正确；现在具备条件恢复"差分修复"。
3. **主密钥明文已在 Q（secret_key.txt）**：这是规划中最严重的即时风险点（V2 §27 违反），虽是只读不处理，但必须记为 P0 执行项（替换为加密 bundle + 移除明文副本并以副本备份保留回滚）。

## 3. 规划优点与已落地模块一致性 ✅

- 阶段纪律（先审计→差量→小改→测试→清理）与项目一贯方法论一致。
- 加密体系要求（AES-256-GCM/scrypt/KEK-DEK/CM/Recovery/错误码/格式版本）与**已落地的 crypto_manager.py 完全吻合**（仅 Argon2id 用 scrypt 替代，已在 CRYPTO_DESIGN 注明）。
- 单向同步/防抖/互斥/降级/重试/版本回滚 = sync_manager 的设计即按此。
- SQLite 一致性备份+加密：backup-enc 已通（.db.enc → 解密 → integrity=ok）。
- 路径管理 path_manager.py 已建；分类文档 6 份已出。
- 双层认证（Access 门禁 + 应用自身登录/admin）与现有 /admin 鉴权方向一致。

## 4. 主要风险与缺口（分级）

| 级别 | 项 | 说明 |
|---|---|---|
| P0 | Q 上明文 secret_key.txt / orders cookies / platform.db | 违反主密钥不出 Q 原则；执行阶段第一步：Q 副本敏感件→加密 bundle+明文删除（保留本地/回滚副本） |
| P0 | OpenList 公网 pan.jiangjiangze.icu 当前能否匿名访问 private/orders | **未审计**；若可，则公网已暴露明文隐私。必须最优先做 OPENLIST_PUBLIC_ACCESS_AUDIT（§58） |
| P1 | "88%"失败根因未定位 | 需差量扫描（MIGRATION_STATUS/FAILURES）才能决定修复动作；本次快照显示缺 crypto/path 等新模块 |
| P1 | 目标结构（public/private/manifest）不存在 | 属尚未执行而非失败；按 MIGRATION_PLAN 补建 |
| P1 | Cloudflare Access 需 Cloudflare 面板/API 权限 | 我方无面板权限（§65-67 无法代为配置），需用户面板操作或提供 API token；可先出策略草案 |
| P1 | 在 Q 网络盘上长期运行 SQLite WAL 风险 | V2 §57 已正确要求"正式运行在本地 NTFS"，落实为启动检测提示（path_manager.is_network_path 已备） |
| P2 | 大规模新增模块（sync/scanner/migration/…）节奏控制 | 14 阶段全推风险高；建议按批交付、每批可回滚（见 §6） |
| P2 | cf\browser_profile≈140MB 加密迁移耗时 | bundle 较大，放非关键路径/异步 |

## 5. 顺序调整建议（对 V2 §92 的修正）

```
批次1（先行, 不依赖面板）  OpenList 公网隔离审计(§58/59/73-75) + Q 明文敏感件加密替换(P0)
批次2  迁移差分修复(MIGRATION_STATUS→差量→100%) + 目标结构(public/private/manifest) 建立
批次3  sensitivity_scanner + migration_manager(export/verify/restore) + 测试
批次4  sync_manager(单向/防抖/降级/重试) + Q 断线模拟
批次5  OpenList 加固落(公网仅 public, 关闭匿名写/WebDAV) —— 需 OpenList 配置权限(用户协作)
批次6  Cloudflare Access(邮箱白名单 OTP) —— 需面板权限(用户协作); Access Service Token 存 CM(§86)
批次7  Tunnel watchdog 增强(进程+连接状态, 非仅 tasklist)
批次8  完整回归(测试矩阵 A–X 中不依赖第三方的子集) + 新机恢复演练(本地模拟)
```

## 6. 需用户配合事项

1. **Cloudflare Access 邮箱白名单与策略**：需在 dash.cloudflare.com 配置（可提供我起草的 policy JSON）。
2. **OpenList 配置**：public 目录作为公网 root、关闭匿名写/WebDAV/目录浏览（若 OpenList 面板可配置）。
3. **公网 pan 可访问性确认**：是否需要我先做一次只读的匿名 URL 面审计（防泄露）——建议立即做（批量 URL 探测，不改配置）。

## 7. 结论

- V2 规划整体合理、与已落地基建高度一致；**真实缺口 = "Q 副本的加密/目标结构未建 + 少量新模块未同步 + 88% 失败根因待差量定位"**，而非大规模重做。
- 建议按「批次1-8」执行；**P0 两项（OpenList 公网泄露审计、Q 明文敏感件加密替换）应先于一切同步/美化**。
- 保持"本地=MASTER、Q=副本、先备份再小改、每批可回滚"。