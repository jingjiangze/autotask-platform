# -*- coding: utf-8 -*-
"""path_manager.py — 轻量路径统一模块（逻辑名 → 绝对路径）
原则：项目根一律来自 __file__（禁止 os.getcwd 作为根来源）；物理结构以兼容现有代码为主，不强制搬迁；
本模块供新增模块（crypto / sync / migration / backup-enc）统一取路径，业务代码不强依赖。
目标结构（Q 层为后续同步/公开隔离）：
    PROJECT_ROOT(=APP_DIR)
    ├─ public/      （可公开层，未来同步/公开 root 用）
    ├─ private/encrypted  （加密 bundle/备份本地暂存）
    ├─ private/backups    （加密数据库备份）
    ├─ runtime/     （短生命周期明文，退出即清）
"""
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
APP_DIR = PROJECT_ROOT

ORDERS_DIR = APP_DIR / "orders"
DB_PATH = ORDERS_DIR / "platform.db"
FUCK_DIR = APP_DIR / "fuckCourse"
CF_DIR = APP_DIR / "cf"
STATIC_DIR = APP_DIR / "static"
TABLER_PKG_DIR = APP_DIR / "tabler_pkg"
ZHES_SCRIPT_DIR = APP_DIR / "zhs_script"
BACKUP_DIR = APP_DIR / "backups"
SECRETS_DIR = APP_DIR / "secrets_store"
TOOLS_DIR = APP_DIR / "tools"
TESTS_DIR = APP_DIR / "tests"

PUBLIC_DIR = APP_DIR / "public"
PRIVATE_DIR = APP_DIR / "private"
ENCRYPTED_DIR = PRIVATE_DIR / "encrypted"
PRIVATE_BACKUP_DIR = PRIVATE_DIR / "backups"
RUNTIME_DIR = APP_DIR / "runtime"
RECOVERY_BUNDLE = SECRETS_DIR / "recovery_bundle.json"

# OpenList 映射目标（Q 盘挂载后使用；未挂载时同步目标为 None）
QWEB_DIR = Path("Q:/Web")


def ensure_dir(path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def is_network_path(path) -> bool:
    """网络/映射盘判断（Q:\\、UNC、non-NTFS 常见标志）"""
    p = str(path)
    return p.startswith("\\\\") or (len(p) >= 2 and p[1] == ":" and p[0].isalpha() and str(path).upper().startswith("Q:"))


if __name__ == "__main__":
    for name, p in [("PROJECT_ROOT", PROJECT_ROOT), ("DB_PATH", DB_PATH),
                    ("ENCRYPTED_DIR", ENCRYPTED_DIR), ("QWEB_DIR", QWEB_DIR)]:
        print(f"{name} = {p}  exists={p.exists()}  net={is_network_path(p)}")