# -*- coding: utf-8 -*-
"""service_manager.py — WK 平台、隧道与 MAA 看板的 Windows 服务生命周期管理。

用法（需管理员权限）::

    python tools/service_manager.py status      # 查看服务的状态与健康
    python tools/service_manager.py install     # 创建并启动服务（开机自启）
    python tools/service_manager.py uninstall   # 停止并删除服务
    python tools/service_manager.py start|stop|restart [platform|cloudflared|maa|all]
    python tools/service_manager.py verify      # 端到端验证（服务+端口+HTTP+隧道）

托管对象
--------
1. ``WKAutoTaskPlatform`` — 平台本体
   binPath: ``<venv>\\python.exe -u D:\\web\\service_platform.py``
   由 service_platform.py 自行完成 SCM 握手；进程存活由 SCM 失败恢复策略托管。

2. ``WKCloudflared`` — Cloudflare 隧道（wk_config.yml）
   binPath: ``D:\\web\\cf\\cloudflared.exe tunnel --config <cfg> --no-autoupdate
   --metrics 127.0.0.1:20241 run wk-platform``
   cloudflared 自带 Windows 服务支持（golang.windows/svc），可直接被 SCM 托管。

3. ``WKMaaStatus`` — MAA 夜间进度看板（只读状态页）
   binPath: ``<venv>\\python.exe -u D:\\web\\maa_status\\maa_status_service.py``
   由 maa_status_service.py 自行完成 SCM 握手，业务逻辑仍在 server.py / collector.py。
   **注意**：该看板的单实例锁名是机器级的 ``Global\\MAAStatusBoard.8791``，
   与旧的 ``MAA-StatusBoard`` 计划任务实例互斥 —— 迁移到服务托管前必须先停用
   并删除该计划任务，否则服务拿不到锁、会配合失败恢复形成重启循环。

设计取舍：以专用服务替代原先「计划任务 → health_manager 看护 → 派生平台」的三层
自研守护模型，消除锁文件、启动器父子进程对与僵尸进程问题（见 ONLINE_MIGRATION_AUDIT
§5.4 A1/A2 与 PROJECT_FULL_STATE §15）。MAA 看板同样从「计划任务每 30 分钟自愈」
迁移到 SCM 托管，判重手段保留（命名互斥体是 OS 级、崩溃自动释放，无可替代价值）。
"""
import os
import sys
import time
import json
import shutil
import argparse
import subprocess
import urllib.request
import urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.dirname(HERE)

PYEXE = os.path.join(
    r"C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts",
    "python.exe")
PYEXE = PYEXE if os.path.exists(PYEXE) else sys.executable

SERVICE_SCRIPT = os.path.join(APP_DIR, "service_platform.py")
CLOUDFLARED = os.path.join(APP_DIR, "cf", "cloudflared.exe")
CF_CONFIG = os.path.join(os.path.expanduser("~"), ".cloudflared", "wk_config.yml")
CF_METRICS = "127.0.0.1:20241"
CF_TUNNEL_NAME = "wk-platform"

# MAA 夜间进度看板（D:\web\maa_status）
MAA_APP_DIR = r"D:\web\maa_status"
MAA_SERVICE_SCRIPT = os.path.join(MAA_APP_DIR, "maa_status_service.py")

SVC_PLATFORM = "WKAutoTaskPlatform"
SVC_TUNNEL = "WKCloudflared"
SVC_MAA = "WKMaaStatus"

HEALTH_URL = "http://127.0.0.1:8766/health"
PUBLIC_URL = "https://order.jiangjiangze.icu/health"

MAA_HEALTH_URL = "http://127.0.0.1:8791/health"
MAA_PUBLIC_URL = "https://maa.jiangjiangze.icu/health"

# 失败恢复：重启间隔 5s / 30s / 60s，24h 后重置计数
FAILURE_ACTIONS = "restart/5000/restart/30000/restart/60000"


# ---------------------------------------------------------------------------
# 基础封装
# ---------------------------------------------------------------------------
def run(args, check=False, timeout=60):
    """执行命令，返回 (returncode, stdout+stderr)。统一 GBK 解码以适配中文 Windows。"""
    try:
        p = subprocess.run(args, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return 124, "(timeout)"
    out = (p.stdout or b"") + (p.stderr or b"")
    text = out.decode("gbk", errors="replace") if out else ""
    if check and p.returncode != 0:
        raise RuntimeError(f"{args[0]} {args[1:2]} failed rc={p.returncode}: {text.strip()}")
    return p.returncode, text.strip()


def require_admin():
    """通过写 HKLM 探测管理员权限（比 net session 判据更稳）。"""
    rc, _ = run(["reg", "add", r"HKLM\SOFTWARE\WKServiceManagerProbe", "/f"])
    if rc != 0:
        print("✗ 需要管理员权限。请以管理员身份运行本脚本。")
        sys.exit(1)
    run(["reg", "delete", r"HKLM\SOFTWARE\WKServiceManagerProbe", "/f"])


def sc(*args, timeout=60):
    return run(["sc.exe", *args], timeout=timeout)


def service_state(name):
    """返回 (存在?, 状态字符串, 启动类型字符串)。"""
    rc, out = sc("query", name)
    if rc != 0:
        return False, "not-installed", "-"
    state = "unknown"
    for key, label in (("RUNNING", "running"), ("STOP_PENDING", "stop-pending"),
                       ("START_PENDING", "start-pending"), ("STOPPED", "stopped"),
                       ("PAUSED", "paused")):
        if key in out:
            state = label
            break
    rc2, out2 = sc("qc", name)
    start = "-"
    for line in out2.splitlines():
        low = line.lower()
        if "start_type" in low or "启动类型" in line:
            for k, v in (("delayed", "delayed-auto"), ("auto", "auto"),
                         ("demand", "manual"), ("disabled", "disabled")):
                if k in low:
                    start = v
                    break
    return True, state, start


def binpath_of(name):
    rc, out = sc("qc", name)
    if rc != 0:
        return ""
    for line in out.splitlines():
        low = line.lower()
        if "binary_path_name" in low or "二进制" in line:
            return line.split(":", 1)[-1].strip()
    return ""


# ---------------------------------------------------------------------------
# 安装 / 卸载
# ---------------------------------------------------------------------------
def _open_service(hscm, name):
    """打开已存在的服务句柄，不存在返回 None。"""
    import win32service as ws
    try:
        return ws.OpenService(hscm, name, ws.SERVICE_ALL_ACCESS)
    except Exception:
        return None


def _create(name, display, desc, binpath, depend=None):
    """创建/更新服务：延迟自动启动 + 失败自动恢复 + 描述。

    为什么用 pywin32 而不是 sc.exe：``sc create binPath= "..."`` 需要把带引号的
    可执行路径作为单个 argv 元素传给子进程，subprocess 的 list2cmdline 会再次
    加引号并转义内部引号，最终 sc.exe 存下的是 `""C:\\..\\python.exe" -u .."`
    这种双重引号，CreateProcess 解析后第一个 token 为空 → 服务 1053/无法启动。
    直接调 Win32 API 传字符串，不存在任何 shell 转义环节。
    """
    import win32service as ws
    hscm = ws.OpenSCManager(None, None, ws.SC_MANAGER_ALL_ACCESS)
    if not hscm:
        print(f"  ✗ 无法打开服务控制管理器")
        return False
    hs = None
    try:
        dep = [depend] if depend else None
        hs = _open_service(hscm, name)
        if hs is None:
            hs = ws.CreateService(
                hscm, name, display,
                ws.SERVICE_ALL_ACCESS,
                ws.SERVICE_WIN32_OWN_PROCESS,
                ws.SERVICE_AUTO_START,
                ws.SERVICE_ERROR_NORMAL,
                binpath,
                None,        # loadOrderGroup
                0,           # tagId
                dep,         # dependencies
                None,        # serviceStartName -> LocalSystem
                None)        # password
            print(f"  ✓ 已创建 {name}")
        else:
            ws.ChangeServiceConfig(
                hs,
                ws.SERVICE_NO_CHANGE,          # serviceType
                ws.SERVICE_AUTO_START,         # startType
                ws.SERVICE_NO_CHANGE,          # errorControl
                binpath,                       # binaryPathName
                None, 0, dep, None, None,      # group/tag/dep/user/pwd
                display)                       # displayName
            print(f"  ✓ 已更新 {name}（binPath / 启动类型已校正）")

        # 延迟自动启动（开机自启但避开登录初期资源争抢）
        ws.ChangeServiceConfig2(hs, ws.SERVICE_CONFIG_DELAYED_AUTO_START_INFO, True)
        # 描述
        ws.ChangeServiceConfig2(hs, ws.SERVICE_CONFIG_DESCRIPTION, desc)
        # 失败恢复：5s / 30s / 60s 重启，24h 重置计数
        ws.ChangeServiceConfig2(hs, ws.SERVICE_CONFIG_FAILURE_ACTIONS, {
            "ResetPeriod": 86400,
            "RebootMsg": "",
            "Command": "",
            "Actions": [(ws.SC_ACTION_RESTART, 5000),
                        (ws.SC_ACTION_RESTART, 30000),
                        (ws.SC_ACTION_RESTART, 60000)],
        })
        # 关键：让恢复动作对「以 0 码退出」同样生效。
        # 默认 FALSE 时，只有崩溃（非零退出）才会被拉活；而 Python 服务若在
        # 主循环里被未捕获异常之外的方式结束（例如 sys.exit(0)），会静默停摆。
        ws.ChangeServiceConfig2(hs,
                                ws.SERVICE_CONFIG_FAILURE_ACTIONS_FLAG, True)
        return True
    except Exception as e:
        print(f"  ✗ 配置 {name} 失败: {type(e).__name__}: {e}")
        return False
    finally:
        if hs:
            ws.CloseServiceHandle(hs)
        ws.CloseServiceHandle(hscm)


TARGETS = {
    "platform": [SVC_PLATFORM],
    "cloudflared": [SVC_TUNNEL],
    "maa": [SVC_MAA],
    "all": [SVC_PLATFORM, SVC_TUNNEL, SVC_MAA],
}


def install(target="all"):
    """创建/更新并启动服务。target 限定范围，避免「只想装 MAA 却重配平台」。"""
    require_admin()
    want = TARGETS[target]
    print(f"== 安装服务（target={target}）==")

    if not os.path.exists(PYEXE):
        print(f"✗ 找不到 Python 解释器: {PYEXE}")
        return 1

    oks = []

    # 1) 平台本体
    if SVC_PLATFORM in want:
        if not os.path.exists(SERVICE_SCRIPT):
            print(f"✗ 缺少 {SERVICE_SCRIPT}")
            return 1
        plat_bin = f'"{PYEXE}" -u "{SERVICE_SCRIPT}"'
        oks.append(_create(SVC_PLATFORM, "WK 自动任务平台 (AutoTask Platform)",
                           "自动任务平台：Flask + Waitress，监听 127.0.0.1:8766；"
                           "按单派生隔离子进程调用任务引擎。开机自启，由 SCM 托管。",
                           plat_bin))

    # 2) Cloudflare 隧道
    if SVC_TUNNEL in want:
        if os.path.exists(CLOUDFLARED) and os.path.exists(CF_CONFIG):
            cf_bin = (f'"{CLOUDFLARED}" tunnel --config "{CF_CONFIG}" '
                      f'--no-autoupdate --metrics {CF_METRICS} run {CF_TUNNEL_NAME}')
            oks.append(_create(SVC_TUNNEL, "WK Cloudflare Tunnel",
                               f"Cloudflare Tunnel：order.jiangjiangze.icu → "
                               f"http://127.0.0.1:8766，maa.jiangjiangze.icu → "
                               f"http://127.0.0.1:8791。配置 {CF_CONFIG}。"
                               f"开机自启，由 SCM 托管。",
                               cf_bin))
        else:
            print("  ! 跳过隧道服务：未找到 cloudflared.exe 或 wk_config.yml")

    # 3) MAA 夜间进度看板
    if SVC_MAA in want:
        if os.path.exists(MAA_SERVICE_SCRIPT):
            maa_bin = f'"{PYEXE}" -u "{MAA_SERVICE_SCRIPT}"'
            oks.append(_create(SVC_MAA, "WK MAA 夜间进度看板 (MAA Status Board)",
                               "MAA 夜间进度看板：只读状态页，仅监听 127.0.0.1:8791；"
                               "公网入口由 WKCloudflared 隧道转发。只读 MAA 日志，"
                               "任何情况下都不启动/触碰模拟器与 MAA。"
                               "开机自启，由 SCM 托管。",
                               maa_bin))
        else:
            print(f"  ! 跳过 MAA 看板服务：未找到 {MAA_SERVICE_SCRIPT}")

    print("\n== 启动服务 ==")
    for name in want:
        exists, _, _ = service_state(name)
        if not exists:
            continue
        sc("start", name)
        time.sleep(1)
        _, st, _ = service_state(name)
        print(f"  {name}: {st}")

    print("\n== 安装结果 ==")
    return 0 if all(oks) else 1


def uninstall(target="all"):
    require_admin()
    print(f"== 停止并删除服务（target={target}）==")
    for name in TARGETS[target]:
        exists, state, _ = service_state(name)
        if not exists:
            print(f"  · {name} 未安装，跳过")
            continue
        if state not in ("stopped",):
            sc("stop", name, timeout=90)
            for _ in range(30):
                time.sleep(1)
                _, st, _ = service_state(name)
                if st == "stopped":
                    break
        rc, out = sc("delete", name)
        print(f"  {'✓' if rc == 0 else '✗'} 删除 {name} {'' if rc == 0 else out}")
    return 0


# ---------------------------------------------------------------------------
# 状态 / 验证
# ---------------------------------------------------------------------------
class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """禁止自动跟随重定向。

    urllib 默认会跟随 302 —— 而 order.* 域名前面有 Cloudflare Access，未认证
    请求会被 302 到 cloudflareaccess.com 登录页，登录页本身返回 200。若跟随
    重定向，就会把「被 Access 拦住」错判成「公网 200 正常」，是个危险的假阳性。
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def _http(url, timeout=8, maxbytes=300, ua=None):
    """GET 请求，返回 (状态码, 响应体前 maxbytes 字节)，**不跟随重定向**。

    4xx/5xx 会被 urllib 抛成 HTTPError（其本身携带响应），必须单独接住，
    否则 403/404 这类「业务上有效的状态码」会退化成一个没有 code 的异常。
    """
    req = urllib.request.Request(url)
    req.add_header("User-Agent", ua or "Mozilla/5.0 (WKServiceManager)")
    try:
        with _OPENER.open(req, timeout=timeout) as r:
            return r.status, r.read(maxbytes).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        try:
            return e.code, e.read(maxbytes).decode("utf-8", "replace")
        except Exception:
            return e.code, ""
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def _is_access_redirect(code, body):
    """判断响应是否为 Cloudflare Access 的登录跳转。"""
    return code in (301, 302, 303, 307) and "403 Forbidden" not in body


def status():
    print("=" * 68)
    for name in (SVC_PLATFORM, SVC_TUNNEL, SVC_MAA):
        exists, st, start = service_state(name)
        print(f"{name}")
        if not exists:
            print("  状态: 未安装")
            continue
        print(f"  状态: {st}   启动类型: {start}")
        bp = binpath_of(name)
        if bp:
            print(f"  映像: {bp}")
    print("-" * 68)
    rc, out = run(["netstat", "-ano"])
    for line in out.splitlines():
        if "LISTENING" in line and (":8766" in line or ":8791" in line
                                    or f":{CF_METRICS.split(':')[1]}" in line):
            print("  端口:", line.strip())
    print("-" * 68)
    code, body = _http(HEALTH_URL)
    print(f"  本地健康检查 {HEALTH_URL} → {code} {body[:160]}")
    code_m, body_m = _http(MAA_HEALTH_URL)
    print(f"  本地健康检查 {MAA_HEALTH_URL} → {code_m} {body_m[:160]}")
    print("=" * 68)
    return 0


def verify():
    """端到端验证：服务状态 → 端口 → 本地 HTTP → 公网隧道。"""
    print("=" * 68)
    print("端到端验证")
    print("=" * 68)
    fails = []

    for name in (SVC_PLATFORM, SVC_TUNNEL, SVC_MAA):
        exists, st, start = service_state(name)
        ok = exists and st == "running" and start in ("delayed-auto", "auto")
        print(f"[{'PASS' if ok else 'FAIL'}] 服务 {name}: 存在={exists} 状态={st} 启动类型={start}")
        if not ok:
            fails.append(f"{name} 未处于 running + 自启")

    rc, out = run(["netstat", "-ano"])
    port8766 = any("LISTENING" in l and ":8766" in l for l in out.splitlines())
    print(f"[{'PASS' if port8766 else 'FAIL'}] 监听 127.0.0.1:8766")
    if not port8766:
        fails.append("8766 未监听")

    port8791 = any("LISTENING" in l and ":8791" in l for l in out.splitlines())
    print(f"[{'PASS' if port8791 else 'FAIL'}] 监听 127.0.0.1:8791（MAA 看板）")
    if not port8791:
        fails.append("8791 未监听")

    code, body = _http(HEALTH_URL)
    ok = code == 200 and '"status":"ok"' in body.replace(" ", "")
    print(f"[{'PASS' if ok else 'FAIL'}] 本地 /health → {code} {body[:140]}")
    if not ok:
        fails.append("本地 /health 非 200/ok")

    code_m, body_m = _http(MAA_HEALTH_URL)
    ok_m = code_m == 200 and '"ok"' in body_m.replace(" ", "")
    print(f"[{'PASS' if ok_m else 'FAIL'}] 看板 /health → {code_m} {body_m[:140]}")
    if not ok_m:
        fails.append("看板 /health 非 200/ok")

    # --- 隧道连通性：以 cloudflared 自带 /ready 为权威判据 -------------------
    # 注意：order.jiangjiangze.icu 前面挂了 Cloudflare Access，未认证请求会在
    # Cloudflare 边缘被 302/403 拦掉，**请求根本到不了隧道**。因此不能用公网
    # /health 是否 200 来判断隧道是否健康，否则会误判。cloudflared 的 metrics
    # 端点 /ready 只有在与 Cloudflare 边缘建连成功后才返回 200，才是真判据。
    ready_url = f"http://{CF_METRICS}/ready"
    code_r, _ = _http(ready_url, timeout=10)
    ok_r = code_r == 200
    print(f"[{'PASS' if ok_r else 'FAIL'}] 隧道边缘连接 {ready_url} → {code_r}")
    if not ok_r:
        fails.append("cloudflared /ready 非 200（隧道未连上 Cloudflare 边缘）")

    # HA 边缘连接数（正常为 2）
    _, metrics = _http(f"http://{CF_METRICS}/metrics", timeout=10, maxbytes=200000)
    if metrics:
        for line in str(metrics).splitlines():
            if line.startswith("cloudflared_tunnel_ha_connections"):
                print(f"[INFO] 隧道 HA 边缘连接数 = {line.split()[-1]}（正常为 2）")
                break

    # --- 公网可达性 ---------------------------------------------------------
    # 语义约定（已关闭重定向跟随，看到的就是边缘第一跳的真实响应）：
    #   302/307 → Cloudflare Access 把未认证访客挡在边缘 = 安全加固生效，PASS
    #   200 且是 /health 的 JSON → 该域名**未受保护**，对公网裸奔 = 风险，FAIL
    #   200 且是 HTML  → 拿到的是登录页之类，视为 Access 生效
    #   403            → Cloudflare 边缘/WAF 拒绝（常见于非浏览器 UA 被拦）
    code2, body2 = _http(PUBLIC_URL, timeout=20)
    bare = code2 == 200 and '"status":"ok"' in (body2 or "").replace(" ", "")
    if bare:
        print(f"[FAIL] 公网 {PUBLIC_URL} → 200 且返回真实 /health JSON —— "
              "该域名对公网裸奔，未启用访问控制！")
        fails.append("公网 /health 可直接访问（无访问控制）")
    elif code2 in (301, 302, 303, 307, 308):
        print(f"[PASS] 公网 {PUBLIC_URL} → {code2}：Cloudflare Access 登录拦截生效")
        print("         （未认证访客在 Cloudflare 边缘即被拦下，请求不会到达本机）")
    elif code2 == 403:
        print(f"[PASS] 公网 {PUBLIC_URL} → 403：Cloudflare 边缘拒绝未认证客户端")
    elif code2 == 200:
        print(f"[PASS] 公网 {PUBLIC_URL} → 200（响应非平台 JSON，应为 Access 登录页）")
    else:
        print(f"[INFO] 公网 {PUBLIC_URL} → {code2} {str(body2)[:100]}")
        print("         （本机 Clash/TUN 代理可能干扰出网探测；以 /ready 为准）")

    # --- MAA 看板公网端到端（该域名走看板自身的表单认证，/health 免认证） ------
    code4, body4 = _http(MAA_PUBLIC_URL, timeout=20)
    ok4 = code4 == 200 and '"ok"' in (body4 or "").replace(" ", "")
    print(f"[{'PASS' if ok4 else 'FAIL'}] 公网 {MAA_PUBLIC_URL} → {code4} "
          f"{str(body4)[:100]}")
    if not ok4:
        fails.append(f"看板公网 /health 非 200/ok（{code4}）——隧道或看板异常")

    code3, body3 = _http("https://maa.jiangjiangze.icu/", timeout=20)
    if code3 == 200:
        print("[PASS] 公网 https://maa.jiangjiangze.icu/ → 200"
              "（未登录时返回看板登录页，属预期）")
    else:
        print(f"[INFO] 公网 https://maa.jiangjiangze.icu/ → {code3}")

    print("-" * 68)
    if fails:
        print("结论: FAIL")
        for f in fails:
            print("  -", f)
        return 1
    print("结论: PASS（核心项全部通过）")
    return 0


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="WK 平台 / 隧道 / MAA 看板服务管理")
    ap.add_argument("action",
                    choices=["status", "install", "uninstall", "start", "stop",
                             "restart", "verify", "console"])
    ap.add_argument("target", nargs="?", default="all",
                    choices=["all", "platform", "cloudflared", "maa"])
    a = ap.parse_args()

    if a.action == "status":
        return status()
    if a.action == "verify":
        return verify()
    if a.action == "install":
        return install(a.target)
    if a.action == "uninstall":
        return uninstall(a.target)
    if a.action == "console":
        script = MAA_SERVICE_SCRIPT if a.target == "maa" else SERVICE_SCRIPT
        os.execv(PYEXE, [PYEXE, "-u", script, "console"])
        return 0   # pragma: no cover

    require_admin()
    names = TARGETS[a.target]
    for n in names:
        sc(a.action, n)
        time.sleep(1)
        _, st, _ = service_state(n)
        print(f"  {n}: {st}")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
