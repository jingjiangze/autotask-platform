# -*- coding: utf-8 -*-
"""RECOVERY 驱动：以隔离目录为 APP_DIR 再次 import 真实 order_platform。
import 即执行 recover_stale_orders（真实重启恢复路径）。禁止加业务逻辑。"""
import sys

root = sys.argv[1]
sys.path.insert(0, root)

import order_platform  # noqa: E402,F401  import 副作用 = 真实重启恢复

print("[recover-driver] done", flush=True)
