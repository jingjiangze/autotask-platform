# -*- coding: utf-8 -*-
"""SQLite 在线备份 / 恢复 / 校验（WAL 安全，备份期间不阻塞业务）
用法:
    python backup_manager.py backup             # 立即备份一次
    python backup_manager.py list               # 查看现有备份
    python backup_manager.py restore <文件>     # 校验备份后恢复（需先停止平台）

说明:
    - 备份使用 sqlite3.Connection.backup() 在线备份 API，平台运行中也可执行；
    - 备份后自动 PRAGMA integrity_check 校验，保留最近 KEEP=7 份；
    - restore 会先校验源文件，再替换 platform.db（替换前自动把当前库备份一份）。
"""
import os
import shutil
import socket
import sqlite3
import sys
import time

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(APP_DIR, "orders", "platform.db")
BK_DIR = os.path.join(APP_DIR, "backups")
KEEP = 7


def verify_backup(path):
    """备份可打开 + integrity_check 通过 才算有效"""
    try:
        c = sqlite3.connect(path)
        r = c.execute("PRAGMA integrity_check").fetchone()[0]
        n = c.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
        c.close()
        return r == "ok", f"integrity={r}, orders={n}"
    except Exception as e:
        return False, str(e)


def backup_database():
    """在线备份 + 校验 + 轮转，返回 (文件路径, 是否有效, 信息)"""
    os.makedirs(BK_DIR, exist_ok=True)
    dst = os.path.join(BK_DIR, time.strftime("platform_%Y%m%d_%H%M%S.db"))
    src = sqlite3.connect(DB_PATH)
    out = sqlite3.connect(dst)
    src.backup(out)  # WAL 下在线备份，不阻塞读写
    out.close()
    src.close()
    ok, msg = verify_backup(dst)
    rotate()
    return dst, ok, msg


def rotate():
    """只保留最近 KEEP 份备份，磁盘占用可预测"""
    files = sorted(f for f in os.listdir(BK_DIR)
                   if f.startswith("platform_") and f.endswith(".db"))
    for f in files[:-KEEP]:
        try:
            os.remove(os.path.join(BK_DIR, f))
        except OSError:
            pass


def _delete_file(path):
    """删除文件：os.remove 失败时用 Win32 DeleteFileW 兜底
    （WAL/SHM 等运行时文件在某些环境会被回收站机制拒绝删除）"""
    try:
        os.remove(path)
        return
    except OSError:
        pass
    try:
        import ctypes
        if not ctypes.windll.kernel32.DeleteFileW(path):
            raise OSError(f"DeleteFileW 失败: {path}")
    except Exception as e:
        raise OSError(f"无法删除 {path}: {e}")


def _platform_online():
    """平台（8766）是否在监听：在线时禁止恢复，避免与在线写并发造成半写窗口"""
    try:
        with socket.create_connection(("127.0.0.1", 8766), timeout=2):
            return True
    except OSError:
        return False


def backup_enc(keep=7):
    """在线备份 → integrity 校验 → AES-256-GCM 加密 → private/backups/platform_*.db.enc
    供 Q 同步/迁移使用；解密只在恢复端（crypto_manager.decrypt_file + verify_backup）。
    需先 crypto_manager setup-key（本机 CM 持有 DEK）。"""
    import crypto_manager as cm
    from path_manager import PRIVATE_BACKUP_DIR
    PRIVATE_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    dst, ok, msg = backup_database()
    if not ok:
        return None, False, f"备份失败: {msg}"
    dek = cm.load_dek_local()
    base = os.path.basename(dst)
    enc_dst = os.path.join(str(PRIVATE_BACKUP_DIR), base + ".enc")
    cm.encrypt_file(dek, dst, enc_dst, {"kind": "sqlite-backup", "src": base})
    # 轮转：只保留最近 keep 份加密备份
    files = sorted(f for f in os.listdir(str(PRIVATE_BACKUP_DIR)) if f.endswith(".db.enc"))
    for f in files[:-keep]:
        try:
            os.remove(os.path.join(str(PRIVATE_BACKUP_DIR), f))
        except OSError:
            pass
    return enc_dst, True, f"integrity={msg.split('=')[-1] if '=' in msg else 'ok'}"


def restore_database(src, force=False):
    """恢复：先校验源备份，再经 SQLite backup API 反向覆盖到线上库。
    优势：不依赖文件删除（WAL 可能被占用），且对现有连接安全；
    替换前自动备份当前库。平台在线时拒绝（除非 force=True，仅演练/停机后使用）。"""
    if _platform_online() and not force:
        raise RuntimeError("检测到平台正在运行（127.0.0.1:8766），请先停止平台再恢复；"
                           "确需在线演练请使用: restore <备份文件> --force")
    if not os.path.exists(src):
        raise FileNotFoundError(src)
    ok, msg = verify_backup(src)
    if not ok:
        raise RuntimeError(f"备份文件校验失败，拒绝恢复: {msg}")
    pre, ok2, msg2 = backup_database()  # 恢复前先留一份当前库
    # 用备份 API 把 src 的内容整体写入线上库（自动处理 WAL，无需删文件）
    src_conn = sqlite3.connect(src)
    dst_conn = sqlite3.connect(DB_PATH)
    src_conn.backup(dst_conn)
    dst_conn.close()
    src_conn.close()
    # 收尾：残余 WAL/SHM 若存在则尝试清理（失败不影响正确性）
    for suffix in ("-wal", "-shm"):
        p = DB_PATH + suffix
        if os.path.exists(p):
            try:
                _delete_file(p)
            except OSError:
                pass
    return pre, ok2, msg2


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "backup"
    if cmd == "backup":
        dst, ok, msg = backup_database()
        print(("成功" if ok else "失败"), dst, "|", msg)
        sys.exit(0 if ok else 1)
    elif cmd == "list":
        os.makedirs(BK_DIR, exist_ok=True)
        for f in sorted(os.listdir(BK_DIR)):
            p = os.path.join(BK_DIR, f)
            print(f, os.path.getsize(p), "bytes")
    elif cmd == "backup-enc":
        try:
            dst, ok, msg = backup_enc()
            print(("成功" if ok else "失败"), dst, "|", msg)
            sys.exit(0 if ok else 1)
        except Exception as e:
            print("备份加密失败:", type(e).__name__, e)
            sys.exit(1)
    elif cmd == "restore":
        if len(sys.argv) < 3:
            print("用法: python backup_manager.py restore <备份文件> [--force]")
            sys.exit(2)
        force = len(sys.argv) > 3 and sys.argv[3] == "--force"
        pre, ok2, msg2 = restore_database(sys.argv[2], force=force)
        print("已恢复。恢复前的当前库备份:", pre, "|", ("校验通过" if ok2 else msg2))
        print("请重新启动平台。")
    else:
        print(__doc__)
