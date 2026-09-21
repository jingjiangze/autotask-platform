# -*- coding: utf-8 -*-
"""进程树回收实测：父进程派生子进程，taskkill /T 树杀后验证父子均消亡、无孤儿残留。
用法: python tests/test_kill_tree.py
"""
import ctypes
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
CHILD_PID_FILE = os.path.join(HERE, "_child_pid.txt")
CHILD_SCRIPT = os.path.join(HERE, "_child_sleeper.py")
PARENT_SCRIPT = os.path.join(HERE, "_parent_spawner.py")
if os.path.exists(CHILD_PID_FILE):
    os.remove(CHILD_PID_FILE)

with open(CHILD_SCRIPT, "w", encoding="utf-8") as f:
    f.write(f"import time,os\nopen(r'{CHILD_PID_FILE}','w').write(str(os.getpid()))\n"
            "time.sleep(600)\n")
with open(PARENT_SCRIPT, "w", encoding="utf-8") as f:
    f.write(f"import subprocess,time\nc=subprocess.Popen([r'{sys.executable}',r'{CHILD_SCRIPT}'])\n"
            "time.sleep(600)\n")


def alive(pid):
    """判活：OpenProcess 成功不代表存活（自身持有的 Popen 句柄会让已终止的
    进程对象滞留），必须用 GetExitCodeProcess 判断是否 STILL_ACTIVE"""
    if not pid:
        return False
    k = ctypes.windll.kernel32
    h = k.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return False
    code = ctypes.c_ulong()
    k.GetExitCodeProcess(h, ctypes.byref(code))
    k.CloseHandle(h)
    return code.value == 259  # STILL_ACTIVE


try:
    parent = subprocess.Popen([sys.executable, PARENT_SCRIPT],
                              creationflags=0x08000000)
    for _ in range(20):  # 等子进程写下自己的 PID
        if os.path.exists(CHILD_PID_FILE):
            break
        time.sleep(0.5)
    child_pid = open(CHILD_PID_FILE).read().strip()
    assert alive(parent.pid) and alive(child_pid), "测试进程未正常拉起"

    subprocess.run(["taskkill", "/F", "/T", "/PID", str(parent.pid)],
                   capture_output=True, creationflags=0x08000000)
    time.sleep(2)

    parent_dead = not alive(parent.pid)
    child_dead = not alive(child_pid)
    ok = parent_dead and child_dead
    print(f"父进程({parent.pid})消亡: {parent_dead} | "
          f"子进程({child_pid})消亡: {child_dead} -> "
          f"{'PASS 无孤儿残留' if ok else 'FAIL 有孤儿!'}")
finally:
    for p in (CHILD_PID_FILE, CHILD_SCRIPT, PARENT_SCRIPT):
        if os.path.exists(p):
            os.remove(p)
sys.exit(0 if ok else 1)
