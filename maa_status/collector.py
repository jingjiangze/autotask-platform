# -*- coding: utf-8 -*-
"""MAA 夜间进度看板 —— 采集器（只读，绝不触碰模拟器 / MAA）

数据来源（全部只读）：
  D:\\Work UP\\MAA6173\\debug\\
    night_report.txt   每遍状态 / 事件时间线（night_watch 写）
    night_watch.log    夜巡实时日志（含 [FinalFight 接管标记）
    gui.log            MAA GUI 日志：开始行动 N 次 -X理智 / GetFightStage / GUI started
    asst.log(+bak)     核心日志：TaskChain 进度 / Current Sanity
    roguelike\\*.png   肉鸽异常截图（张数 = 卡死健康度判据）
  工作区 maa_night_report_YYYY-MM-DD.md   每日汇总报告（WorkBuddy 生成）

窗口约束：本采集器在任何时间都可运行（纯读文件），但页面会显示是否处于
00:00-06:30 运行窗口。
"""
import datetime
import glob
import json
import os
import re
import subprocess

MAA_DIR = r"D:\Work UP\MAA6173"
DEBUG = os.path.join(MAA_DIR, "debug")
REPORT_DIR = r"C:\Users\Administrator\WorkBuddy\2026-09-09-13-08-42"

NIGHT_REPORT = os.path.join(DEBUG, "night_report.txt")
WATCH_LOG = os.path.join(DEBUG, "night_watch.log")
GUI_LOG = os.path.join(DEBUG, "gui.log")
ASST_LOG = os.path.join(DEBUG, "asst.log")
ASST_BAK = os.path.join(DEBUG, "asst.bak.log")
ROGUE_DIR = os.path.join(DEBUG, "roguelike")

WIN_START, WIN_END = (0, 0), (6, 30)

# 队列顺序（key 用 MAA 日志里的实际 taskchain 名，页面按此渲染进度条）
QUEUE = [
    ("StartUp", "启动游戏 + 自动登录"),
    ("Copilot", "作业站指定关卡（限时 EX）"),
    ("Fight", "刷理智（剿灭 → 兜底清空）"),
    ("Infrast", "基建换班 / 收菜 / 线索"),
    ("Recruit", "自动公招"),
    ("Mall", "信用购物 + 访问好友"),
    ("Award", "领取奖励 / 邮件"),
    ("Roguelike", "肉鸽（界园 · 经验）"),
    ("UserDataUpdate", "数据上传（干员 + 仓库）"),
]


def _tail(path, nbytes):
    """只读文件尾部 n 字节（gui.log 一周 2.4MB、asst.bak.log 单份 67MB，全读没必要）。"""
    try:
        size = os.path.getsize(path)
    except OSError:
        return ""
    try:
        with open(path, "rb") as f:
            if size > nbytes:
                f.seek(size - nbytes)
            data = f.read()
        return data.decode("utf-8", errors="ignore")
    except OSError:
        return ""


def _scan_back(path, regex, max_scan=16 * 1024 * 1024, chunk=2 * 1024 * 1024, limit=40):
    """从文件尾部往前分块扫描，返回按时间正序的匹配列表（最多 limit 条）。

    为什么不用 _tail：`Current Sanity` 这类关键行在 asst.log 里可能位于 75% 处，
    尾部 1MB 根本扫不到 —— 必须按块倒扫，命中足够多即停。
    """
    try:
        size = os.path.getsize(path)
    except OSError:
        return []
    out = []
    pos = size
    scanned = 0
    while pos > 0 and scanned < max_scan and len(out) < limit:
        start = max(0, pos - chunk)
        overlap = 512 if start > 0 else 0        # 多读 512B，避免匹配被块边界切断
        try:
            with open(path, "rb") as f:
                f.seek(start - overlap)
                buf = f.read(pos - (start - overlap))
        except OSError:
            break
        text = buf.decode("utf-8", errors="ignore")
        block, seen = [], set()
        for m in regex.finditer(text):
            key = m.group(0)
            if key in seen:
                continue
            seen.add(key)
            block.append(m.groups())
        out = block + out          # 后块前插：块内正序 + 块间正序 = 整体正序
        pos = start
        scanned += chunk
    return out[-limit:]


def _last_match(path, regex):
    """倒扫取最后一条匹配（最省 IO）。"""
    hits = _scan_back(path, regex, limit=4)
    return hits[-1] if hits else None


def sanity_track():
    """实测理智轨迹 —— 最硬判据（asst 日志的 analyze_sanity_remain）。

    ⚠️ 不要用 gui.log 的「开始行动 N 次, -X理智」求和：MAA 会按轮次重复打印，
    直接累加会重复计数（09-21 实测 15 行累加 750，实际整晚约 -264）。
    """
    # 实机行样：... [2026-09-21 05:55:19.123][INF][Px45704][Tx12592] asst::FightTimesTaskPlugin::analyze_sanity_remain Current Sanity: 13
    pat = re.compile(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\.\d+\][^\n]{0,90}?"
                     r"analyze_sanity_remain Current Sanity: (\d+)")
    rows = []
    for p in (ASST_BAK, ASST_LOG):     # bak 在前（更旧）
        rows += [(t, int(v), os.path.basename(p)) for t, v in _scan_back(p, pat)]
    seen, uniq = set(), []
    for t, v, src in rows:
        k = (t, v)
        if k in seen:
            continue
        seen.add(k)
        uniq.append({"t": t, "v": v, "src": src})
    uniq.sort(key=lambda r: r["t"])

    today = datetime.date.today().strftime("%Y-%m-%d")
    yday = (datetime.date.today() - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
    tonight_rows = [r for r in uniq if r["t"][:10] in (today, yday)]
    first = tonight_rows[0] if tonight_rows else (uniq[0] if uniq else None)
    last = uniq[-1] if uniq else None
    spent = None
    if first and last and first["t"] != last["t"]:
        spent = first["v"] - last["v"]
        if spent < 0:      # 跨了 04:00 恢复理智，差值无意义
            spent = None
    return {"first": first, "last": last, "spent": spent,
            "trace": uniq[-12:], "samples": len(uniq)}


def spend_events():
    """gui.log 的「开始行动」原始事件（近 24h，同一批只保留最后一条，不做求和）。"""
    today = datetime.date.today().strftime("%Y-%m-%d")
    yday = (datetime.date.today() - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
    gui = _tail(GUI_LOG, 4 * 1024 * 1024)
    rows = []
    for m in re.finditer(r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})[^\]]*\]"
                         r"\[[A-Z]+\]\[TaskQueueViewModel\][^<]*<(\d+)> *开始行动 ([\d~]+) 次, -(\d+)理智", gui):
        ts, _q, n, cost = m.groups()
        if ts[:10] not in (today, yday):
            continue
        rows.append({"t": ts, "n": n, "cost": int(cost)})
    # 同一批去重：3 分钟内消耗值相同 → 只留最后一条（MAA 会逐次刷进度行）
    merged = []
    for r in rows:
        if merged:
            prev = merged[-1]
            same_batch = (r["cost"] == prev["cost"]
                          and _seconds_between(prev["t"], r["t"]) <= 180)
            if same_batch:
                merged[-1] = r
                continue
        merged.append(r)
    return merged


def _seconds_between(a, b):
    fmt = "%Y-%m-%d %H:%M:%S"
    try:
        return abs((datetime.datetime.strptime(b, fmt) - datetime.datetime.strptime(a, fmt)).total_seconds())
    except ValueError:
        return 1e9


def _mtime(path):
    try:
        return datetime.datetime.fromtimestamp(os.path.getmtime(path))
    except OSError:
        return None


def _run(cmd, timeout=15):
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout)
        return (r.stdout or b"").decode("gbk", errors="ignore")
    except Exception:
        return ""


# ---------------------------------------------------------------- 进程 / 计划任务
def procs():
    out = _run(["tasklist", "/FO", "CSV"])
    watching = ("MAA.exe", "MuMuPlayer.exe", "cloudflared.exe")
    found = {n: (n in out) for n in watching}
    return found


def scheduled_tasks():
    """读取 MAA-* 计划任务状态（pywin32 不可用时降级为空）。

    注意：服务端是 ThreadingHTTPServer，每个请求跑在新线程里；而 win32com
    要求本线程先 CoInitialize，否则抛「尚未调用 CoInitialize」。
    所以这里必须自己 CoInitialize / CoUninitialize，并且**不能跨请求缓存**
    Dispatch 对象（COM 指针绑定线程）。
    """
    info = {"tasks": [], "shutdown_next": None, "error": None}
    try:
        import pythoncom
        import win32com.client
    except Exception as e:
        info["error"] = "pywin32 不可用: %r" % e
        return info

    def _query():
        sched = win32com.client.Dispatch("Schedule.Service")
        sched.Connect()
        root = sched.GetFolder("\\")
        tasks = root.GetTasks(1)
        rows = []
        for i in range(1, tasks.Count + 1):
            t = tasks.Item(i)
            if not t.Name.startswith("MAA-"):
                continue
            nxt = None
            try:
                nrt = t.NextRunTime
                if nrt is not None:
                    nxt = nrt.strftime("%Y-%m-%d %H:%M")
            except Exception:
                pass
            rows.append({"name": t.Name, "enabled": bool(t.Enabled), "state": int(t.State), "next": nxt})
        return sorted(rows, key=lambda r: r["name"])

    # COM 引用必须全部随 _query 出栈释放，再 CoUninitialize；
    # 否则 Python 在 COM 关闭后析构对象，会刷
    # "Win32 exception occurred releasing IUnknown" 警告。
    pythoncom.CoInitialize()
    try:
        rows = _query()
        info["tasks"] = rows
        for r in rows:
            if r["name"] == "MAA-ShutdownPC-0629":
                info["shutdown_next"] = r["next"]
    except Exception as e:
        info["error"] = repr(e)
    finally:
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass
    return info


# ---------------------------------------------------------------- MAA 数据
def tonight():
    """今晚（最近一次夜巡）概况：节段、状态、重启次数、时间线。"""
    txt = _tail(NIGHT_REPORT, 400 * 1024)
    watch = _tail(WATCH_LOG, 400 * 1024)
    today = datetime.date.today().strftime("%Y-%m-%d")
    yesterday = (datetime.date.today() - datetime.timedelta(days=1)).strftime("%Y-%m-%d")

    header = None
    m = re.search(r"MAA 夜间运行报告 (\d{4}-\d{2}-\d{2})", txt)
    if m:
        header = m.group(1)

    passes = []
    for blk in re.split(r"-{20,}\n", txt):
        mm = re.search(r"\[第 (\d+) 遍\] (\S+) ~ (\S+)", blk)
        if not mm:
            continue
        st = re.search(r"本遍状态: (.+)", blk)
        rs = re.search(r"MAA 重启次数: (\d+)\s+模拟器重启次数: (\d+)", blk)
        ph = re.search(r"最后观察到的任务阶段: (.+)", blk)
        ev = re.findall(r"^\s+(\d{2}:\d{2}:\d{2}) \[(\w+)\] (.+)$", blk, re.M)
        passes.append({
            "n": int(mm.group(1)),
            "start": mm.group(2),
            "end": mm.group(3),
            "status": st.group(1) if st else "",
            "maa_restarts": int(rs.group(1)) if rs else 0,
            "emu_restarts": int(rs.group(2)) if rs else 0,
            "phase": (ph.group(1) if ph else ""),
            "events": [{"t": t, "k": k, "m": msg} for t, k, msg in ev][-40:],
        })

    wl = [ln for ln in watch.splitlines() if ln.strip()]
    watch_tail = wl[-25:] if wl else []
    last_watch_ts = None
    if wl:
        m2 = re.search(r"\[(?:NightWatch|FinalFight) (\d{2}:\d{2}:\d{2})\]", wl[-1])
        if m2:
            last_watch_ts = m2.group(1)
    switched = "[FinalFight" in watch

    return {
        "report_date": header,
        "passes": passes,
        "switched_to_final": switched,
        "watch_tail": watch_tail,
        "watch_last": last_watch_ts,
        "is_today": header in (today, yesterday),
    }


# 子链 → 队列项的归并（UserDataUpdate 会拆成 OperBox / Depot 两条子链）
ALIAS = {"OperBox": "UserDataUpdate", "Depot": "UserDataUpdate"}


def queue_progress():
    """从 asst 日志事件流推断「最近这一遍」走到哪一步。

    必须倒扫（尾部 2MB 常被 Depot/肉鸽识别的密集日志淹没），故用 _scan_back。
    """
    pat = re.compile(r'(TaskChainStart|TaskChainCompleted|TaskChainError) \{"taskchain":"(\w+)"')
    evs = []
    for p in (ASST_BAK, ASST_LOG):     # 旧 → 新
        for kind, name in _scan_back(p, pat, limit=600):
            evs.append((kind, ALIAS.get(name, name)))

    # 定位最后一次 StartUp（= 最近一遍队列的起点）
    start_idx = 0
    for i, (kind, name) in enumerate(evs):
        if kind == "TaskChainStart" and name == "StartUp":
            start_idx = i
    seg = evs[start_idx:]

    last_state = {}
    for kind, name in seg:
        last_state[name] = {"TaskChainStart": "running",
                            "TaskChainCompleted": "done",
                            "TaskChainError": "error"}[kind]

    rows = []
    for key, label in QUEUE:
        rows.append({"key": key, "label": label, "state": last_state.get(key, "pending")})

    current = None
    for key, _label in QUEUE:
        if last_state.get(key) == "running":
            current = key
    if current is None and seg:
        current = seg[-1][1]

    # 只把「最终仍未恢复」的项算作错误（中途掉线但后来补跑成功的不算）
    errors = sorted({k for k, st in last_state.items() if st == "error"})
    timeline = [{"k": k, "t": n} for k, n in seg[-24:]]
    return {"rows": rows, "current": current, "errors": errors, "timeline": timeline}


def sanity():
    """理智：实测轨迹（asst 日志，最硬） + gui.log 消耗事件（原样，不聚合）。"""
    track = sanity_track()
    gui = _tail(GUI_LOG, 4 * 1024 * 1024)
    today = datetime.date.today().strftime("%Y-%m-%d")
    yday = (datetime.date.today() - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
    recent = [ln for ln in gui.splitlines() if (today in ln or yday in ln)]
    recent_text = "\n".join(recent)
    stage = None
    m = re.findall(r'GetFightStage: from \[([^\]]*)\], selected (\S+)', recent_text)
    if m:
        stage = {"from": m[-1][0], "selected": m[-1][1]}
    null_count = len(re.findall(r"selected null", recent_text))
    starts_gui = len(re.findall(r"MaaAssistantArknights GUI started", recent_text))
    cur = track["last"]["v"] if track["last"] else None
    st = spend_events()
    return {"current": cur, "track": track, "spend_events": st,
            "last_stage": stage, "stage_null": null_count, "gui_started": starts_gui}


def roguelike_health():
    """肉鸽异常截图：最近 12 小时内新增的 Page2_Error 张数（≫4 即为卡死循环）。"""
    now = datetime.datetime.now()
    recent = []
    if os.path.isdir(ROGUE_DIR):
        for p in glob.glob(os.path.join(ROGUE_DIR, "*.png")):
            mt = _mtime(p)
            if mt and (now - mt).total_seconds() < 12 * 3600:
                recent.append((mt, os.path.basename(p)))
    recent.sort()
    level = "ok"
    if len(recent) >= 8:
        level = "bad"
    elif len(recent) >= 4:
        level = "warn"
    return {"count": len(recent), "level": level,
            "latest": [n for _, n in recent[-6:]]}


def _digest(md):
    """从报告正文里抠一句摘要（各期格式不同：`> 结论：…` / `**结论：…**` / `一句话：…`）。"""
    lines = md.splitlines()
    for ln in lines[:60]:
        s = ln.strip()
        if not s or s.startswith(("#", "|", "---")):
            continue
        for kw in ("结论", "一句话"):
            m = re.search(kw + r"[：:]\s*(.+)", s)
            if m:
                t = m.group(1).strip().strip(">*_ ")
                t = re.sub(r"\*\*", "", t)
                if len(t) > 8:
                    return t[:220]
    for ln in lines[:60]:
        s = ln.strip().lstrip("> ").strip().strip("*_ ")
        if len(s) > 40 and not s.startswith(("#", "|", "---")):
            return s[:220]
    return ""


def reports_index():
    """最近 7 份每日汇总报告（WorkBuddy 生成的 md）。"""
    out = []
    for p in sorted(glob.glob(os.path.join(REPORT_DIR, "maa_night_report_*.md")), reverse=True)[:7]:
        m = re.search(r"(\d{4}-\d{2}-\d{2})", os.path.basename(p))
        date = m.group(1) if m else ""
        try:
            with open(p, "r", encoding="utf-8", errors="ignore") as f:
                head = f.read(6000)
            conclusion = _digest(head)
        except OSError:
            conclusion = ""
        out.append({"date": date, "file": os.path.basename(p),
                    "conclusion": conclusion, "mtime": str(_mtime(p) or "")})
    return out


def read_report(date):
    """按日期读单份报告（白名单：只认 YYYY-MM-DD，绝不拼任意路径）。"""
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date or ""):
        return None
    p = os.path.join(REPORT_DIR, "maa_night_report_%s.md" % date)
    if not os.path.isfile(p):
        return None
    with open(p, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()


def collect():
    now = datetime.datetime.now()
    in_window = WIN_START <= (now.hour, now.minute) <= WIN_END
    p = procs()
    running = p.get("MAA.exe") or p.get("MuMuPlayer.exe")
    return {
        "now": now.strftime("%Y-%m-%d %H:%M:%S"),
        "in_window": in_window,
        "running": bool(running),
        "procs": p,
        "tonight": tonight(),
        "queue": queue_progress(),
        "sanity": sanity(),
        "roguelike": roguelike_health(),
        "reports": reports_index(),
        "tasks": scheduled_tasks(),
    }


if __name__ == "__main__":
    print(json.dumps(collect(), ensure_ascii=False, indent=2))
