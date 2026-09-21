# MIGRATION_PLAN — 单向同步 + 跨机迁移计划

> Q 盘当前未挂载；本计划的分阶段执行以 Q 可用为前提（Q 挂载后启动同步/迁移阶段）。

## 1. 目标结构（逻辑，非强制物理重构）

```
Q:\Web
├─ public\                      (OpenList 公开 root，仅此层公开)
│   └─ app 镜像：*.py, static/tabler_pkg(工程源码明文)  ← PUBLIC 分类
├─ private\
│   ├─ encrypted\bundle_*.enc   (cookie/浏览器/engine 凭据/归档)
│   └─ backups\platform_*.db.enc(一致性备份加密)
├─ manifest.json                (版本/格式/依赖/bundle 清单；无密钥)
├─ 启动平台.bat                  (模板；跨机替换路径)
└─ README_迁移.txt               (恢复指引)
```

## 2. 同步规则（sync_manager 待建，§31-41）

- 方向：仅 LOCAL→Q；单一同步任务 + dirty 标记；防抖 1–3s。
- 普通文件：tmp→上传→校验→替换；bundle：tmp→验证大小→完整性→正式文件；失败保留旧版本。
- Q 断开：state=degraded，本地业务不受影响，指数退避重试；恢复后自动续传。
- 状态字段：enabled/state/target_available/last_sync_at/last_success_at/last_error/retry_count/pending_changes。

## 3. SQLite 处理（§42-44）

- 禁止实时复制 .db/.wal/.shm；用 `backup_manager.backup_database()`（Online Backup）→ integrity_check → **crypto_manager 加密** → private\backups\platform_*.db.enc。
- 恢复（新机）：解密 → integrity_check → 备份现有库 → 替换 → 启动（平台需停机，复用 backup_manager.restore 语义）。

## 4. 跨机迁移路径 A→B（§22、§47、§54）

```
Q:\Web(完整) → 复制 → D:\WorkBuddy(B)
安装 Python(venv) + 依赖(cryptography/pywin32/waitress/requests/…按 manifest)
首次启动：无本机 DEK → 输入 Recovery Password → 验证 recovery_bundle → 恢复 DEK → 写本机 CM
验证数据库 .db.enc → 恢复 orders/cookies/browser_profile/engine 凭据（目录 bundle）
改 4 处路径触点：计划任务 / 启动 bat / Startup VBS / cloudflared 配置目录
启动平台（原有业务逻辑零改动）
```

## 5. 需要先解密恢复的 KEY 顺序（§25 按需解密）

1. manifest（明文）→ 确认版本/格式
2. recovery_bundle（明文 meta，解密需密码）→ DEK
3. secret_key.txt.enc → 平台口令体系存活
4. orders cookie bundle / engine 凭据 bundle → 业务可继续
5. browser_profile bundle → 浏览器登录态（按需）
6. platform_*.db.enc → 历史订单数据（恢复完整）

## 6. 分阶段执行顺序（Q 挂载后）

| 阶段 | 内容 | 验证 |
|---|---|---|
| P0 | 目标探测（Q 存在/可写/私有目录不可公开） | 目录清单 |
| P1 | 首批 bundle：secret_key / orders cookies / browser_profile / engine 凭据 | 逐包 decrypt 往返 |
| P2 | SQLite 加密备份落地 private\backups | integrity + decrypt 往返 |
| P3 | public 明文层复制（源码/文档/static） | 文件比对 |
| P4 | sync_manager（防抖/降级/重试/状态） | 断网模拟 |
| P5 | manifest.json 生成 + 版本校验 | 双机校验 |
| P6 | migration_manager export/verify/restore 工具 | 无真实 B 机则本地复盘 |
| P7 | 回归：本地平台零影响；Q 断开不中断 | /health + 订单流程 |

## 7. 风险与回滚

- 不做破坏性删除：仅复制+验证；旧结构保留至双机恢复成功。
- 每 bundle 生成前先 decrypt 校验一次；manifest 记录 hash；回滚=还原 Q 旧版本 bundle。
- PYEXE/计划任务/VBS/cloudflared 4 触点变更全部记录进 manifest 提示。