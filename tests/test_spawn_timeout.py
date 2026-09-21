# -*- coding: utf-8 -*-
"""超时熔断实测（约 10.5 分钟）：让 _spawn 跑一个 10 分钟都不会结束的哑进程，
order_timeout_min 置 0 → 到达最小熔断窗（10 分钟）后应强杀整树并返回 -9。
运行期间队列临时 paused（真实订单不会被执行），结束后自动恢复。
用法: python tests/test_spawn_timeout.py
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.dirname(HERE)
sys.path.insert(0, APP_DIR)

import order_platform as P  # noqa: E402  （导入即初始化 DB 与后台线程）

_real_get_setting = P.get_setting


def fake_get_setting(key, default=""):
    if key == "order_timeout_min":
        return "0"  # 最小熔断窗 = max(10, 0) * 60 = 600 秒
    return _real_get_setting(key, default)


P.get_setting = fake_get_setting
P.set_setting("paused", "1")
ok = False
try:
    log_path = os.path.join(APP_DIR, "orders", "_query", "timeout_test_log.txt")
    cmd = [sys.executable, "-c", "import time; time.sleep(600)"]
    t0 = time.time()
    rc = P._spawn(cmd, os.environ.copy(), APP_DIR, log_path, oid=None)
    cost = time.time() - t0
    ok = (rc == -9) and (540 < cost < 750)
    print(f"rc={rc}（期望 -9）耗时={cost:.0f}s（期望 ~600s）-> "
          f"{'PASS 超时熔断有效' if ok else 'FAIL'}")
finally:
    P.set_setting("paused", "0")  # 无论成败都恢复队列
    print("队列已恢复（paused=0）")
sys.exit(0 if ok else 1)
