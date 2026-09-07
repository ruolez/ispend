import re

from flask import Blueprint, jsonify, request, session

import db
import seed_categories
from auth import login_required
from util import api_error, audit, json_body, parse_int_list, to_int

bp = Blueprint("categories", __name__, url_prefix="/api/categories")

KINDS = ("expense", "income", "transfer")
COLOR_SLOTS = {f"c{i}" for i in range(1, 13)}


def slugify(name, parent_slug=None):
    base = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "category"
    return f"{parent_slug}.{base}" if parent_slug else base


def _unique_slug(user_id, slug):
    candidate, n = slug, 2
    while db.query("SELECT 1 FROM categories WHERE user_id = %s AND slug = %s", (user_id, candidate), one=True):
        candidate = f"{slug}_{n}"
        n += 1
    return candidate


def load_tree(user_id, with_counts=True):
    rows = db.query(
        """SELECT c.id, c.parent_id, c.name, c.slug, c.kind, c.color, c.icon, c.is_system, c.sort_order,
                  COALESCE(t.n, 0) AS txn_count
           FROM categories c
           LEFT JOIN (SELECT category_id, COUNT(*) AS n FROM transactions WHERE user_id = %s GROUP BY category_id) t
             ON t.category_id = c.id
           WHERE c.user_id = %s
           ORDER BY c.sort_order, c.name""",
        (user_id, user_id),
    )
    by_id = {r["id"]: {**r, "children": []} for r in rows}
    roots = []
    for r in by_id.values():
        if r["parent_id"] and r["parent_id"] in by_id:
            by_id[r["parent_id"]]["children"].append(r)
        else:
            roots.append(r)
    return roots


@bp.get("")
@login_required
def list_categories():
    if request.args.get("flat") == "1":
        rows = db.query(
            """SELECT id, parent_id, name, slug, kind, color, icon, is_system, sort_order
               FROM categories WHERE user_id = %s ORDER BY sort_order, name""",
            (session["user_id"],),
        )
        return jsonify(rows)
    return jsonify(load_tree(session["user_id"]))


@bp.post("")
@login_required
def create_category():
    data = json_body()
    name = (data.get("name") or "").strip()
    if not name:
        return api_error("Name is required")
    uid = session["user_id"]
    parent_id = to_int(data.get("parent_id"), "Parent category")
    parent = None
    if parent_id:
        parent = db.query("SELECT * FROM categories WHERE id = %s AND user_id = %s", (parent_id, uid), one=True)
        if not parent:
            return api_error("Parent category not found", 404)
        if parent["parent_id"]:
            return api_error("Only two levels of categories are supported")
    kind = parent["kind"] if parent else (data.get("kind") or "expense")
    if kind not in KINDS:
        return api_error("Invalid kind")
    color = data.get("color") or (parent["color"] if parent else _next_color(uid))
    if color not in COLOR_SLOTS:
        return api_error("Invalid color slot")
    icon = _clean_icon(data.get("icon") or (parent["icon"] if parent else "tag"))
    if not icon:
        return api_error("Invalid icon name")
    dup = db.query(
        "SELECT id FROM categories WHERE user_id = %s AND COALESCE(parent_id, 0) = %s AND lower(name) = lower(%s)",
        (uid, parent_id or 0, name), one=True,
    )
    if dup:
        return api_error("A category with that name already exists here")
    slug = _unique_slug(uid, slugify(name, parent["slug"] if parent else None))
    order = db.query(
        "SELECT COALESCE(MAX(sort_order), -1) + 1 AS n FROM categories WHERE user_id = %s AND COALESCE(parent_id, 0) = %s",
        (uid, parent_id or 0), one=True,
    )["n"]
    row = db.execute(
        """INSERT INTO categories (user_id, parent_id, name, slug, kind, color, icon, sort_order)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
        (uid, parent_id, name, slug, kind, color, icon, order), returning=True,
    )
    audit("category.create", {"id": row["id"], "name": name})
    cat = db.query("SELECT id, parent_id, name, slug, kind, color, icon, is_system, sort_order FROM categories WHERE id = %s",
                   (row["id"],), one=True)
    return jsonify(cat), 201


ICON_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")


def _clean_icon(value):
    """Icon names are looked up on the client; only plain kebab-case identifiers are stored."""
    value = str(value or "").strip()
    return value if ICON_NAME.match(value) else None


def _count(n, noun):
    if not n:
        return None
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def _sync_transfer_flags(category_ids, new_kind, old_kind):
    """Rows filed under these categories follow a kind change to or from transfer; paired legs keep theirs."""
    if new_kind == "transfer":
        db.execute(
            """UPDATE transactions SET is_transfer = TRUE, is_excluded = TRUE, updated_at = now()
               WHERE category_id = ANY(%s) AND category_status = 'confirmed' AND NOT (is_transfer AND is_excluded)""",
            (category_ids,))
    elif old_kind == "transfer":
        db.execute(
            """UPDATE transactions SET is_transfer = FALSE, is_excluded = FALSE, updated_at = now()
               WHERE category_id = ANY(%s) AND transfer_pair_id IS NULL AND (is_transfer OR is_excluded)""",
            (category_ids,))


def _next_color(uid):
    used = db.query("SELECT color, COUNT(*) AS n FROM categories WHERE user_id = %s AND parent_id IS NULL GROUP BY color", (uid,))
    counts = {r["color"]: r["n"] for r in used}
    return min((f"c{i}" for i in range(1, 13)), key=lambda c: (counts.get(c, 0), int(c[1:])))


@bp.put("/<int:cat_id>")
@login_required
def update_category(cat_id):
    uid = session["user_id"]
    cat = db.query("SELECT * FROM categories WHERE id = %s AND user_id = %s", (cat_id, uid), one=True)
    if not cat:
        return api_error("Category not found", 404)
    data = json_body()
    name = (data.get("name") or cat["name"]).strip()
    if not name:
        return api_error("Name is required")
    color = data.get("color") or cat["color"]
    if color not in COLOR_SLOTS:
        return api_error("Invalid color slot")
    icon = _clean_icon(data.get("icon") or cat["icon"] or "tag")
    if not icon:
        return api_error("Invalid icon name")
    kind = data.get("kind") or cat["kind"]
    if kind not in KINDS:
        return api_error("Invalid kind")
    parent_id = to_int(data.get("parent_id", cat["parent_id"]), "Parent category")
    if parent_id == cat_id:
        return api_error("A category cannot be its own parent")
    if parent_id:
        parent = db.query("SELECT id, parent_id, kind FROM categories WHERE id = %s AND user_id = %s", (parent_id, uid), one=True)
        if not parent or parent["parent_id"]:
            return api_error("Invalid parent category")
        has_children = db.query("SELECT 1 FROM categories WHERE parent_id = %s LIMIT 1", (cat_id,), one=True)
        if has_children:
            return api_error("A category with subcategories cannot be nested")
        kind = parent["kind"]
    dup = db.query(
        """SELECT id FROM categories WHERE user_id = %s AND COALESCE(parent_id, 0) = %s
           AND lower(name) = lower(%s) AND id <> %s""",
        (uid, parent_id or 0, name, cat_id), one=True,
    )
    if dup:
        return api_error("A category with that name already exists here")
    db.execute(
        "UPDATE categories SET name=%s, color=%s, icon=%s, kind=%s, parent_id=%s WHERE id=%s",
        (name, color, icon, kind, parent_id, cat_id),
    )
    if data.get("propagate_color") and not parent_id:
        db.execute("UPDATE categories SET color = %s WHERE parent_id = %s", (color, cat_id))
    if kind != cat["kind"]:
        child_ids = [r["id"] for r in db.query("SELECT id FROM categories WHERE parent_id = %s", (cat_id,)) or []]
        if child_ids:
            db.execute("UPDATE categories SET kind = %s WHERE parent_id = %s", (kind, cat_id))
        _sync_transfer_flags([cat_id, *child_ids], kind, cat["kind"])
    audit("category.update", {"id": cat_id})
    return jsonify({"ok": True})


@bp.put("/reorder")
@login_required
def reorder():
    data = json_body()
    ids = parse_int_list(data.get("ids"))
    uid = session["user_id"]
    with db.transaction():
        for i, cid in enumerate(ids):
            db.execute("UPDATE categories SET sort_order = %s WHERE id = %s AND user_id = %s", (i, cid, uid), commit=False)
    return jsonify({"ok": True})


@bp.post("/<int:cat_id>/merge")
@login_required
def merge(cat_id):
    uid = session["user_id"]
    data = json_body()
    into = to_int(data.get("into"), "Target category")
    src = db.query("SELECT * FROM categories WHERE id = %s AND user_id = %s", (cat_id, uid), one=True)
    dst = db.query("SELECT * FROM categories WHERE id = %s AND user_id = %s", (into, uid), one=True) if into else None
    if not src or not dst or src["id"] == dst["id"]:
        return api_error("Choose a different category to merge into")
    child_ids = [r["id"] for r in db.query("SELECT id FROM categories WHERE parent_id = %s", (cat_id,))]
    ids = [cat_id] + child_ids
    with db.transaction():
        moved = db.query("SELECT COUNT(*) AS n FROM transactions WHERE category_id = ANY(%s) AND user_id = %s",
                         (ids, uid), one=True, commit=False)["n"]
        _move_references(uid, ids, dst["id"])
        db.execute("DELETE FROM categories WHERE id = ANY(%s)", (ids,), commit=False)
    audit("category.merge", {"from": cat_id, "into": dst["id"], "moved": moved})
    return jsonify({"ok": True, "moved": moved})


@bp.delete("/<int:cat_id>")
@login_required
def delete_category(cat_id):
    """Refuses while anything references the category unless ?reassign_to= names the new home."""
    uid = session["user_id"]
    cat = db.query("SELECT * FROM categories WHERE id = %s AND user_id = %s", (cat_id, uid), one=True)
    if not cat:
        return api_error("Category not found", 404)
    if cat["is_system"] and str(cat["slug"] or "").startswith("transfers"):
        return api_error("Transfer categories are needed to recognise transfers; rename them instead of deleting.", 409)
    child_ids = [r["id"] for r in db.query("SELECT id FROM categories WHERE parent_id = %s", (cat_id,))]
    ids = [cat_id] + child_ids
    refs = db.query(
        """SELECT (SELECT COUNT(*) FROM transactions WHERE user_id = %s AND category_id = ANY(%s)) AS transactions,
                  (SELECT COUNT(*) FROM rules WHERE user_id = %s AND category_id = ANY(%s)) AS rules,
                  (SELECT COUNT(*) FROM merchant_memory WHERE user_id = %s AND category_id = ANY(%s)) AS merchants""",
        (uid, ids, uid, ids, uid, ids), one=True,
    )
    reassign = to_int(request.args.get("reassign_to"), "reassign_to")
    in_use = refs["transactions"] or refs["rules"] or refs["merchants"]
    if in_use and not reassign:
        parts = [_count(refs["transactions"], "transaction"), _count(refs["rules"], "rule"),
                 _count(refs["merchants"], "remembered merchant")]
        msg = ", ".join(p for p in parts if p) + " still use this category. Merge it into another category instead."
        return jsonify({"error": msg, "references": dict(refs)}), 409
    target = None
    if reassign:
        target = db.query("SELECT id FROM categories WHERE id = %s AND user_id = %s", (reassign, uid), one=True)
        if not target or target["id"] in ids:
            return api_error("Target category not found", 404)
    with db.transaction():
        if target:
            _move_references(uid, ids, target["id"])
        db.execute("DELETE FROM categories WHERE id = ANY(%s)", (ids,), commit=False)
    audit("category.delete", {"id": cat_id, "reassigned_to": target["id"] if target else None, "references": dict(refs)})
    return jsonify({"ok": True, "references": dict(refs)})


def _move_references(uid, ids, dst_id):
    """Point transactions, rules, preview rows and merchant memory at dst_id (memory duplicates merge)."""
    db.execute("UPDATE transactions SET category_id = %s WHERE category_id = ANY(%s) AND user_id = %s", (dst_id, ids, uid), commit=False)
    db.execute("UPDATE rules SET category_id = %s WHERE category_id = ANY(%s) AND user_id = %s", (dst_id, ids, uid), commit=False)
    db.execute("UPDATE import_rows SET category_id = %s WHERE category_id = ANY(%s)", (dst_id, ids), commit=False)
    db.execute("""DELETE FROM merchant_memory m WHERE m.user_id = %s AND m.category_id = ANY(%s)
                  AND EXISTS (SELECT 1 FROM merchant_memory o WHERE o.user_id = m.user_id
                              AND o.merchant_key = m.merchant_key AND o.category_id = %s
                              AND (o.times_used > m.times_used OR (o.times_used = m.times_used AND o.id < m.id)))""",
               (uid, ids, dst_id), commit=False)
    db.execute("""DELETE FROM merchant_memory o WHERE o.user_id = %s AND o.category_id = %s
                  AND EXISTS (SELECT 1 FROM merchant_memory m WHERE m.user_id = o.user_id
                              AND m.merchant_key = o.merchant_key AND m.category_id = ANY(%s))""",
               (uid, dst_id, ids), commit=False)
    db.execute("UPDATE merchant_memory SET category_id = %s WHERE category_id = ANY(%s) AND user_id = %s",
               (dst_id, ids, uid), commit=False)


@bp.post("/reset-defaults")
@login_required
def reset_defaults():
    seed_categories.seed_for_user(db.get_db(), session["user_id"])
    audit("category.reset_defaults")
    return jsonify(load_tree(session["user_id"]))
