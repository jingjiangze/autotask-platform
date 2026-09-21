# -*- coding: utf-8 -*-
"""探测知到共享课练习/考试 API"""
import os
import sys
import json
import random
import string

APP_DIR = os.path.dirname(os.path.abspath(__file__))
os.environ.setdefault("FUCKCOURSE_CONFIG", os.path.join(APP_DIR, "config.json"))
os.environ.setdefault("FUCKCOURSE_COOKIES", os.path.join(APP_DIR, "cookies.json"))
sys.path.insert(0, os.path.join(APP_DIR, "zhs"))

from fucker import Fucker, _set_origin_referer

f = Fucker(tree_view=False, progressbar_view=False)
root_cookies = json.load(open(os.path.join(APP_DIR, "cookies.json"), encoding="utf-8"))
f.cookies = root_cookies["zhs"]
f.getHikeList()

COURSES = ["11497022", "11344911", "10936157"]

for cid in COURSES:
    print(f"\n===== 课程 {cid} =====")
    s = f.session
    _set_origin_referer(s, "https://hike.zhihuishu.com")
    uuid = "".join(random.choices(string.ascii_letters + string.digits, k=8))
    # 1. 练习答题卡
    url = f"https://hike-examstu.zhihuishu.com/zhsathome/randomExercise/queryAnswerSheet"
    params = {"courseId": cid, "isFirst": "true", "randomExerciseStyle": "0", "uuid": uuid}
    try:
        r = s.get(url, params=params, timeout=15)
        print("[queryAnswerSheet]", r.status_code, r.text[:600])
    except Exception as e:
        print("[queryAnswerSheet] failed:", e)
    # 2. 章节测验列表(可能存在的接口)
    for probe in [
        f"https://hike-examstu.zhihuishu.com/zhsathome/exam/queryExamList?courseId={cid}",
        f"https://hike.zhihuishu.com/exam/stuExamList?courseId={cid}",
    ]:
        try:
            r = s.get(probe, timeout=15)
            print(f"[probe {probe.split('/')[-1]}]", r.status_code, r.text[:300])
        except Exception as e:
            print(f"[probe] failed:", e)
