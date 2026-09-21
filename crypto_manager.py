# -*- coding: utf-8 -*-
"""crypto_manager.py — 私有数据加密系统（AES-256-GCM + scrypt KDF + KEK/DEK + 本机 Credential Manager + Recovery）
设计要点（依指令 §12–§21、§55–§57）：
  - 算法：AES-256-GCM（cryptography 库）；每文件唯一随机 12B nonce；GCM tag 负责篡改/密码错误检测（绝不返回部分明文）。
  - KDF：scrypt（cryptography 内置，成熟抗 GPU；本机未装 argon2-cffi，按"A 无则选成熟替代"采用 scrypt，参数 n=2**15/r=8/p=1，16B 盐）。
  - 密钥体系：随机 32B DEK（数据密钥）；Recovery Password 经 scrypt → KEK → AES-GCM wrap DEK。
    修改 Recovery Password 只需重新 wrap DEK，不重加密数据（§56）。
  - 本机自动解锁：DEK 存 Windows Credential Manager（win32cred，pywin32 已装），日常运行自动读取、无需手动输密。
  - 跨机恢复：Recovery Password + recovery_bundle.json（仅含 kdf 盐与 wrapped DEK，不含任何明文密钥）→ 恢复 DEK。
  - 主密钥/Recovery Password 绝不进入 Q:\\Web（由 sync 规则排除；recovery_bundle 默认存本机 secrets_store 并提示离线下线保存）。
错误分类（§55）：BAD_PASSWORD / MISSING_KEY / BAD_FORMAT / UNSUPPORTED_VERSION / CORRUPTED_DATA / AUTH_FAILED

注意：`script 自身与平台既有 SECRET（secrets_store/secret_key.txt）相互独立`，本模块不读取平台 SECRET，
避免两套密钥互相污染；平台业务代码暂不接入（兼容层优先），由后续 sync/migration/backup-enc 使用。
"""
import base64
import hashlib
import io
import json
import os
import shutil
import tarfile
import tempfile
import time

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# ---------- 常量 ----------
FORMAT_NAME = "WBENC"
FORMAT_VERSION = 1
ALGO = "AES-256-GCM"
KDF_NAME = "scrypt"
KDF_VERSION = 1
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2 ** 15, 8, 1
SALT_LEN = 16
DEK_LEN = 32
NONCE_LEN = 12
CM_TARGET = "WorkBuddy::DEK"
CM_USER = "workbuddy"
RECOVERY_KEY_BUNDLE = None  # 运行时由 _key_bundle_path() 提供

# ---------- 错误类型 ----------
class CryptoError(Exception):
    code = "GENERIC"


class BadPassword(CryptoError):
    code = "BAD_PASSWORD"


class MissingKey(CryptoError):
    code = "MISSING_KEY"


class BadFormat(CryptoError):
    code = "BAD_FORMAT"


class UnsupportedVersion(CryptoError):
    code = "UNSUPPORTED_VERSION"


class CorruptedData(CryptoError):
    code = "CORRUPTED_DATA"


class AuthFailed(CryptoError):
    code = "AUTH_FAILED"


# ---------- KDF / KEK ----------
def _kdf_derive(password: str, salt: bytes) -> bytes:
    return Scrypt(salt=salt, length=32, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P).derive(password.encode("utf-8"))


def _wrap_dek(dek: bytes, kek: bytes, aad: bytes = b"") -> dict:
    nonce = os.urandom(NONCE_LEN)
    ct = AESGCM(kek).encrypt(nonce, dek, aad)
    return {"nonce": base64.b64encode(nonce).decode(), "ct": base64.b64encode(ct).decode()}


def _unwrap_dek(wrap: dict, kek: bytes, aad: bytes = b"") -> bytes:
    try:
        nonce = base64.b64decode(wrap["nonce"])
        ct = base64.b64decode(wrap["ct"])
        return AESGCM(kek).decrypt(nonce, ct, aad)
    except KeyError:
        raise BadFormat("wrap 缺少字段")
    except InvalidTag:
        raise BadPassword("Recovery Password 错误或数据被篡改")


# ---------- 本机 Credential Manager ----------
def save_dek_local(dek: bytes) -> None:
    import win32cred
    b64 = base64.b64encode(dek).decode("ascii")
    cred = {
        "Type": win32cred.CRED_TYPE_GENERIC,
        "TargetName": CM_TARGET,
        "UserName": CM_USER,
        "CredentialBlob": b64,
        "Persist": win32cred.CRED_PERSIST_LOCAL_MACHINE,
    }
    win32cred.CredWrite(cred, 0)


def load_dek_local():
    """从本机 Credential Manager 读取 DEK；不存在则抛 MissingKey"""
    import win32cred
    try:
        cred = win32cred.CredRead(CM_TARGET, win32cred.CRED_TYPE_GENERIC)
    except Exception:
        raise MissingKey("本机 Credential Manager 无 DEK（首次需 Setup / Recovery）")
    try:
        blob = cred["CredentialBlob"]
        b64 = blob.decode("utf-16-le") if isinstance(blob, bytes) else str(blob)
        return base64.b64decode(b64)
    except Exception:
        raise CorruptedData("本机 DEK 数据损坏")


def has_local_dek() -> bool:
    try:
        load_dek_local()
        return True
    except CryptoError:
        return False


def delete_dek_local(target: str = CM_TARGET) -> None:
    import win32cred
    try:
        win32cred.CredDelete(target, win32cred.CRED_TYPE_GENERIC, 0)
    except Exception:
        pass


# ---------- 通用命名 Secret（复用同一 Credential Manager 后端）----------
# 用途：Cloudflare API Token、Service Token 等不应落盘的凭据。
# 与 DEK 走同一后端（win32cred），仅把 TargetName 泛化成 WorkBuddy::<name>。
# 约定：secret 一律以本地机器持久化，不写入源码 / Git / 日志 / 明文文件。
CM_NS = "WorkBuddy::"


def secret_set(name: str, value: str, user: str = CM_USER) -> bool:
    """写入命名 Secret。value 直接以 UTF-16 存进 Credential Manager，不落盘。"""
    import win32cred
    win32cred.CredWrite({
        "Type": win32cred.CRED_TYPE_GENERIC,
        "TargetName": CM_NS + name,
        "UserName": user,
        "CredentialBlob": value,
        "Persist": win32cred.CRED_PERSIST_LOCAL_MACHINE,
    }, 0)
    return True


def secret_get(name: str):
    """读取命名 Secret；不存在返回 None（不抛异常，便于 Agent 判断）"""
    import win32cred
    try:
        cred = win32cred.CredRead(CM_NS + name, win32cred.CRED_TYPE_GENERIC)
    except Exception:
        return None
    blob = cred["CredentialBlob"]
    return blob.decode("utf-16-le") if isinstance(blob, bytes) else str(blob)


def secret_delete(name: str) -> bool:
    import win32cred
    try:
        win32cred.CredDelete(CM_NS + name, win32cred.CRED_TYPE_GENERIC, 0)
        return True
    except Exception:
        return False


def secret_exists(name: str) -> bool:
    return secret_get(name) is not None


# ---------- Recovery Bundle（不含明文密钥，可离线保存） ----------
def _recovery_payload(password: str, dek: bytes) -> dict:
    salt = os.urandom(SALT_LEN)
    kek = _kdf_derive(password, salt)
    wrap = _wrap_dek(dek, kek, aad=b"WBENC-recovery-v1")
    return {
        "format": FORMAT_NAME, "kind": "recovery", "version": FORMAT_VERSION,
        "kdf": {"name": KDF_NAME, "version": KDF_VERSION, "salt": base64.b64encode(salt).decode(),
                "n": SCRYPT_N, "r": SCRYPT_R, "p": SCRYPT_P},
        "wrap": wrap, "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def save_recovery(password: str, dek: bytes, path=None) -> dict:
    """生成 Recovery Bundle（JSON）。默认写 secrets_store\\recovery_bundle.json；调用方应提示用户离线另存。"""
    payload = _recovery_payload(password, dek)
    if path:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    return payload


def recover_dek(password: str, payload=None, path=None):
    """用 Recovery Password 从 bundle 恢复 DEK。bundle 可在任意位置（跨机）。"""
    if payload is None:
        if not path:
            from path_manager import RECOVERY_BUNDLE
            path = RECOVERY_BUNDLE
        try:
            with open(path, "r", encoding="utf-8") as f:
                payload = json.load(f)
        except Exception:
            raise BadFormat("无法读取 Recovery Bundle")
    if payload.get("format") != FORMAT_NAME or payload.get("kind") != "recovery":
        raise BadFormat("不是有效 Recovery Bundle")
    if payload.get("version") != FORMAT_VERSION:
        raise UnsupportedVersion(f"Recovery 版本 {payload.get('version')} 不支持")
    kdf = payload.get("kdf") or {}
    try:
        salt = base64.b64decode(kdf["salt"])
        kek = Scrypt(salt=salt, length=32, n=int(kdf.get("n", SCRYPT_N)),
                     r=int(kdf.get("r", SCRYPT_R)), p=int(kdf.get("p", SCRYPT_P))).derive(password.encode("utf-8"))
    except Exception:
        raise BadFormat("KDF 参数异常")
    return _unwrap_dek(payload["wrap"], kek, aad=b"WBENC-recovery-v1")


# ---------- 数据加密（每 bundle 唯一 nonce；AAD 仅元数据） ----------
def _aad(meta: dict) -> bytes:
    return json.dumps({"f": FORMAT_NAME, "v": FORMAT_VERSION, "m": meta or {}}, ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8")


def encrypt_bytes(dek: bytes, data: bytes, meta: dict = None) -> dict:
    nonce = os.urandom(NONCE_LEN)
    ct = AESGCM(dek).encrypt(nonce, data, _aad(meta))
    return {
        "format": FORMAT_NAME, "version": FORMAT_VERSION, "algo": ALGO,
        "meta": meta or {}, "nonce": base64.b64encode(nonce).decode(),
        "ciphertext": base64.b64encode(ct).decode(),
    }


def decrypt_bytes(dek: bytes, payload: dict) -> bytes:
    if payload.get("format") != FORMAT_NAME:
        raise BadFormat("不是 WBENC 数据包")
    if payload.get("version") != FORMAT_VERSION:
        raise UnsupportedVersion(f"版本 {payload.get('version')} 不支持")
    try:
        nonce = base64.b64decode(payload["nonce"])
        ct = base64.b64decode(payload["ciphertext"])
    except KeyError:
        raise CorruptedData("数据包缺少 nonce/ciphertext")
    except Exception:
        raise CorruptedData("数据包 base64 损坏")
    try:
        return AESGCM(dek).decrypt(nonce, ct, _aad(payload.get("meta")))
    except InvalidTag:
        raise AuthFailed("数据被篡改或密钥错误（AES-GCM 校验失败）")


# ---------- 文件 / 目录 Bundle ----------
def encrypt_file(dek: bytes, src, dst, meta: dict = None) -> dict:
    with open(src, "rb") as f:
        data = f.read()
    payload = encrypt_bytes(dek, data, meta)
    with open(dst, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    return payload


def decrypt_file(dek: bytes, src, dst) -> bytes:
    from pathlib import Path
    with open(src, "r", encoding="utf-8") as f:
        payload = json.load(f)
    data = decrypt_bytes(dek, payload)
    d = Path(dst)
    d.parent.mkdir(parents=True, exist_ok=True)
    d.write_bytes(data)
    return data


def make_dir_bundle(dek: bytes, dir_path, meta: dict = None) -> dict:
    """目录 → tar.gz（保留相对路径 + 文件列表 manifest）→ GCM 加密 → dict（可写 .enc.bundle）"""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for root, _, files in sorted(os.walk(dir_path)):
            for fn in sorted(files):
                fp = os.path.join(root, fn)
                tar.add(fp, arcname=os.path.relpath(fp, dir_path))
    buf.seek(0)
    meta = dict(meta or {})
    meta.setdefault("kind", "dir")
    payload = encrypt_bytes(dek, buf.read(), meta)
    payload["file_manifest"] = sorted(
        os.path.relpath(os.path.join(r, f), dir_path).replace(os.sep, "/")
        for r, _, fs in os.walk(dir_path) for f in fs)
    return payload


def restore_dir_bundle(dek: bytes, payload: dict, out_dir) -> int:
    """解密并还原目录（先解到临时目录，成功后整体移入 out_dir，失败不产生半成品）"""
    from pathlib import Path
    data = decrypt_bytes(dek, payload)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix=".wbundle_", dir=str(out_dir.parent))
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
            tar.extractall(tmp)
        for item in os.listdir(tmp):
            src = os.path.join(tmp, item)
            dst = os.path.join(out_dir, item)
            if os.path.isdir(dst):
                shutil.rmtree(dst)
            elif os.path.exists(dst):
                os.remove(dst)
            shutil.move(src, dst)
        return len(payload.get("file_manifest", []))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------- 顶层流程 ----------
def _key_bundle_path():
    global RECOVERY_KEY_BUNDLE
    if RECOVERY_KEY_BUNDLE is None:
        from path_manager import SECRETS_DIR
        RECOVERY_KEY_BUNDLE = SECRETS_DIR / "recovery_key.bundle.json"
    RECOVERY_KEY_BUNDLE.parent.mkdir(parents=True, exist_ok=True)
    return RECOVERY_KEY_BUNDLE


def setup_key() -> dict:
    """命令行初始化：随机 DEK（写本机 CM）+ 随机 32B Recovery Key（scrypt wrap DEK）写本机文件。
    调用方务必把返回的 key 离线保存；recovery_key.bundle.json 不含任何明文密钥。"""
    if has_local_dek():
        raise CryptoError("本机已有 DEK；如需重建请先 wipe-local")
    import secrets as _secrets
    key = _secrets.token_hex(32)
    dek = os.urandom(DEK_LEN)
    save_dek_local(dek)
    payload = save_recovery(key, dek, path=_key_bundle_path())
    kp = _key_bundle_path().with_name("recovery_key.txt")
    with open(kp, "w", encoding="utf-8") as f:
        f.write("Recovery Key (offline backup; never upload to Q drive)\n")
        f.write(key + "\n")
        f.write("Losing this key AND local credential manager => encrypted data unrecoverable\n")
    return {"recovery_key": key, "key_file": str(kp), "bundle": str(_key_bundle_path())}


def recover_with_key(key: str, path=None, persist_local: bool = True) -> bytes:
    """跨机/CM 丢失：Recovery Key 恢复 DEK（可选写回本机 CM）"""
    dek = recover_dek(key, path=path or _key_bundle_path())
    if persist_local:
        save_dek_local(dek)
    return dek


def setup(recovery_password: str, recovery_path=None) -> dict:
    """首次建立：随机 DEK → 存本机 CM → 生成 Recovery Bundle。返回 bundle 内容。"""
    if has_local_dek():
        raise CryptoError("本机已有 DEK；如需重建请先 delete_dek_local()")
    dek = os.urandom(DEK_LEN)
    save_dek_local(dek)
    return save_recovery(recovery_password, dek, path=recovery_path)


def unlock_from_recovery(recovery_password: str, payload=None, path=None, persist_local: bool = True) -> bytes:
    """跨机/CM 丢失时用 Recovery 恢复 DEK（可选写回本机 CM 完成本机自动解锁）。"""
    dek = recover_dek(recovery_password, payload=payload, path=path)
    if persist_local:
        save_dek_local(dek)
    return dek


def rotate_recovery_password(old_pwd: str, new_pwd: str, payload=None, path=None) -> dict:
    """修改 Recovery Password：仅重新 wrap DEK（不重加密任何数据）。
    需先以旧密码解锁 DEK（本机 CM 或旧 bundle）。"""
    dek = None
    try:
        dek = load_dek_local()
    except MissingKey:
        pass
    if dek is None:
        dek = recover_dek(old_pwd, payload=payload, path=path)
    return save_recovery(new_pwd, dek, path=path)


def storage_status() -> dict:
    from path_manager import RECOVERY_BUNDLE
    return {
        "local_dek": has_local_dek(),
        "recovery_bundle_exists": RECOVERY_BUNDLE.exists(),
        "format": FORMAT_NAME,
        "version": FORMAT_VERSION,
        "kdf": KDF_NAME,
    }


if __name__ == "__main__":
    import sys
    import json
    if len(sys.argv) > 1 and sys.argv[1] == "status":
        print(json.dumps(storage_status(), ensure_ascii=False, indent=2))
    elif len(sys.argv) > 1 and sys.argv[1] == "setup-key":
        out = setup_key()
        print("DEK 已写入本机 Credential Manager")
        print("Recovery Key(完整值见文件):", str(out["key_file"]))
        print("Recovery Bundle(不含密钥):", str(out["bundle"]))
        print("!!! 请将 recovery_key.txt 离线备份（勿入 Q 盘）；丢失将无法跨机恢复")
    elif len(sys.argv) > 1 and sys.argv[1] == "wipe-local":
        delete_dek_local()
        print("已删除本机 Credential Manager 中的 DEK")
    else:
        print(__doc__)