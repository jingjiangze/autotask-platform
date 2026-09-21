# -*- coding: utf-8 -*-
"""自动任务下单平台 v4（Tabler UI 重制）
================================================
前端基于 Tabler 1.5.1 官方组件体系（本地 vendor，不依赖外网 CDN），暗色主题。
结构: 商品橱窗 / 下单(含知到扫码) / 访客查单 / 我的订单 / 批量下单 / 管理看板
启动: python order_platform.py  →  http://127.0.0.1:8766   (admin / admin123)
"""
import base64
import hashlib
import html
import hmac
import json
import os
import random
import re
import shutil
import sqlite3
import subprocess
import threading
import time
import uuid
from datetime import datetime

import requests
from flask import Flask, request, redirect, jsonify, Response, make_response

APP_DIR = os.path.dirname(os.path.abspath(__file__))
FUCK_DIR = os.path.join(APP_DIR, "fuckCourse")
ORDER_DIR = os.path.join(APP_DIR, "orders")
DB_PATH = os.path.join(ORDER_DIR, "platform.db")
os.makedirs(ORDER_DIR, exist_ok=True)

PYEXE = r"C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
# 会话/口令哈希密钥：外置到本地文件（首启自动生成，删除该文件并重启可轮换，
# 轮换后所有用户口令哈希失效，管理员口令需重新设置）
_SECRET_FILE = os.path.join(APP_DIR, "secrets_store", "secret_key.txt")

def _load_secret():
    try:
        if os.path.exists(_SECRET_FILE):
            v = open(_SECRET_FILE, encoding="utf-8").read().strip()
            if v:
                return v
    except Exception:
        pass
    try:
        os.makedirs(os.path.dirname(_SECRET_FILE), exist_ok=True)
        v = base64.b64encode(os.urandom(32)).decode()
        with open(_SECRET_FILE, "w", encoding="utf-8") as f:
            f.write(v)
        return v
    except Exception:
        return "wk-platform-local-secret-2026"  # 文件系统异常时的兜底

SECRET = _load_secret()
MAX_CONCURRENCY = 10   # worker 池硬上限（机器扛得住可继续调大）

# ================= DB =================
def db():
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    # WAL 模式：读写并发不互相阻塞（worker 写 + 页面读）
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=15000")
    except sqlite3.OperationalError:
        pass
    return conn

# ---- 资源控制 ----
def free_mem_mb():
    """可用物理内存（MB），不依赖第三方库"""
    try:
        import ctypes

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

        st = MEMORYSTATUSEX()
        st.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))
        return int(st.ullAvailPhys / 1024 / 1024)
    except Exception:
        return 9999

def tail_keep(path, keep_bytes=None):
    """日志文件裁剪：只保留尾部 keep_bytes，防止无限膨胀"""
    keep = keep_bytes or int(get_setting("log_keep_kb", "300")) * 1024
    try:
        if os.path.exists(path) and os.path.getsize(path) > keep * 2:
            with open(path, "rb") as f:
                f.seek(-keep, os.SEEK_END)
                data = f.read()
            with open(path, "wb") as f:
                f.write("...[已裁剪历史日志]...\n".encode("utf-8") + data)
    except Exception:
        pass

class RollingLog:
    """滚动日志写入器：子进程输出经此写盘，文件体积恒定，不占内存"""

    def __init__(self, path, keep_kb):
        self.path = path
        self.keep = keep_kb * 1024

    @staticmethod
    def _scrub(chunk):
        """日志落盘前对常见明文口令/token/api-key 形态脱敏（仅平台侧写盘，不触碰引擎）"""
        try:
            s = chunk.decode("utf-8", errors="replace")
            s = re.sub(r"(?i)(password|pwd|passwd|token|api[_-]?key|secret|authorization)\s*([=:])\s*([^\s,;]+)",
                       r"\1\2****", s)
            return s.encode("utf-8", errors="replace")
        except Exception:
            return chunk

    def run(self, proc):
        f = None
        try:
            f = open(self.path, "ab")
            while True:
                chunk = proc.stdout.readline()
                if not chunk:
                    if proc.poll() is not None:
                        break
                    time.sleep(0.05)
                    continue
                f.write(self._scrub(chunk))
                if os.path.getsize(self.path) > self.keep * 3:  # 超过阈值就裁剪
                    f.close()
                    with open(self.path, "rb") as rf:
                        rf.seek(-self.keep, os.SEEK_END)
                        data = rf.read()
                    with open(self.path, "wb") as wf:
                        wf.write("...[已裁剪历史日志]...\n".encode("utf-8") + data)
                    f = open(self.path, "ab")  # 重建句柄，避免偏移错位引发重复重写
        except Exception:
            pass
        finally:
            try:
                if f:
                    f.close()
            except Exception:
                pass
            try:
                proc.stdout.close()
            except Exception:
                pass

# 子进程优先级：低于正常（保证前台使用流畅）
BELOW_NORMAL = 0x00004000
CREATE_NO_WINDOW = 0x08000000

def init_db():
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            pw_hash TEXT NOT NULL,
            is_admin INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now','localtime')));
        CREATE TABLE IF NOT EXISTS orders(
            id TEXT PRIMARY KEY,
            user_id INTEGER,
            product TEXT DEFAULT '',
            platform TEXT, account TEXT, password TEXT, courses TEXT,
            status TEXT DEFAULT 'pending',
            note TEXT DEFAULT '', qr_state TEXT DEFAULT '',
            created_at TEXT, started_at TEXT, finished_at TEXT,
            exit_code INTEGER, worker_running INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS products(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE, name TEXT, desc TEXT, price TEXT,
            platform TEXT, enabled INTEGER DEFAULT 1, sort INTEGER DEFAULT 0);
        """)
        for col, ddl in [("product", "TEXT DEFAULT ''"), ("env_profile", "TEXT DEFAULT ''"),
                         ("risk_flags", "TEXT DEFAULT ''"), ("speed", "REAL DEFAULT 0"),
                         ("pid", "INTEGER DEFAULT 0"), ("attempt", "INTEGER DEFAULT 0"),
                         ("heartbeat_at", "TEXT DEFAULT ''")]:
            try:
                c.execute(f"ALTER TABLE orders ADD COLUMN {col} {ddl}")
            except sqlite3.OperationalError:
                pass
        c.execute("INSERT OR IGNORE INTO users(username,pw_hash,is_admin) VALUES(?,?,1)",
                  ("admin", hash_pw("admin123")))
        for code, name, desc, price, plat, sort in [
            ("cx_video", "学习通 · 视频刷取", "自动完成全部视频任务点，2 倍速，含自动签到与进度上报", "测试免费", "chaoxing", 1),
            ("zhs_video", "知到 · 视频刷取", "自动完成视频任务并上报进度，1.25 倍速拟真节奏", "测试免费", "zhs", 2),
            ("zhs_qr", "知到 · 扫码刷取", "无需密码，知到 App 扫码授权后自动执行，全程免密", "测试免费", "zhsqr", 3),
        ]:
            c.execute("INSERT OR IGNORE INTO products(code,name,desc,price,platform,sort) VALUES(?,?,?,?,?,?)",
                      (code, name, desc, price, plat, sort))

def hash_pw(pw, salt="wk"):
    return hmac.new(SECRET.encode(), (salt + pw).encode(), hashlib.sha256).hexdigest()

def login_user(name, pw):
    with db() as c:
        r = c.execute("SELECT * FROM users WHERE username=?", (name,)).fetchone()
    return r and r["pw_hash"] == hash_pw(pw)

def current_user():
    tok = request.cookies.get("wk_token", "")
    if not tok or ":" not in tok:
        return None
    uid, sig = tok.split(":", 1)
    if sig != hash_pw("u" + uid):
        return None
    with db() as c:
        return c.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()

def set_order(oid, **kw):
    with db() as c:
        sets = ", ".join(f"{k}=?" for k in kw)
        c.execute(f"UPDATE orders SET {sets} WHERE id=?", (*kw.values(), oid))

def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

MAX_RETRY = 1  # 超时/崩溃类错误的自动重试上限

def safe_set_order(oid, **kw):
    """状态写入兜底：DB 忙时短暂重试，避免兜底本身失败导致订单永久 running"""
    for i in range(3):
        try:
            set_order(oid, **kw)
            return True
        except sqlite3.OperationalError:
            time.sleep(1 + i)
    try:
        with open(os.path.join(APP_DIR, "platform_error.log"), "a", encoding="utf-8") as f:
            f.write(f"{now_str()} set_order({oid}) 连续失败: {kw}\n")
    except Exception:
        pass
    return False

def _pid_alive(pid):
    """仅凭 PID 判断进程是否存在（存在不等于属于我们，调用方需自行确认语义）"""
    if not pid:
        return False
    try:
        import ctypes
        h = ctypes.windll.kernel32.OpenProcess(0x00100000, False, int(pid))  # SYNCHRONIZE
        if not h:
            return False
        ctypes.windll.kernel32.CloseHandle(h)
        return True
    except Exception:
        return False

def _kill_tree(pid):
    """强制终止进程树并验证真正退出（最多等 5 秒）"""
    try:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                       capture_output=True, creationflags=CREATE_NO_WINDOW)
    except Exception:
        pass
    for _ in range(10):
        if not _pid_alive(pid):
            return True
        time.sleep(0.5)
    return not _pid_alive(pid)

def ensure_admin_password():
    """安全兜底：仅当管理员口令仍为默认 admin123 时重置为随机口令并写入 secrets_store"""
    pw_file = os.path.join(APP_DIR, "secrets_store", "admin_password.txt")
    if os.path.exists(pw_file):
        return
    with db() as c:
        r = c.execute("SELECT pw_hash FROM users WHERE username='admin'").fetchone()
    if not r or r["pw_hash"] != hash_pw("admin123"):
        return  # 已是自定义口令，不动
    chars = "ABCDEFGHJKMNPQRSTUVWXYZabcdefghjkmnpqrstuvwxyz23456789@#$%"
    pw = "".join(random.SystemRandom().choice(chars) for _ in range(16))
    with db() as c:
        c.execute("UPDATE users SET pw_hash=? WHERE username='admin'", (hash_pw(pw),))
    os.makedirs(os.path.dirname(pw_file), exist_ok=True)
    with open(pw_file, "w", encoding="utf-8") as f:
        f.write(f"admin / {pw}\n(自动生成于 {now_str()})\n")
    print("[安全] 检测到默认管理口令，已重置，新口令见 secrets_store/admin_password.txt")

def recover_stale_orders():
    """启动恢复：清理上次运行残留的 running/waiting_qr，杜绝永久 running"""
    try:
        with db() as c:
            rows = c.execute("SELECT * FROM orders WHERE status IN ('running','waiting_qr')").fetchall()
    except sqlite3.OperationalError:
        return
    n = 0
    for o in rows:
        oid, st = o["id"], o["status"]
        if st == "waiting_qr":
            # 扫码会话在内存中，重启后已丢失
            safe_set_order(oid, status="canceled", qr_state="expired",
                           finished_at=now_str(), note="平台重启导致扫码会话失效，请重新下单")
            n += 1
            continue
        alive = _pid_alive(o["pid"])
        if alive:
            # 进程仍在但已无人管理：终止整个任务树再标记
            if not _kill_tree(o["pid"]):
                # 旧进程确认未退出：不冒险回 pending（避免<旧进程+新进程>同单双执行）
                safe_set_order(oid, status="failed", worker_running=0, finished_at=now_str(),
                               note="重启恢复时旧进程未能终止，已标记 failed（人工确认后可重新下单）")
                n += 1
                continue
        if (o["attempt"] or 0) < MAX_RETRY:
            safe_set_order(oid, status="pending", worker_running=0, pid=0, heartbeat_at="",
                           note="平台重启时任务中断，已自动重新排队")
        else:
            safe_set_order(oid, status="failed", worker_running=0, finished_at=now_str(),
                           note="平台重启时任务中断(crashed)，已达重试上限")
        n += 1
    if n:
        print(f"[恢复] 已处理 {n} 个中断订单")

_RATE = {}

def rate_limit(key, limit, window=60):
    """轻量内存限流：按 (接口, IP) 计数，超限返回 False（不引入任何依赖）"""
    # IP 信任策略：仅当请求经本机 cloudflared 回源（对端=回环）才取 CF-Connecting-IP
    # （Cloudflare 边缘设置、不可伪造）；其余来源一律用 TCP 对端地址，避免伪造头绕过。
    ip = request.remote_addr or "?"
    if ip in ("127.0.0.1", "::1"):
        ip = (request.headers.get("CF-Connecting-IP") or "").strip() or ip
    k = (key, ip)
    t = time.time()
    lst = [x for x in _RATE.get(k, []) if t - x < window]
    if len(lst) >= limit:
        _RATE[k] = lst
        return False
    lst.append(t)
    _RATE[k] = lst
    if len(_RATE) > 5000:  # 防止字典无限膨胀
        _RATE.clear()
    return True

def same_origin_ok():
    """跨站防护：浏览器跨站 POST 必带 Origin/Referer，不匹配即拒绝；
    无头的非浏览器客户端（curl 等）无此头，放行（仍有登录态约束）。
    同时放行 http/https 两种主机形式（经 Cloudflare Tunnel 时 scheme 会被改写）"""
    origin = request.headers.get("Origin", "")
    referer = request.headers.get("Referer", "")
    if not origin and not referer:
        return True
    host = request.host
    allowed = {f"http://{host}", f"https://{host}"}
    if origin:
        return origin.rstrip("/") in allowed
    return any(referer.startswith(a + "/") for a in allowed)

# ---- 订单密码加密存储（纯标准库流式加密，防 DB 文件直接泄露明文）----
_ENC_PREFIX = "enc:v1:"
_ENC_KEY = hashlib.sha256((SECRET + "wk-enc").encode()).digest()

def _keystream(nonce: bytes, length: int) -> bytes:
    out = b""
    counter = 0
    while len(out) < length:
        out += hashlib.sha256(_ENC_KEY + nonce + counter.to_bytes(8, "big")).digest()
        counter += 1
    return out

def encrypt_secret(plain: str) -> str:
    if not plain or plain.startswith(_ENC_PREFIX):
        return plain
    nonce = os.urandom(16)
    data = plain.encode("utf-8")
    ks = _keystream(nonce, len(data))
    ct = bytes(a ^ b for a, b in zip(data, ks))
    return _ENC_PREFIX + base64.b64encode(nonce + ct).decode()

def decrypt_secret(stored: str) -> str:
    if not stored or not stored.startswith(_ENC_PREFIX):
        return stored  # 兼容历史明文
    try:
        raw = base64.b64decode(stored[len(_ENC_PREFIX):])
        nonce, ct = raw[:16], raw[16:]
        ks = _keystream(nonce, len(ct))
        return bytes(a ^ b for a, b in zip(ct, ks)).decode("utf-8")
    except Exception:
        return ""  # 密钥轮换后无法解密，任务会以认证失败收场

def migrate_encrypt_passwords():
    """把存量明文密码就地加密（幂等，只处理未加密的行）"""
    try:
        with db() as c:
            rows = c.execute("SELECT id, password FROM orders "
                             "WHERE password != '' AND password NOT LIKE 'enc:v1:%'").fetchall()
        n = 0
        for r in rows:
            enc = encrypt_secret(r["password"])
            if enc != r["password"]:
                with db() as c:
                    c.execute("UPDATE orders SET password=? WHERE id=?", (enc, r["id"]))
                n += 1
        if n:
            print(f"[安全] 已加密存量订单密码 {n} 条")
    except Exception as e:
        print("[安全] 密码加密迁移失败:", e)


import re as _re_year

def migrate_timestamp_years(year=2026):
    """存量时间文本补年份（旧格式 %m-%d %H:%M:%S → %Y-%m-%d %H:%M:%S）。
    存量数据全部创建于 2026-09（平台为当月上线，出处可考），统一补 2026；
    幂等：已带年份的行不处理。数据库写入前请确保已有备份（backup_manager）。"""
    pat = _re_year.compile(r"^(\d{2})-(\d{2}) (\d{2}:\d{2}:\d{2})$")
    n = 0
    try:
        with db() as c:
            for col in ("created_at", "started_at", "finished_at"):
                rows = c.execute(f"SELECT id, {col} v FROM orders WHERE {col} IS NOT NULL AND {col} != ''").fetchall()
                for r in rows:
                    m = pat.match(r["v"])
                    if m:
                        nv = f"{year}-{m.group(1)}-{m.group(2)} {m.group(3)}"
                        c.execute(f"UPDATE orders SET {col}=? WHERE id=?", (nv, r["id"]))
                        n += 1
    except Exception as e:
        print("[迁移] 时间戳年份补全失败:", e)
        return
    if n:
        print(f"[迁移] 已为 {n} 个时间文本补全年份 {year}")


def proc_mem_mb(pid):
    """进程工作集内存(MB)，纯标准库 Win32 API；取不到返回 '-'"""
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        class _PMC(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_INFORMATION
        if not h:
            return "-"
        try:
            pmc = _PMC()
            pmc.cb = ctypes.sizeof(_PMC)
            _gpm = k32.K32GetProcessMemoryInfo
            _gpm.argtypes = [ctypes.wintypes.HANDLE, ctypes.POINTER(_PMC), ctypes.wintypes.DWORD]
            _gpm.restype = ctypes.wintypes.BOOL
            ok = _gpm(h, ctypes.byref(pmc), pmc.cb)
            ctypes.windll.kernel32.CloseHandle(h)
            return f"{pmc.WorkingSetSize / 1048576:.1f}" if ok else "-"
        except Exception:
            try:
                ctypes.windll.kernel32.CloseHandle(h)
            except Exception:
                pass
    except Exception:
        pass
    return "-"

def _dur_txt(s):
    """距 start 的时长文本（纯展示）"""
    try:
        t = datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
        secs = max(0, int((datetime.now() - t).total_seconds()))
        if secs < 60:
            return f"{secs}s"
        if secs < 3600:
            return f"{secs // 60}m"
        return f"{secs // 3600}h{secs % 3600 // 60}m"
    except Exception:
        return "-"


def order_dir(oid):
    d = os.path.join(ORDER_DIR, oid)
    os.makedirs(d, exist_ok=True)
    return d

# ================= 平台设置 =================
DEFAULT_REG_CODE = "JJZ-2026"

def _ensure_settings():
    with db() as c:
        c.execute("CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT)")
        for k, v in [("reg_code", DEFAULT_REG_CODE), ("jobs", "2"), ("min_free_mb", "500"),
                     ("log_keep_kb", "300"), ("log_keep_days", "3"), ("speed", "2.0"),
                     ("concurrency", "1"), ("proxy_pool", ""), ("spoof", "1"), ("jitter", "1"),
                     ("order_timeout_min", "180"), ("verbose", "0")]:
            c.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (k, v))

def get_setting(key, default=""):
    try:
        with db() as c:
            r = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return r["value"] if r else default
    except sqlite3.OperationalError:
        return default

def set_setting(key, value):
    _ensure_settings()
    with db() as c:
        c.execute("INSERT INTO settings(key,value) VALUES(?,?) "
                  "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))

# ================= 知到扫码 =================
QR_SESSIONS = {}

def qr_thread(oid):
    st = QR_SESSIONS[oid]
    s = st["session"]
    try:
        while True:
            time.sleep(1)
            r = s.get("https://passport.zhihuishu.com/qrCodeLogin/getLoginQrInfo",
                      params={"qrToken": st["qrToken"]}, timeout=10).json()
            status = r.get("status")
            if status == 0:
                st["state"] = "scanned"
                set_order(oid, qr_state="scanned", note="已扫码，请在 App 上确认")
            elif status == 1:
                s.get("https://passport.zhihuishu.com/login",
                      params={"service": "https://onlineservice-api.zhihuishu.com/login/gologin",
                              "pwd": r.get("oncePassword")}, timeout=10)
                cookies = requests.utils.dict_from_cookiejar(s.cookies)
                cp = os.path.join(order_dir(oid), "cookies.json")
                data = json.load(open(cp, encoding="utf-8")) if os.path.exists(cp) else {}
                data["zhs"] = [{"name": k, "value": v, "domain": ".zhihuishu.com"}
                               for k, v in cookies.items()]
                json.dump(data, open(cp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
                st["state"] = "confirmed"
                set_order(oid, status="pending", qr_state="confirmed", note="扫码登录成功")
                break
            elif status == 2:
                st["state"] = "expired"
                set_order(oid, status="canceled", qr_state="expired",
                          finished_at=now_str(), note="二维码已过期，请重新下单")
                break
            elif status == 3:
                st["state"] = "canceled"
                set_order(oid, status="canceled", qr_state="canceled",
                          finished_at=now_str(), note="已取消登录")
                break
    except Exception as e:
        st["state"] = "error"
        set_order(oid, status="canceled", qr_state="error",
                  finished_at=now_str(), note=str(e).replace(APP_DIR, "…")[:120])

# ================= 按单环境隔离 + 指纹伪装 =================
UA_POOL = [
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36",
     '"Windows"', '"Chromium";v="118", "Google Chrome";v="118", "Not=A?Brand";v="99"', "zh-CN,zh;q=0.9"),
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
     '"Windows"', '"Not_A Brand";v="8", "Chromium";v="120", "Microsoft Edge";v="120"', "zh-CN,zh;q=0.9,en;q=0.8"),
    ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
     '"macOS"', '"Google Chrome";v="119", "Chromium";v="119", "Not?A_Brand";v="24"', "zh-CN,zh;q=0.9"),
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
     '"Windows"', '"Chromium";v="121", "Not(A:Brand";v="24"', "zh-CN,zh;q=0.8,zh-TW;q=0.7"),
]

_proxy_idx = {"i": 0}

def pick_proxy():
    pool = [p.strip() for p in (get_setting("proxy_pool", "") or "").splitlines() if p.strip()]
    if not pool:
        return None
    _proxy_idx["i"] = (_proxy_idx["i"] + 1) % len(pool)
    return pool[_proxy_idx["i"]]

def build_order_env(oid, o):
    """单订单隔离运行环境：独立工作目录/cookies/日志/TEMP/HOME + 指纹 + 出口 IP"""
    d = order_dir(oid)
    work = os.path.join(d, "work")
    tmp = os.path.join(d, "tmp")
    home = os.path.join(d, "home")
    logs = os.path.join(d, "logs")
    for p in (work, tmp, home, logs):
        os.makedirs(p, exist_ok=True)

    spoof = get_setting("spoof", "1") != "0"
    if spoof:
        ua, plat, chua, lang = random.choice(UA_POOL)
    else:
        ua, plat, chua, lang = UA_POOL[0]

    env = os.environ.copy()
    env["FUCKCOURSE_CONFIG"] = os.path.join(FUCK_DIR, "config.json")
    env["FUCKCOURSE_COOKIES"] = os.path.join(d, "cookies.json")
    env["FUCKCOURSE_LOG_DIR"] = logs
    env["HOME"] = home
    env["USERPROFILE"] = home
    env["TMP"] = tmp
    env["TEMP"] = tmp
    env["OMP_NUM_THREADS"] = "1"
    env["OPENBLAS_NUM_THREADS"] = "1"
    env["MKL_NUM_THREADS"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["WK_UA"] = ua
    env["WK_PLATFORM"] = plat
    env["WK_CHUA"] = chua
    env["WK_LANG"] = lang
    env["TZ"] = "Asia/Shanghai"
    proxy = pick_proxy()
    if proxy:
        for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
            env[k] = proxy
    profile = {"ua": ua, "platform": plat.strip('"'), "lang": lang,
               "proxy": proxy or "直连", "spoof": spoof}
    return env, work, profile

RISK_PATTERNS = [
    ("captcha", ["验证码", "captcha", "滑块", "checktype", "打码", "安全验证"]),
    ("forbidden", ["403", "forbidden", "无权限", "没有被授权", "无权访问"]),
    ("risk_ctrl", ["风控", "异常行为", "操作频繁", "次数限制", "too many", "禁止", "冻结", "封禁", "限制访问"]),
    ("login_fail", ["登录失败", "用户名或密码错误", "账号或密码", "login failed", "login error", "cookie"]),
    ("network", ["max retries", "connectionerror", "timed out", "connection aborted", "连接失败", "remote end closed"]),
]

def scan_risk(oid):
    """扫描日志中的风控信号（大小写不敏感），落库用于对比不同环境组合的效果"""
    p = os.path.join(order_dir(oid), "log.txt")
    if not os.path.exists(p):
        return ""
    try:
        raw = open(p, "rb").read()[-400 * 1024:].decode("utf-8", errors="replace").lower()
    except Exception:
        return ""
    hits = []
    for name, kws in RISK_PATTERNS:
        if any(k.lower() in raw for k in kws):
            hits.append(name)
    return ",".join(hits)

# ================= 任务引擎 =================
def _spawn(cmd, env, cwd, log_path, oid=None):
    """启动子进程：低优先级 + 滚动日志 + 超时熔断 + PID 落库 + 心跳"""
    tail_keep(log_path)
    keep_kb = int(get_setting("log_keep_kb", "300"))
    p = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         cwd=cwd, creationflags=BELOW_NORMAL | CREATE_NO_WINDOW)
    if oid:
        try:
            set_order(oid, pid=p.pid, heartbeat_at=now_str())
        except Exception:
            pass
    t = threading.Thread(target=RollingLog(log_path, keep_kb).run, args=(p,), daemon=True)
    t.start()
    timeout_min = int(get_setting("order_timeout_min", "180"))
    deadline = max(10, timeout_min) * 60
    waited, timed_out = 0, False
    while True:
        try:
            p.wait(timeout=20)
            break
        except subprocess.TimeoutExpired:
            pass
        waited += 20
        if oid:  # 低频心跳（20 秒级），仅用于存活判定
            try:
                set_order(oid, heartbeat_at=now_str())
            except Exception:
                pass
        if waited >= deadline:
            timed_out = True
            break
    if timed_out:
        _kill_tree(p.pid)  # 树杀 + 验证退出，不只 kill 单进程
        try:
            with open(log_path, "ab") as f:
                f.write(f"\n[平台] 超过 {timeout_min} 分钟未完成，已强制终止\n".encode("utf-8"))
        except Exception:
            pass
        t.join(timeout=3)
        return -9
    t.join(timeout=5)
    return p.returncode

def run_chaoxing(oid, o, env, work):
    jobs = max(1, min(4, int(get_setting("jobs", "2"))))
    try:
        speed = float(o["speed"]) if o["speed"] else float(get_setting("speed", "2.0"))
    except (KeyError, TypeError, ValueError):
        speed = float(get_setting("speed", "2.0"))
    if get_setting("jitter", "1") == "1":
        speed = round(max(1.0, min(2.0, speed + random.uniform(-0.15, 0.15))), 2)
    courses = o["courses"] or "264628209"
    cmd = [PYEXE, "-u", os.path.join(FUCK_DIR, "chaoxing", "main.py"),
           "-l", courses, "-s", str(speed), "-j", str(jobs), "--auto-sign"]
    env["WK_ACCOUNT"] = o["account"]
    env["WK_PASSWORD"] = decrypt_secret(o["password"])
    if get_setting("verbose", "0") == "1":
        cmd.append("--verbose")  # 实验模式：捕获 isPassed/验证码等 TRACE 响应
    return _spawn(cmd, env, work, os.path.join(order_dir(oid), "log.txt"), oid=oid)

def run_zhs(oid, o, env, work):
    if o["courses"]:
        args = ["full"] + o["courses"].split()
    else:
        lst = subprocess.run([PYEXE, os.path.join(FUCK_DIR, "run_zhs.py"), "list"],
                             env=env, capture_output=True, text=True, timeout=180)
        ids = [ln.rsplit("=", 1)[-1].strip() for ln in lst.stdout.splitlines()
               if "[hike]" in ln or "[zhidao]" in ln]
        args = ["full"] + ids
    cmd = [PYEXE, os.path.join(FUCK_DIR, "run_zhs.py")] + args
    return _spawn(cmd, env, work, os.path.join(order_dir(oid), "log.txt"), oid=oid)

def claim_order():
    """原子抢占一单（多 worker 并发安全；DB 忙视为本轮抢不到，不误伤订单）"""
    try:
        with db() as c:
            row = c.execute("SELECT * FROM orders WHERE status='pending' AND worker_running=0 "
                            "ORDER BY created_at LIMIT 1").fetchone()
            if row is None:
                return None
            cur = c.execute("UPDATE orders SET worker_running=1, status='running', started_at=?, "
                            "attempt=COALESCE(attempt,0)+1, heartbeat_at=? "
                            "WHERE id=? AND worker_running=0",
                            (now_str(), now_str(), row["id"]))
            if cur.rowcount == 0:
                return None
        return dict(row)
    except sqlite3.OperationalError:
        return None

def worker(wid=0):
    idle = 0
    while True:
        oid = None
        try:
            if get_setting("paused", "0") == "1":
                time.sleep(3)
                continue
            if free_mem_mb() < int(get_setting("min_free_mb", "500")):
                time.sleep(10)
                continue
            o = claim_order()
            if o is None:
                idle += 1
                time.sleep(2 if idle < 15 else 5)  # 空闲退避：长时间无单降低轮询频率
                continue
            idle = 0
            oid = o["id"]
            env, work, profile = build_order_env(oid, o)
            set_order(oid, env_profile=json.dumps(profile, ensure_ascii=False))
            rc = run_chaoxing(oid, o, env, work) if o["platform"] == "chaoxing" else run_zhs(oid, o, env, work)
            risk = scan_risk(oid)
            env_txt = f"[环境] {profile['platform']} · 出口 {profile['proxy']}"
            finished = now_str()
            if rc == 0:
                safe_set_order(oid, status="done", finished_at=finished,
                               exit_code=0, risk_flags=risk,
                               note=(f"[风控] {risk}" if risk else env_txt))
            else:
                # 重试策略：超时(-9)/崩溃(负退出码)可自动重试 MAX_RETRY 次；
                # 引擎正常返回的非零码（参数/认证/业务错误）不重试，直接 failed
                attempt = o.get("attempt") or 0
                retryable = rc == -9 or rc < 0
                if retryable and attempt < MAX_RETRY:
                    why = "超时" if rc == -9 else "进程异常退出"
                    safe_set_order(oid, status="pending", exit_code=rc, risk_flags=risk,
                                   note=f"第 {attempt} 次执行{why}(rc={rc})，自动重试")
                else:
                    safe_set_order(oid, status="failed", finished_at=finished,
                                   exit_code=rc, risk_flags=risk,
                                   note=(f"[风控] {risk} · 退出码 {rc}" if risk else f"退出码 {rc}，详见日志"))
        except Exception as e:
            try:
                if oid:
                    # 异常文本去掉本地路径再落库
                    safe_set_order(oid, status="failed", finished_at=now_str(),
                                   note=str(e).replace(APP_DIR, "…")[:160])
            except Exception:
                pass
            time.sleep(1)
        finally:
            try:
                if oid:
                    safe_set_order(oid, worker_running=0, heartbeat_at="")
            except Exception:
                pass

def order_watchdog():
    """运行期订单进程存活探测（P0-1 收敛）：DB=running 但 pid 死亡 → 依 attempt 收敛。
    与 worker 的 rc 处理幂等（终态重复写无害）；超时仍由 _spawn 负责。
    每 30s 一轮；仅只读 + 必要写，空闲消耗可忽略。"""
    while True:
        time.sleep(30)
        try:
            with db() as c:
                rows = c.execute(
                    "SELECT * FROM orders WHERE status='running' AND pid IS NOT NULL AND pid != 0").fetchall()
        except Exception:
            continue
        if not rows:
            continue
        for o in rows:
            try:
                if _pid_alive(o["pid"]):
                    continue  # 存活：不干预（进度/超时由 worker 负责）
                if (o["attempt"] or 0) < MAX_RETRY:
                    safe_set_order(o["id"], status="pending", worker_running=0, pid=0, heartbeat_at="",
                                   note="检测到任务进程已退出，自动重新排队")
                else:
                    safe_set_order(o["id"], status="failed", worker_running=0, pid=0, finished_at=now_str(),
                                   note="检测到任务进程已退出(crashed)，已达重试上限")
            except Exception:
                continue


def concurrency_manager():
    """按设置维持 worker 线程数（并发多单）"""
    workers = []
    while True:
        try:
            want = max(1, min(MAX_CONCURRENCY, int(get_setting("concurrency", "4"))))
        except ValueError:
            want = 4
        workers = [t for t in workers if t.is_alive()]
        while len(workers) < want:
            t = threading.Thread(target=worker, args=(len(workers),), daemon=True)
            t.start()
            workers.append(t)
        time.sleep(5)

def qr_janitor():
    """清理过期/已完成的扫码会话，防止内存常驻增长"""
    while True:
        time.sleep(120)
        try:
            now = time.time()
            dead = [k for k, v in list(QR_SESSIONS.items())
                    if now - v.get("ts", now) > 600 or v.get("state") in ("confirmed", "expired", "canceled", "error")]
            for k in dead:
                QR_SESSIONS.pop(k, None)
        except Exception:
            pass

def housekeeping():
    """定期清理：老订单日志目录 + 平台自身日志"""
    while True:
        time.sleep(1800)
        try:
            keep_days = int(get_setting("log_keep_days", "3"))
            cutoff = time.time() - keep_days * 86400
            for oid_dir in os.listdir(ORDER_DIR):
                p = os.path.join(ORDER_DIR, oid_dir)
                if os.path.isdir(p) and oid_dir != "_query":
                    try:
                        if os.path.getmtime(p) < cutoff:
                            shutil.rmtree(p, ignore_errors=True)
                    except Exception:
                        pass
            # 清理查课临时 cookies
            qd = os.path.join(ORDER_DIR, "_query")
            if os.path.isdir(qd):
                for fn in os.listdir(qd):
                    fp = os.path.join(qd, fn)
                    try:
                        if time.time() - os.path.getmtime(fp) > 3600:
                            os.remove(fp)
                    except Exception:
                        pass
            # 裁剪引擎自身日志（学习通/知到 loguru 文件会无限增长）
            for lp in [os.path.join(FUCK_DIR, "logs", "chaoxing.log")]:
                tail_keep(lp, 512 * 1024)
            zdir = os.path.join(FUCK_DIR, "logs", "zhs_logs")
            if os.path.isdir(zdir):
                for fn in os.listdir(zdir):
                    tail_keep(os.path.join(zdir, fn), 512 * 1024)
            # loguru 按日期轮转的历史日志（chaoxing.2026-xx-xx_*.log）按保留天数删除
            lg = os.path.join(FUCK_DIR, "logs")
            if os.path.isdir(lg):
                for fn in os.listdir(lg):
                    fp = os.path.join(lg, fn)
                    if os.path.isfile(fp) and re.search(r"\d{4}-\d{2}-\d{2}", fn):
                        try:
                            tail_keep(fp, 512 * 1024)  # 大小上限：删除前先裁剪，防止长到 10MB+
                            if os.path.getmtime(fp) < cutoff:
                                os.remove(fp)
                        except Exception:
                            pass
            # cloudflared 日志裁剪，防止隧道日志无限增长
            tail_keep(os.path.join(APP_DIR, "cf", "cloudflared.log"), 512 * 1024)
            # 平台错误日志裁剪
            tail_keep(os.path.join(APP_DIR, "platform_error.log"), 256 * 1024)
            # 平台崩溃尸检日志裁剪
            tail_keep(os.path.join(APP_DIR, "cf", "platform_stdout.log"), 512 * 1024)
            # 每日一次数据库在线备份（WAL 安全，不阻塞业务）
            try:
                import backup_manager
                if time.time() - float(get_setting("last_backup", "0") or 0) > 86400:
                    dst, ok, msg = backup_manager.backup_database()
                    if ok:
                        set_setting("last_backup", str(time.time()))
                    else:
                        raise RuntimeError(msg)
            except Exception:
                pass
        except Exception:
            pass

init_db()
_ensure_settings()
ensure_admin_password()
migrate_encrypt_passwords()
migrate_timestamp_years()
recover_stale_orders()
threading.Thread(target=order_watchdog, daemon=True).start()
threading.Thread(target=concurrency_manager, daemon=True).start()
threading.Thread(target=qr_janitor, daemon=True).start()
threading.Thread(target=housekeeping, daemon=True).start()

# ================= UI (Tabler) =================
app = Flask(__name__)
app.secret_key = SECRET

@app.before_request
def _csrf_guard():
    """全站 POST 跨站防护：所有状态变更接口统一校验同源"""
    if request.method == "POST" and not same_origin_ok():
        return "拒绝跨站请求", 403

BADGE_CLS = {"pending": "bg-yellow-lt", "running": "bg-blue-lt", "done": "bg-green-lt",
             "failed": "bg-red-lt", "waiting_qr": "bg-purple-lt", "canceled": "bg-secondary-lt"}
BADGE = {"pending": "排队中", "running": "执行中", "done": "已完成",
         "failed": "失败", "waiting_qr": "待扫码", "canceled": "已取消"}
PICON = {"chaoxing": "🚀", "zhs": "🌿", "zhsqr": "📷"}

def esc(v):
    """HTML 转义：所有用户/引擎可控文本渲染前调用，杜绝存储型/日志型 XSS"""
    try:
        return html.escape(str(v), quote=True) if v is not None else ""
    except Exception:
        return ""


def badge(st):
    return f'<span class="badge {BADGE_CLS.get(st, "bg-secondary-lt")} me-1"></span>{BADGE.get(st, st)}'

def _env_txt(o):
    """订单环境画像展示"""
    try:
        p = json.loads(o.get("env_profile") or "{}")
    except Exception:
        p = {}
    if not p:
        return '<span class="text-secondary">未记录</span>'
    return (f'{p.get("platform","-")} · {p.get("lang","-")} · 出口 {p.get("proxy","-")}'
            f'<br><span class="text-secondary small">{p.get("ua","")[:70]}</span>')

def page(title, body, user, active=""):
    links = [
        ("/", "首页", "home"), ("/my", "我的订单", "my"), ("/query", "查单", "query"),
        ("/batch", "批量下单", "batch")]
    if user and user["is_admin"]:
        links.append(("/admin", "管理后台", "admin"))
    def navlink(href, label, key):
        cls = "nav-link active" if active == key else "nav-link"
        return f'<a class="{cls}" href="{href}">{label}</a>'
    if user:
        right = ("".join(navlink(h, l, k) for h, l, k in links)
                 + f'<a class="nav-link" href="/logout">退出 ({user["username"]})</a>')
    else:
        right = (navlink("/", "首页", "home") + navlink("/query", "查单", "query")
                 + '<a class="nav-link" href="/login">登录</a><a class="nav-link" href="/register">注册</a>')
    html = """<!doctype html>
<html lang="zh" data-bs-theme="dark">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__ · 自动任务平台</title>
<link rel="stylesheet" href="/static/vendor/css/tabler.min.css">
<style>
.log-console{background:#0d1117;border-radius:0 0 8px 8px;max-height:460px;overflow:auto;
font-family:ui-monospace,Consolas,monospace;font-size:12px;line-height:1.55;color:#adbac7;
padding:12px 14px;white-space:pre-wrap;word-break:break-all;margin:0}
.hero{padding:2.2rem 0 0.6rem}
.hero h1{background:linear-gradient(90deg,#fff 30%,#a5b4fc 70%,#67e8f9);
-webkit-background-clip:text;background-clip:text;color:transparent;font-weight:800}
.glow{box-shadow:0 8px 30px rgba(99,102,241,.25)}
.copy-btn{cursor:pointer}
footer{padding:1.6rem 0;color:var(--tblr-secondary);font-size:.8rem;text-align:center}
</style>
</head>
<body class="theme-dark">
<div class="page">
<header class="navbar navbar-expand-md d-print-none">
  <div class="container-xl">
    <h1 class="navbar-brand mb-0 h1">⚡ 自动任务平台</h1>
    <div class="navbar-nav flex-row order-md-last ms-auto">__NAV__</div>
  </div>
</header>
<div class="page-wrapper">
  <div class="page-body">
    <div class="container-xl">
__BODY__
    </div>
  </div>
</div>
<footer>自动任务平台 · 本地部署 · 引擎 fuckCourse · 可经 Cloudflare Tunnel 发布</footer>
</div>
<script src="/static/vendor/js/tabler.min.js"></script>
<div class="toast position-fixed bottom-0 end-0 m-3" id="toast" style="z-index:99">
  <div class="toast-body" id="toastbody"></div></div>
<script>
function toast(m){var t=document.getElementById('toastbody');t.textContent=m;
var el=document.getElementById('toast');el.classList.add('show');
setTimeout(()=>el.classList.remove('show'),1800)}
function cp(s){navigator.clipboard.writeText(s);toast('已复制到剪贴板')}
</script>
</body></html>"""
    return Response(html.replace("__TITLE__", title).replace("__NAV__", right)
                    .replace("__BODY__", body), mimetype="text/html")

def require_login(f):
    from functools import wraps
    @wraps(f)
    def w(*a, **kw):
        u = current_user()
        if not u:
            return redirect("/login")
        return f(u, *a, **kw)
    return w

def stat_card(label, num, cls=""):
    return f"""<div class="col-sm-6 col-lg"><div class="card card-sm glow">
<div class="card-body"><div class="subheader">{label}</div><div class="h1 mb-0 me-2 {cls}">{num}</div></div></div></div>"""

# ---- 首页: 商品橱窗 ----
@app.route("/")
def home():
    user = current_user()
    with db() as c:
        products = c.execute("SELECT * FROM products WHERE enabled=1 ORDER BY sort").fetchall()
        n_done = c.execute("SELECT COUNT(*) n FROM orders WHERE status='done'").fetchone()["n"]
        n_all = c.execute("SELECT COUNT(*) n FROM orders").fetchone()["n"]
        n_run = c.execute("SELECT COUNT(*) n FROM orders WHERE status IN ('running','pending')").fetchone()["n"]
    cards = "".join(f"""<div class="col-md-6 col-lg-4">
  <div class="card" style="min-height:230px">
    <div class="card-body">
      <div class="avatar avatar-md mb-2" style="background:rgba(99,102,241,.18)">{PICON.get(p['platform'],'⚙')}</div>
      <h3 class="card-title">{p['name']}</h3>
      <p class="text-secondary">{p['desc']}</p>
      <div class="d-flex align-items-center justify-content-between mt-auto">
        <span class="badge bg-green-lt">{p['price']}</span>
        <a class="btn btn-primary" href="/buy/{p['code']}">立即下单</a>
      </div>
    </div>
  </div>
</div>""" for p in products)
    body = f"""<div class="hero"><h1>任务交给自动化，时间留给自己</h1>
<p class="text-secondary">选择商品下单，系统自动排队执行；全程日志可查、进度实时可见。当前队列单线程顺序执行，数据仅存本机。</p></div>
<div class="row row-deck row-cards mt-2">{cards}</div>
<div class="row row-cards mt-1">
{stat_card('累计订单', n_all)}{stat_card('已完成', n_done, 'text-green')}{stat_card('排队/执行中', n_run, 'text-blue')}{stat_card('在售商品', len(products))}
</div>
<p class="text-secondary mt-3">没有账号？<a href="/register">注册</a> 后下单 · 已有订单？<a href="/query">凭单号查单</a></p>"""
    return page("首页", body, user, "home")

# ---- 课程查询工具 ----
QUERY_TOOL = os.path.join(APP_DIR, "tools_query_courses.py")
QUERY_DIR = os.path.join(ORDER_DIR, "_query")
os.makedirs(QUERY_DIR, exist_ok=True)

_QUERY_SLOT = threading.BoundedSemaphore(2)  # 查课并发上限：防全部 Waitress 线程被 180s 查询占满

def query_courses(platform, account="", password="", cookie_path=None):
    """子进程查询课程，返回 (ok, courses|error)。
    并发护栏：同一时刻最多 2 个查询；忙等 30s 后快速失败，避免占满请求线程。"""
    if not _QUERY_SLOT.acquire(timeout=30):
        return False, "查询服务繁忙（并发受限），请稍后再试"
    try:
        return _query_courses(platform, account, password, cookie_path)
    finally:
        _QUERY_SLOT.release()


def _query_courses(platform, account, password, cookie_path):
    """查询实现（内层）"""
    args = [PYEXE, QUERY_TOOL]
    qenv = os.environ.copy()
    qenv["WK_ACCOUNT"] = account
    qenv["WK_PASSWORD"] = password
    if platform == "chaoxing":
        args += ["chaoxing", "", "", cookie_path or os.path.join(QUERY_DIR, uuid.uuid4().hex + ".json")]
    elif platform == "zhs":
        args += ["zhs", "", "", cookie_path or os.path.join(QUERY_DIR, uuid.uuid4().hex + ".json")]
    else:  # zhs_cookie（扫码登录后）
        args += ["zhs_cookie", cookie_path]
    try:
        r = subprocess.run(args, env=qenv, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=180, cwd=APP_DIR)
        data = json.loads((r.stdout or "{}").strip().splitlines()[-1] if r.stdout.strip() else "{}")
        if data.get("ok"):
            return True, data.get("courses", [])
        return False, data.get("error", f"查询失败(exit={r.returncode})")
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"[:160]

# ---- 下单向导 ----
WIZARD_CSS = """
.wiz-step{display:flex;gap:10px;align-items:center;margin-bottom:14px;flex-wrap:wrap}
.wiz-num{width:26px;height:26px;border-radius:50%;background:rgba(255,255,255,.08);display:grid;
place-items:center;font-size:13px;font-weight:700;color:#c9d3e3}
.wiz-num.on{background:linear-gradient(135deg,#6366f1,#22d3ee);color:#fff}
.course-list{max-height:320px;overflow:auto;border:1px solid rgba(255,255,255,.09);border-radius:10px;padding:6px}
.course-item{display:flex;gap:10px;align-items:center;padding:8px 10px;border-radius:8px}
.course-item:hover{background:rgba(255,255,255,.04)}
.course-item input{width:auto;margin:0}
.qr-box{display:flex;flex-direction:column;align-items:center;justify-content:center;
min-height:240px;border:2px dashed rgba(255,255,255,.18);border-radius:14px;padding:20px}
"""

@app.route("/buy/<code>", methods=["GET", "POST"])
def buy(code):
    user = current_user()
    with db() as c:
        p = c.execute("SELECT * FROM products WHERE code=? AND enabled=1", (code,)).fetchone()
    if not p:
        return page("错误", """<div class="empty"><div class="empty-header">🤷</div>
<p class="empty-title">商品不存在</p><div class="empty-action"><a class="btn btn-primary" href="/">返回首页</a></div></div>""", user)
    if not user:
        return redirect("/login")
    platform = p["platform"]
    is_qr = platform == "zhsqr"

    # 表单提交（已选课程）
    if request.method == "POST":
        account = request.form.get("account", "").strip()
        password = request.form.get("password", "").strip()
        courses = request.form.get("courses", "").strip()
        oid = uuid.uuid4().hex
        with db() as c:
            c.execute("INSERT INTO orders(id,user_id,product,platform,account,password,courses,status,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                      (oid, user["id"], p["code"], platform, account, encrypt_secret(password), courses, "pending",
                       now_str()))
        return redirect(f"/order/{oid}")

    default_course = "264628209" if platform == "chaoxing" else ""
    cred_block = "" if is_qr else """
      <div class="mb-3"><label class="form-label">账号（手机号）</label>
        <input class="form-control" id="acc" placeholder="请输入登录账号"></div>
      <div class="mb-3"><label class="form-label">密码</label>
        <input class="form-control" id="pwd" type="text" placeholder="请输入登录密码"></div>"""
    qr_block = """
      <div class="qr-box" id="qrbox">
        <p class="text-secondary mb-2">扫码登录无需账号密码</p>
        <button class="btn btn-primary" id="qrbtn" onclick="startQr()">获取登录二维码</button>
        <p class="text-secondary small mt-3" id="qrtip"></p>
      </div>""" if is_qr else ""

    body = f"""<style>{WIZARD_CSS}</style>
<div class="page-header d-print-none"><div class="row align-items-center">
<div class="col"><h2 class="page-title">{PICON.get(platform,'⚙')} {p['name']}</h2>
<div class="text-secondary mt-1">{p['desc']}</div></div>
<div class="col-auto"><span class="badge bg-green-lt">{p['price']}</span></div></div></div>
<div class="row justify-content-center"><div class="col-lg-8"><div class="card"><div class="card-body">
<div class="wiz-step"><span class="wiz-num on" id="s1">1</span><span>登录 / 授权</span>
<span class="text-secondary">→</span><span class="wiz-num" id="s2">2</span><span>查询并勾选课程</span>
<span class="text-secondary">→</span><span class="wiz-num" id="s3">3</span><span>提交订单</span></div>
{cred_block}{qr_block}
<div id="coursetools" style="display:none">
  <div class="d-flex align-items-center justify-content-between mb-2">
    <label class="form-label mb-0">选择要刷的课程（可多选）</label>
    <div>
      <a class="btn btn-sm btn-outline-secondary" onclick="selAll(true)">全选</a>
      <a class="btn btn-sm btn-outline-secondary" onclick="selAll(false)">清空</a>
    </div>
  </div>
  <input class="form-control mb-2" id="filter" placeholder="搜索课程名…" oninput="renderCourses()">
  <div class="course-list" id="courselist"></div>
  <p class="text-secondary small mt-2" id="selinfo">已选 0 门</p>
</div>
<div class="d-flex gap-2 mt-3">
  <button class="btn btn-secondary" id="qbtn" onclick="doQuery()" {'style=display:none' if is_qr else ''}>🔍 查询课程</button>
  <button class="btn btn-primary" id="submitbtn" disabled onclick="doSubmit()">提交订单</button>
</div>
<div class="alert alert-info mt-2 mb-0 small" id="msg" style="display:none"></div>
</div></div></div></div>
<input type="hidden" id="courses_field" value="{default_course}">
<script>
var PRODUCT = "{p['code']}";
var PLATFORM = "{platform}";
var ORDER_ID = null, COURSES = [], SELECTED = {{}};
function msg(t, cls){{var m=document.getElementById('msg');m.textContent=t;
m.className='alert alert-'+(cls||'info')+' mt-2 mb-0 small';m.style.display='block';}}
function step(n){{for(var i=1;i<=3;i++){{document.getElementById('s'+i).className='wiz-num'+(i<=n?' on':'');}}}}
async function startQr(){{
  document.getElementById('qrbtn').disabled=true;
  document.getElementById('qrtip').textContent='正在获取二维码…';
  try{{
    var r=await fetch('/api/qr_start',{{method:'POST',headers:{{'Content-Type':'application/json'}},
      body:JSON.stringify({{product:PRODUCT}})}});
    var j=await r.json();
    if(!j.ok){{msg(j.error,'danger');document.getElementById('qrbtn').disabled=false;return;}}
    ORDER_ID=j.oid;
    document.getElementById('qrbox').innerHTML='<img src="/qr/'+j.oid+'" style="width:210px;border:8px solid #fff;border-radius:14px">'+
      '<p class="text-secondary small mt-2" id="qrtip">请用知到 App 扫码</p>';
    step(2);
    pollQr(j.oid);
  }}catch(e){{msg('获取二维码失败: '+e,'danger');document.getElementById('qrbtn').disabled=false;}}
}}
async function pollQr(oid){{
  var t=setInterval(async function(){{
    try{{
      var j=await(await fetch('/qr_status/'+oid)).json();
      var tip=document.getElementById('qrtip'); if(tip) tip.textContent='状态: '+j.state;
      if(j.state==='confirmed'){{clearInterval(t);
        var tip2=document.getElementById('qrtip'); if(tip2) tip2.innerHTML='<b class="text-green">扫码成功</b>，正在查询课程…';
        await queryByOrder(oid);}}
      else if(j.state==='expired'||j.state==='canceled'||j.state==='error'){{clearInterval(t);
        msg('二维码已失效，请重新获取','danger');}}
    }}catch(e){{}}
  }},1500);
}}
async function queryByOrder(oid){{
  try{{
    var r=await fetch('/api/courses',{{method:'POST',headers:{{'Content-Type':'application/json'}},
      body:JSON.stringify({{platform:'zhs',order_id:oid}})}});
    var j=await r.json();
    if(!j.ok){{msg(j.error,'danger');return;}}
    COURSES=j.courses; SELECTED={{}};
    document.getElementById('coursetools').style.display='block';
    document.getElementById('submitbtn').disabled=false;
    renderCourses(); msg('已获取 '+j.courses.length+' 门课程，请勾选','success');
  }}catch(e){{msg('查询失败: '+e,'danger');}}
}}
async function doQuery(){{
  var acc=document.getElementById('acc').value.trim(), pwd=document.getElementById('pwd').value.trim();
  if(!acc||!pwd){{msg('请先填写账号和密码','warning');return;}}
  document.getElementById('qbtn').disabled=true;
  msg('正在登录并查询课程，请稍候（约 10-30 秒）…','info');
  try{{
    var r=await fetch('/api/courses',{{method:'POST',headers:{{'Content-Type':'application/json'}},
      body:JSON.stringify({{platform:PLATFORM,account:acc,password:pwd}})}});
    var j=await r.json();
    if(!j.ok){{msg('查询失败: '+j.error,'danger');return;}}
    COURSES=j.courses; SELECTED={{}};
    document.getElementById('coursetools').style.display='block';
    document.getElementById('submitbtn').disabled=false;
    step(2); renderCourses(); msg('已获取 '+j.courses.length+' 门课程，请勾选','success');
  }}catch(e){{msg('查询失败: '+e,'danger');}}
  finally{{document.getElementById('qbtn').disabled=false;}}
}}
function renderCourses(){{
  var kw=(document.getElementById('filter').value||'').toLowerCase();
  var html='';
  COURSES.forEach(function(c,i){{
    if(kw && c.name.toLowerCase().indexOf(kw)<0) return;
    html+='<label class="course-item"><input type="checkbox" '+(SELECTED[c.id]?'checked':'')+
      ' onchange="toggle(\\''+c.id+'\\')"><span>'+c.name+
      ' <span class="text-secondary small">('+c.kind+')</span></span></label>';
  }});
  document.getElementById('courselist').innerHTML=html||'<p class="text-secondary small p-2">无匹配课程</p>';
  updSel();
}}
function toggle(id){{SELECTED[id]=!SELECTED[id];updSel();}}
function selAll(v){{COURSES.forEach(function(c){{SELECTED[c.id]=v;}});renderCourses();}}
function updSel(){{
  var n=Object.keys(SELECTED).filter(function(k){{return SELECTED[k];}}).length;
  document.getElementById('selinfo').textContent='已选 '+n+' 门';
}}
function chosen(){{
  return COURSES.filter(function(c){{return SELECTED[c.id];}}).map(function(c){{return c.id;}});
}}
async function doSubmit(){{
  var ids=chosen();
  if(!ids.length){{msg('请至少勾选一门课程','warning');return;}}
  var courses=PLATFORM==='chaoxing'?ids.join(','):ids.join(' ');
  if(ORDER_ID){{
    var r=await fetch('/order/'+ORDER_ID+'/set_courses',{{method:'POST',
      headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{courses:courses}})}});
    var j=await r.json();
    if(j.ok){{location='/order/'+ORDER_ID;}}else{{msg(j.error||'提交失败','danger');}}
  }}else{{
    var f=document.createElement('form');f.method='POST';f.action='/buy/'+PRODUCT;
    var kv={{courses:courses,account:document.getElementById('acc').value,
            password:document.getElementById('pwd').value}};
    for(var k in kv){{var i=document.createElement('input');i.type='hidden';i.name=k;i.value=kv[k];f.appendChild(i);}}
    document.body.appendChild(f);f.submit();
  }}
}}
</script>"""
    return page("下单", body, user)


# ---- 课程查询 / 扫码 / 选课接口 ----
@app.route("/api/courses", methods=["POST"])
@require_login
def api_courses(user):
    if not rate_limit("api_courses", 12):
        return jsonify(ok=False, error="操作过于频繁，请稍后再试")
    d = request.get_json(silent=True) or {}
    platform = d.get("platform")
    account = (d.get("account") or "").strip()
    password = (d.get("password") or "").strip()
    order_id = d.get("order_id")

    if platform == "zhs" and order_id:
        with db() as c:
            o = c.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
        if not o or (not user["is_admin"] and o["user_id"] != user["id"]):
            return jsonify(ok=False, error="订单不存在")
        ck = os.path.join(order_dir(order_id), "cookies.json")
        ok, res = query_courses("zhs_cookie", cookie_path=ck)
        return jsonify(ok=ok, courses=res) if ok else jsonify(ok=False, error=res)

    if platform not in ("chaoxing", "zhs"):
        return jsonify(ok=False, error="不支持的平台")
    if not account or not password:
        return jsonify(ok=False, error="请先填写账号和密码")
    ok, res = query_courses(platform, account, password)
    if ok:
        return jsonify(ok=True, courses=res)
    return jsonify(ok=False, error=res)


@app.route("/api/qr_start", methods=["POST"])
@require_login
def api_qr_start(user):
    if not rate_limit("qr_start", 6):
        return jsonify(ok=False, error="操作过于频繁，请稍后再试")
    d = request.get_json(silent=True) or {}
    code = d.get("product", "zhs_qr")
    with db() as c:
        p = c.execute("SELECT * FROM products WHERE code=?", (code,)).fetchone()
    if not p or p["platform"] != "zhsqr":
        return jsonify(ok=False, error="商品不支持扫码")
    oid = uuid.uuid4().hex
    with db() as c:
        c.execute("INSERT INTO orders(id,user_id,product,platform,account,password,courses,status,qr_state,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                  (oid, user["id"], code, "zhs", "", "", "", "waiting_qr", "waiting",
                   now_str()))
    try:
        s = requests.Session()
        s.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/118"})
        r = s.get("https://passport.zhihuishu.com/qrCodeLogin/getLoginQrImg", timeout=15).json()
        QR_SESSIONS[oid] = dict(session=s, qrToken=r["qrToken"],
                                img=base64.b64decode(r["img"]), state="waiting", ts=time.time())
        threading.Thread(target=qr_thread, args=(oid,), daemon=True).start()
        return jsonify(ok=True, oid=oid)
    except Exception as e:
        return jsonify(ok=False, error=f"获取二维码失败: {e}")


@app.route("/order/<oid>/set_courses", methods=["POST"])
@require_login
def order_set_courses(user, oid):
    with db() as c:
        o = c.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
    if not o or (not user["is_admin"] and o["user_id"] != user["id"]):
        return jsonify(ok=False, error="订单不存在")
    d = request.get_json(silent=True) or {}
    courses = (d.get("courses") or "").strip()
    if not courses:
        return jsonify(ok=False, error="请至少选择一门课程")
    set_order(oid, courses=courses, status="pending", note="")
    if o["status"] == "waiting_qr":
        set_order(oid, status="pending")
    return jsonify(ok=True)

# ---- 查单（访客）----
@app.route("/query", methods=["GET", "POST"])
def query():
    user = current_user()
    result = ""
    if request.method == "POST":
        q = request.form.get("oid", "").strip().lower()
        with db() as c:
            row = None
            if len(q) >= 6:
                row = c.execute("SELECT id,product,platform,status,note,created_at,started_at,finished_at FROM orders WHERE id LIKE ? OR substr(id,1,8)=?",
                                (q + "%", q[:8])).fetchone()
        if row:
            o = dict(row)
            result = f"""<div class="card mt-3"><div class="card-body">
<h3 class="card-title">查询结果</h3>
<div class="datagrid">
<div class="datagrid-item"><div class="datagrid-title">单号</div><div class="datagrid-content">{o['id'][:8]}
<a class="copy-btn ms-1" onclick="cp('{o['id']}')">⧉ 复制完整单号</a></div></div>
<div class="datagrid-item"><div class="datagrid-title">商品</div><div class="datagrid-content">{o['product']}</div></div>
<div class="datagrid-item"><div class="datagrid-title">平台</div><div class="datagrid-content">{o['platform']}</div></div>
<div class="datagrid-item"><div class="datagrid-title">状态</div><div class="datagrid-content">{badge(o['status'])} {esc(o['note'])}</div></div>
<div class="datagrid-item"><div class="datagrid-title">下单</div><div class="datagrid-content">{o['created_at']}</div></div>
<div class="datagrid-item"><div class="datagrid-title">开始 / 完成</div><div class="datagrid-content">{o['started_at'] or '-'} / {o['finished_at'] or '-'}</div></div>
</div></div></div>"""
        else:
            result = """<div class="empty mt-3"><div class="empty-header">🔍</div>
<p class="empty-title">未找到订单</p><p class="empty-subtitle text-secondary">请核对单号（可只输入前 8 位）</p></div>"""
    body = f"""<div class="page-header"><h2 class="page-title">🔎 订单查询</h2></div>
<div class="row justify-content-center"><div class="col-lg-6"><div class="card"><div class="card-body">
<form method="post"><label class="form-label">订单号（完整或前 8 位）</label>
<input class="form-control" name="oid" placeholder="例如: 23b0e7f1">
<button class="btn btn-primary w-100 mt-2">查询</button></form></div></div></div></div>{result}"""
    return page("查单", body, user, "query")

# ---- 我的订单 ----
@app.route("/my")
@require_login
def my_orders(user):
    with db() as c:
        rows = c.execute("SELECT o.*,p.name pname FROM orders o LEFT JOIN products p ON p.code=o.product WHERE o.user_id=? ORDER BY o.created_at DESC", (user["id"],)).fetchall()
    if rows:
        tr = "".join(f"""<tr><td class="text-muted">{r['id'][:8]}</td>
<td>{PICON.get(r['platform'],'⚙')} {esc(r['pname']) or esc(r['product'])}</td>
<td>{esc(r['account']) or '(扫码)'}</td><td>{badge(r['status'])}</td>
<td class="text-secondary">{esc(r['note'])}</td>
<td class="text-secondary">{r['created_at']} → {r['finished_at'] or '…'}</td>
<td><a class="btn btn-sm btn-outline-primary" href="/order/{r['id']}">详情</a></td></tr>""" for r in rows)
        body = f"""<div class="page-header d-print-none"><h2 class="page-title">📒 我的订单</h2></div>
<div class="card"><div class="table-responsive"><table class="table table-vcenter card-table">
<thead><tr><th>单号</th><th>商品</th><th>账号</th><th>状态</th><th>备注</th><th>时间</th><th></th></tr></thead>
<tbody>{tr}</tbody></table></div></div>"""
    else:
        body = """<div class="empty"><div class="empty-header">📭</div>
<p class="empty-title">还没有订单</p><p class="empty-subtitle text-secondary">选择一个商品，立即开始自动化</p>
<div class="empty-action"><a class="btn btn-primary" href="/">去下单</a></div></div>"""
    return page("我的订单", body, user, "my")

# ---- 订单详情 ----
@app.route("/order/<oid>")
@require_login
def order_detail(user, oid):
    with db() as c:
        o = c.execute("SELECT o.*,p.name pname FROM orders o LEFT JOIN products p ON p.code=o.product WHERE o.id=?", (oid,)).fetchone()
    if not o or (not user["is_admin"] and o["user_id"] != user["id"]):
        return page("错误", """<div class="empty"><div class="empty-header">🤷</div>
<p class="empty-title">订单不存在</p><div class="empty-action"><a class="btn btn-primary" href="/my">返回</a></div></div>""", user)
    o = dict(o)
    step_active = {"pending": 1, "waiting_qr": 1, "running": 2, "done": 3, "failed": 3, "canceled": 3}
    cur = step_active.get(o["status"], 1)
    steps = f"""<ul class="steps steps-blue mt-2">
<li class="step-item {'active' if cur >= 1 else ''}"><span class="step-number">1</span>下单</li>
<li class="step-item {'active' if cur >= 2 else ''}"><span class="step-number">2</span>执行</li>
<li class="step-item {'active' if cur >= 3 else ''}"><span class="step-number">3</span>完成</li>
</ul>"""
    qr_block = ""
    if o["status"] == "waiting_qr":
        qr_block = f"""<div class="text-center my-2"><p>请用<b>知到 App</b> 扫描二维码</p>
<img class="qrimg" src="/qr/{oid}" style="width:210px;border:8px solid #fff;border-radius:14px">
<p id="qrstate" class="text-secondary">状态: {esc(o.get('qr_state', ''))}</p>
<script>
setInterval(async()=>{{
let j=await(await fetch('/qr_status/{oid}')).json();
document.getElementById('qrstate').innerText='状态: '+j.state;
if(j.state==='confirmed')location.reload();}},1500)
</script></div>"""
    log_path = os.path.join(order_dir(oid), "log.txt")
    log_tail = "（暂无日志，等待执行）"
    if os.path.exists(log_path):
        log_tail = open(log_path, "rb").read()[-8000:].decode("utf-8", errors="replace")
    log_tail = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", log_tail)
    auto = "<script>setTimeout(()=>location.reload(),4000)</script>" if o["status"] in ("running", "waiting_qr") else ""
    body = f"""<div class="page-header d-print-none"><div class="row align-items-center">
<div class="col"><h2 class="page-title">{PICON.get(o['platform'],'⚙')} 订单 {oid[:8]} {badge(o['status'])}</h2>
<div class="text-secondary mt-1">完整单号: {oid} <a class="copy-btn" onclick="cp('{oid}')">⧉ 复制</a></div></div>
<div class="col-auto"><a class="btn btn-ghost-secondary" href="/my">← 返回列表</a></div></div></div>
<div class="card"><div class="card-body">{steps}</div></div>
<div class="card mt-1"><div class="card-body">
<div class="datagrid">
<div class="datagrid-item"><div class="datagrid-title">商品</div><div class="datagrid-content">{esc(o['pname']) or esc(o['product'])}</div></div>
<div class="datagrid-item"><div class="datagrid-title">账号</div><div class="datagrid-content">{esc(o['account']) or '(扫码授权)'}</div></div>
<div class="datagrid-item"><div class="datagrid-title">课程</div><div class="datagrid-content">{esc(o['courses']) or '全部'}</div></div>
<div class="datagrid-item"><div class="datagrid-title">备注</div><div class="datagrid-content">{esc(o['note']) or '-'}</div></div>
<div class="datagrid-item"><div class="datagrid-title">运行环境</div><div class="datagrid-content">{_env_txt(o)}</div></div>
<div class="datagrid-item"><div class="datagrid-title">风控信号</div><div class="datagrid-content">{('<span class="badge bg-red-lt">' + esc(o['risk_flags']) + '</span>') if o.get('risk_flags') else '<span class="text-secondary">无</span>'}</div></div>
</div></div></div>
{qr_block}
<div class="card mt-1"><div class="card-header"><h3 class="card-title">执行日志</h3></div>
<div class="log-console">{esc(log_tail)}</div></div>{auto}"""
    return page("订单详情", body, user)

@app.route("/qr/<oid>")
@require_login
def qr_img(user, oid):
    st = QR_SESSIONS.get(oid)
    if not st:
        return "QR 不存在或已过期", 404
    return Response(st["img"], mimetype="image/png")

@app.route("/qr_status/<oid>")
@require_login
def qr_status(user, oid):
    with db() as c:
        o = c.execute("SELECT qr_state,status FROM orders WHERE id=?", (oid,)).fetchone()
    if not o:
        return jsonify(state="unknown")
    return jsonify(state=o["qr_state"] if o["status"] == "waiting_qr" else "confirmed")

# ---- 批量下单 ----
@app.route("/batch", methods=["GET", "POST"])
@require_login
def batch(user):
    if request.method == "POST":
        lines = request.form.get("lines", "").strip().splitlines()
        n = 0
        with db() as c:
            for ln in lines:
                parts = [x.strip() for x in ln.split(",")]
                if len(parts) < 3:
                    continue
                plat, acc, pwd = parts[0].lower(), parts[1], parts[2]
                if plat not in ("chaoxing", "zhs"):
                    continue
                courses = parts[3] if len(parts) > 3 else ""
                oid = uuid.uuid4().hex
                c.execute("INSERT INTO orders(id,user_id,product,platform,account,password,courses,status,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                          (oid, user["id"], f"{plat}_video", plat, acc, encrypt_secret(pwd), courses, "pending",
                           now_str()))
                n += 1
        body = f"""<div class="empty"><div class="empty-header">🎉</div>
<p class="empty-title">已受理 {n} 个订单</p><p class="empty-subtitle text-secondary">进入队列依次执行</p>
<div class="empty-action"><a class="btn btn-primary" href="/my">查看我的订单</a></div></div>"""
        return page("批量下单", body, user)
    body = """<div class="page-header d-print-none"><h2 class="page-title">📦 批量下单</h2></div>
<div class="row justify-content-center"><div class="col-lg-8"><div class="card"><div class="card-body">
<form method="post">
<label class="form-label">每行一条: 平台,账号,密码,课程ID（可空）</label>
<textarea class="form-control" rows="6" placeholder="chaoxing,13800000000,pwd123,264628209&#10;zhs,13900000000,pwd456,"></textarea>
<button class="btn btn-primary w-100 mt-2">批量提交</button></form>
<p class="text-secondary small mt-2">平台取值: chaoxing（学习通）/ zhs（知到）；课程ID留空 = 刷该账号全部课程。</p>
</div></div></div></div>"""
    return page("批量下单", body, user, "batch")

# ---- 管理后台 ----
@app.route("/admin")
@require_login
def admin(user):
    if not user["is_admin"]:
        return page("拒绝", """<div class="empty"><div class="empty-header">🔒</div>
<p class="empty-title">仅管理员可访问</p><div class="empty-action"><a class="btn btn-primary" href="/">返回</a></div></div>""", user)
    with db() as c:
        stats = {r["status"]: r["n"] for r in c.execute("SELECT status,COUNT(*) n FROM orders GROUP BY status").fetchall()}
        users = c.execute("SELECT id,username,is_admin,created_at FROM users").fetchall()
        running = c.execute("SELECT * FROM orders WHERE status='running'").fetchall()
        recent = c.execute("SELECT o.*,u.username,p.name pname FROM orders o LEFT JOIN users u ON u.id=o.user_id LEFT JOIN products p ON p.code=o.product ORDER BY o.created_at DESC LIMIT 10").fetchall()
    g = lambda k: stats.get(k, 0)
    total = sum(stats.values()) or 1
    colors = {"pending": "var(--tblr-warning)", "running": "var(--tblr-primary)",
              "done": "var(--tblr-success)", "failed": "var(--tblr-danger)", "waiting_qr": "var(--tblr-purple)"}
    bars = "".join(f"""<div class="mt-2"><div class="d-flex justify-content-between" style="font-size:.8rem">
<span>{BADGE.get(k, k)}</span><span class="text-secondary">{v} 单 · {v * 100 // total}%</span></div>
<div class="progress" style="height:7px"><div class="progress-bar" style="width:{max(v * 100 // total, 2)}%;background:{colors.get(k, 'var(--tblr-primary)')}"></div></div></div>"""
                   for k, v in stats.items())
    stat_html = (stat_card("排队中", g('pending'), 'text-yellow') + stat_card("执行中", g('running'), 'text-blue')
                 + stat_card("已完成", g('done'), 'text-green') + stat_card("失败", g('failed'), 'text-red')
                 + stat_card("用户", len(users)))
    def _run_row(r):
        mem = proc_mem_mb(r["pid"]) if r.get("pid") else "-"
        hb = r.get("heartbeat_at") or "-"
        dur = _dur_txt(r["started_at"]) if r.get("started_at") else "-"
        return (f"<tr><td><a href='/order/{r['id']}'>{r['id'][:8]}</a></td>"
                f"<td>{r['platform']}</td><td>{esc(r['account']) or '(扫码)'}</td>"
                f"<td>{r.get('pid') or '-'}</td><td>{mem}</td>"
                f"<td class='text-secondary'>{hb}</td><td class='text-secondary'>{dur}</td></tr>")
    rtr = "".join(_run_row(r) for r in running) or \
          "<tr><td colspan=7 class='text-center text-secondary py-3'>队列空闲</td></tr>"
    utr = "".join(f"<tr><td>{u['id']}</td><td>{esc(u['username'])}</td><td>{'<span class=\"badge bg-purple-lt\">管理员</span>' if u['is_admin'] else '用户'}</td><td class='text-secondary'>{u['created_at']}</td></tr>" for u in users)
    otr = "".join(f"""<tr><td class="text-muted">{r['id'][:8]}</td><td>{esc(r['username']) or '?'}</td><td>{esc(r['pname']) or esc(r['product'])}</td>
<td>{esc(r['account']) or '(扫码)'}</td><td>{badge(r['status'])}</td><td class="text-secondary">{r['created_at']}</td></tr>""" for r in recent)
    # ---- 系统状态（轻量检测，仅管理页触发）----
    cf_status = "未检测到"
    try:
        _tl = subprocess.run(["tasklist"], capture_output=True,
                           creationflags=CREATE_NO_WINDOW, timeout=10)
        cf_status = "运行中" if "cloudflared.exe" in _tl.stdout.decode("gbk", errors="replace") else "未运行"
    except Exception:
        pass
    try:
        _du = shutil.disk_usage(APP_DIR)
        disk_free = f"{_du.free / 1024**3:.1f} GB"
    except Exception:
        disk_free = "-"
    try:
        _lb = float(get_setting("last_backup", "0") or 0)
        last_backup = datetime.fromtimestamp(_lb).strftime("%Y-%m-%d %H:%M") if _lb > 0 else "无"
    except Exception:
        last_backup = "无"
    try:
        with db() as c:
            last_hb = c.execute("SELECT MAX(heartbeat_at) h FROM orders WHERE status='running'").fetchone()["h"] or "-"
    except Exception:
        last_hb = "-"
    sys_card = f"""<div class="card mb-1"><div class="card-body">
<h3 class="card-title">🖥 系统状态</h3>
<div class="row mb-2">
<div class="col"><span class="text-secondary small">平台进程</span><div class="h2 mb-0 text-green">运行中 (PID {os.getpid()})</div></div>
<div class="col"><span class="text-secondary small">SQLite</span><div class="h2 mb-0 text-green">正常</div></div>
<div class="col"><span class="text-secondary small">CF 隧道</span><div class="h2 mb-0 {'text-green' if cf_status == '运行中' else 'text-red'}">{cf_status}</div></div>
<div class="col"><span class="text-secondary small">可用内存</span><div class="h2 mb-0">{free_mem_mb()} MB</div></div>
<div class="col"><span class="text-secondary small">磁盘剩余</span><div class="h2 mb-0">{disk_free}</div></div>
<div class="col"><span class="text-secondary small">最近备份</span><div class="h2 mb-0">{last_backup}</div></div>
<div class="col"><span class="text-secondary small">最近心跳</span><div class="h2 mb-0">{last_hb}</div></div>
</div></div></div>"""
    body = f"""<div class="page-header d-print-none"><div class="row align-items-center">
<div class="col"><h2 class="page-title">🛠 管理后台</h2></div>
<div class="col-auto"><span class="text-secondary small">注册口令: <b>{get_setting('reg_code', DEFAULT_REG_CODE)}</b></span></div></div></div>
<div class="card mb-1"><div class="card-body">
<form method="post" action="/admin/regcode" class="row g-2 align-items-end">
<div class="col-auto"><label class="form-label mb-0">修改注册口令</label></div>
<div class="col"><input class="form-control" name="reg_code" value="{get_setting('reg_code', DEFAULT_REG_CODE)}"></div>
<div class="col-auto"><button class="btn btn-primary">保存</button></div></form>
<p class="text-secondary small mb-0 mt-2">只有知道该口令的人才能在本站注册账号。</p>
</div></div>
<div class="card mb-1"><div class="card-body">
<h3 class="card-title">⚙ 资源与性能</h3>
<div class="row mb-2">
<div class="col"><span class="text-secondary small">当前可用内存</span><div class="h2 mb-0">{free_mem_mb()} MB</div></div>
<div class="col"><span class="text-secondary small">执行线程（每单）</span><div class="h2 mb-0">{get_setting('jobs','2')}</div></div>
<div class="col"><span class="text-secondary small">内存护栏</span><div class="h2 mb-0">{get_setting('min_free_mb','500')} MB</div></div>
<div class="col"><span class="text-secondary small">单日志上限</span><div class="h2 mb-0">{get_setting('log_keep_kb','300')} KB</div></div>
<div class="col"><span class="text-secondary small">日志保留</span><div class="h2 mb-0">{get_setting('log_keep_days','3')} 天</div></div>
<div class="col"><span class="text-secondary small">默认倍速</span><div class="h2 mb-0">{get_setting('speed','2.0')}x</div></div>
</div>
<form method="post" action="/admin/tune" class="row g-2 align-items-end">
<div class="col-auto"><label class="form-label mb-0">执行线程</label>
<select class="form-select" name="jobs">{''.join(f'<option value="{i}" {"selected" if get_setting("jobs","2")==str(i) else ""}>{i}</option>' for i in (1,2,3,4))}</select></div>
<div class="col-auto"><label class="form-label mb-0">内存护栏(MB)</label>
<input class="form-control" name="min_free_mb" value="{get_setting('min_free_mb','500')}" style="width:110px"></div>
<div class="col-auto"><label class="form-label mb-0">日志上限(KB)</label>
<input class="form-control" name="log_keep_kb" value="{get_setting('log_keep_kb','300')}" style="width:110px"></div>
<div class="col-auto"><label class="form-label mb-0">日志保留(天)</label>
<input class="form-control" name="log_keep_days" value="{get_setting('log_keep_days','3')}" style="width:110px"></div>
<div class="col-auto"><label class="form-label mb-0">默认倍速</label>
<input class="form-control" name="speed" value="{get_setting('speed','2.0')}" style="width:100px"></div>
<div class="col-auto"><button class="btn btn-primary">应用</button></div></form>
<p class="text-secondary small mb-0 mt-2">子进程以「低于正常」优先级运行；可用内存低于护栏值时暂停接单；日志超限自动裁剪旧内容。</p>
</div></div>
<div class="card mb-1"><div class="card-body">
<h3 class="card-title">🧪 并发与风控实验</h3>
<div class="row mb-2">
<div class="col"><span class="text-secondary small">并发单数</span><div class="h2 mb-0">{get_setting('concurrency','1')}</div></div>
<div class="col"><span class="text-secondary small">指纹伪装</span><div class="h2 mb-0">{'开' if get_setting('spoof','1')=='1' else '关'}</div></div>
<div class="col"><span class="text-secondary small">倍速抖动</span><div class="h2 mb-0">{'开' if get_setting('jitter','1')=='1' else '关'}</div></div>
<div class="col"><span class="text-secondary small">代理池</span><div class="h2 mb-0">{len([x for x in (get_setting('proxy_pool','') or '').splitlines() if x.strip()])} 条</div></div>
<div class="col"><span class="text-secondary small">单订单超时</span><div class="h2 mb-0">{get_setting('order_timeout_min','180')} 分</div></div>
</div>
<form method="post" action="/admin/tune" class="row g-2 align-items-end">
<div class="col-auto"><label class="form-label mb-0">并发单数(1-6)</label>
<select class="form-select" name="concurrency">{''.join(f'<option value="{i}" {"selected" if get_setting("concurrency","1")==str(i) else ""}>{i}</option>' for i in range(1,7))}</select></div>
<div class="col-auto"><label class="form-label mb-0">指纹伪装</label>
<select class="form-select" name="spoof"><option value="1" {'selected' if get_setting('spoof','1')=='1' else ''}>开</option><option value="0" {'selected' if get_setting('spoof','1')=='0' else ''}>关</option></select></div>
<div class="col-auto"><label class="form-label mb-0">倍速抖动</label>
<select class="form-select" name="jitter"><option value="1" {'selected' if get_setting('jitter','1')=='1' else ''}>开</option><option value="0" {'selected' if get_setting('jitter','1')=='0' else ''}>关</option></select></div>
<div class="col-auto"><label class="form-label mb-0">调试日志</label>
<select class="form-select" name="verbose"><option value="1" {'selected' if get_setting('verbose','0')=='1' else ''}>开(实验)</option><option value="0" {'selected' if get_setting('verbose','0')=='0' else ''}>关</option></select></div>
<div class="col-auto"><label class="form-label mb-0">单订单超时(分)</label>
<input class="form-control" name="order_timeout_min" value="{get_setting('order_timeout_min','180')}" style="width:110px"></div>
<div class="col"><label class="form-label mb-0">代理池（每行一条，如 http://user:pass@ip:port）</label>
<textarea class="form-control" name="proxy_pool" rows="2" placeholder="http://127.0.0.1:7897">{get_setting('proxy_pool','')}</textarea></div>
<div class="col-auto"><button class="btn btn-primary">应用</button></div></form>
<p class="text-secondary small mb-0 mt-2">每单独立工作目录/cookies/TEMP/HOME，并按 UA 池随机伪装指纹；填代理池后每单轮询不同出口 IP。执行完自动扫描日志中的验证码/403/风控关键词并标记。</p>
</div></div>
<div class="row row-cards mb-1">{stat_html}</div>
{sys_card}
<div class="row row-deck mt-1">
<div class="col-lg-5"><div class="card"><div class="card-body">
<h3 class="card-title">📊 订单状态分布</h3>{bars or '<p class="text-secondary">暂无订单</p>'}</div></div></div>
<div class="col-lg-7"><div class="card"><div class="card-body">
<h3 class="card-title">⚡ 执行中队列</h3>
<div class="table-responsive"><table class="table table-vcenter card-table">
<thead><tr><th>单号</th><th>平台</th><th>账号</th><th>PID</th><th>内存<br><span class='text-secondary small'>(MB)</span></th><th>最近心跳</th><th>运行时长</th></tr></thead><tbody>{rtr}</tbody></table></div>
</div></div></div></div>
<div class="card mt-1"><div class="card-body">
<h3 class="card-title">🕒 最近订单</h3>
<div class="table-responsive"><table class="table table-vcenter card-table">
<thead><tr><th>单号</th><th>用户</th><th>商品</th><th>账号</th><th>状态</th><th>下单时间</th></tr></thead><tbody>{otr}</tbody></table></div>
</div></div>
<div class="card mt-1"><div class="card-body">
<h3 class="card-title">👥 用户（{len(users)}）</h3>
<div class="table-responsive"><table class="table table-vcenter card-table">
<thead><tr><th>ID</th><th>用户名</th><th>角色</th><th>注册时间</th></tr></thead><tbody>{utr}</tbody></table></div>
</div></div>
<p class="text-secondary small mt-2">执行器: 单线程顺序队列 ｜ 引擎: fuckCourse（学习通+知到） ｜ 发布: 预留 Cloudflare Tunnel</p>"""
    return page("管理后台", body, user, "admin")

# ---- 登录注册 ----
@app.route("/admin/tune", methods=["POST"])
@require_login
def admin_tune(user):
    if not user["is_admin"]:
        return "无权限", 403
    for key in ("jobs", "min_free_mb", "log_keep_kb", "log_keep_days", "speed",
                "concurrency", "spoof", "jitter", "order_timeout_min", "proxy_pool", "verbose"):
        if key == "proxy_pool":
            set_setting("proxy_pool", request.form.get("proxy_pool", "").strip())
            continue
        v = request.form.get(key, "").strip()
        if v:
            set_setting(key, v)
    return redirect("/admin")


@app.route("/admin/regcode", methods=["POST"])
@require_login
def admin_regcode(user):
    if not user["is_admin"]:
        return "无权限", 403
    code = request.form.get("reg_code", "").strip()
    if code:
        set_setting("reg_code", code)
    return redirect("/admin")


@app.route("/register", methods=["GET", "POST"])
def register():
    user = current_user()
    if request.method == "POST":
        if not rate_limit("register", 5):
            return page("注册", """<div class="empty"><div class="empty-header">⏳</div>
<p class="empty-title">操作过于频繁</p><p class="empty-subtitle text-secondary">请一分钟后再试</p>
<div class="empty-action"><a class="btn btn-primary" href="/register">返回</a></div></div>""", None)
        name = request.form.get("username", "").strip()
        pw = request.form.get("password", "")
        code = request.form.get("reg_code", "").strip()
        if code != get_setting("reg_code", DEFAULT_REG_CODE):
            return page("注册", """<div class="empty"><div class="empty-header">🔐</div>
<p class="empty-title">注册口令不正确</p><p class="empty-subtitle text-secondary">本站需要口令才能注册</p>
<div class="empty-action"><a class="btn btn-primary" href="/register">返回</a></div></div>""", None)
        if not name or len(pw) < 4:
            return page("注册", """<div class="empty"><div class="empty-header">⚠️</div>
<p class="empty-title">用户名或密码过短</p><p class="empty-subtitle text-secondary">密码至少 4 位</p>
<div class="empty-action"><a class="btn btn-primary" href="/register">返回</a></div></div>""", None)
        with db() as c:
            try:
                c.execute("INSERT INTO users(username,pw_hash) VALUES(?,?)", (name, hash_pw(pw)))
            except sqlite3.IntegrityError:
                return page("注册", """<div class="empty"><div class="empty-header">⚠️</div>
<p class="empty-title">用户名已存在</p><div class="empty-action"><a class="btn btn-primary" href="/register">返回</a></div></div>""", None)
        return redirect("/login")
    body = """<div class="row justify-content-center"><div class="col-lg-5"><div class="card">
<div class="card-body"><h2 class="card-title text-center mb-3">注册</h2>
<form method="post"><div class="mb-3"><label class="form-label">用户名</label>
<input class="form-control" name="username"></div>
<div class="mb-3"><label class="form-label">密码（≥4位）</label>
<input class="form-control" name="password" type="password"></div>
<div class="mb-3"><label class="form-label">注册口令</label>
<input class="form-control" name="reg_code" placeholder="请向管理员索取"></div>
<button class="btn btn-primary w-100">注册</button></form>
<p class="text-secondary small mt-3 text-center">已有账号？<a href="/login">去登录</a></p>
</div></div></div></div>"""
    return page("注册", body, user)

@app.route("/login", methods=["GET", "POST"])
def login():
    user = current_user()
    if request.method == "POST":
        if not rate_limit("login", 10):
            return page("登录", """<div class="empty"><div class="empty-header">⏳</div>
<p class="empty-title">尝试过于频繁</p><p class="empty-subtitle text-secondary">请一分钟后再试</p>
<div class="empty-action"><a class="btn btn-primary" href="/login">返回</a></div></div>""", None)
        name = request.form.get("username", "").strip()
        pw = request.form.get("password", "")
        if login_user(name, pw):
            with db() as c:
                row = c.execute("SELECT id FROM users WHERE username=?", (name,)).fetchone()
            if not row:
                return page("登录", "<div class='empty'><div class='empty-header'>⚠️</div><p class='empty-title'>用户不存在</p></div>", None)
            uid = str(row["id"])
            resp = make_response(redirect("/"))
            resp.set_cookie("wk_token", f"{uid}:{hash_pw('u' + uid)}", httponly=True)
            return resp
        return page("登录", """<div class="empty"><div class="empty-header">⚠️</div>
<p class="empty-title">用户名或密码错误</p><div class="empty-action"><a class="btn btn-primary" href="/login">返回</a></div></div>""", None)
    body = """<div class="row justify-content-center"><div class="col-lg-5"><div class="card">
<div class="card-body"><h2 class="card-title text-center mb-3">登录</h2>
<form method="post"><div class="mb-3"><label class="form-label">用户名</label>
<input class="form-control" name="username"></div>
<div class="mb-3"><label class="form-label">密码</label>
<input class="form-control" name="password" type="password"></div>
<button class="btn btn-primary w-100">登录</button></form>
<p class="text-secondary small mt-3 text-center">登录后可下单与查看订单 · 没有账号？<a href="/register">注册</a></p>
</div></div></div></div>"""
    return page("登录", body, user)

@app.route("/logout")
def logout():
    resp = make_response(redirect("/login"))
    resp.delete_cookie("wk_token")
    return resp

@app.route("/health")
def health():
    """健康检查：只返回非敏感状态（供看护进程与人工诊断使用）"""
    db_ok = "ok"
    try:
        with db() as c:
            c.execute("SELECT 1").fetchone()
    except Exception:
        db_ok = "error"
    try:
        with db() as c:
            n_pend = c.execute("SELECT COUNT(*) n FROM orders WHERE status='pending'").fetchone()["n"]
            n_run = c.execute("SELECT COUNT(*) n FROM orders WHERE status='running'").fetchone()["n"]
    except Exception:
        n_pend = n_run = -1
    return jsonify(status="ok" if db_ok == "ok" else "degraded",
                   database=db_ok, queue="ok", pending=n_pend, running=n_run)

if __name__ == "__main__":
    print("平台 v4(Tabler): http://127.0.0.1:8766 （健康检查 /health）")
    try:
        from waitress import serve
        serve(app, host="127.0.0.1", port=8766, threads=16)  # 生产 WSGI；失败回退开发服务器
    except ImportError:
        app.run(host="127.0.0.1", port=8766, debug=False)
