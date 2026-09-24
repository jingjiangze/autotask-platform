# -*- coding: utf-8 -*-
"""stage-cloud-02 — 本地功能基线（隔离运行真实 order_platform.py）

方案：
  把仓库根的 order_platform.py **字节级原样复制**到 pytest 临时目录，以该目录
  作为 APP_DIR 启动真实平台（waitress 子进程）。真实代码的 APP_DIR 派生自
  __file__，因此 SQLite / secrets_store / 订单目录 / 日志全部落在临时目录，
  对 D:/web 生产环境**零接触**（测试进程绝不 import 真实模块、绝不连真实库）。

  关键事实（order_platform.py 已提交版，:937-946）：import 即执行 init_db /
  ensure_admin_password / migrate_* / recover_stale_orders 并启动
  order_watchdog / concurrency_manager / qr_janitor / housekeeping 四个守护
  线程 —— 因此任何"进程内 import + 补丁"方案都会触碰生产库或误启动真实
  worker，必须用子进程 + 独立 APP_DIR。

隔离与确定性手段（全部使用真实代码自带机制，无 Mock）：
  - 预置 settings.last_backup = now  → housekeeping 跳过每日备份
  - 预置 settings.paused = "1"       → worker 线程空转（order_platform.py:746），
    由 EXECUTION 用例显式解除以驱动真实认领

真实第三方（学习通 / 知到 passport.zhihuishu.com）交互不在基线范围，
按 stage-cloud-02 规则登记为 NOT AVAILABLE，禁止用 Mock 伪造 PASS。
"""
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest
import requests

ROOT = Path(__file__).resolve().parents[2]
PYEXE = sys.executable
REG_CODE = "JJZ-2026"
BASELINE_ACCOUNT = "baseline-acc-ORDA1"
BASELINE_PW = "baseline-pw-ORDA1"


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wait_health(base: str, timeout: float = 60.0) -> None:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            r = requests.get(base + "/health", timeout=3)
            if r.status_code == 200:
                return
            last = r.status_code
        except Exception as e:  # noqa: BLE001
            last = repr(e)
        time.sleep(0.5)
    raise RuntimeError(f"隔离平台 /health 未就绪（最后状态: {last}），见 _baseline_server.log")


class PlatformHandle:
    """隔离运行中的真实平台句柄"""

    def __init__(self, root: Path, port: int, proc: subprocess.Popen):
        self.root = root
        self.port = port
        self.proc = proc
        self.base = f"http://127.0.0.1:{port}"

    # ---- 直接访问隔离 SQLite（WAL，与平台并发安全） ----
    @property
    def db_path(self) -> Path:
        return self.root / "orders" / "platform.db"

    def sql(self, query: str, args: tuple = ()) -> list:
        conn = sqlite3.connect(str(self.db_path), timeout=15)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(query, args).fetchall()
            conn.commit()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def set_setting(self, key: str, value: str) -> None:
        self.sql(
            "INSERT INTO settings(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

    # ---- HTTP 助手（自动带同源 Origin/Referer，满足 _csrf_guard） ----
    def post(self, sess: requests.Session, path: str, data: dict, **kw):
        headers = kw.pop("headers", {})
        headers.setdefault("Origin", self.base)
        headers.setdefault("Referer", self.base + "/")
        return sess.post(self.base + path, data=data, headers=headers, **kw)

    def register(self, sess: requests.Session, name: str, pw: str,
                 code: str = REG_CODE):
        return self.post(sess, "/register",
                         {"username": name, "password": pw, "reg_code": code},
                         allow_redirects=False)

    def login(self, sess: requests.Session, name: str, pw: str):
        return self.post(sess, "/login", {"username": name, "password": pw},
                         allow_redirects=False)

    def admin_password(self) -> str:
        """真实启动路径产物：ensure_admin_password 写入的隔离管理员口令"""
        f = self.root / "secrets_store" / "admin_password.txt"
        m = re.search(r"admin / (.+)", f.read_text(encoding="utf-8"))
        assert m, "admin_password.txt 格式异常"
        return m.group(1).strip()


@pytest.fixture(scope="session")
def platform(tmp_path_factory):
    root = tmp_path_factory.mktemp("stage-cloud-02-baseline")

    # 1) 字节级复制真实平台代码（复制后校验哈希一致）
    src = ROOT / "order_platform.py"
    dst = root / "order_platform.py"
    shutil.copy2(src, dst)
    import hashlib
    assert hashlib.sha256(src.read_bytes()).hexdigest() == \
        hashlib.sha256(dst.read_bytes()).hexdigest(), "复制后代码不一致"

    # 2) 预置隔离库：跳过每日备份 + 暂停 worker（真实机制，非 Mock）
    (root / "orders").mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(root / "orders" / "platform.db"))
    conn.execute("CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('last_backup',?)",
                 (str(time.time()),))
    conn.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('paused','1')")
    conn.commit()
    conn.close()

    # 3) 子进程启动真实平台（waitress）
    port = _free_port()
    driver = Path(__file__).parent / "_server_driver.py"
    log_fp = open(root / "_baseline_server.log", "wb")
    env = dict(__import__("os").environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    proc = subprocess.Popen(
        [PYEXE, str(driver), str(root), str(port)],
        cwd=str(root), stdout=log_fp, stderr=subprocess.STDOUT, env=env)
    try:
        _wait_health(f"http://127.0.0.1:{port}")
        yield PlatformHandle(root, port, proc)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        log_fp.close()


def make_user(platform: PlatformHandle, name: str, pw: str) -> requests.Session:
    """注册 + 登录，返回带登录态的会话（注册口令用真实默认值）"""
    s = requests.Session()
    r = platform.register(s, name, pw)
    assert r.status_code == 302, f"注册失败: {r.status_code}"
    r = platform.login(s, name, pw)
    assert r.status_code == 302 and "wk_token" in s.cookies, f"登录失败: {r.status_code}"
    return s


@pytest.fixture(scope="session")
def users(platform):
    """共享登录态（注册接口限流 5 次/分钟/IP，全基线注册预算恰好 5 次：
    auth 3 次（含 2 次预期失败）+ 普通用户 2 次；管理员走引导口令不注册）"""
    class Users:
        a = None  # 普通用户（下单者）
        b = None  # 普通用户（越权探测者 / 非管理员）
    u = Users()
    u.a = make_user(platform, "baseline_ord_a", "pass1234")
    u.b = make_user(platform, "baseline_ord_b", "pass1234")
    return u
