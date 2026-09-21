# -*- coding: utf-8 -*-
"""routes/admin.py — 门户后台（规范 §十五 / §16 / §44 / §56）

硬约束：
  §14  独立鉴权；未登录一律 401/403（场景 7）
  §7/$16 目录选择器**只经 OpenList API**，绝不读 Windows 文件系统
  §10  删除资源只删 DB，绝不删文件
  §19  凭据存 Windows 凭据管理器，不落源码/日志
  §44  敏感目录名告警（提示，不替代人工确认）
"""
import functools

from flask import (Blueprint, flash, jsonify, redirect, render_template, request,
                   session, url_for)

import config
import db
from services import openlist_service as ol
from services import portal_service as ps
from utils import auth as auth_util
from utils import weblog

bp = Blueprint("admin", __name__, url_prefix="/admin")


def _is_api_request():
    """后台下的 JSON 接口路径是 /admin/api/...，不是 /api/...，
    必须单独判断，否则未登录时会返回 302 重定向而不是 401 JSON。"""
    p = request.path
    return p.startswith("/api/") or p.startswith("/admin/api/")


def admin_required(fn):
    @functools.wraps(fn)
    def wrapper(*a, **kw):
        if not auth_util.is_admin():
            if _is_api_request():
                return jsonify({"ok": False, "error": "unauthorized"}), 401
            return redirect(url_for("admin.login", next=request.path))
        return fn(*a, **kw)
    return wrapper


# ==================== 登录 ====================

@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        ip = auth_util.client_ip()
        if not auth_util.rate_limit_ok(ip):
            weblog.warn("登录限流触发 ip=%s", ip)
            return render_template("admin/login.html", site=db.get_settings(),
                                   error="尝试过于频繁，请稍后再试"), 429
        pwd = request.form.get("password", "")
        if auth_util.verify_password(pwd):
            auth_util.login(ip)
            weblog.info("后台登录成功 ip=%s", ip)
            nxt = request.args.get("next") or url_for("admin.dashboard")
            if not nxt.startswith("/admin"):
                nxt = url_for("admin.dashboard")
            return redirect(nxt)
        weblog.warn("后台登录失败 ip=%s", ip)
        return render_template("admin/login.html", site=db.get_settings(),
                               error="口令错误"), 401
    return render_template("admin/login.html", site=db.get_settings(), error=None)


@bp.route("/logout")
def logout():
    auth_util.logout()
    return redirect(url_for("admin.login"))


# ==================== 仪表盘 ====================

@bp.route("/")
@admin_required
def dashboard():
    st = ps.dashboard_stats()
    ol_ok, ol_code = ol.health()
    creds_ok = ol.credentials_configured()
    recent = ps.list_resources(public_only=False, limit=8)["items"]
    return render_template("admin/dashboard.html", site=db.get_settings(), st=st,
                           ol_ok=ol_ok, ol_code=ol_code, creds_ok=creds_ok, recent=recent)


# ==================== OpenList 目录选择器（只走 API）====================

@bp.route("/api/openlist/directories")
@admin_required
def ol_directories():
    path = request.args.get("path") or "/"
    try:
        data = ol.list_directory(path, per_page=500)
    except ol.OpenListError as e:
        return jsonify({"ok": False, "error": e.code,
                        "hint": "请检查 OpenList 凭据与运行状态"}), 502
    dirs = [x for x in data["items"] if x["is_dir"]]
    return jsonify({
        "ok": True,
        "path": data["path"],
        "parent": ("/" if data["path"] in ("/", "") else
                   "/" + "/".join([p for p in data["path"].split("/") if p][:-1])),
        "directories": [{"name": d["name"], "path": (data["path"].rstrip("/") + "/" + d["name"])}
                        for d in dirs],
        "files_count": len([x for x in data["items"] if not x["is_dir"]]),
        "warnings": ol.sensitive_warnings(data["path"]),
    })


@bp.route("/api/openlist/check")
@admin_required
def ol_check():
    """发布前/运行时校验目录可用性（§56 / 场景 6）"""
    path = request.args.get("path") or ""
    ok, why = ol.directory_exists(path)
    return jsonify({"ok": ok, "path": path, "reason": why,
                    "warnings": ol.sensitive_warnings(path),
                    "in_public_root": ps.path_in_public_root(path),
                    "public_root": ps.public_root(),
                    "public_url": ps.public_file_url(path)})


# ==================== 资源管理 ====================

@bp.route("/resources")
@admin_required
def resources():
    status = request.args.get("status") or None
    q = request.args.get("q") or None
    data = ps.list_resources(public_only=False, status=status, q=q, limit=300)
    return render_template("admin/resources.html", site=db.get_settings(),
                           items=data["items"], total=data["total"],
                           status=status or "", q=q or "",
                           categories=ps.list_categories(public_only=False))


@bp.route("/resources/new", methods=["GET", "POST"])
@bp.route("/resources/<int:rid>/edit", methods=["GET", "POST"])
@admin_required
def resource_edit(rid=None):
    if request.method == "POST":
        form = {k: v for k, v in request.form.items()}
        form["is_featured"] = request.form.get("is_featured") == "on"
        form["tags"] = request.form.get("tags", "")
        try:
            form["category_id"] = int(form.get("category_id") or 0) or None
        except ValueError:
            form["category_id"] = None
        new_id, err = ps.save_resource(form, rid)
        if err:
            flash(f"保存失败：{err}", "error")
            return render_template("admin/resource_form.html", site=db.get_settings(),
                                   r=form, rid=rid, categories=ps.list_categories(False),
                                   warnings=ol.sensitive_warnings(form.get("openlist_path", ""))), 400
        flash("已保存" if rid else "已创建", "ok")
        return redirect(url_for("admin.resource_edit", rid=new_id))

    r = ps.get_resource(rid, public_only=False) if rid else None
    if rid and not r:
        flash("资源不存在", "error")
        return redirect(url_for("admin.resources"))
    return render_template("admin/resource_form.html", site=db.get_settings(), r=r, rid=rid,
                           categories=ps.list_categories(False),
                           warnings=ol.sensitive_warnings((r or {}).get("openlist_path", "")))


@bp.route("/resources/<int:rid>/status", methods=["POST"])
@admin_required
def resource_status(rid):
    st = request.form.get("status") or request.json.get("status") if request.is_json else request.form.get("status")
    okk, err = ps.set_status(rid, st)
    if request.is_json or _is_api_request():
        return jsonify({"ok": okk, "error": err})
    flash("状态已更新" if okk else f"失败：{err}", "ok" if okk else "error")
    return redirect(url_for("admin.resources"))


@bp.route("/resources/<int:rid>/feature", methods=["POST"])
@admin_required
def resource_feature(rid):
    val = request.form.get("value", "1") in ("1", "true", "on")
    ps.toggle_featured(rid, val)
    return jsonify({"ok": True, "featured": val})


@bp.route("/resources/<int:rid>/check", methods=["POST"])
@admin_required
def resource_check(rid):
    res = ps.resource_path_status(rid)
    if res is None:
        return jsonify({"ok": False, "error": "not_found"}), 404
    okk, why = res
    return jsonify({"ok": True, "path_ok": okk, "reason": why})


@bp.route("/resources/<int:rid>/delete", methods=["POST"])
@admin_required
def resource_delete(rid):
    """⚠️ 只删 Portal 记录。OpenList 目录与所有文件保持不变（规范 §10）"""
    ps.delete_resource(rid)
    weblog.info("删除门户资源记录 id=%s（未触碰任何文件）", rid)
    if request.is_json:
        return jsonify({"ok": True, "note": "已删除门户记录；文件未受影响"})
    flash("已删除门户记录（OpenList 文件未受影响）", "ok")
    return redirect(url_for("admin.resources"))


# ==================== 分类 ====================

@bp.route("/categories", methods=["GET", "POST"])
@admin_required
def admin_categories():
    if request.method == "POST":
        cid, err = ps.upsert_category(request.form, request.form.get("id") or None)
        flash("已保存" if not err else f"失败：{err}", "ok" if not err else "error")
        return redirect(url_for("admin.admin_categories"))
    return render_template("admin/categories.html", site=db.get_settings(),
                           items=ps.list_categories(public_only=False))


@bp.route("/categories/<int:cid>/delete", methods=["POST"])
@admin_required
def admin_category_delete(cid):
    ps.delete_category(cid)
    flash("分类已删除（资源未被删除，仅解绑）", "ok")
    return redirect(url_for("admin.admin_categories"))


# ==================== 标签 ====================

@bp.route("/tags")
@admin_required
def admin_tags():
    return render_template("admin/tags.html", site=db.get_settings(),
                           items=ps.list_tags(public_only=False))


@bp.route("/tags/<int:tid>/delete", methods=["POST"])
@admin_required
def admin_tag_delete(tid):
    ps.delete_tag(tid)
    flash("标签已删除", "ok")
    return redirect(url_for("admin.admin_tags"))


# ==================== 公告 ====================

@bp.route("/announcements", methods=["GET", "POST"])
@admin_required
def admin_announcements():
    if request.method == "POST":
        aid, err = ps.upsert_announcement(request.form, request.form.get("id") or None)
        flash("已保存" if not err else f"失败：{err}", "ok" if not err else "error")
        return redirect(url_for("admin.admin_announcements"))
    return render_template("admin/announcements.html", site=db.get_settings(),
                           items=ps.list_announcements(public_only=False))


@bp.route("/announcements/<int:aid>/delete", methods=["POST"])
@admin_required
def admin_announcement_delete(aid):
    ps.delete_announcement(aid)
    flash("公告已删除", "ok")
    return redirect(url_for("admin.admin_announcements"))


# ==================== 首页与站点设置 ====================

@bp.route("/site", methods=["GET", "POST"])
@admin_required
def admin_site():
    if request.method == "POST":
        if request.form.get("_form") == "modules":
            payload = []
            for m in db.get_modules():
                k = m["module_key"]
                payload.append({"module_key": k,
                                "enabled": request.form.get(f"mod_{k}") == "on",
                                "sort_order": int(request.form.get(f"ord_{k}") or 100)})
            ps.set_modules(payload)
            flash("首页模块已保存", "ok")
        else:
            ps.update_site_settings(request.form)
            flash("站点设置已保存", "ok")
        return redirect(url_for("admin.admin_site"))
    return render_template("admin/site.html", site=db.get_settings(),
                           modules=ps.modules())


# ==================== 凭据配置 ====================

@bp.route("/credentials", methods=["GET", "POST"])
@admin_required
def admin_credentials():
    if request.method == "POST":
        u = (request.form.get("ol_user") or "").strip()
        p = request.form.get("ol_pass") or ""
        try:
            import sys
            sys.path.insert(0, r"D:\web")
            import crypto_manager as cm
            if u:
                cm.secret_set(config.CRED_OPENLIST_USER, u)
            if p:
                cm.secret_set(config.CRED_OPENLIST_PASS, p)
            flash("凭据已存入 Windows 凭据管理器（不落盘）", "ok")
        except Exception:
            flash("写入凭据管理器失败", "error")
        return redirect(url_for("admin.admin_credentials"))
    ok_creds = ol.credentials_configured()
    ol_ok, code = ol.health()
    return render_template("admin/credentials.html", site=db.get_settings(),
                           ok_creds=ok_creds, ol_ok=ol_ok, ol_code=code)
