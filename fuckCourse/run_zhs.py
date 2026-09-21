# -*- coding: utf-8 -*-
"""知到(智慧树)自动化驱动 — cookies 免登录（扫码登录后自动保存）。

用法:
    python run_zhs.py list              # 列出全部课程
    python run_zhs.py trial <course_id> # 试刷该课程第一个未完成视频
    python run_zhs.py full <id1> [id2 id3]  # 完整刷课(支持多个)

course_id 说明: 知到课(recruitAndCourseId, 含字母) / 共享课(courseId, 纯数字) 均支持。
"""
import os
import sys
import json

APP_DIR = os.path.dirname(os.path.abspath(__file__))
ZHS_DIR = os.path.join(APP_DIR, "zhs")
os.environ.setdefault("FUCKCOURSE_CONFIG", os.path.join(APP_DIR, "config.json"))
os.environ.setdefault("FUCKCOURSE_COOKIES", os.path.join(APP_DIR, "cookies.json"))
sys.path.insert(0, ZHS_DIR)


def load_cookies(fucker):
    """从统一 cookies.json 恢复 zhs 会话，返回是否成功"""
    path = os.environ["FUCKCOURSE_COOKIES"]
    if not os.path.exists(path):
        return False
    with open(path, "r", encoding="utf-8") as f:
        root = json.load(f)
    cookies = root.get("zhs")
    if not cookies:
        return False
    try:
        fucker.cookies = cookies
    except Exception as e:
        print(f"cookies 恢复失败: {e}")
        return False
    return True


def verify_login(fucker):
    """验证会话有效性：能拉到课程列表即有效"""
    try:
        fucker.getHikeList()
        return True
    except Exception:
        return False


def first_unfinished_video(fucker, course_id):
    """从课程资源树中找到第一个未看完的视频 file_id"""
    root = fucker.getHikeContext(course_id).root

    def walk(node):
        if isinstance(node, list):  # 根节点或同级列表
            for item in node:
                r = walk(item)
                if r is not None:
                    return r
            return None
        if node.childList:
            for child in node.childList:
                r = walk(child)
                if r is not None:
                    return r
            return None
        # 叶子 = 文件
        if node.dataType == 3:  # 视频
            study = node.studyTime or 0
            if study < node.totalTime * fucker.end_thre:
                return node.id
        return None

    return walk(root)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    mode = sys.argv[1]

    from fucker import Fucker

    fucker = Fucker(tree_view=True, progressbar_view=True)

    print("=== 使用已保存 cookies 登录知到 ===")
    if not load_cookies(fucker) or not verify_login(fucker):
        print("cookies 缺失或已失效！请先运行扫码登录（zhs/main.py）获取新 cookies")
        sys.exit(1)
    print("=== 登录成功 ===\n")

    if mode == "list":
        print("--- 知到课程 (recruitAndCourseId) ---")
        try:
            for c in fucker.getZhidaoList():
                print(f"  [zhidao] {c.courseName}  id={c.secret}")
        except Exception as e:
            print(f"  获取失败: {e}")
        print("--- 共享课 (courseId) ---")
        try:
            for c in fucker.getHikeList():
                print(f"  [hike]   {c.courseName}  id={c.courseId}")
        except Exception as e:
            print(f"  获取失败: {e}")
        print("--- AI 课 (courseType 8, 含考试) ---")
        try:
            for c in fucker.getZhidaoAiList():
                print(f"  [ai]     {c.courseName}  courseId={c.courseId} classId={c.classId}")
        except Exception as e:
            print(f"  获取失败: {e}")
        return

    if mode == "trial":
        course_id = sys.argv[2]
        print(f"=== 试刷: 课程 {course_id} ===")
        vid = first_unfinished_video(fucker, course_id)
        if vid is None:
            print("该课程没有未完成的视频（可能已全部刷完）")
            return
        print(f"找到第一个未完成视频: file_id={vid}, 开始试刷...")
        fucker.fuckHikeVideo(course_id, vid)
        print("=== 试刷完成（进度已上报） ===")
        return

    if mode == "full":
        course_ids = sys.argv[2:]
        print(f"=== 完整刷课, 共 {len(course_ids)} 门 ===")
        for cid in course_ids:
            print(f"\n>>> 开始课程: {cid}")
            try:
                fucker.fuckCourse(cid)
                print(f">>> 课程 {cid} 完成")
            except Exception as e:
                print(f">>> 课程 {cid} 出错: {e}")
        print("\n=== 全部课程处理完毕 ===")
        return

    if mode == "aifull":
        # AI 课：视频 + 考试全自动（作答走知到官方 AI，提交含 submitExam）
        conf_path = os.environ["FUCKCOURSE_CONFIG"]
        root = json.load(open(conf_path, encoding="utf-8"))
        ai_cfg = (root.get("zhs") or {}).get("ai") or root.get("ai")
        if not ai_cfg or not ai_cfg.get("enabled"):
            print("AI 配置未启用（config.json → zhs.ai.enabled）")
            sys.exit(1)
        try:
            lst = fucker.getZhidaoAiList()
        except Exception as e:
            print(f"获取 AI 课程列表失败: {e}")
            sys.exit(1)
        if not lst:
            print("当前账号没有 AI 课程")
            return
        print(f"=== AI 课程 {len(lst)} 门, 开始执行（含考试自动作答+提交） ===")
        for course in lst:
            print(f"\n>>> AI 课程: {course.courseName} (courseId={course.courseId})")
            try:
                fucker.fuckAiCourse(course.courseId, course.classId, aiConfig=ai_cfg)
                print(f">>> 完成")
            except Exception as e:
                print(f">>> 出错: {e}")
        return

    print("未知模式:", mode)
    sys.exit(1)


if __name__ == "__main__":
    main()
