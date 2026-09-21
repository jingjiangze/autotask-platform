# -*- coding: utf-8 -*-
"""config.py — 门户配置（路径 / 常量 / 环境）

原则：不硬编码业务配置（那些进 DB 的 site_settings）；这里只放"跑起来就固定"的东西。
"""
import os

# ---------- 路径 ----------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_DIR = os.path.join(BASE_DIR, "templates")
STATIC_DIR = os.path.join(BASE_DIR, "static")
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "portal.db")
LOG_PATH = os.path.join(BASE_DIR, "logs", "portal.log")

# ---------- 服务 ----------
PORTAL_HOST = "127.0.0.1"          # 只绑回环，绝不绑全网卡（规范 §二十一）
PORTAL_PORT = 8777
PORTAL_THREADS = 8

OPENLIST_BASE = "http://127.0.0.1:5244"
OPENLIST_CONNECT_TIMEOUT = 3
OPENLIST_READ_TIMEOUT = 8
OPENLIST_RETRY_NETWORK = 2          # 仅网络类错误重试；403/404 不重试（规范 §五十四/五十五）

# 公众侧访问 OpenList 的地址（用于详情页"浏览/下载"跳转，不用于 API 调用）
OPENLIST_PUBLIC_BASE = "https://fs.jiangjiangze.icu"
# 公开文件层根对应的 OpenList 路径（与 OpenList guest 的 base_path 保持一致）
OPENLIST_PUBLIC_ROOT = "/quark/公开分享"

# ---------- 凭据条目名（Windows 凭据管理器，规范 §十九） ----------
CRED_OPENLIST_USER = "portal_openlist_user"
CRED_OPENLIST_PASS = "portal_openlist_pass"
CRED_PORTAL_ADMIN = "portal_admin_password"     # 仅首次生成时暂存，便于用户取回

# ---------- 会话 ----------
SESSION_COOKIE_NAME = "wk_portal"
SESSION_LIFETIME_HOURS = 12
LOGIN_RATE_LIMIT = 10               # 每 IP 每分钟
LOGIN_RATE_WINDOW = 60

# ---------- 敏感词（目录选择告警，规范 §四十四） ----------
SENSITIVE_DIR_KEYWORDS = [
    "backup", "bak", "secret", "config", "database", "db", "cookie", "token",
    "order", "private", "temp", "tmp", "log", "key", "credential", "password",
    "备份", "密钥", "订单", "私密", "临时", "日志",
]

for d in (DATA_DIR, os.path.dirname(LOG_PATH)):
    os.makedirs(d, exist_ok=True)
