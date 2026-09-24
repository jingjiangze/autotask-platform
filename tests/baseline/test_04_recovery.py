# -*- coding: utf-8 -*-
"""RECOVERY 基线：真实重启恢复路径（recover_stale_orders 于 import 时执行）

做法：向隔离库预置三种中断态订单，然后用独立子进程再次 import 真实
order_platform —— 等价于一次真实"平台重启"，import 即触发恢复逻辑，
全程无 Mock。执行前确保 worker 暂停，避免重新入队的订单被认领。
"""
import datetime
import subprocess
import sys
import time
import uuid


def _seed(platform, status, attempt, qr_state=""):
    oid = uuid.uuid4().hex
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    platform.sql(
        "INSERT INTO orders(id,user_id,product,platform,account,password,courses,"
        "status,qr_state,note,created_at,pid,attempt,worker_running) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (oid, 1, "cx_video", "chaoxing", "baseline-rec-acc", "baseline-rec-pw",
         "264628209", status, qr_state, "", now, 0, attempt, 0))
    return oid


def test_restart_recovery_paths(platform):
    platform.set_setting("paused", "1")

    oid_qr = _seed(platform, "waiting_qr", 0, qr_state="waiting")
    oid_retry = _seed(platform, "running", 0)      # attempt < MAX_RETRY → 重新排队
    oid_fail = _seed(platform, "running", 1)       # attempt == MAX_RETRY → failed

    driver = sys.modules["conftest"].ROOT / "tests" / "baseline" / "_recover_driver.py"
    import os
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    proc = subprocess.run(
        [sys.executable, str(driver), str(platform.root)],
        cwd=str(platform.root), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=120, env=env)
    assert proc.returncode == 0, f"恢复驱动失败: {proc.stdout}\n{proc.stderr}"

    rows = {r["id"]: r for r in platform.sql(
        "SELECT * FROM orders WHERE id IN (?,?,?)", (oid_qr, oid_retry, oid_fail))}

    # 扫码会话在内存中，重启后必失效（真实语义）
    r = rows[oid_qr]
    assert r["status"] == "canceled" and r["qr_state"] == "expired"
    assert "重新下单" in r["note"]

    # 未达重试上限 → 回队列
    r = rows[oid_retry]
    assert r["status"] == "pending" and r["worker_running"] == 0
    assert "重新排队" in r["note"]

    # 达到 MAX_RETRY=1 → 终态 failed
    r = rows[oid_fail]
    assert r["status"] == "failed" and r["finished_at"], "达重试上限应收敛为 failed"
