# -*- coding: utf-8 -*-
"""stage-cloud-02 隔离平台启动驱动

以临时目录为 APP_DIR 启动真实 order_platform（import 副作用全部落在该目录），
再用 waitress 提供真实生产级 HTTP 服务。禁止在此文件里加任何业务逻辑。
"""
import os
import sys

root, port = sys.argv[1], int(sys.argv[2])
os.chdir(root)
sys.path.insert(0, root)

import order_platform  # noqa: E402  真实平台（隔离 APP_DIR），import 即完成 init/迁移/恢复
from waitress import serve  # noqa: E402

print(f"[driver] order_platform imported, APP_DIR={order_platform.APP_DIR}", flush=True)
serve(order_platform.app, host="127.0.0.1", port=port, threads=4)
