# -*- coding: utf-8 -*-
"""EXECUTION 基线：真实 worker 认领 → 真实子进程 spawn → 结果分类落库

真实性声明（stage-cloud-02 规则）：
  - claim_order 原子抢占 / build_order_env 隔离环境 / _spawn 真实进程启动 /
    RollingLog 落盘 / rc 分类与状态收敛 —— 全部为真实代码真实路径；
  - 引擎脚本在隔离 APP_DIR 中不存在 → 子进程以非零码退出（真实引擎级失败，
    非 Mock、非 sleep 桩）；
  - 真实第三方（学习通/知到）执行：NOT AVAILABLE（本基线不触碰）。
"""
import datetime
import time
import uuid


def test_worker_claim_execute_classify(platform):
    # 解除暂停，让真实 worker 线程开始认领（paused 是平台真实机制 :746）
    platform.set_setting("paused", "0")
    oid = uuid.uuid4().hex
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # 密码为无前缀占位（走 decrypt_secret 的历史明文兼容路径，无真实凭据）
    platform.sql(
        "INSERT INTO orders(id,user_id,product,platform,account,password,courses,"
        "status,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
        (oid, 1, "cx_video", "chaoxing", "baseline-exec-acc",
         "baseline-exec-placeholder", "264628209", "pending", now))

    deadline = time.time() + 90
    final = None
    while time.time() < deadline:
        rows = platform.sql("SELECT * FROM orders WHERE id=?", (oid,))
        st = rows[0]["status"]
        if st in ("done", "failed"):
            final = rows[0]
            break
        time.sleep(0.3)
    assert final is not None, "90s 内 worker 未收敛订单（claim/执行链路卡死）"

    # 状态机：pending → running（claim 原子抢占，attempt+1）→ failed
    assert final["status"] == "failed"
    assert (final["attempt"] or 0) == 1, "非零正退出码不可重试，attempt 应停在 1"
    assert final["exit_code"] not in (None, 0), "引擎缺失应产生非零退出码"
    assert final["worker_running"] == 0, "worker 收尾必须清 worker_running"
    assert "超时" not in (final["note"] or ""), "非 -9 退出不应走超时重试分支"

    # 真实 spawn + RollingLog：隔离订单目录里有真实日志
    log = platform.root / "orders" / oid / "log.txt"
    assert log.exists() and log.stat().st_size > 0, "RollingLog 应产出真实日志文件"

    # 收尾：重新暂停 worker，避免影响后续用例
    platform.set_setting("paused", "1")
