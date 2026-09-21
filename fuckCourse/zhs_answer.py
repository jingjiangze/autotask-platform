# -*- coding: utf-8 -*-
"""知到(智慧树)自动答题模块
================================================
能力一：视频弹题自动作答（共享学分课/知到课, RAC 流程）
  - 弹题时间点: popupAnswer/loadVideoPointerInfo
  - 拉取题目:   popupAnswer/lessonPopupExam
  - 作答:      接口返回的选项自带 result=='1' 正确标记, 直接取正确答案
               (可选 AI 兜底: 知到官方 AI 通道, 无需 API key)
  - 提交:      popupAnswer/saveLessonPopupExamSaveAnswer
  - 已内置到 fuckZhidaoVideo 播放循环, 刷知到课时自动生效

能力二：hike(校内学分课, 即你 current 三门课)弹题/考试
  - 状态: 接口通道已验证可达, 但缺 recruitId 映射, 需一次浏览器抓包
  - 抓到 recruitId 后, 用 `python zhs_answer.py link <courseId> <recruitId>` 绑定
    即可让 hike 视频刷取时自动带弹题作答

用法:
    python zhs_answer.py probe                    # 检测当前环境答题能力
    python zhs_answer.py rac <RAC_id>             # 完整刷知到课(含弹题自动答)
    python zhs_answer.py popups <RAC_id>          # 仅回答指定课全部弹题(不刷视频)
    python zhs_answer.py link <hikeCourseId> <recruitId>  # 绑定 hike 课映射
"""
import os
import sys
import json
import time
import random
import string

APP_DIR = os.path.dirname(os.path.abspath(__file__))
ZHS_DIR = os.path.join(APP_DIR, "zhs")
os.environ.setdefault("FUCKCOURSE_CONFIG", os.path.join(APP_DIR, "config.json"))
os.environ.setdefault("FUCKCOURSE_COOKIES", os.path.join(APP_DIR, "cookies.json"))
sys.path.insert(0, ZHS_DIR)

LINK_FILE = os.path.join(APP_DIR, ".zhs_hike_links.json")


def new_fucker():
    from fucker import Fucker
    f = Fucker(tree_view=True, progressbar_view=True)
    root = json.load(open(os.environ["FUCKCOURSE_COOKIES"], encoding="utf-8"))
    f.cookies = root["zhs"]
    f.getHikeList()
    return f


def load_links():
    if os.path.exists(LINK_FILE):
        return json.load(open(LINK_FILE, encoding="utf-8"))
    return {}


def save_links(links):
    json.dump(links, open(LINK_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


def answer_popups_for_video(f, rac_id, video_id):
    """主动模式：不播放视频，直接拉取并回答指定视频的全部弹题"""
    from logger import logger
    pointer = f.loadVideoPointerInfo(rac_id, video_id)
    points = (pointer.questionPoint or []) if pointer else []
    if not points:
        return 0
    answered = 0
    for p in points:
        try:
            q = f.lessonPopoupExam(rac_id, video_id, p.questionIds) \
                    .lessonTestQuestionUseInterfaceDtos[0].testQuestion
            ans = f.answerZhidao(q)
            time.sleep(random.randint(3, 8))  # 模拟读题
            f.saveLessonPopupExamSaveAnswer(rac_id, video_id, q.questionId, ans)
            answered += 1
            print(f"  ✓ 弹题 {q.questionId} 已作答: {ans}")
        except Exception as e:
            print(f"  ✗ 弹题 {getattr(p, 'questionIds', '?')} 失败: {e}")
    return answered


def cmd_probe(f):
    print("== 知到自动答题能力检测 ==")
    print("[1] 弹题管线(zhidao/RAC 课): 已内置, 刷 RAC 课时自动生效")
    print("[2] hike 课绑定映射:", json.dumps(load_links(), ensure_ascii=False) or "无")
    print("[3] hike 弹题/考试接口: 通道可达, 待 recruitId 绑定")
    print("提示: 浏览器打开课程视频播放页 -> F12 Network 中找 loadVideoPointerInfo")
    print("      请求里的 recruitId / lessonId 参数, 然后用 link 命令绑定")


def cmd_popups(f, rac_id):
    """遍历 RAC 课全部视频, 回答所有未答弹题"""
    print(f"== 遍历课程 {rac_id} 全部弹题 ==")
    chapters = f.videoList(rac_id)
    course_id = chapters.courseId
    total = 0
    for chapter in chapters.videoChapterDtos:
        for lesson in chapter.videoLessons:
            if "videoId" in lesson:
                vids = [lesson]
            else:
                vids = lesson.videoSmallLessons or []
            for v in vids:
                n = answer_popups_for_video(f, rac_id, v.videoId)
                total += n
    print(f"== 完成, 共作答 {total} 道弹题 (courseId={course_id}) ==")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    cmd = sys.argv[1]

    if cmd == "link":
        course_id, recruit_id = sys.argv[2], sys.argv[3]
        links = load_links()
        links[course_id] = recruit_id
        save_links(links)
        print(f"已绑定: hike 课 {course_id} -> recruitId {recruit_id}")
        return

    f = new_fucker()
    print("=== cookies 登录成功 ===")

    if cmd == "probe":
        cmd_probe(f)
    elif cmd == "rac":
        rac_id = sys.argv[2]
        f.fuckZhidaoCourse(rac_id)  # 播放循环内置弹题自动作答
    elif cmd == "popups":
        cmd_popups(f, sys.argv[2])
    else:
        print("未知命令:", cmd)
        sys.exit(1)


if __name__ == "__main__":
    main()
