"""/api/profiles: a person's sets of books. Switching lives at /api/auth/me/profile."""
from flask import Blueprint, g, jsonify, session

import db
import profile_move
import profiles
from auth import login_required
from scope import current_profile_id
from util import api_error, audit, json_body, parse_int_list, rows_json, to_int

bp = Blueprint("profiles", __name__, url_prefix="/api/profiles")


def _customer():
    return session.get("role") == "user"


@bp.get("")
@login_required
def list_profiles():
    return jsonify(rows_json(profiles.list_for(session["user_id"])))


@bp.post("")
@login_required
def create_profile():
    if not _customer():
        return api_error("Admin accounts have no profiles", 403)
    uid = session["user_id"]
    data = json_body()
    try:
        f = profiles.clean_fields(data)
        copy_from = to_int(data.get("copy_from"), "copy_from") if data.get("start") == "copy" else None
    except (profiles.ProfileError, ValueError) as e:
        return api_error(str(e))
    if profiles.name_taken(uid, f["name"]):
        return api_error("You already have a profile with that name")
    if copy_from is not None and not profiles.owned(uid, copy_from):
        return api_error("Profile to copy from not found", 404)
    with db.transaction() as conn:
        pid = profiles.create(conn, uid, f["name"], f["kind"], f["color"], taxonomy=False if copy_from else None)
        if copy_from:
            profiles.copy_setup(conn, uid, copy_from, pid)
    audit("profile.create", {"id": pid, "kind": f["kind"], "copied_from": copy_from})
    return jsonify(profiles.owned(uid, pid)), 201


@bp.put("/order")
@login_required
def reorder():
    uid = session["user_id"]
    ids = parse_int_list(json_body().get("ids"))
    with db.transaction():
        for i, pid in enumerate(ids):
            db.execute("UPDATE profiles SET sort_order = %s, updated_at = now() WHERE id = %s AND user_id = %s",
                       (i, pid, uid), commit=False)
    return jsonify(rows_json(profiles.list_for(uid)))


@bp.put("/<int:profile_id>")
@login_required
def update_profile(profile_id):
    uid = session["user_id"]
    existing = profiles.owned(uid, profile_id)
    if not existing:
        return api_error("Profile not found", 404)
    try:
        f = {**existing, **profiles.clean_fields(json_body(), existing)}
    except profiles.ProfileError as e:
        return api_error(str(e))
    if profiles.name_taken(uid, f["name"], exclude_id=profile_id):
        return api_error("You already have a profile with that name")
    db.execute("UPDATE profiles SET name = %s, kind = %s, color = %s, updated_at = now() WHERE id = %s AND user_id = %s",
               (f["name"], f["kind"], f["color"], profile_id, uid))
    audit("profile.update", {"id": profile_id})
    return jsonify(profiles.owned(uid, profile_id))


@bp.delete("/<int:profile_id>")
@login_required
def delete_profile(profile_id):
    """The client asks the person to type the profile's name; it comes back as `confirm`."""
    uid = session["user_id"]
    existing = profiles.owned(uid, profile_id)
    if not existing:
        return api_error("Profile not found", 404)
    if " ".join(str(json_body().get("confirm") or "").split()).lower() != existing["name"].lower():
        return api_error("Type the profile's name to delete it")
    try:
        result = profiles.delete(uid, profile_id)
    except profiles.ProfileError as e:
        return api_error(str(e), e.status)
    if g.get("profile_id") == profile_id or session.get("profile_id") == profile_id:
        session["profile_id"] = g.profile_id = result["next_profile_id"]
    audit("profile.delete", {"id": profile_id, **{k: v for k, v in result.items() if k != "next_profile_id"}})
    return jsonify({"ok": True, **result, "active_profile_id": session.get("profile_id")})


def _move_args(profile_id):
    uid = session["user_id"]
    src = current_profile_id()
    if profile_id == src or not profiles.owned(uid, profile_id):
        raise profiles.ProfileError("Choose another of your profiles to move to", 404 if profile_id != src else 400)
    data = json_body()
    account_ids = parse_int_list(data.get("account_ids"))
    if not account_ids:
        raise profiles.ProfileError("Choose the accounts to move")
    renames = data.get("rename") if isinstance(data.get("rename"), dict) else {}
    try:
        renames = {int(k): v for k, v in renames.items()}
    except (TypeError, ValueError):
        raise profiles.ProfileError("rename maps account ids to new names") from None
    return uid, src, account_ids, renames


@bp.post("/<int:profile_id>/move-preview")
@login_required
def move_preview(profile_id):
    """What moving these accounts of the active profile into profile_id would do."""
    try:
        uid, src, account_ids, renames = _move_args(profile_id)
        return jsonify(profile_move.preview(uid, src, profile_id, account_ids, renames))
    except profiles.ProfileError as e:
        return api_error(str(e), e.status)


@bp.post("/<int:profile_id>/move")
@login_required
def move_accounts(profile_id):
    try:
        uid, src, account_ids, renames = _move_args(profile_id)
        return jsonify(profile_move.move(uid, src, profile_id, account_ids, renames))
    except profiles.ProfileError as e:
        return api_error(str(e), e.status)
