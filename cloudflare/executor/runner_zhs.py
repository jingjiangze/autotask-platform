"""stage-cloud-29 — 知到（超星尔雅/智慧树）真实 Runner。

引擎契约与 order_platform.run_zhs 一致：
    fuckCourse/run_zhs.py full <course_id...>   （courses 空格分隔）
课程查询：tools_query_courses.py zhs（WK_ACCOUNT/WK_PASSWORD env，末行 JSON）。
凭据经中央租约解封（§36），仅存内存/子进程 env。
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from typing import Any

from runner_chaoxing import PYEXE, build_chaoxing_env

FUCK_DIR = r"D:\web\fuckCourse"
QUERY_TOOL = r"D:\web\tools_query_courses.py"


def _creds(ctx) -> tuple[str, str]:
    creds = ctx.credentials()
    account = next((c["plaintext"] for c in creds if c["credential_type"] == "account"), "")
    password = next((c["plaintext"] for c in creds if c["credential_type"] == "account_password"), "")
    if not account or not password:
        raise RuntimeError("credentials missing (need account + account_password)")
    return account, password


def run_zhs(payload: dict[str, Any], ctx) -> dict[str, Any]:
    """知到刷课：run_zhs.py full <ids>。凭据租约解封。"""
    courses = str(payload.get("courses") or "")
    timeout = int(payload.get("timeout_seconds") or 1800)
    account, password = _creds(ctx)

    env, work, log_path = build_chaoxing_env(ctx.task_id, account, password)
    ids = courses.replace(",", " ").split()
    cmd = [PYEXE, "-u", os.path.join(FUCK_DIR, "run_zhs.py"), "full"] + ids

    started = time.time()
    with open(log_path, "ab") as logf:
        proc = subprocess.Popen(cmd, env=env, cwd=work, stdout=logf, stderr=subprocess.STDOUT)
        ctx.proc = proc
        try:
            rc = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=15)
            rc = -9
        finally:
            ctx.proc = None
    result: dict[str, Any] = {"exit_code": rc, "courses": courses, "elapsed_s": round(time.time() - started, 1)}
    if os.path.isfile(log_path):
        with open(log_path, "rb") as f:
            ctx.upload(f.read(), artifact_type="stdout")
    return result


def query_courses(payload: dict[str, Any], ctx) -> dict[str, Any]:
    """知到查课表：tools_query_courses.py zhs → 末行 JSON。"""
    platform = str(payload.get("platform") or "zhs")
    timeout = int(payload.get("timeout_seconds") or 180)
    account, password = _creds(ctx)

    env, work, log_path = build_chaoxing_env(ctx.task_id, account, password)
    cookie_path = os.path.join(work, f"query_{ctx.task_id[:8]}.json")
    cmd = [PYEXE, QUERY_TOOL, "zhs", "", "", cookie_path]

    started = time.time()
    proc = subprocess.Popen(cmd, env=env, cwd=work, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
    ctx.proc = proc
    try:
        stdout, _ = proc.communicate(timeout=timeout)
        rc = proc.returncode
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate(timeout=15)
        rc, stdout = -9, ""
    finally:
        ctx.proc = None
    elapsed = round(time.time() - started, 1)

    data: dict[str, Any] = {}
    for line in reversed((stdout or "").strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                data = json.loads(line)
                break
            except json.JSONDecodeError:
                continue
    result: dict[str, Any] = {
        "ok": bool(data.get("ok")),
        "platform": platform,
        "courses": data.get("courses", []) if data.get("ok") else [],
        "error": data.get("error") if not data.get("ok") else (None if data.get("ok") else f"query failed (exit={rc})"),
        "exit_code": rc,
        "elapsed_s": elapsed,
    }
    if os.path.isfile(log_path):
        with open(log_path, "rb") as f:
            ctx.upload(f.read(), artifact_type="stdout")
    return result
