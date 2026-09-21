# -*- coding: utf-8 -*-
"""探测: hike 课能否直接调 studyservice-api 的弹题接口"""
import os
import sys
import json
import time

APP_DIR = os.path.dirname(os.path.abspath(__file__))
os.environ.setdefault("FUCKCOURSE_CONFIG", os.path.join(APP_DIR, "config.json"))
os.environ.setdefault("FUCKCOURSE_COOKIES", os.path.join(APP_DIR, "cookies.json"))
sys.path.insert(0, os.path.join(APP_DIR, "zhs"))

from fucker import Fucker, _set_origin_referer

f = Fucker(tree_view=False, progressbar_view=False)
root_cookies = json.load(open(os.path.join(APP_DIR, "cookies.json"), encoding="utf-8"))
f.cookies = root_cookies["zhs"]
f.getHikeList()

COURSE_ID = "11497022"
ctx = f.getHikeContext(COURSE_ID).root

# 收集: 章节 id 和第一个视频 file id
chapter_id, file_id = None, None
def walk(node):
    global chapter_id, file_id
    if isinstance(node, list):
        for i in node: walk(i)
        return
    if node.childList:
        if chapter_id is None: chapter_id = node.id
        for c in node.childList: walk(c)
        return
    if node.dataType == 3 and file_id is None:
        file_id = node.id
walk(ctx)
print(f"chapter_id={chapter_id} file_id={file_id}")

_set_origin_referer(f.session, "https://studyh5.zhihuishu.com")

# 组合探测 loadVideoPointerInfo (zhidao 加密通道)
def try_pointer(params_desc, data):
    try:
        ret = f.zhidaoQuery(
            "https://studyservice-api.zhihuishu.com/gateway/t/v1/popupAnswer/loadVideoPointerInfo",
            data, ok_code=None)
        print(f"[{params_desc}] OK:", json.dumps(dict(ret), ensure_ascii=False, default=str)[:500])
    except Exception as e:
        print(f"[{params_desc}] FAIL:", str(e)[:200])

for desc, data in [
    ("courseId=hike, lessonId=file", {"lessonId": file_id, "recruitId": "", "courseId": COURSE_ID}),
    ("courseId=hike, lessonId=chapter", {"lessonId": chapter_id, "recruitId": "", "courseId": COURSE_ID}),
    ("courseId=hike, recruitId=hike, lessonId=file", {"lessonId": file_id, "recruitId": COURSE_ID, "courseId": COURSE_ID}),
]:
    try_pointer(desc, dict(data))

# 也试一下 hike 自己的域有没有 pointer 接口
try:
    r = f.session.get(
        f"https://studyservice-api.zhihuishu.com/gateway/t/v1/popupAnswer/loadVideoPointerInfoHike?courseId={COURSE_ID}&fileId={file_id}",
        timeout=15)
    print("[hike-pointer-probe]", r.status_code, r.text[:300])
except Exception as e:
    print("[hike-pointer-probe] FAIL:", e)
