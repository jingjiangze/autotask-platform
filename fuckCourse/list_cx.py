# -*- coding: utf-8 -*-
"""学习通课程列表查询（复用已保存的 cookies，失败则重新登录）"""
import os
import sys

APP_DIR = os.path.dirname(os.path.abspath(__file__))
CX_DIR = os.path.join(APP_DIR, "chaoxing")
os.environ["FUCKCOURSE_COOKIES"] = os.path.join(APP_DIR, "cookies.json")
os.environ["FUCKCOURSE_LOG_DIR"] = os.path.join(APP_DIR, "logs")
sys.path.insert(0, CX_DIR)

from api.base import Chaoxing, Account

username = sys.argv[1]
password = sys.argv[2]

cx = Chaoxing(account=Account(username, password))
state = cx.login(login_with_cookies=True)
print("登录状态:", state)
if not state.get("status"):
    state = cx.login(login_with_cookies=False)
    print("重新登录状态:", state)
    if not state.get("status"):
        sys.exit(1)

for c in cx.get_course_list():
    print(f"courseId={c['courseId']}  clazzId={c['clazzId']}  课程名={c['title']}")
