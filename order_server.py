# -*- coding: utf-8 -*-
"""网课批量测试下单系统（本地）
========================================
- 网页下单: 填账号密码, 或 知到扫码登录
- 后台单线程队列执行刷课任务, 每单独立 cookies/日志
- 端口 127.0.0.1:8765, 不对外网暴露

启动: python order_server.py
页面: http://127.0.0.1:8765
"""
import base64
import json
import os
import subprocess
import threading
import time
import uuid
from datetime import datetime

import requests
from flask import Flask, request, jsonify, Response

APP_DIR = os.path.dirname(os.path.abspath(__file__))
FUCK_DIR = os.path.join(APP_DIR, "fuckCourse")
ORDER_DIR = os.path.join(APP_DIR, "orders")
os.makedirs(ORDER_DIR, exist_ok=True)

PYEXE = r"C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\python.exe"

app = Flask(__name__)

# ---------- 存储 ----------
_lock = threading.Lock()

def orders_index_path():
    return os.path.join(ORDER_DIR, "index.json")

def load_orders():
    with _lock:
        if os.path.exists(orders_index_path()):
            return json.load(open(orders_index_path(), encoding="utf-8"))
        return {}

def save_orders(orders):
    with _lock:
        json.dump(orders, open(orders_index_path(), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)

def order_dir(oid):
    d = os.path.join(ORDER_DIR, oid)
    os.makedirs(d, exist_ok=True)
    return d

def update_order(oid, **kw):
    orders = load_orders()
    if oid in orders:
        orders[oid].update(kw)
        save_orders(orders)

# ---------- 知到扫码会话 ----------
QR_SESSIONS = {}  # oid -> dict(session, qrToken, img_bytes, state)

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
                update_order(oid, qr_state="scanned")
            elif status == 1:
                # 一次性密码完成登录
                s.get("https://passport.zhihuishu.com/login",
                      params={"service": "https://onlineservice-api.zhihuishu.com/login/gologin",
                              "pwd": r.get("oncePassword")}, timeout=10)
                cookies = requests.utils.dict_from_cookiejar(s.cookies)
                # 保存为 run_zhs 兼容格式
                cookie_path = os.path.join(order_dir(oid), "cookies.json")
                data = {}
                if os.path.exists(cookie_path):
                    data = json.load(open(cookie_path, encoding="utf-8"))
                data["zhs"] = [{ "name": k, "value": v, "domain": ".zhihuishu.com" }
                               for k, v in cookies.items()]
                json.dump(data, open(cookie_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
                st["state"] = "confirmed"
                update_order(oid, status="pending", qr_state="confirmed",
                             account_note="扫码登录成功")
                break
            elif status == 2:
                st["state"] = "expired"
                update_order(oid, qr_state="expired")
                break
            elif status == 3:
                st["state"] = "canceled"
                update_order(oid, qr_state="canceled")
                break
    except Exception as e:
        st["state"] = f"error: {e}"
        update_order(oid, qr_state=st["state"])

# ---------- 执行器 ----------
def run_chaoxing_order(oid, o):
    d = order_dir(oid)
    env = os.environ.copy()
    env["FUCKCOURSE_CONFIG"] = os.path.join(FUCK_DIR, "config.json")
    env["FUCKCOURSE_COOKIES"] = os.path.join(d, "cookies.json")
    env["FUCKCOURSE_LOG_DIR"] = os.path.join(FUCK_DIR, "logs")
    courses = o.get("courses") or "264628209"
    cmd = [PYEXE, "-u", os.path.join(FUCK_DIR, "chaoxing", "main.py"),
           "-u", o["account"], "-p", o["password"], "-l", courses, "-s", "2.0", "--auto-sign"]
    with open(os.path.join(d, "log.txt"), "ab") as lf:
        p = subprocess.run(cmd, env=env, stdout=lf, stderr=subprocess.STDOUT, cwd=os.path.join(FUCK_DIR, "chaoxing"))
    return p.returncode

def run_zhs_order(oid, o):
    d = order_dir(oid)
    env = os.environ.copy()
    env["FUCKCOURSE_CONFIG"] = os.path.join(FUCK_DIR, "config.json")
    env["FUCKCOURSE_COOKIES"] = os.path.join(d, "cookies.json")
    args = ["full"] + (o.get("courses").split() if o.get("courses") else [])
    if not o.get("courses"):
        # 未指定课程 → 刷全部: 先查课程列表
        lst = subprocess.run([PYEXE, os.path.join(FUCK_DIR, "run_zhs.py"), "list"],
                             env=env, capture_output=True, text=True, timeout=180)
        ids = [ln.rsplit("=", 1)[-1].strip() for ln in lst.stdout.splitlines()
               if "[hike]" in ln or "[zhidao]" in ln]
        args = ["full"] + ids
    cmd = [PYEXE, os.path.join(FUCK_DIR, "run_zhs.py")] + args
    with open(os.path.join(d, "log.txt"), "ab") as lf:
        p = subprocess.run(cmd, env=env, stdout=lf, stderr=subprocess.STDOUT, cwd=FUCK_DIR)
    return p.returncode

def worker():
    while True:
        orders = load_orders()
        oid = next((k for k, v in orders.items() if v["status"] == "pending"
                    and not v.get("worker_running")), None)
        if oid is None:
            time.sleep(3)
            continue
        o = orders[oid]
        update_order(oid, worker_running=True, status="running",
                     started_at=datetime.now().strftime("%H:%M:%S"))
        try:
            if o["platform"] == "chaoxing":
                rc = run_chaoxing_order(oid, o)
            else:
                rc = run_zhs_order(oid, o)
            update_order(oid, status="done" if rc == 0 else "failed",
                         finished_at=datetime.now().strftime("%H:%M:%S"),
                         exit_code=rc)
        except Exception as e:
            update_order(oid, status="failed", error=str(e),
                         finished_at=datetime.now().strftime("%H:%M:%S"))
        finally:
            update_order(oid, worker_running=False)

threading.Thread(target=worker, daemon=True).start()

# ---------- 路由 ----------
PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>网课批量测试下单</title>
<style>
body{font-family:system-ui,'Microsoft YaHei';max-width:760px;margin:30px auto;padding:0 16px;background:#111;color:#eee}
input,select,button{padding:8px;margin:4px 0;border-radius:6px;border:1px solid #555;background:#222;color:#eee;width:100%;box-sizing:border-box}
button{background:#0a7;cursor:pointer;font-weight:bold}
table{width:100%%;border-collapse:collapse;margin-top:12px}
td,th{border:1px solid #444;padding:6px;font-size:14px}
.tag{padding:2px 8px;border-radius:4px;font-size:12px}
.pending{background:#b80} .running{background:#07a} .done{background:#0a7} .failed{background:#a33} .waiting_qr{background:#86f}
pre{background:#1a1a1a;padding:8px;max-height:260px;overflow:auto;font-size:12px}
</style></head><body>
<h2>📋 网课批量测试下单</h2>
<form method="post" action="/order">
平台: <select name="platform" id="pf" onchange="pfChanged()">
  <option value="chaoxing">学习通（账号密码）</option>
  <option value="zhs">知到（账号密码）</option>
  <option value="zhsqr">知到（扫码登录）</option>
</select>
账号: <input name="account" id="acc" placeholder="手机号（扫码可留空）">
密码: <input name="password" type="text" id="pwd" placeholder="密码（扫码可留空）">
课程ID: <input name="courses" placeholder="留空=该账号全部课程；学习通默认 264628209（数字信息综合）">
<button type="submit">提交订单</button>
</form>
<script>
function pfChanged(){
  var pf=document.getElementById('pf').value;
  document.getElementById('acc').disabled = (pf==='zhsqr');
  document.getElementById('pwd').disabled = (pf==='zhsqr');
}
</script>
<h3>订单列表 <a href="/orders" style="color:#0af;font-size:14px">刷新</a></h3>
%(order_table)s
</body></html>""".replace("%(order_table)s", "__ORDER_TABLE__")

ORDER_ROW = """<tr><td>%(oid)s</td><td>%(platform)s</td><td>%(account)s</td>
<td><span class="tag %(status)s">%(status)s %(qr)s</span></td>
<td>%(courses)s</td><td>%(times)s</td>
<td><a href="/order_detail?oid=%(oid)s">详情/日志</a></td></tr>"""

def render_index():
    orders = load_orders()
    rows = []
    for oid, o in reversed(list(orders.items())):
        rows.append(ORDER_ROW % dict(
            oid=oid[:8], platform=o["platform"], account=o.get("account") or "(扫码)",
            status=o["status"], qr=("·" + o["qr_state"]) if o.get("qr_state") else "",
            courses=o.get("courses") or "全部",
            times=f'{o.get("started_at","-")}→{o.get("finished_at","-")}'))
    return PAGE.replace("__ORDER_TABLE__", (
        "<table><tr><th>单号</th><th>平台</th><th>账号</th><th>状态</th><th>课程</th><th>时间</th><th></th></tr>"
        + ("".join(rows) or "<tr><td colspan=7>暂无订单</td></tr>") + "</table>"))

@app.route("/")
def index():
    return render_index()

@app.route("/order", methods=["POST"])
def create_order():
    platform = request.form.get("platform")
    account = request.form.get("account", "").strip()
    password = request.form.get("password", "").strip()
    courses = request.form.get("courses", "").strip()
    oid = uuid.uuid4().hex
    order = dict(platform=("zhs" if platform == "zhsqr" else platform),
                 account=account, password=password, courses=courses,
                 status="pending", created_at=datetime.now().strftime("%H:%M:%S"))
    if platform == "zhsqr":
        order.update(status="waiting_qr", qr_state="waiting", account="")
        save_orders({**load_orders(), oid: order})
        # 起扫码会话
        s = requests.Session()
        s.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/118"})
        r = s.get("https://passport.zhihuishu.com/qrCodeLogin/getLoginQrImg", timeout=10).json()
        QR_SESSIONS[oid] = dict(session=s, qrToken=r["qrToken"],
                                img=base64.b64decode(r["img"]), state="waiting")
        threading.Thread(target=qr_thread, args=(oid,), daemon=True).start()
    else:
        if not account or not password:
            return "账号密码不能为空 <a href='/'>返回</a>", 400
        save_orders({**load_orders(), oid: order})
    return Response(f"""<script>setTimeout(()=>location='/order_detail?oid={oid}',800)</script>
    已受理，单号 {oid[:8]}，<a href='/order_detail?oid={oid}'>进入详情</a>""", mimetype="text/html")

@app.route("/qr/<oid>")
def qr_img(oid):
    st = QR_SESSIONS.get(oid)
    if not st:
        return "QR 不存在或已过期", 404
    return Response(st["img"], mimetype="image/png")

@app.route("/qr_status/<oid>")
def qr_status(oid):
    st = QR_SESSIONS.get(oid, {})
    return jsonify(state=st.get("state", "unknown"))

@app.route("/order_detail")
def order_detail():
    oid = request.args.get("oid")
    orders = load_orders()
    o = orders.get(oid)
    if not o:
        return "订单不存在", 404
    qr_block = ""
    if o["status"] == "waiting_qr":
        qr_block = f"""<p>请用<b>知到App</b>扫描二维码 <img src="/qr/{oid}" style="width:220px;border:8px solid #fff;border-radius:8px">
<p id="qrstate">状态: {o.get('qr_state','')}</p>
<script>setInterval(async()=>{{
 let r=await fetch('/qr_status/{oid}');let j=await r.json();
 document.getElementById('qrstate').innerText='状态: '+j.state;
 if(j.state==='confirmed')location.reload();
}},1500)</script>"""
    log_path = os.path.join(order_dir(oid), "log.txt")
    log_tail = ""
    if os.path.exists(log_path):
        raw = open(log_path, "rb").read()[-6000:]
        try:
            log_tail = raw.decode("utf-8", errors="replace")
        except Exception:
            log_tail = raw.decode("gbk", errors="replace")
    import re
    log_tail = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", log_tail)
    auto = "<script>setTimeout(()=>location.reload(),4000)</script>" if o["status"] in ("running", "waiting_qr") else ""
    return Response(f"""<!doctype html><html><head><meta charset="utf-8"><title>订单 {oid[:8]}</title>
<style>body{{font-family:system-ui,'Microsoft YaHei';max-width:900px;margin:30px auto;padding:0 16px;background:#111;color:#eee}}
pre{{background:#1a1a1a;padding:10px;max-height:420px;overflow:auto;font-size:12px;white-space:pre-wrap}}
a{{color:#0af}} .tag{{padding:2px 8px;border-radius:4px}} .running{{background:#07a}} .done{{background:#0a7}} .failed{{background:#a33}} .pending{{background:#b80}} .waiting_qr{{background:#86f}}</style>
</head><body>
<h2>订单 {oid[:8]} <span class="tag {o['status']}">{o['status']}</span>
<a href="/orders">← 返回列表</a></h2>
<p>平台: {o['platform']} ｜ 账号: {o.get('account') or '(扫码)'} ｜ 课程: {o.get('courses') or '全部'}</p>
{qr_block}
<pre id="log">{log_tail or '(暂无日志，等待执行)'}</pre>
{auto}</body></html>""", mimetype="text/html")

@app.route("/orders")
def orders_redirect():
    return Response("<script>location='/'</script>", mimetype="text/html")

if __name__ == "__main__":
    print("下单系统: http://127.0.0.1:8765")
    app.run(host="127.0.0.1", port=8765, debug=False)
