"""Column mappings remembered per file layout: list them, forget one."""
from flask import Blueprint, jsonify, request

import layout_memory
from auth import login_required
from scope import current_profile_id
from util import api_error, audit, rows_json

bp = Blueprint("import_layouts", __name__, url_prefix="/api/import-layouts")


@bp.get("")
@login_required
def list_layouts():
    return jsonify(rows_json(layout_memory.list_for(current_profile_id())))


@bp.delete("/<int:layout_id>")
@login_required
def forget_layout(layout_id):
    every_account = request.args.get("scope") == "layout"
    if not layout_memory.forget(current_profile_id(), layout_id, every_account=every_account):
        return api_error("Saved layout not found", 404)
    audit("layout.forget", {"id": layout_id, "every_account": every_account})
    return jsonify({"ok": True})
