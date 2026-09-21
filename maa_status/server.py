# -*- coding: utf-8 -*-
"""MAA 夜间进度看板 —— HTTP 服务（单文件、零依赖，仅监听 127.0.0.1）

  GET /                看板页面（Basic 认证）
  GET /api/status      进度 JSON（Basic 认证）
  GET /api/report/<date>  单份日报全文（Basic 认证，白名单日期）
  GET /health          探针（免认证，仅 {"ok":true}）

安全设计：
  * 仅绑定 127.0.0.1，公网入口交给 cloudflared，不开放任何入站端口
  * 全部接口 Basic 认证（除 /health）；失败次数过多进入短时封禁
  * 无静态目录服务、无任意路径读取（报告按 YYYY-MM-DD 白名单）
  * 单实例锁（msvcrt 独占），重复启动立即退出
"""
import base64
import json
import logging
import os
import secrets
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from logging.handlers import TimedRotatingFileHandler
from urllib.parse import parse_qs, unquote, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import collector  # noqa: E402

CONF_PATH = os.path.join(HERE, "config.json")
LOG_DIR = os.path.join(HERE, "logs")
CACHE_TTL = 20          # 采集结果缓存秒数（tasklist/COM 调用有开销）
AUTH_FAIL_LIMIT = 12    # 连续认证失败上限
AUTH_BAN_SECONDS = 300

DEFAULT_CONF = {"port": 8791, "bind": "127.0.0.1", "username": "maa", "password": "CHANGE_ME"}


def load_conf():
    conf = dict(DEFAULT_CONF)
    try:
        with open(CONF_PATH, "r", encoding="utf-8") as f:
            conf.update(json.load(f))
    except Exception:
        pass
    return conf


def setup_logging():
    os.makedirs(LOG_DIR, exist_ok=True)
    handler = TimedRotatingFileHandler(os.path.join(LOG_DIR, "server.log"),
                                       when="midnight", backupCount=14, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)


def acquire_single_instance():
    """Windows 命名互斥体（OS 级）：进程退出/崩溃由系统自动释放，不留残留。

    为什么不用文件锁：Windows 上 msvcrt 的字节区间锁会被「后启动进程用 "w"
    打开同名文件」这一步截断动作破坏，实测出现过两个实例同时存活
    （一个占着 8791，另一个空转不服务）。命名互斥体没有这个缺口。
    """
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateMutexW.restype = wintypes.HANDLE
        k32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
        ERROR_ALREADY_EXISTS = 183
        for ns in ("Global", "Local"):
            name = "%s\\MAAStatusBoard.8791" % ns
            ctypes.set_last_error(0)
            h = k32.CreateMutexW(None, False, name)
            if not h:
                continue
            if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
                k32.CloseHandle(h)
                return None          # 同命名空间下已有实例
            return h                 # 拿到唯一互斥体
        return None
    except Exception:
        return None


def release_single_instance(handle):
    try:
        import ctypes
        ctypes.WinDLL("kernel32").CloseHandle(handle)
    except Exception:
        pass


CONF = load_conf()
_state = {"data": None, "ts": 0.0}
_auth = {"fails": 0, "ban_until": 0.0}
_sessions = {}          # token -> 过期时间戳
SESSION_TTL = 7 * 24 * 3600
COOKIE = "maa_sess"


def _new_session():
    tok = secrets.token_urlsafe(24)
    _sessions[tok] = time.time() + SESSION_TTL
    # 顺手清理过期会话
    for k, exp in list(_sessions.items()):
        if exp < time.time():
            _sessions.pop(k, None)
    return tok


def cached_status():
    now = time.time()
    if _state["data"] is None or now - _state["ts"] > CACHE_TTL:
        _state["data"] = collector.collect()
        _state["ts"] = now
    return _state["data"]


class Handler(BaseHTTPRequestHandler):
    server_version = "maa-status"
    sys_version = ""

    # ---------- 工具 ----------
    def _send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Robots-Tag", "noindex, nofollow")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _check_credentials(self, user, pwd):
        ok = (user == CONF["username"] and pwd == CONF["password"])
        if not ok:
            _auth["fails"] += 1
            if _auth["fails"] >= AUTH_FAIL_LIMIT:
                _auth["ban_until"] = time.time() + AUTH_BAN_SECONDS
                _auth["fails"] = 0
                logging.warning("认证失败过多，封禁 %d 秒", AUTH_BAN_SECONDS)
        else:
            _auth["fails"] = 0
        return ok

    def _authorized(self):
        """Cookie 会话（浏览器表单登录）或 Basic（脚本/curl）任一通过即可。

        注：Cloudflare 会剥掉 WWW-Authenticate 头，浏览器不会弹原生 Basic 登录框，
        所以公网走表单 + Cookie 是主路径。
        """
        if time.time() < _auth["ban_until"]:
            return False
        # 1) Cookie
        for part in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == COOKIE and _sessions.get(v, 0) > time.time():
                return True
        # 2) Basic
        raw = self.headers.get("Authorization", "")
        if raw.startswith("Basic "):
            try:
                user, _, pwd = base64.b64decode(raw[6:]).decode("utf-8").partition(":")
            except Exception:
                return False
            return self._check_credentials(user, pwd)
        return False

    def _require_auth(self):
        if self._authorized():
            return True
        self._send(401, json.dumps({"error": "unauthorized"}, ensure_ascii=False),
                   extra={"WWW-Authenticate": 'Basic realm="MAA Status"'})
        return False

    # ---------- 路由 ----------
    def do_GET(self):
        path = urlparse(self.path).path
        try:
            if path == "/health":
                return self._send(200, json.dumps({"ok": True}))
            if path == "/login":
                return self._send(200, LOGIN_HTML, "text/html; charset=utf-8")
            if path == "/logout":
                return self._send(302, "", "text/plain",
                                  extra={"Location": "/login",
                                         "Set-Cookie": "%s=; Path=/; Max-Age=0" % COOKIE})
            if not self._authorized() and path in ("/", "/index.html"):
                return self._send(200, LOGIN_HTML, "text/html; charset=utf-8")
            if not self._require_auth():
                return
            if path in ("/", "/index.html"):
                return self._send(200, INDEX_HTML, "text/html; charset=utf-8")
            if path == "/api/status":
                return self._send(200, json.dumps(cached_status(), ensure_ascii=False))
            if path.startswith("/api/report/"):
                date = unquote(path[len("/api/report/"):]).strip()
                text = collector.read_report(date)
                if text is None:
                    return self._send(404, json.dumps({"error": "not found"}))
                return self._send(200, json.dumps({"date": date, "markdown": text},
                                                  ensure_ascii=False))
            return self._send(404, json.dumps({"error": "not found"}))
        except BrokenPipeError:
            pass
        except Exception as e:
            logging.exception("GET %s failed", path)
            try:
                self._send(500, json.dumps({"error": repr(e)}, ensure_ascii=False))
            except Exception:
                pass

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            if path != "/login":
                return self._send(404, json.dumps({"error": "not found"}))
            if time.time() < _auth["ban_until"]:
                return self._send(429, LOGIN_HTML.replace("<!--ERR-->",
                                  "尝试过于频繁，请稍后再试"), "text/html; charset=utf-8")
            n = int(self.headers.get("Content-Length") or 0)
            data = parse_qs(self.rfile.read(min(n, 4096)).decode("utf-8", "ignore"))
            user = (data.get("username") or [""])[0]
            pwd = (data.get("password") or [""])[0]
            if self._check_credentials(user, pwd):
                tok = _new_session()
                logging.info("登录成功，来源 %s", self.address_string())
                return self._send(302, "", "text/plain",
                                  extra={"Location": "/",
                                         "Set-Cookie": "%s=%s; Path=/; Max-Age=%d; HttpOnly; "
                                                       "SameSite=Lax" % (COOKIE, tok, SESSION_TTL)})
            logging.warning("登录失败，来源 %s", self.address_string())
            return self._send(401, LOGIN_HTML.replace("<!--ERR-->", "用户名或密码错误"),
                              "text/html; charset=utf-8")
        except Exception:
            logging.exception("POST %s failed", path)
            try:
                self._send(500, json.dumps({"error": "internal"}, ensure_ascii=False))
            except Exception:
                pass

    do_HEAD = do_GET

    def log_message(self, fmt, *args):
        logging.info("%s - %s", self.address_string(), fmt % args)


LOGIN_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>MAA 夜间进度 · 登录</title>
<style>
  body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
       background:#0d1117;color:#e6edf3;
       font:14px/1.6 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif}
  .box{width:min(92vw,340px);background:#161b22;border:1px solid #2a3240;border-radius:14px;
       padding:24px 22px;box-shadow:0 10px 40px rgba(0,0,0,.45)}
  h1{font-size:17px;margin:0 0 4px}
  p.sub{color:#8b949e;font-size:12px;margin:0 0 18px}
  label{display:block;color:#8b949e;font-size:12px;margin:12px 0 5px}
  input{width:100%;box-sizing:border-box;padding:9px 11px;border-radius:8px;
        border:1px solid #2a3240;background:#0d1117;color:#e6edf3;font-size:14px}
  input:focus{outline:none;border-color:#58a6ff}
  button{width:100%;margin-top:18px;padding:10px;border:0;border-radius:8px;
         background:#1f6feb;color:#fff;font-size:14px;cursor:pointer}
  button:hover{background:#388bfd}
  .err{margin-top:14px;color:#f85149;font-size:12.5px;min-height:16px}
  .foot{margin-top:16px;color:#8b949e;font-size:11px;text-align:center}
</style>
</head>
<body>
<form class="box" method="post" action="/login">
  <h1>MAA 夜间进度</h1>
  <p class="sub">仅授权访问 · 需要登录</p>
  <label for="u">用户名</label>
  <input id="u" name="username" autocomplete="username" autofocus>
  <label for="p">密码</label>
  <input id="p" name="password" type="password" autocomplete="current-password">
  <button type="submit">登录</button>
  <div class="err"><!--ERR--></div>
  <div class="foot">登录状态保持 7 天 · 隧道入口 Cloudflare</div>
</form>
</body>
</html>
"""


INDEX_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>MAA 夜间进度</title>
<style>
  :root{
    --bg:#0d1117; --panel:#161b22; --panel2:#1c2230; --line:#2a3240;
    --fg:#e6edf3; --dim:#8b949e; --accent:#58a6ff;
    --ok:#3fb950; --warn:#d29922; --bad:#f85149; --run:#58a6ff;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--fg);
       font:14px/1.55 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif}
  .wrap{max-width:1040px;margin:0 auto;padding:18px 14px 60px}
  h1{font-size:19px;margin:0 0 2px}
  .sub{color:var(--dim);font-size:12px}
  .row{display:flex;flex-wrap:wrap;gap:12px;margin-top:14px}
  .card{background:var(--panel);border:1px solid var(--line);border-radius:12px;
        padding:14px 15px;flex:1 1 300px;min-width:280px}
  .card h2{font-size:13px;margin:0 0 10px;color:var(--dim);font-weight:600;
           letter-spacing:.03em;text-transform:uppercase}
  .big{font-size:26px;font-weight:700}
  .badge{display:inline-block;padding:2px 9px;border-radius:999px;font-size:12px;
         border:1px solid transparent;white-space:nowrap}
  .b-ok{background:rgba(63,185,80,.14);color:var(--ok);border-color:rgba(63,185,80,.35)}
  .b-warn{background:rgba(210,153,34,.14);color:var(--warn);border-color:rgba(210,153,34,.35)}
  .b-bad{background:rgba(248,81,73,.14);color:var(--bad);border-color:rgba(248,81,73,.35)}
  .b-run{background:rgba(88,166,255,.14);color:var(--run);border-color:rgba(88,166,255,.35)}
  .b-dim{background:rgba(139,148,158,.12);color:var(--dim);border-color:rgba(139,148,158,.3)}
  table{width:100%;border-collapse:collapse;font-size:13px}
  td,th{padding:5px 4px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
  th{color:var(--dim);font-weight:600;font-size:12px}
  .q{list-style:none;margin:0;padding:0}
  .q li{display:flex;align-items:center;gap:9px;padding:5px 0;border-bottom:1px dashed var(--line)}
  .q li:last-child{border-bottom:0}
  .dot{width:9px;height:9px;border-radius:50%;background:#39414d;flex:0 0 9px}
  .q .done .dot{background:var(--ok)} .q .running .dot{background:var(--run);
      box-shadow:0 0 0 4px rgba(88,166,255,.18)} .q .error .dot{background:var(--bad)}
  .q .pending{color:var(--dim)}
  .mono{font-family:ui-monospace,Consolas,monospace;font-size:12px}
  .kv{display:flex;justify-content:space-between;gap:10px;padding:4px 0;
      border-bottom:1px dashed var(--line)}
  .kv:last-child{border-bottom:0}
  .kv span:first-child{color:var(--dim)}
  .ev{max-height:280px;overflow:auto}
  .ev div{padding:3px 0;border-bottom:1px dashed var(--line);font-size:12.5px}
  .k-warn{color:var(--warn)} .k-action{color:var(--accent)} .k-done{color:var(--ok)}
  button{background:var(--panel2);color:var(--fg);border:1px solid var(--line);
         border-radius:8px;padding:5px 11px;cursor:pointer;font-size:13px}
  button:hover{border-color:var(--accent)}
  a{color:var(--accent)}
  details{margin-top:8px}
  summary{cursor:pointer;color:var(--dim);font-size:13px}
  pre{white-space:pre-wrap;word-break:break-word;background:var(--panel2);
      padding:10px;border-radius:8px;font-size:12px;max-height:420px;overflow:auto}
  .foot{color:var(--dim);font-size:11.5px;margin-top:22px;text-align:center}
</style>
</head>
<body>
<div class="wrap">
  <div class="row" style="align-items:center">
    <div style="flex:1 1 auto">
      <h1>MAA 夜间进度</h1>
      <div class="sub" id="sub">加载中…</div>
    </div>
    <div>
      <span id="stateBadge" class="badge b-dim">…</span>
      <button onclick="load()">刷新</button>
    </div>
  </div>

  <div class="row">
    <div class="card" style="flex:1 1 240px">
      <h2>当前状态</h2>
      <div id="nowBox"></div>
    </div>
    <div class="card" style="flex:1 1 260px">
      <h2>理智（实测）</h2>
      <div id="sanityBox"></div>
    </div>
    <div class="card" style="flex:1 1 240px">
      <h2>健康度</h2>
      <div id="healthBox"></div>
    </div>
  </div>

  <div class="row">
    <div class="card">
      <h2>最近一遍队列进度</h2>
      <ul class="q" id="queue"></ul>
      <div id="queueNote" style="color:var(--dim);font-size:12px;margin-top:8px"></div>
    </div>
    <div class="card">
      <h2>两遍状态</h2>
      <div id="passes"></div>
    </div>
  </div>

  <div class="row">
    <div class="card">
      <h2>自动关机 / 计划任务</h2>
      <div id="tasks"></div>
    </div>
    <div class="card">
      <h2>事件时间线</h2>
      <div class="ev" id="events"></div>
    </div>
  </div>

  <div class="row">
    <div class="card" style="flex:1 1 100%">
      <h2>历史日报</h2>
      <table id="reports"></table>
      <div id="reportBody"></div>
    </div>
  </div>

  <div class="row">
    <div class="card" style="flex:1 1 100%">
      <h2>夜巡日志尾部 / 消耗事件</h2>
      <details><summary>MAA 记录的消耗事件（原始行，MAA 会重复打印，仅作参考）</summary>
        <div id="spendEvents" class="mono" style="margin-top:6px"></div></details>
      <details><summary>夜巡日志尾部</summary>
        <div class="ev mono" id="watch" style="margin-top:6px"></div></details>
    </div>
  </div>

  <div class="foot">数据源：MAA debug 日志（只读）· 每 30 秒自动刷新 · 运行窗口 00:00–06:30
    ｜ <a href="/logout">退出登录</a></div>
</div>

<script>
function esc(s){return String(s==null?'':s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}
function badge(text, cls){return `<span class="badge ${cls}">${esc(text)}</span>`}
function strip(s){return String(s==null?'':s).replace(/[\r\n]+/g,' ').trim()}

async function load(){
  try{
    const r = await fetch('/api/status',{cache:'no-store'});
    if(!r.ok) throw new Error('HTTP '+r.status);
    render(await r.json());
  }catch(e){
    document.getElementById('sub').textContent = '读取失败：'+e.message;
  }
}

function render(d){
  document.getElementById('sub').textContent = '数据时间 '+d.now+' ｜ 每 30 秒自动刷新';
  const sb = document.getElementById('stateBadge');
  if(d.running) { sb.className='badge b-run'; sb.textContent='运行中'; }
  else if(d.in_window){ sb.className='badge b-warn'; sb.textContent='窗口内·未运行'; }
  else { sb.className='badge b-dim'; sb.textContent='窗口外·待机'; }

  const q = d.queue, t = d.tonight, s = d.sanity, tr = s.track;
  const curRow = q.rows.find(x=>x.state==='running');
  const curLabel = curRow ? curRow.label
      : (q.current && q.rows.find(x=>x.key===q.current) ? q.rows.find(x=>x.key===q.current).label+'（已完成）' : '—');

  // 当前状态
  document.getElementById('nowBox').innerHTML =
    `<div class="big" style="font-size:20px">${esc(curLabel)}</div>
     <div class="kv"><span>MAA 进程</span><span>${d.procs['MAA.exe']?badge('在跑','b-run'):badge('未运行','b-dim')}</span></div>
     <div class="kv"><span>MuMu 模拟器</span><span>${d.procs['MuMuPlayer.exe']?badge('在跑','b-run'):badge('未运行','b-dim')}</span></div>
     <div class="kv"><span>隧道 cloudflared</span><span>${d.procs['cloudflared.exe']?badge('正常','b-ok'):badge('缺失','b-bad')}</span></div>
     <div class="kv"><span>夜巡最后活动</span><span class="mono">${esc(t.watch_last||'—')}</span></div>
     <div class="kv"><span>下线前那遍</span><span>${t.switched_to_final?badge('已接管','b-ok'):badge('未到点','b-dim')}</span></div>`;

  // 理智（实测轨迹，最硬判据）
  const last = tr.last, first = tr.first;
  const clearBadge = (s.current!=null && s.current<=5) ? badge('已清空','b-ok') : badge('有剩余','b-warn');
  document.getElementById('sanityBox').innerHTML =
    `<div class="big">${s.current==null?'—':s.current}
       <span style="font-size:13px;color:var(--dim)"> 理智</span> ${s.current==null?'':clearBadge}</div>
     <div class="kv"><span>实测采样</span><span class="mono">${first?esc(first.t.slice(5,16)): '—'} → ${last?esc(last.t.slice(5,16)):'—'}</span></div>
     <div class="kv"><span>实测净消耗</span><span>${tr.spent==null?'—':'−'+tr.spent+' 理智（'+first.v+' → '+last.v+'）'}</span></div>
     <div class="kv"><span>兜底关</span><span class="mono">${s.last_stage?esc(s.last_stage.selected):'—'}</span></div>
     <div class="kv"><span>候选顺序</span><span class="mono">${s.last_stage?esc(s.last_stage.from):'—'}</span></div>
     <div class="kv"><span>selected null</span><span>${s.stage_null?badge(s.stage_null+' 次','b-bad'):badge('0 次','b-ok')}</span></div>
     <div class="kv"><span>采样点</span><span>${tr.samples} 个</span></div>`;

  // 健康度
  const rg = d.roguelike;
  const rgCls = rg.level==='ok'?'b-ok':(rg.level==='warn'?'b-warn':'b-bad');
  const maxR = Math.max(...t.passes.map(p=>p.maa_restarts),0);
  const maxE = Math.max(...t.passes.map(p=>p.emu_restarts),0);
  document.getElementById('healthBox').innerHTML =
    `<div class="kv"><span>肉鸽异常截图(12h)</span><span>${badge(rg.count+' 张', rgCls)}</span></div>
     <div class="kv"><span>MAA 重启</span><span>${maxR} 次</span></div>
     <div class="kv"><span>模拟器重启</span><span>${maxE} 次</span></div>
     <div class="kv"><span>GUI 启动(24h)</span><span>${s.gui_started} 次</span></div>
     <div class="kv"><span>未恢复的异常</span><span>${q.errors.length?badge(q.errors.join(', '),'b-bad'):badge('无','b-ok')}</span></div>
     <div class="kv"><span>肉鸽最新截图</span><span class="mono" style="font-size:11px">${rg.latest.length?esc(rg.latest[rg.latest.length-1]):'—'}</span></div>`;

  // 队列
  const label = st => st==='done'?'完成':st==='running'?'进行中':st==='error'?'异常':(st==='pending'?'待执行':'');
  document.getElementById('queue').innerHTML = q.rows.map(r=>
    `<li class="${r.state}"><span class="dot"></span><span>${esc(r.label)}</span>
     <span style="margin-left:auto;color:var(--dim)" class="mono">${label(r.state)}</span></li>`).join('');
  document.getElementById('queueNote').textContent =
    '依据 asst 日志事件流（最后一次「启动游戏」起的这一遍）· Copilot 未配置作业码时始终为「待执行」';

  // 两遍
  document.getElementById('passes').innerHTML = t.passes.length ? t.passes.map(p=>
    `<div style="margin-bottom:10px">
       <div>${badge('第 '+p.n+' 遍','b-dim')} <span class="mono">${esc(p.start)} → ${esc(p.end)}</span></div>
       <div style="margin-top:5px">${esc(strip(p.status))}</div>
       <div class="kv"><span>最后阶段</span><span class="mono">${esc(strip(p.phase))}</span></div>
       <div class="kv"><span>重启</span><span>MAA ${p.maa_restarts} / 模拟器 ${p.emu_restarts}</span></div>
     </div>`).join('')
    : `<div style="color:var(--dim)">暂无（报告在夜巡收尾时写入；01:00–06:22 期间这里会显示上一晚的）</div>`;

  // 计划任务
  const T = d.tasks;
  const rows = T.tasks.map(x=>`<tr><td>${esc(x.name)}</td>
      <td>${x.enabled?badge('启用','b-ok'):badge('禁用','b-dim')}</td>
      <td class="mono">${esc(x.next||'—')}</td></tr>`).join('');
  document.getElementById('tasks').innerHTML =
    `<div class="kv"><span>下次自动关机</span><span>${T.shutdown_next?badge(T.shutdown_next,'b-warn'):'—'}</span></div>
     <table><tr><th>任务</th><th>状态</th><th>下次运行</th></tr>${rows}</table>
     ${T.error?`<div style="color:var(--bad);margin-top:6px">${esc(T.error)}</div>`:''}`;

  // 事件时间线
  const evs = t.passes.flatMap(p=>p.events.map(e=>({...e,p:p.n})));
  document.getElementById('events').innerHTML = evs.length ? evs.map(e=>
    `<div><span class="mono" style="color:var(--dim)">${esc(e.t)}</span>
      <span class="k-${esc(e.k)}">[${esc(e.k)}]</span> ${esc(strip(e.m))}</div>`).join('')
    : '<div style="color:var(--dim)">无事件</div>';

  // 历史日报
  document.getElementById('reports').innerHTML =
    '<tr><th>日期</th><th>结论摘要</th><th></th></tr>' +
    d.reports.map(r=>`<tr><td class="mono">${esc(r.date)}</td>
       <td>${esc(strip(r.conclusion))||'<span style="color:var(--dim)">—</span>'}</td>
       <td><button onclick="openReport('${esc(r.date)}')">全文</button></td></tr>`).join('');

  document.getElementById('spendEvents').innerHTML = s.spend_events.length
    ? s.spend_events.map(e=>`<div>${esc(e.t)} &nbsp; 开始行动 ${esc(e.n)} 次, −${e.cost} 理智</div>`).join('')
    : '<div style="color:var(--dim)">近 24h 无记录</div>';
  document.getElementById('watch').innerHTML =
    t.watch_tail.map(l=>`<div>${esc(strip(l))}</div>`).join('') || '<div style="color:var(--dim)">—</div>';
}

async function openReport(date){
  const box = document.getElementById('reportBody');
  box.innerHTML = '<div style="color:var(--dim)">加载中…</div>';
  const r = await fetch('/api/report/'+encodeURIComponent(date),{cache:'no-store'});
  if(!r.ok){ box.innerHTML='<div style="color:var(--bad)">读取失败</div>'; return; }
  const j = await r.json();
  box.innerHTML = `<details open style="margin-top:12px"><summary>${esc(date)} 报告全文</summary>
      <pre>${esc(j.markdown)}</pre></details>`;
}

load();
setInterval(load, 30000);
</script>
</body>
</html>
"""


class Server(ThreadingHTTPServer):
    """HTTPServer 默认 allow_reuse_address=1，而 Windows 的 SO_REUSEADDR
    语义与 Linux 不同：它允许第二个进程也绑上同一端口（端口被抢占，
    连接随机落到某一个进程）。这里显式关掉，让重复启动直接报占用。"""
    allow_reuse_address = False
    daemon_threads = True


def main():
    setup_logging()
    os.makedirs(LOG_DIR, exist_ok=True)
    lock = acquire_single_instance()
    if lock is None:
        logging.info("已有实例在运行，本次退出")
        sys.exit(0)
    try:
        srv = Server((CONF["bind"], int(CONF["port"])), Handler)
    except OSError as e:
        logging.error("端口 %s 绑定失败（可能已有旧实例）：%r", CONF["port"], e)
        release_single_instance(lock)
        sys.exit(1)
    logging.info("MAA 进度看板启动：http://%s:%s（仅本机，公网经 cloudflared）",
                 CONF["bind"], CONF["port"])
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            srv.server_close()
        except Exception:
            pass
        release_single_instance(lock)


if __name__ == "__main__":
    main()
