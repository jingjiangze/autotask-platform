# -*- coding: utf-8 -*-
"""In-memory fake platform backend for Local adapter tests.

Mirrors the shapes the Local adapters depend on (order_platform.py): db() returns
a sqlite3 connection with Row factory (:64), hash_pw (:205), login_user (:208),
get_setting/set_setting (:503/:511), set_order/safe_set_order (:223/:233),
now_str (:228), order_dir (:486), RISK_PATTERNS (:627), query_courses (:1082),
QR_SESSIONS (:518), _kill_tree (:262), build_order_env (:581), run_chaoxing (:694),
run_zhs (:711).

It uses an in-memory SQLite database: the same SQL code paths as production,
without ever opening orders/platform.db. Scenario sentinels (SYN-*) let the
same test case drive the local and the synthetic side.
"""
from __future__ import annotations

import base64
import hashlib
import os
import sqlite3
from datetime import datetime
from typing import Dict, Optional, Tuple

# --- scenario sentinels shared by both sides of the contract tests ----------
ACCOUNT_OK = "SYN-USER-OK"
ACCOUNT_AUTH_FAIL = "SYN-AUTH-FAIL"
ACCOUNT_EMPTY = "SYN-EMPTY"
ACCOUNT_RATE_LIMIT = "SYN-RATE-LIMIT"
ACCOUNT_TRANSIENT = "SYN-TRANSIENT"

ORDER_RC_OK = "SYN-RC-OK"
ORDER_RC_TIMEOUT = "SYN-RC-TIMEOUT"
ORDER_RC_CRASH = "SYN-RC-CRASH"
ORDER_RC_BUSINESS = "SYN-RC-BUSINESS"

FAKE_PNG = b"\x89PNG\r\n\x1a\n" + b"fake-png-payload"

SCHEMA = """
CREATE TABLE users(id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL,
                   pw_hash TEXT NOT NULL, is_admin INTEGER DEFAULT 0,
                   created_at TEXT DEFAULT (datetime('now','localtime')));
CREATE TABLE orders(id TEXT PRIMARY KEY, user_id INTEGER, product TEXT DEFAULT '',
                    platform TEXT, account TEXT, password TEXT, courses TEXT,
                    status TEXT DEFAULT 'pending', note TEXT DEFAULT '',
                    qr_state TEXT DEFAULT '', created_at TEXT, started_at TEXT,
                    finished_at TEXT, exit_code INTEGER, worker_running INTEGER DEFAULT 0,
                    env_profile TEXT DEFAULT '', risk_flags TEXT DEFAULT '',
                    speed REAL DEFAULT 0, pid INTEGER DEFAULT 0, attempt INTEGER DEFAULT 0,
                    heartbeat_at TEXT DEFAULT '');
CREATE TABLE products(id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT UNIQUE, name TEXT,
                      desc TEXT, price TEXT, platform TEXT, enabled INTEGER DEFAULT 1,
                      sort INTEGER DEFAULT 0);
CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT);
"""

# Mirrors order_platform.py:627-633 (data table, kept in sync deliberately).
RISK_PATTERNS = (
    ("captcha", ["验证码", "captcha", "滑块", "checktype", "打码", "安全验证"]),
    ("forbidden", ["403", "forbidden", "无权限", "没有被授权", "无权访问"]),
    ("risk_ctrl", ["风控", "异常行为", "操作频繁", "次数限制", "too many", "禁止", "冻结", "封禁", "限制访问"]),
    ("login_fail", ["登录失败", "用户名或密码错误", "账号或密码", "login failed", "login error", "cookie"]),
    ("network", ["max retries", "connectionerror", "timed out", "connection aborted", "连接失败", "remote end closed"]),
)

FAKE_REG_CODE = "FAKE-REG-CODE"

COURSE_SUCCESS = [{"id": "FAKE-COURSE-001", "name": "Fake Course One", "kind": "知到课"},
                  {"id": "FAKE-COURSE-002", "name": "Fake Course Two", "kind": "共享课"}]


class FakeBackend:
    DEFAULT_REG_CODE = FAKE_REG_CODE
    RISK_PATTERNS = RISK_PATTERNS

    def __init__(self, work_dir: str, qr_scenario: str = "confirm"):
        self._conn = sqlite3.connect(":memory:")
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        for code, name, plat, sort in (("fake_cx", "Fake 学习通", "chaoxing", 1),
                                       ("fake_zhs", "Fake 知到", "zhs", 2),
                                       ("fake_qr", "Fake 扫码", "zhsqr", 3)):
            self._conn.execute("INSERT INTO products(code,name,desc,price,platform,sort) "
                               "VALUES(?,?,?,?,?,?)", (code, name, "fake", "0", plat, sort))
        for k, v in (("reg_code", FAKE_REG_CODE), ("concurrency", "1"),
                     ("order_timeout_min", "10"), ("proxy_pool", "")):
            self._conn.execute("INSERT INTO settings(key,value) VALUES(?,?)", (k, v))
        self._conn.commit()
        self.work_dir = work_dir
        self.QR_SESSIONS: Dict[str, dict] = {}
        self.qr_scenario = qr_scenario
        self.calls: list = []
        self.killed: list = []
        self.risk_by_oid: Dict[str, str] = {}

    # ------------------------------------------------------------ core helpers
    def db(self):
        return self._conn

    @staticmethod
    def hash_pw(pw, salt="wk"):
        return hashlib.sha256((salt + pw).encode()).hexdigest()

    @staticmethod
    def now_str():
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def login_user(self, name, pw):
        r = self._conn.execute("SELECT * FROM users WHERE username=?", (name,)).fetchone()
        return bool(r) and r["pw_hash"] == self.hash_pw(pw)

    def get_setting(self, key, default=""):
        r = self._conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return r["value"] if r else default

    def set_setting(self, key, value):
        self._conn.execute("INSERT INTO settings(key,value) VALUES(?,?) "
                           "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))
        self._conn.commit()

    def set_order(self, oid, **kw):
        sets = ", ".join(f"{k}=?" for k in kw)
        self._conn.execute(f"UPDATE orders SET {sets} WHERE id=?", (*kw.values(), oid))
        self._conn.commit()

    def safe_set_order(self, oid, **kw):
        try:
            self.set_order(oid, **kw)
            return True
        except sqlite3.OperationalError:
            return False

    @staticmethod
    def encrypt_secret(plain: str) -> str:
        return "cipher:" + base64.b64encode(plain.encode()).decode()

    @staticmethod
    def decrypt_secret(stored: str) -> str:
        return base64.b64decode(stored.split(":", 1)[1]).decode()

    def order_dir(self, oid: str) -> str:
        return os.path.join(self.work_dir, oid)

    def scan_risk(self, oid: str) -> str:
        return self.risk_by_oid.get(oid, "")

    # ------------------------------------------------------- platform-ish APIs
    def query_courses(self, platform, account="", password="", cookie_path=None):
        self.calls.append(("query_courses", platform, account, password, cookie_path))
        sentinel = (account or "").upper()
        if sentinel == ACCOUNT_AUTH_FAIL:
            return False, "登录失败: 用户名或密码错误"
        if sentinel == ACCOUNT_RATE_LIMIT:
            return False, "查询服务繁忙（并发受限），请稍后再试"
        if sentinel == ACCOUNT_TRANSIENT:
            return False, "timeout: Max retries exceeded with url"
        if sentinel == ACCOUNT_EMPTY:
            return True, []
        if platform == "zhs_cookie" and not cookie_path:
            return False, "cookies 不存在，请先登录"
        return True, [dict(c) for c in COURSE_SUCCESS]

    def build_order_env(self, oid, o):
        return {"WK_ACCOUNT": o.get("account", "")}, self.work_dir, {"platform": "fake", "proxy": "none"}

    def run_chaoxing(self, oid, o, env, work):
        return self._rc_for(o)

    def run_zhs(self, oid, o, env, work):
        return self._rc_for(o)

    @staticmethod
    def _rc_for(o) -> int:
        sentinel = (o.get("account") or "").upper()
        return {ORDER_RC_TIMEOUT: -9, ORDER_RC_CRASH: -1, ORDER_RC_BUSINESS: 1}.get(sentinel, 0)

    def _kill_tree(self, pid):
        self.killed.append(pid)

    # ---------------------------------------------------------------- QR side
    def qr_create_session(self, oid):
        self.QR_SESSIONS[oid] = {"img": FAKE_PNG, "state": "waiting", "ts": 0.0,
                                 "polls": 0, "oid": oid}
        self._conn.execute("INSERT OR REPLACE INTO orders(id,user_id,product,platform,account,"
                           "password,courses,status,qr_state,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                           (oid, 1, "fake_qr", "zhs", "", "", "", "waiting_qr", "waiting",
                            self.now_str()))
        self._conn.commit()
        return {"state": "waiting"}

    def qr_advance(self, oid):
        st = self.QR_SESSIONS.get(oid)
        if st is None:
            return "unknown"
        if st["state"] in ("confirmed", "expired", "canceled", "error"):
            return st["state"]
        st["polls"] += 1
        if self.qr_scenario == "confirm":
            st["state"] = {1: "waiting", 2: "scanned"}.get(st["polls"], "confirmed")
        elif self.qr_scenario == "expire":
            st["state"] = "scanned" if st["polls"] == 1 else "expired"
        elif self.qr_scenario == "cancel":
            st["state"] = "canceled"
        elif self.qr_scenario == "error":
            st["state"] = "error"
        else:
            st["state"] = "waiting"
        if st["state"] == "confirmed":
            self.set_order(oid, status="pending", qr_state="confirmed", note="扫码登录成功")
        elif st["state"] in ("expired", "canceled", "error"):
            self.set_order(oid, status="canceled", qr_state=st["state"], finished_at=self.now_str())
        else:
            self.set_order(oid, qr_state=st["state"])
        return st["state"]

    # ------------------------------------------------------------------ utils
    def fetch_user(self, username) -> Optional[sqlite3.Row]:
        return self._conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()

    def add_user(self, username: str, password: str, is_admin: bool = False) -> int:
        cur = self._conn.execute("INSERT INTO users(username,pw_hash,is_admin) VALUES(?,?,?)",
                                 (username, self.hash_pw(password), 1 if is_admin else 0))
        self._conn.commit()
        return int(cur.lastrowid)

    def make_order(self, oid: str, platform: str = "zhs", account: str = ACCOUNT_OK,
                   courses: str = "", status: str = "pending", password: str = "pw") -> str:
        self._conn.execute("INSERT INTO orders(id,user_id,product,platform,account,password,"
                           "courses,status,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                           (oid, 1, f"{platform}_video", platform, account,
                            self.encrypt_secret(password), courses, status, self.now_str()))
        self._conn.commit()
        return oid
