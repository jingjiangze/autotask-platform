# CRYPTO_DESIGN — 加密系统设计（已落地 crypto_manager.py + tests/test_crypto_bundle.py）

## 1. 摘要

私有数据（cookie/浏览器状态/订单登录态/引擎凭据/加密数据库备份）以 **AES-256-GCM** 加密为 `.enc` 数据包或目录 `bundle` 进入 Q；**主密钥与 Recovery Password 永不入 Q**；本机由 **Windows Credential Manager** 自动解锁，跨机由 **Recovery Password** 恢复。

## 2. 环境就绪度（实测）

- `cryptography 50.0.0` ✅（AES-256-GCM / scrypt）
- `pywin32`（win32cred）✅（Credential Manager）
- `argon2-cffi` ❌ 未装 → **KDF 采用 scrypt**（cryptography 内置、抗 GPU、无需新依赖；指令允许"A 无则成熟替代"）；若未来 want Argon2id 可加依赖并升 KDF_VERSION=2。

## 3. 密钥体系（§13–§15）

```
Recovery Password ──scrypt(n=2^15,r=8,p=1,salt16B)──▶ KEK(32B)
DEK(32B 随机, 每项目一种) ──AES-GCM(wrap, AAD=WBENC-recovery-v1)──▶ wrapped_DEK(recovery_bundle.json)
日常：DEK 存本机 Credential Manager(WorkBuddy::DEK) → 运行期 load_dek_local() 自动解密
跨机：Recovery Password + recovery_bundle.json → recover_dek()
改密：rotate_recovery_password() 仅重 wrap（不重加密数据）
```

## 4. 数据包格式（version 显式，可迁移升级）

```json
{ "format":"WBENC", "version":1, "algo":"AES-256-GCM",
  "meta":{...}, "nonce":"b64(12B 随机)", "ciphertext":"b64(GCM ct+tag)" }
```
- 每包唯一随机 nonce；禁止固定/时间戳 nonce。
- AAD = 仅元数据（format/version/meta）；**不含任何真实私有数据**。
- GCM tag 负责：篡改检测 / 密码错误检测 / 损坏检测；任何失败**不返回部分明文**（AUTH_FAILED 等）。

## 5. 目录 Bundle（§8/§35）

```
目录 → tar.gz(相对路径+manifest) → AES-GCM → bundle_<random>.enc
恢复：先解到临时目录 → 校验 → 整体移入 → 失败不留半成品
```
真实目录结构/敏感文件名仅存在于加密包内。

## 6. 错误分类（§55）

| 场景 | 错误码 |
|---|---|
| Recovery 密码错 / wrap 篡改 | BAD_PASSWORD |
| CM 无 DEK | MISSING_KEY |
| 包结构缺字段/非 WBENC | BAD_FORMAT |
| 版本不支持 | UNSUPPORTED_VERSION |
| base64 损坏/字段缺失 | CORRUPTED_DATA |
| GCM 校验失败（篡改/AAD 不符） | AUTH_FAILED |

## 7. 测试覆盖（tests/test_crypto_bundle.py，已 PASS）

Recovery 往返 / 错密码 / wrap 篡改 / 数据往返 / AAD 不符 / ciphertext 篡改 / 截断 / 版本 99 / 缺字段 / 文件 bundle / 目录 bundle（manifest+恢复）/ rotate（新有效旧失效）/ storage_status。

## 8. 密钥生命周期

- 首次：`setup(recovery_password)` → DEK 写 CM + 生成 recovery_bundle.json（用户离线另存）。
- 轮换：`rotate_recovery_password(old,new)`。
- 丢失：CM 丢失 → Recovery 仍可恢复（unlock_from_recovery 可重写 CM）；**双方全丢 = 加密数据不可恢复（如实声明，不伪造恢复）**。
- 主密钥不入 Q（sync 规则排除 secrets_store 明文、recovery_bundle 提示离线保存）。

## 9. 与平台现有 SECRET 的关系

独立两套：平台 SECRET（secrets_store\secret_key.txt，登录哈希/ECP 用）与加密系统 DEK/Recovery 完全隔离；**但迁移必须同时携带 secret_key.txt（加密形式）**，否则恢复后平台口令/密文失效——已纳入 FILE_CLASSIFICATION。