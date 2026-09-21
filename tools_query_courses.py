# -*- coding: utf-8 -*-
"""课程查询工具（供平台子进程调用，输出 JSON）
用法:
    python tools_query_courses.py chaoxing <账号> <密码> [cookies.json]
    python tools_query_courses.py zhs <账号> <密码> [cookies.json]
    python tools_query_courses.py zhs_cookie <cookies.json>      # 扫码后查课
输出: {"ok":true,"courses":[{"id":"...","name":"...","kind":"..."}]}
      {"ok":false,"error":"..."}
"""
import json
import os
import sys
import uuid

APP_DIR = os.path.dirname(os.path.abspath(__file__))
FUCK_DIR = os.path.join(APP_DIR, "fuckCourse")
QUERY_DIR = os.path.join(APP_DIR, "orders", "_query")
os.makedirs(QUERY_DIR, exist_ok=True)


def out(obj):
    print(json.dumps(obj, ensure_ascii=False))
    sys.exit(0 if obj.get("ok") else 1)


def main():
    if len(sys.argv) < 3:
        out({"ok": False, "error": "参数不足"})
    mode = sys.argv[1]

    if mode in ("chaoxing", "zhs"):
        account = sys.argv[2] if len(sys.argv) > 2 and sys.argv[2] else os.environ.get("WK_ACCOUNT", "")
        password = sys.argv[3] if len(sys.argv) > 3 and sys.argv[3] else os.environ.get("WK_PASSWORD", "")
        cookie_path = sys.argv[4] if len(sys.argv) > 4 else os.path.join(QUERY_DIR, uuid.uuid4().hex + ".json")
    else:  # zhs_cookie
        account = password = ""
        cookie_path = sys.argv[2]

    os.environ["FUCKCOURSE_COOKIES"] = cookie_path
    os.environ["FUCKCOURSE_CONFIG"] = os.path.join(FUCK_DIR, "config.json")
    os.environ.setdefault("FUCKCOURSE_LOG_DIR", os.path.join(FUCK_DIR, "logs"))

    try:
        if mode == "chaoxing":
            sys.path.insert(0, os.path.join(FUCK_DIR, "chaoxing"))
            from api.base import Chaoxing, Account  # type: ignore
            cx = Chaoxing(account=Account(account, password))
            state = cx.login(login_with_cookies=False)
            if not state.get("status"):
                out({"ok": False, "error": state.get("msg") or "登录失败"})
            courses = [{"id": str(c["courseId"]), "name": c["title"],
                        "kind": f"班级 {c.get('clazzId', '')}"}
                       for c in cx.get_course_list()]
            out({"ok": True, "courses": courses})

        else:  # zhs / zhs_cookie
            sys.path.insert(0, os.path.join(FUCK_DIR, "zhs"))
            from fucker import Fucker  # type: ignore
            f = Fucker(tree_view=False, progressbar_view=False)
            if mode == "zhs" and account and password:
                # 账号密码登录后查课
                try:
                    f.login(account, password, interactive=False)
                except Exception as e:
                    out({"ok": False, "error": f"登录失败: {e}"[:200]})
                # 保存 cookies 以便后续复用
                try:
                    from utils import cookie_jar_to_list  # type: ignore
                    json.dump({"zhs": cookie_jar_to_list(f.cookies)},
                              open(cookie_path, "w", encoding="utf-8"),
                              ensure_ascii=False, indent=2)
                except Exception:
                    pass
            else:
                if not os.path.exists(cookie_path):
                    out({"ok": False, "error": "cookies 不存在，请先登录"})
                raw = json.load(open(cookie_path, encoding="utf-8"))
                ck = raw.get("zhs") if isinstance(raw, dict) else raw
                if not ck:
                    out({"ok": False, "error": "cookies 为空，请重新扫码"})
                f.cookies = ck
            courses = []
            try:
                for c in f.getZhidaoList():
                    courses.append({"id": c.secret, "name": c.courseName, "kind": "知到课"})
            except Exception:
                pass
            try:
                for c in f.getHikeList():
                    courses.append({"id": str(c.courseId), "name": c.courseName, "kind": "共享课"})
            except Exception:
                pass
            if not courses:
                out({"ok": False, "error": "未查询到课程（账号可能未选课或登录态失效）"})
            out({"ok": True, "courses": courses})

    except Exception as e:
        out({"ok": False, "error": f"{type(e).__name__}: {e}"[:200]})


if __name__ == "__main__":
    main()
