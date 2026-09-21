# -*- coding: utf-8 -*-
"""portal_service.py — 门户业务逻辑（规范 §五十二）

硬约束：
  §10  delete_resource 只删 DB 记录，**绝不删除 OpenList 目录或任何文件**
  §14  公开查询一律强制 status='published'
  §9   一个资源 = 一个 openlist_path（唯一约束）
  §15  排序：is_featured DESC, sort_order ASC, updated_at DESC
  §56  发布前校验 OpenList 路径存在且是目录
"""
import re
import time

import db
from services import openlist_service

PUBLIC_STATUS = "published"
VALID_STATUS = ("draft", "published", "hidden", "archived")


def _slugify(text, fallback="item"):
    s = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "-", (text or "").lower()).strip("-")
    return s or fallback


# ==================== 公开文件层地址换算 ====================
# 背景：公开文件层（fs.*）的匿名 guest 被收窄在 base_path（如 /quark/公开分享），
#      其客户端路径是**相对 base_path** 的。若直接拼绝对路径会触发双重前缀 → 500。
# 因此门户必须知道"公开层的根对应哪个 OpenList 路径"，生成链接时把该前缀去掉。

def public_root():
    """公开文件层根目录对应的 OpenList 逻辑路径（不含结尾斜杠）"""
    return (db.get_settings().get("openlist_public_root") or "").rstrip("/")


def path_in_public_root(openlist_path):
    """该资源路径是否落在公开层范围内（否则匿名用户点不开）"""
    root = public_root()
    if not root:
        return True
    p = "/" + (openlist_path or "").lstrip("/")
    return p == root or p.startswith(root + "/")


def public_file_url(openlist_path):
    """生成匿名可访问的公开文件层 URL。
    在公开根之下 → 去掉前缀；不在其下 → 返回完整路径（可能不可达，后台会告警）。"""
    s = db.get_settings()
    base = (s.get("openlist_public_base") or "").rstrip("/")
    root = (s.get("openlist_public_root") or "").rstrip("/")
    p = "/" + (openlist_path or "").lstrip("/")
    if root and (p == root or p.startswith(root + "/")):
        rel = p[len(root):].strip("/")
        return base + ("/" + rel if rel else "")
    return base + p


# ==================== 站点设置 / 首页模块 ====================

def site_settings():
    return db.get_settings()


def update_site_settings(items: dict):
    allow = {"site_title", "site_description", "hero_title", "hero_subtitle", "hero_text",
             "logo", "favicon", "main_color", "footer", "seo_keywords", "seo_description",
             "openlist_public_base", "openlist_public_root"}
    for k, v in (items or {}).items():
        if k in allow:
            db.set_setting(k, str(v)[:2000])


def modules():
    return db.get_modules()


def set_modules(payload):
    """payload: [{module_key, enabled, sort_order}]"""
    with db.tx() as con:
        for m in payload or []:
            key = m.get("module_key")
            if not key:
                continue
            con.execute("UPDATE homepage_modules SET enabled=?, sort_order=? WHERE module_key=?",
                        (1 if m.get("enabled") else 0, int(m.get("sort_order") or 100), key))


# ==================== 分类 ====================

def list_categories(public_only=True):
    sql = "SELECT * FROM categories"
    if public_only:
        sql += " WHERE status='published'"
    sql += " ORDER BY sort_order, id"
    with db.ro() as con:
        rows = [dict(r) for r in con.execute(sql)]
    for r in rows:
        r["resource_count"] = count_resources(category_id=r["id"], public_only=public_only)
    return rows


def upsert_category(data, cid=None):
    ts = db.now()
    name = (data.get("name") or "").strip()
    if not name:
        return None, "name_required"
    slug = _slugify(data.get("slug") or name, "cat")
    with db.tx() as con:
        try:
            if cid:
                con.execute(
                    "UPDATE categories SET name=?,slug=?,description=?,icon=?,color=?,"
                    "sort_order=?,status=?,updated_at=? WHERE id=?",
                    (name, slug, data.get("description", ""), data.get("icon", ""),
                     data.get("color", ""), int(data.get("sort_order") or 100),
                     data.get("status") or "published", ts, cid))
                return cid, None
            cur = con.execute(
                "INSERT INTO categories(name,slug,description,icon,color,sort_order,status,"
                "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (name, slug, data.get("description", ""), data.get("icon", ""),
                 data.get("color", ""), int(data.get("sort_order") or 100),
                 data.get("status") or "published", ts, ts))
            return cur.lastrowid, None
        except Exception as e:
            return None, f"slug_conflict ({e})"


def delete_category(cid):
    """只删分类；资源通过外键 SET NULL 解绑，不删资源、不碰文件"""
    with db.tx() as con:
        con.execute("DELETE FROM categories WHERE id=?", (cid,))
    return True, None


# ==================== 标签 ====================

def list_tags(public_only=True):
    if public_only:
        sql = ("SELECT t.*, COUNT(rt.resource_id) AS resource_count FROM tags t "
               "JOIN resource_tags rt ON rt.tag_id=t.id "
               "JOIN resources r ON r.id=rt.resource_id AND r.status='published' "
               "GROUP BY t.id ORDER BY resource_count DESC, t.name")
    else:
        sql = ("SELECT t.*, COUNT(rt.resource_id) AS resource_count FROM tags t "
               "LEFT JOIN resource_tags rt ON rt.tag_id=t.id GROUP BY t.id ORDER BY t.name")
    with db.ro() as con:
        return [dict(r) for r in con.execute(sql)]


def _ensure_tag(con, name):
    name = (name or "").strip()
    if not name:
        return None
    slug = _slugify(name, "tag")
    row = con.execute("SELECT id FROM tags WHERE slug=? OR name=?", (slug, name)).fetchone()
    if row:
        return row["id"]
    cur = con.execute("INSERT INTO tags(name,slug) VALUES(?,?)", (name, slug))
    return cur.lastrowid


def delete_tag(tid):
    with db.tx() as con:
        con.execute("DELETE FROM tags WHERE id=?", (tid,))
    return True, None


# ==================== 资源 ====================

def _resource_row(con, rid, with_tags=True):
    r = con.execute("SELECT * FROM resources WHERE id=?", (rid,)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["tags"] = [dict(x) for x in con.execute(
        "SELECT t.id,t.name,t.slug FROM tags t JOIN resource_tags rt ON rt.tag_id=t.id "
        "WHERE rt.resource_id=? ORDER BY t.name", (rid,))]
    cat = con.execute("SELECT id,name,slug,icon,color FROM categories WHERE id=?",
                      (d.get("category_id"),)).fetchone()
    d["category"] = dict(cat) if cat else None
    return d


def list_resources(public_only=True, category_id=None, tag_id=None, featured=None,
                   status=None, q=None, limit=50, offset=0, order="default"):
    where, args = [], []
    if public_only:
        where.append("r.status='published'")
    elif status:
        where.append("r.status=?")
        args.append(status)
    if category_id:
        where.append("r.category_id=?")
        args.append(category_id)
    if featured is not None:
        where.append("r.is_featured=?")
        args.append(1 if featured else 0)
    if tag_id:
        where.append("EXISTS(SELECT 1 FROM resource_tags rt WHERE rt.resource_id=r.id AND rt.tag_id=?)")
        args.append(tag_id)
    if q:
        like = f"%{q}%"
        where.append("(r.title LIKE ? OR r.description LIKE ? OR r.openlist_path LIKE ? "
                     "OR EXISTS(SELECT 1 FROM resource_tags rt JOIN tags t ON t.id=rt.tag_id "
                     "WHERE rt.resource_id=r.id AND t.name LIKE ?) "
                     "OR EXISTS(SELECT 1 FROM categories c WHERE c.id=r.category_id AND c.name LIKE ?))")
        args += [like, like, like, like, like]

    order_sql = {
        "default": "r.is_featured DESC, r.sort_order, r.updated_at DESC",
        "latest": "r.updated_at DESC",
        "popular": "r.portal_click_count DESC, r.view_count DESC",
        "created": "r.created_at DESC",
    }.get(order, "r.is_featured DESC, r.sort_order, r.updated_at DESC")

    sql = ("SELECT r.* FROM resources r"
           + (" WHERE " + " AND ".join(where) if where else "")
           + f" ORDER BY {order_sql} LIMIT ? OFFSET ?")
    cnt = ("SELECT COUNT(*) n FROM resources r"
           + (" WHERE " + " AND ".join(where) if where else ""))
    with db.ro() as con:
        rows = [dict(x) for x in con.execute(sql, (*args, limit, offset))]
        total = con.execute(cnt, tuple(args)).fetchone()["n"]
        for d in rows:
            d["tags"] = [dict(x) for x in con.execute(
                "SELECT t.id,t.name,t.slug FROM tags t JOIN resource_tags rt ON rt.tag_id=t.id "
                "WHERE rt.resource_id=? ORDER BY t.name", (d["id"],))]
            cat = con.execute("SELECT id,name,slug,icon,color FROM categories WHERE id=?",
                              (d.get("category_id"),)).fetchone()
            d["category"] = dict(cat) if cat else None
    return {"total": total, "items": rows}


def get_resource(rid, public_only=True):
    with db.ro() as con:
        d = _resource_row(con, rid)
    if not d:
        return None
    if public_only and d["status"] != PUBLIC_STATUS:
        return None                    # 非 published 一律当作不存在（§14）
    return d


def count_resources(category_id=None, public_only=True, status=None):
    where, args = [], []
    if public_only:
        where.append("status='published'")
    elif status:
        where.append("status=?")
        args.append(status)
    if category_id:
        where.append("category_id=?")
        args.append(category_id)
    sql = "SELECT COUNT(*) n FROM resources" + (" WHERE " + " AND ".join(where) if where else "")
    with db.ro() as con:
        return con.execute(sql, tuple(args)).fetchone()["n"]


def save_resource(data, rid=None):
    """新增/更新资源。**不触碰 OpenList**（除发布时的路径校验）。"""
    ts = db.now()
    title = (data.get("title") or "").strip()
    path = (data.get("openlist_path") or "").strip()
    if not title:
        return None, "title_required"
    if not path.startswith("/") or ".." in path or "\\" in path or ":" in path:
        return None, "openlist_path_invalid"      # §三 只认 OpenList 逻辑路径

    status = data.get("status") or "draft"
    if status not in VALID_STATUS:
        return None, "status_invalid"
    if status == "published":                     # §56 发布前校验
        ok, why = openlist_service.directory_exists(path)
        if not ok:
            return None, f"openlist_path_unusable:{why}"

    slug = _slugify(data.get("slug") or title, "res")
    tags = data.get("tags") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in re.split(r"[,，\s]+", tags) if t.strip()]

    with db.tx() as con:
        try:
            if rid:
                con.execute(
                    "UPDATE resources SET title=?,slug=?,description=?,openlist_path=?,cover=?,"
                    "category_id=?,status=?,is_featured=?,sort_order=?,version=?,author=?,"
                    "updated_at=?,published_at=CASE WHEN ?='published' AND published_at IS NULL "
                    "THEN ? ELSE published_at END, path_ok=1, path_checked_at=? "
                    "WHERE id=?",
                    (title, slug, data.get("description", ""), path, data.get("cover", ""),
                     data.get("category_id") or None, status,
                     1 if data.get("is_featured") else 0,
                     int(data.get("sort_order") or 100), data.get("version", ""),
                     data.get("author", ""), ts, status, ts, ts, rid))
                target = rid
            else:
                cur = con.execute(
                    "INSERT INTO resources(title,slug,description,openlist_path,cover,"
                    "category_id,status,is_featured,sort_order,version,author,path_ok,"
                    "path_checked_at,created_at,updated_at,published_at)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (title, slug, data.get("description", ""), path, data.get("cover", ""),
                     data.get("category_id") or None, status,
                     1 if data.get("is_featured") else 0,
                     int(data.get("sort_order") or 100), data.get("version", ""),
                     data.get("author", ""), 1, ts, ts, ts,
                     ts if status == "published" else None))
                target = cur.lastrowid

            con.execute("DELETE FROM resource_tags WHERE resource_id=?", (target,))
            for t in tags:
                tid = _ensure_tag(con, t)
                if tid:
                    con.execute("INSERT OR IGNORE INTO resource_tags(resource_id,tag_id)"
                                " VALUES(?,?)", (target, tid))
            return target, None
        except Exception as e:
            msg = str(e)
            if "UNIQUE" in msg and "openlist_path" in msg:
                return None, "openlist_path_already_bound"      # §9 一个目录只能绑一个资源
            if "UNIQUE" in msg and "slug" in msg:
                return None, "slug_conflict"
            return None, f"db_error:{msg[:120]}"


def set_status(rid, status):
    if status not in VALID_STATUS:
        return False, "status_invalid"
    with db.ro() as con:
        r = con.execute("SELECT openlist_path FROM resources WHERE id=?", (rid,)).fetchone()
    if not r:
        return False, "not_found"
    if status == "published":                           # §56 发布前校验
        ok, why = openlist_service.directory_exists(r["openlist_path"])
        if not ok:
            return False, f"openlist_path_unusable:{why}"
    ts = db.now()
    with db.tx() as con:
        con.execute(
            "UPDATE resources SET status=?,updated_at=?,"
            "published_at=CASE WHEN ?='published' AND published_at IS NULL THEN ? "
            "ELSE published_at END WHERE id=?", (status, ts, status, ts, rid))
    return True, None


def toggle_featured(rid, value):
    with db.tx() as con:
        con.execute("UPDATE resources SET is_featured=?,updated_at=? WHERE id=?",
                    (1 if value else 0, db.now(), rid))
    return True, None


def delete_resource(rid):
    """⚠️ 只删 Portal DB 记录。
    绝不调用 OpenList 的删除接口，绝不触碰任何文件（规范 §10）。"""
    with db.tx() as con:
        con.execute("DELETE FROM resources WHERE id=?", (rid,))   # resource_tags 级联
    return True, None


def resource_path_status(rid):
    """运行时校验（§56/场景6）：目录是否仍可用。只更新标记，不改发布状态。"""
    with db.ro() as con:
        r = con.execute("SELECT openlist_path FROM resources WHERE id=?", (rid,)).fetchone()
    if not r:
        return None
    ok, why = openlist_service.directory_exists(r["openlist_path"])
    with db.tx() as con:
        con.execute("UPDATE resources SET path_ok=?,path_checked_at=?,path_check_msg=?"
                    " WHERE id=?", (1 if ok else 0, db.now(), why, rid))
    return ok, why


def bump_view(rid):
    try:
        with db.tx() as con:
            con.execute("UPDATE resources SET view_count=view_count+1 WHERE id=?", (rid,))
    except Exception:
        pass


def bump_click(rid):
    """门户侧点击计数。**不是真实下载次数**（规范 §28）"""
    try:
        with db.tx() as con:
            con.execute("UPDATE resources SET portal_click_count=portal_click_count+1"
                        " WHERE id=?", (rid,))
    except Exception:
        pass


# ==================== 公告 ====================

def list_announcements(public_only=True):
    sql = "SELECT * FROM announcements"
    if public_only:
        sql += " WHERE status='published'"
    sql += " ORDER BY pinned DESC, sort_order, COALESCE(published_at, created_at) DESC"
    with db.ro() as con:
        return [dict(r) for r in con.execute(sql)]


def upsert_announcement(data, aid=None):
    ts = db.now()
    title = (data.get("title") or "").strip()
    if not title:
        return None, "title_required"
    with db.tx() as con:
        if aid:
            con.execute("UPDATE announcements SET title=?,content=?,status=?,pinned=?,"
                        "sort_order=?,updated_at=? WHERE id=?",
                        (title, data.get("content", ""), data.get("status") or "published",
                         1 if data.get("pinned") else 0,
                         int(data.get("sort_order") or 100), ts, aid))
            return aid, None
        cur = con.execute("INSERT INTO announcements(title,content,status,pinned,sort_order,"
                          "published_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                          (title, data.get("content", ""), data.get("status") or "published",
                           1 if data.get("pinned") else 0,
                           int(data.get("sort_order") or 100), ts, ts, ts))
        return cur.lastrowid, None


def delete_announcement(aid):
    with db.tx() as con:
        con.execute("DELETE FROM announcements WHERE id=?", (aid,))
    return True, None


# ==================== 首页聚合 ====================

def home_payload():
    mods = [m for m in db.get_modules(only_enabled=True)]
    keys = [m["module_key"] for m in mods]
    out = {"modules": [{"key": m["module_key"], "title": m["title"],
                        "order": m["sort_order"]} for m in mods]}
    if "categories" in keys:
        out["categories"] = list_categories(public_only=True)
    if "announcement" in keys:
        out["announcements"] = list_announcements(public_only=True)[:5]
    if "featured" in keys:
        out["featured"] = list_resources(public_only=True, featured=True, limit=8)["items"]
    if "latest" in keys:
        out["latest"] = list_resources(public_only=True, limit=10, order="latest")["items"]
    if "popular" in keys:
        out["popular"] = list_resources(public_only=True, limit=6, order="popular")["items"]
    if "tags" in keys:
        out["tags"] = list_tags(public_only=True)[:24]
    return out


def dashboard_stats():
    with db.ro() as con:
        g = lambda s: con.execute(s).fetchone()["n"]
        return {
            "resources_total": g("SELECT COUNT(*) n FROM resources"),
            "published": g("SELECT COUNT(*) n FROM resources WHERE status='published'"),
            "draft": g("SELECT COUNT(*) n FROM resources WHERE status='draft'"),
            "hidden": g("SELECT COUNT(*) n FROM resources WHERE status='hidden'"),
            "archived": g("SELECT COUNT(*) n FROM resources WHERE status='archived'"),
            "featured": g("SELECT COUNT(*) n FROM resources WHERE is_featured=1"),
            "categories": g("SELECT COUNT(*) n FROM categories"),
            "tags": g("SELECT COUNT(*) n FROM tags"),
            "views": con.execute("SELECT COALESCE(SUM(view_count),0) n FROM resources").fetchone()["n"],
            "clicks": con.execute("SELECT COALESCE(SUM(portal_click_count),0) n"
                                  " FROM resources").fetchone()["n"],
            "path_broken": g("SELECT COUNT(*) n FROM resources WHERE path_ok=0"),
        }
