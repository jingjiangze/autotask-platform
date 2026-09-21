# -*- coding: utf-8 -*-
"""routes/api.py — 公开 JSON API（规范 §13 / §14）

硬约束：
  §14 所有公开接口强制 status='published'（由 portal_service 保证）
  §46 错误只返回 error code，不返回内部信息
  §三十二 搜索只查 Portal DB，**不搜索 OpenList 文件系统**
"""
from flask import Blueprint, jsonify, request

import db
from services import openlist_service, portal_service as ps

bp = Blueprint("api", __name__, url_prefix="/api")


def ok(**kw):
    d = {"ok": True}
    d.update(kw)
    return jsonify(d)


def err(code, status=400, **kw):
    d = {"ok": False, "error": code}
    d.update(kw)
    return jsonify(d), status


@bp.route("/site")
def site():
    s = db.get_settings()
    return ok(site={
        "title": s.get("site_title", ""),
        "description": s.get("site_description", ""),
        "hero_title": s.get("hero_title", ""),
        "hero_subtitle": s.get("hero_subtitle", ""),
        "hero_text": s.get("hero_text", ""),
        "logo": s.get("logo", ""),
        "favicon": s.get("favicon", ""),
        "main_color": s.get("main_color", "#2f6feb"),
        "footer": s.get("footer", ""),
    }, modules=[{"key": m["module_key"], "title": m["title"], "order": m["sort_order"]}
                for m in db.get_modules(only_enabled=True)])


@bp.route("/home")
def home():
    return ok(**ps.home_payload())


@bp.route("/categories")
def categories():
    return ok(categories=ps.list_categories(public_only=True))


@bp.route("/tags")
def tags():
    return ok(tags=ps.list_tags(public_only=True))


@bp.route("/announcements")
def announcements():
    return ok(announcements=ps.list_announcements(public_only=True))


@bp.route("/resources")
def resources():
    try:
        limit = min(max(int(request.args.get("limit") or 24), 1), 100)
        offset = max(int(request.args.get("offset") or 0), 0)
    except ValueError:
        return err("invalid_pagination")
    data = ps.list_resources(
        public_only=True,
        category_id=request.args.get("category_id", type=int),
        tag_id=request.args.get("tag_id", type=int),
        featured=(True if request.args.get("featured") in ("1", "true") else None),
        limit=limit, offset=offset,
        order=request.args.get("order") or "default")
    return ok(total=data["total"], items=[_public_view(r) for r in data["items"]])


@bp.route("/resources/search")
def search():
    q = (request.args.get("q") or "").strip()
    if not q:
        return err("query_required")
    if len(q) > 100:
        return err("query_too_long")
    limit = min(max(int(request.args.get("limit") or 30), 1), 100)
    data = ps.list_resources(public_only=True, q=q, limit=limit)
    db.log_access(request.remote_addr or "", "api_search", None, q)
    return ok(query=q, total=data["total"],
              items=[_public_view(r) for r in data["items"]])


@bp.route("/resources/<int:rid>")
def resource(rid):
    r = ps.get_resource(rid, public_only=True)
    if not r:
        return err("resource_not_found", 404)       # 场景 8：404 且不含 DB 内部信息
    ps.bump_view(rid)
    v = _public_view(r)
    v["description"] = r.get("description", "")
    v["openlist_url"] = ps.public_file_url(r["openlist_path"])
    v["openlist_reachable"] = ps.path_in_public_root(r["openlist_path"])
    return ok(resource=v)


def _public_view(r):
    """公开视图：**不暴露** Windows 路径、内部标记、DB 结构以外的任何东西"""
    return {
        "id": r["id"],
        "title": r["title"],
        "slug": r.get("slug"),
        "summary": (r.get("description") or "")[:120],
        "cover": r.get("cover") or "",
        "openlist_path": r.get("openlist_path"),      # OpenList 逻辑路径，非 Windows 路径
        "category": (r.get("category") or {}).get("name"),
        "category_slug": (r.get("category") or {}).get("slug"),
        "tags": [t["name"] for t in (r.get("tags") or [])],
        "updated_at": r.get("updated_at"),
        "view_count": r.get("view_count", 0),
        "portal_click_count": r.get("portal_click_count", 0),
        "is_featured": bool(r.get("is_featured")),
        "version": r.get("version") or "",
    }
