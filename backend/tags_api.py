"""/api/tags: the user's labels. Attaching tags to transactions happens through /api/transactions
(PUT {tag_ids}, bulk tag/untag) so the row stays the single source of truth."""
from flask import Blueprint, jsonify, session

import db
from auth import login_required
from util import api_error, audit, json_body, next_color_slot

bp = Blueprint("tags", __name__, url_prefix="/api/tags")

COLOR_SLOTS = {f"c{i}" for i in range(1, 13)}
NAME_MAX = 40
FIELDS = "g.id, g.name, g.color, g.sort_order, g.created_at"


def _uid():
    return session["user_id"]


def _row(tag_id):
    return db.query(
        f"""SELECT {FIELDS}, (SELECT COUNT(*) FROM transaction_tags tt WHERE tt.tag_id = g.id) AS txn_count
            FROM tags g WHERE g.id = %s AND g.user_id = %s""",
        (tag_id, _uid()), one=True,
    )


def _json(r):
    return {"id": r["id"], "name": r["name"], "color": r["color"], "sort_order": r["sort_order"], "txn_count": r.get("txn_count", 0),
            "created_at": r["created_at"].isoformat() if r.get("created_at") else None}


def _clean_name(value):
    name = " ".join(str(value or "").split())
    if not name:
        return None, "Name is required"
    if len(name) > NAME_MAX:
        return None, f"Name must be at most {NAME_MAX} characters"
    return name, None


def _taken(uid, name, exclude_id=None):
    row = db.query("SELECT id FROM tags WHERE user_id = %s AND lower(name) = lower(%s)", (uid, name), one=True)
    return bool(row) and row["id"] != exclude_id


@bp.get("")
@login_required
def list_tags():
    rows = db.query(
        f"""SELECT {FIELDS}, COALESCE(n.c, 0) AS txn_count
            FROM tags g LEFT JOIN (SELECT tag_id, COUNT(*) AS c FROM transaction_tags GROUP BY tag_id) n ON n.tag_id = g.id
            WHERE g.user_id = %s ORDER BY g.sort_order, lower(g.name)""",
        (_uid(),),
    ) or []
    return jsonify([_json(r) for r in rows])


@bp.post("")
@login_required
def create_tag():
    uid = _uid()
    data = json_body()
    name, problem = _clean_name(data.get("name"))
    if problem:
        return api_error(problem)
    if _taken(uid, name):
        return api_error("A tag with that name already exists")
    color = data.get("color") or next_color_slot(uid, "tags")
    if color not in COLOR_SLOTS:
        return api_error("Invalid color slot")
    row = db.execute("INSERT INTO tags (user_id, name, color) VALUES (%s, %s, %s) RETURNING id", (uid, name, color), returning=True)
    audit("tag.create", {"id": row["id"], "name": name})
    return jsonify(_json(_row(row["id"]))), 201


@bp.put("/<int:tag_id>")
@login_required
def update_tag(tag_id):
    uid = _uid()
    row = _row(tag_id)
    if not row:
        return api_error("Tag not found", 404)
    data = json_body()
    name = row["name"]
    if "name" in data:
        name, problem = _clean_name(data.get("name"))
        if problem:
            return api_error(problem)
        if _taken(uid, name, exclude_id=tag_id):
            return api_error("A tag with that name already exists")
    color = data.get("color") or row["color"]
    if color not in COLOR_SLOTS:
        return api_error("Invalid color slot")
    db.execute("UPDATE tags SET name = %s, color = %s WHERE id = %s AND user_id = %s", (name, color, tag_id, uid))
    audit("tag.update", {"id": tag_id, "name": name, "color": color})
    return jsonify(_json(_row(tag_id)))


@bp.delete("/<int:tag_id>")
@login_required
def delete_tag(tag_id):
    uid = _uid()
    row = _row(tag_id)
    if not row:
        return api_error("Tag not found", 404)
    removed = int(row.get("txn_count") or 0)
    db.execute("DELETE FROM tags WHERE id = %s AND user_id = %s", (tag_id, uid))
    audit("tag.delete", {"id": tag_id, "name": row["name"], "removed_from": removed})
    return jsonify({"ok": True, "removed_from": removed})
