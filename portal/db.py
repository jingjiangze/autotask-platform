# -*- coding: utf-8 -*-
"""db.py — SQLite 访问层 + 建表 + 种子数据

规范约束：
  §25 WAL / busy_timeout / 参数化 SQL / 短事务 / 索引
  §26 门户 DB 与 OpenList DB 完全分离（本文件只碰 portal.db）
  §53 事务不跨网络请求（本层不做任何 HTTP）
"""
import os
import sqlite3
import time
from contextlib import contextmanager

import config

SCHEMA_VERSION = 1

DDL = """
CREATE TABLE IF NOT EXISTS schema_meta (
  key TEXT PRIMARY KEY, value TEXT
);

CREATE TABLE IF NOT EXISTS categories (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  slug TEXT UNIQUE NOT NULL,
  description TEXT DEFAULT '',
  icon TEXT DEFAULT '',
  color TEXT DEFAULT '',
  sort_order INTEGER NOT NULL DEFAULT 100,
  status TEXT NOT NULL DEFAULT 'published' CHECK(status IN ('published','hidden')),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS resources (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  slug TEXT UNIQUE,
  description TEXT DEFAULT '',
  openlist_path TEXT NOT NULL UNIQUE,
  cover TEXT DEFAULT '',
  category_id INTEGER REFERENCES categories(id) ON DELETE SET NULL,
  status TEXT NOT NULL DEFAULT 'draft'
         CHECK(status IN ('draft','published','hidden','archived')),
  is_featured INTEGER NOT NULL DEFAULT 0,
  sort_order INTEGER NOT NULL DEFAULT 100,
  version TEXT DEFAULT '',
  author TEXT DEFAULT '',
  view_count INTEGER NOT NULL DEFAULT 0,
  portal_click_count INTEGER NOT NULL DEFAULT 0,
  path_ok INTEGER DEFAULT 1,
  path_checked_at TEXT,
  path_check_msg TEXT DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  published_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_res_status_sort
  ON resources(status, is_featured DESC, sort_order, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_res_cat ON resources(category_id);

CREATE TABLE IF NOT EXISTS tags (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  slug TEXT UNIQUE NOT NULL
);

CREATE TABLE IF NOT EXISTS resource_tags (
  resource_id INTEGER NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
  tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
  PRIMARY KEY(resource_id, tag_id)
);
CREATE INDEX IF NOT EXISTS idx_rt_tag ON resource_tags(tag_id);

CREATE TABLE IF NOT EXISTS announcements (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  content TEXT DEFAULT '',
  status TEXT NOT NULL DEFAULT 'published' CHECK(status IN ('published','hidden')),
  pinned INTEGER NOT NULL DEFAULT 0,
  sort_order INTEGER NOT NULL DEFAULT 100,
  published_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS homepage_modules (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  module_key TEXT UNIQUE NOT NULL,
  title TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  sort_order INTEGER NOT NULL DEFAULT 100,
  config_json TEXT DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS site_settings (
  key TEXT PRIMARY KEY,
  value TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS access_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  ip TEXT DEFAULT '',
  action TEXT DEFAULT '',
  resource_id INTEGER,
  detail TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_log_ts ON access_log(ts);
"""

DEFAULT_MODULES = [
    ("hero", "顶部横幅", 1, 10),
    ("announcement", "公告", 1, 20),
    ("categories", "分类", 1, 30),
    ("featured", "精选资源", 1, 40),
    ("latest", "最新资源", 1, 50),
    ("popular", "热门资源", 1, 60),
    ("tags", "标签", 1, 70),
    ("footer_note", "页脚说明", 1, 80),
]

DEFAULT_SETTINGS = {
    "site_title": "资源分享",
    "site_description": "一个简洁的资源导航与分享站",
    "hero_title": "资源分享中心",
    "hero_subtitle": "一个简洁的资源介绍和搜索入口",
    "hero_text": "按分类浏览，或直接搜索你需要的资源。",
    "logo": "",
    "favicon": "",
    "main_color": "#2f6feb",
    "footer": "本站仅提供资源索引与访问入口，文件由 OpenList 提供。",
    "seo_keywords": "",
    "seo_description": "",
    "openlist_public_base": config.OPENLIST_PUBLIC_BASE,
    # 公开文件层根目录对应的 OpenList 逻辑路径（guest 的 base_path）
    "openlist_public_root": "/quark/公开分享",
}

DEFAULT_CATEGORIES = [
    ("软件", "software", "应用与工具", "🧩", "#2f6feb"),
    ("学习", "study", "课程与资料", "📚", "#16a34a"),
    ("设计", "design", "设计素材与模板", "🎨", "#db2777"),
    ("开发", "dev", "开发资源", "⚙️", "#7c3aed"),
    ("文档", "docs", "文档与手册", "📄", "#0891b2"),
    ("其他", "other", "未分类", "📦", "#6b7280"),
]


def now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def connect():
    con = sqlite3.connect(config.DB_PATH, timeout=10, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=5000")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA foreign_keys=ON")
    return con


@contextmanager
def tx():
    """短事务：只包 DB 操作，绝不跨网络请求（§53）"""
    con = connect()
    try:
        con.execute("BEGIN IMMEDIATE")
        yield con
        con.execute("COMMIT")
    except Exception:
        try:
            con.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        con.close()


@contextmanager
def ro():
    con = connect()
    try:
        yield con
    finally:
        con.close()


def init_db():
    """建表 + 种子。幂等：可重复执行。"""
    con = connect()
    try:
        con.executescript(DDL)
        cur = con.execute("SELECT value FROM schema_meta WHERE key='version'")
        row = cur.fetchone()
        if row is None:
            con.execute("INSERT INTO schema_meta(key,value) VALUES('version',?)",
                        (str(SCHEMA_VERSION),))

        ts = now()
        for key, title, enabled, order in DEFAULT_MODULES:
            con.execute(
                "INSERT OR IGNORE INTO homepage_modules(module_key,title,enabled,sort_order)"
                " VALUES(?,?,?,?)", (key, title, enabled, order))
        for k, v in DEFAULT_SETTINGS.items():
            con.execute("INSERT OR IGNORE INTO site_settings(key,value) VALUES(?,?)", (k, v))
        for name, slug, desc, icon, color in DEFAULT_CATEGORIES:
            con.execute(
                "INSERT OR IGNORE INTO categories(name,slug,description,icon,color,"
                "sort_order,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (name, slug, desc, icon, color, 100, "published", ts, ts))
        con.commit()
    finally:
        con.close()


def get_settings():
    with ro() as con:
        return {r["key"]: r["value"] for r in con.execute("SELECT key,value FROM site_settings")}


def set_setting(key, value):
    with tx() as con:
        con.execute(
            "INSERT INTO site_settings(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))


def get_modules(only_enabled=False):
    sql = "SELECT * FROM homepage_modules"
    if only_enabled:
        sql += " WHERE enabled=1"
    sql += " ORDER BY sort_order, id"
    with ro() as con:
        return [dict(r) for r in con.execute(sql)]


def log_access(ip, action, resource_id=None, detail=""):
    try:
        with tx() as con:
            con.execute("INSERT INTO access_log(ts,ip,action,resource_id,detail)"
                        " VALUES(?,?,?,?,?)", (now(), ip or "", action, resource_id, detail[:300]))
    except Exception:
        pass


if __name__ == "__main__":
    init_db()
    print("portal.db 初始化完成:", config.DB_PATH)
