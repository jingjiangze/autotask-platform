# -*- coding: utf-8 -*-
"""备份恢复演练（真实环境）：对线上库执行 backup → restore → 平台自动重启 → 校验。
注意：会短暂停止平台（约 1-2 分钟），需与看护计划任务配合执行，不建议单独运行。
用法: python tests/test_restore_drill.py
"""
import os
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.dirname(HERE)
sys.path.insert(0, APP_DIR)
import backup_manager  # noqa: E402

op = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def health():
    try:
        return op.open("http://127.0.0.1:8766/health", timeout=5).read().decode()
    except Exception as e:
        return f"down({type(e).__name__})"


def sh(cmd):
    return subprocess.run(cmd, capture_output=True, creationflags=0x08000000)


print("1) 演练前备份")
dst, ok, msg = backup_manager.backup_database()
assert ok, msg
print("   备份:", dst, "|", msg)

print("2) 停看护计划任务（防止恢复中途被拉起）")
sh(["powershell", "-NoProfile", "-Command",
    "Stop-ScheduledTask -TaskName 'WK_AutoTaskPlatform'"])
time.sleep(2)
r = sh(["netstat", "-ano", "-p", "tcp"])
for ln in r.stdout.decode("gbk", errors="replace").splitlines():
    if "8766" in ln and "LISTENING" in ln:
        sh(["taskkill", "/F", "/PID", ln.split()[-1]])
time.sleep(2)
print("   平台已停止:", health())

print("3) 恢复刚才的备份")
pre, ok2, msg2 = backup_manager.restore_database(dst)
print("   恢复完成，恢复前库已另存:", pre, "|", ("校验通过" if ok2 else msg2))

print("4) 重新启动看护计划任务")
sh(["powershell", "-NoProfile", "-Command",
    "Start-ScheduledTask -TaskName 'WK_AutoTaskPlatform'"])

print("5) 等待平台自动恢复（最长 150 秒）")
back = False
for _ in range(15):
    time.sleep(10)
    h = health()
    if "database" in h:
        back = True
        print("   平台恢复:", h)
        break
    print("   等待中...", h)

print("6) 校验订单数量一致")
import sqlite3  # noqa: E402
db = sqlite3.connect(os.path.join(APP_DIR, "orders", "platform.db"))
n = db.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
db.close()
print(f"   orders={n}（演练前备份时也应为 19）-> {'PASS' if back and n >= 19 else 'FAIL'}")
sys.exit(0 if back and n >= 19 else 1)
