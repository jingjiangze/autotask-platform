# -*- coding: utf-8 -*-
"""以完全脱离的方式启动 cloudflared 隧道（脱离本会话进程树，不会被回收）"""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
CFD = os.path.join(HERE, "cloudflared.exe")
CF_HOME = os.path.join(os.path.expanduser("~"), ".cloudflared")
CONFIG = os.path.join(CF_HOME, "wk_config.yml")
LOG = os.path.join(HERE, "cloudflared.log")

env = os.environ.copy()
for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
    env.pop(k, None)
env["NO_PROXY"] = "*"

DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_BREAKAWAY_FROM_JOB = 0x01000000

cmd = [CFD, "tunnel", "--config", CONFIG, "--no-autoupdate", "run", "wk-platform"]
lf = open(LOG, "ab")

p = None
for flags, desc in [
    (DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB, "detached+breakaway"),
    (DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP, "detached"),
    (CREATE_NEW_PROCESS_GROUP, "group"),
]:
    try:
        p = subprocess.Popen(cmd, stdout=lf, stderr=subprocess.STDOUT,
                             creationflags=flags, env=env, close_fds=True)
        print(f"启动方式: {desc}, pid={p.pid}")
        break
    except OSError as e:
        print(f"{desc} 失败: {e}")
        p = None

if p is None:
    print("所有启动方式均失败")
    sys.exit(1)

time.sleep(15)
alive = p.poll() is None
print("隧道进程存活:" , alive)
if not alive:
    print("进程已退出，请查看 cloudflared.log")
