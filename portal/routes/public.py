# -*- coding: utf-8 -*-
"""routes/public.py — 前台页面（服务端渲染）

规范约束（本轮前台改造，后端语义不变）：
  §十七 只读 status='published'（由 portal_service 强制，路由不自行放宽）
  §二十八 portal_click_count 不冒充真实下载次数
  §八   详情页「访问资源」跳转到 OpenList，门户不重复实现文件能力
  §十三 /category 路由保留（不删），仅从主导航移除
"""
from flask import Blueprint, abort, redirect, render_template, request, url_for

import db
from services import portal_service as ps
from utils import auth as auth_util
from utils import weblog

bp = Blueprint("public", __name__)

PER_PAGE = 12          # 列表流每页条数（首页与列表页统一）


def _site():
    return db.get_settings()


def _page_no():
    """分页参数：非数字或越界一律回落，避免异常输入"""
    raw = request.args.get("page") or "1"
    try:
        n = int(raw)
    except (TypeError, ValueError):
        n = 1
    return max(1, n)


@bp.route("/")
def index():
    """首页 = 资源列表流：Hero + 搜索 + 公告 + 推荐 + 最新（可翻页）"""
    page = _page_no()
    data = ps.home_payload()
    # home_payload() 也会返回 latest（模块化用），这里以分页查询结果为准，
    # 必须先移除，否则 render_template 会收到重复关键字参数
    data.pop("latest", None)
    total = ps.count_resources(public_only=True)
    latest = ps.list_resources(public_only=True, limit=PER_PAGE,
                               offset=(page - 1) * PER_PAGE,
                               order="latest")["items"]
    return render_template("index.html", site=_site(),
                           page=page, per_page=PER_PAGE, total=total, latest=latest,
                           show_tagbar=False, **data)


@bp.route("/resources")
def resources():
    """全部资源（可按标签筛选）"""
    tag_slug = request.args.get("tag")
    tag_id = None
    if tag_slug:
        for t in ps.list_tags(public_only=True):
            if t["slug"] == tag_slug:
                tag_id = t["id"]
                break
    page = _page_no()
    order = request.args.get("order") or "default"
    data = ps.list_resources(public_only=True, tag_id=tag_id, limit=PER_PAGE,
                             offset=(page - 1) * PER_PAGE, order=order)
    return render_template("list.html", site=_site(),
                           title=("标签：" + tag_slug) if tag_slug else "全部资源",
                           subtitle="", resources=data["items"], total=data["total"],
                           page=page, per_page=PER_PAGE,
                           base_url=url_for("public.resources"),
                           tags=ps.list_tags(public_only=True)[:30], active_tag=tag_slug,
                           show_tagbar=True, order=order)


@bp.route("/tags")
def tags():
    """标签总览（前台主要发现入口之一）"""
    return render_template("tags.html", site=_site(),
                           tags=ps.list_tags(public_only=True))


@bp.route("/search")
def search():
    """搜索：只查 Portal DB 中已发布的资源（§三十二），不搜索 OpenList 文件系统"""
    q = (request.args.get("q") or "").strip()[:100]
    page = _page_no()
    data = {"items": [], "total": 0}
    if q:
        data = ps.list_resources(public_only=True, q=q, limit=PER_PAGE,
                                 offset=(page - 1) * PER_PAGE)
        db.log_access(auth_util.client_ip(), "search", None, q)
    return render_template("list.html", site=_site(), title="搜索资源",
                           subtitle=("关键词：" + q) if q else "请输入关键词",
                           resources=data["items"], total=data["total"], page=page,
                           per_page=PER_PAGE, base_url=url_for("public.search", q=q),
                           q=q, show_tagbar=False)


@bp.route("/category/<slug>")
def category(slug):
    """分类页保留（数据库兼容），但已不在主导航中展示"""
    cats = ps.list_categories(public_only=True)
    cat = next((c for c in cats if c["slug"] == slug), None)
    if not cat:
        abort(404)
    page = _page_no()
    data = ps.list_resources(public_only=True, category_id=cat["id"],
                             limit=PER_PAGE, offset=(page - 1) * PER_PAGE)
    return render_template("list.html", site=_site(), title=cat["name"],
                           subtitle=cat.get("description") or "",
                           resources=data["items"], total=data["total"],
                           page=page, per_page=PER_PAGE,
                           base_url=url_for("public.category", slug=slug),
                           show_tagbar=False)


@bp.route("/categories")
def categories():
    """分类总览保留（不删，不入口）"""
    return render_template("categories.html", site=_site(),
                           categories=ps.list_categories(public_only=True),
                           modules=ps.modules())


@bp.route("/resource/<int:rid>")
def resource_detail(rid):
    r = ps.get_resource(rid, public_only=True)
    if not r:
        abort(404)                          # 非 published 一律 404，不泄露存在性
    ps.bump_view(rid)
    db.log_access(auth_util.client_ip(), "view", rid)
    # 经 service 换算：公开层 guest 的路径是相对 base_path 的，不能直接拼绝对路径
    r["openlist_url"] = ps.public_file_url(r["openlist_path"])
    r["path_reachable"] = ps.path_in_public_root(r["openlist_path"])
    related = []
    if r.get("tags"):
        t0 = r["tags"][0]["id"]          # 优先按共同标签找相关资源
        related = [x for x in ps.list_resources(public_only=True, tag_id=t0, limit=6)["items"]
                   if x["id"] != rid][:4]
    if not related:                       # 标签无结果时回落到最新资源
        related = [x for x in ps.list_resources(public_only=True, limit=6)["items"]
                   if x["id"] != rid][:4]
    return render_template("resource.html", site=_site(), r=r, related=related)


@bp.route("/go/<int:rid>")
def go(rid):
    """统计门户点击后跳转到 OpenList（门户不代理文件，§十二 / §二十二）"""
    r = ps.get_resource(rid, public_only=True)
    if not r:
        abort(404)
    ps.bump_click(rid)
    db.log_access(auth_util.client_ip(), "goto_openlist", rid)
    return redirect(ps.public_file_url(r["openlist_path"]), code=302)
