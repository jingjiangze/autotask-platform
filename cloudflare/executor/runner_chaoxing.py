"""stage-cloud-12（真实接入）— 超星 ChaoxingRunner。

原则（§61/§62）：绝不重写引擎，复用 D:/web/fuckCourse/chaoxing/main.py，
环境变量契约与 order_platform.build_order_env/run_chaoxing 完全一致：
    WK_ACCOUNT / WK_PASSWORD / FUCKCOURSE_CONFIG / FUCKCOURSE_COOKIES /
    FUCKCOURSE_LOG_DIR / WK_UA / WK_PLATFORM / WK_CHUA / WK_LANG
凭据来源：中央凭据解封（ctx.credentials()，租约门控），仅存内存/子进程 env，
任务结束随目录清理。
"""

from __future__ import annotations

import os
import random
import shutil
import subprocess
import time
from typing import Any

# 与 order_platform.py 对齐的本机路径
APP_DIR = r"D:\web"
FUCK_DIR = os.path.join(APP_DIR, "fuckCourse")
PYEXE = r"C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\python.exe"

UA_POOL = [
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36",
     '"Windows"', '"Chromium";v="118", "Google Chrome";v="118", "Not=A?Brand";v="99"', "zh-CN,zh;q=0.9"),
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
     '"Windows"', '"Not_A Brand";v="8", "Chromium";v="120", "Microsoft Edge";v="120"', "zh-CN,zh;q=0.9,en;q=0.8"),
    ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
     '"macOS"', '"Google Chrome";v="119", "Chromium";v="119", "Not?A_Brand";v="24"', "zh-CN,zh;q=0.9"),
]

TASK_ROOT = os.path.join(APP_DIR, "orders", "cloud_executor")  # 隔离于本地 orders/<id>


def build_chaoxing_env(task_id: str, account: str, password: str) -> tuple[dict[str, str], str, str]:
    """单任务隔离环境（work/tmp/home/logs）+ 引擎 env；返回 (env, work_dir, log_path)。"""
    d = os.path.join(TASK_ROOT, task_id)
    work, tmp, home, logs = (os.path.join(d, x) for x in ("work", "tmp", "home", "logs"))
    for p in (work, tmp, home, logs):
        os.makedirs(p, exist_ok=True)

    ua, plat, chua, lang = random.choice(UA_POOL)
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
    env["PYTHONIOENCODING"] = "utf-8"
    env["TZ"] = "Asia/Shanghai"
    env["WK_UA"] = ua
    env["WK_PLATFORM"] = plat
    env["WK_CHUA"] = chua
    env["WK_LANG"] = lang
    env["WK_ACCOUNT"] = account
    env["WK_PASSWORD"] = password
    return env, work, os.path.join(d, "log.txt")


def cleanup_task_dir(task_id: str) -> None:
    d = os.path.join(TASK_ROOT, task_id)
    if os.path.isdir(d):
        shutil.rmtree(d, ignore_errors=True)


def run_chaoxing(payload: dict[str, Any], ctx) -> dict[str, Any]:
    """真实引擎执行：登录 → 课程处理 → 退出码。凭据经中央解封（§36）。"""
    courses = str(payload.get("courses") or "264628209")
    speed = float(payload.get("speed") or 2.0)
    jobs = max(1, min(4, int(payload.get("jobs") or 2)))
    timeout = int(payload.get("timeout_seconds") or 1800)

    # §36：凭据租约门控解封（runtime-only）
    creds = ctx.credentials()
    account = next((c["plaintext"] for c in creds if c["credential_type"] == "account"), "")
    password = next((c["plaintext"] for c in creds if c["credential_type"] == "account_password"), "")
    if not account or not password:
        raise RuntimeError("credentials missing (need account + account_password)")

    env, work, log_path = build_chaoxing_env(ctx.task_id, account, password)
    cmd = [PYEXE, "-u", os.path.join(FUCK_DIR, "chaoxing", "main.py"),
           "-l", courses, "-s", str(speed), "-j", str(jobs), "--auto-sign"]

    started = time.time()
    with open(log_path, "ab") as logf:
        proc = subprocess.Popen(cmd, env=env, cwd=work, stdout=logf, stderr=subprocess.STDOUT)
        try:
            rc = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=15)
            rc = -9
    elapsed = round(time.time() - started, 1)

    result: dict[str, Any] = {"exit_code": rc, "courses": courses, "elapsed_s": elapsed}
    # 日志上传（读取后常驻内存直到任务结束由运行时清理目录）
    if os.path.isfile(log_path):
        with open(log_path, "rb") as f:
            ctx.upload(f.read(), artifact_type="stdout")
    return result
