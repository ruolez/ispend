"""/api/budgets: per-category monthly budgets and their progress. Never touches reports.py; the
progress view composes reports.by_category so existing report payloads stay byte-identical."""
import re

from flask import Blueprint, abort, jsonify, request, session

import budgets as budgets_lib
import db
import reports
from auth import login_required
from util import api_error, audit, json_body, parse_int_list, row_json, to_int, to_money

bp = Blueprint("budgets", __name__, url_prefix="/api/budgets")

MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
NOTE_MAX = 200
FIELDS = "b.id, b.category_id, b.month, b.amount, b.note, b.created_at, b.updated_at"


def _uid():
    return session["user_id"]


def _month(value):
    """'YYYY-MM' (default: the current month) -> (first day, last day)."""
    if value in (None, ""):
        y, m = reports.parse_month(None)
    else:
        if not MONTH_RE.match(str(value)):
            abort(400, "month must be YYYY-MM")
        y, m = reports.parse_month(value)
    return reports.month_bounds(y, m)


def _json(r):
    out = row_json(r)
    out["month"] = str(out["month"])[:7]
    return out


def _own(budget_id):
    return db.query(f"SELECT {FIELDS} FROM budgets b WHERE b.id = %s AND b.user_id = %s", (budget_id, _uid()), one=True)


def _note(value):
    if value in (None, ""):
        return None
    note = str(value).strip()
    if len(note) > NOTE_MAX:
        abort(400, f"note must be at most {NOTE_MAX} characters")
    return note or None


def _check_overlap(uid, cat, month):
    """A category and one of its subcategories must not both carry a budget for the same month."""
    if cat["parent_id"] is not None:
        parent = db.query("SELECT b.id, c.name FROM budgets b JOIN categories c ON c.id = b.category_id WHERE b.user_id = %s AND b.month = %s AND b.category_id = %s",
                          (uid, month, cat["parent_id"]), one=True)
        if parent:
            abort(400, f"Budget {parent['name']} or its subcategories, not both")
    else:
        child = db.query("SELECT b.id FROM budgets b JOIN categories c ON c.id = b.category_id WHERE b.user_id = %s AND b.month = %s AND c.parent_id = %s",
                         (uid, month, cat["id"]), one=True)
        if child:
            abort(400, f"Budget {cat['name']} or its subcategories, not both")


@bp.get("")
@login_required
def list_budgets():
    uid = _uid()
    start, _end = _month(request.args.get("month"))
    rows = db.query(f"SELECT {FIELDS} FROM budgets b WHERE b.user_id = %s AND b.month = %s ORDER BY b.id", (uid, start)) or []
    months = db.query("SELECT DISTINCT to_char(month, 'YYYY-MM') AS m FROM budgets WHERE user_id = %s ORDER BY m DESC LIMIT 24", (uid,)) or []
    return jsonify({"month": start.strftime("%Y-%m"), "budgets": [_json(r) for r in rows], "months_with_budgets": [r["m"] for r in months]})


@bp.post("")
@login_required
def upsert_budget():
    uid = _uid()
    data = json_body()
    category_id = to_int(data.get("category_id"), "category_id", required=True)
    start, _end = _month(data.get("month"))
    amount = to_money(data.get("amount"), "amount")
    if amount <= 0:
        return api_error("amount must be greater than zero")
    note = _note(data.get("note"))
    cat = db.query("SELECT id, name, parent_id, kind FROM categories WHERE id = %s AND user_id = %s", (category_id, uid), one=True)
    if not cat:
        return api_error("Category not found", 404)
    if cat["kind"] == "transfer":
        return api_error("Transfers cannot be budgeted")
    _check_overlap(uid, cat, start)
    row = db.execute(
        """INSERT INTO budgets (user_id, category_id, month, amount, note) VALUES (%s, %s, %s, %s, %s)
           ON CONFLICT (user_id, category_id, month) DO UPDATE SET amount = EXCLUDED.amount, note = EXCLUDED.note, updated_at = now()
           RETURNING id, (xmax = 0) AS inserted""",
        (uid, category_id, start, amount, note), returning=True,
    )
    audit("budget.create" if row and row.get("inserted") else "budget.update", {"category_id": category_id, "month": start.strftime("%Y-%m"), "amount": float(amount)})
    return jsonify(_json(_own(row["id"]))), (201 if row and row.get("inserted") else 200)


@bp.put("/<int:budget_id>")
@login_required
def update_budget(budget_id):
    row = _own(budget_id)
    if not row:
        return api_error("Budget not found", 404)
    data = json_body()
    amount = to_money(data["amount"], "amount") if "amount" in data else row["amount"]
    if amount <= 0:
        return api_error("amount must be greater than zero")
    note = _note(data.get("note")) if "note" in data else row.get("note")
    db.execute("UPDATE budgets SET amount = %s, note = %s, updated_at = now() WHERE id = %s AND user_id = %s", (amount, note, budget_id, _uid()))
    audit("budget.update", {"id": budget_id, "amount": float(amount)})
    return jsonify(_json(_own(budget_id)))


@bp.delete("/<int:budget_id>")
@login_required
def delete_budget(budget_id):
    row = _own(budget_id)
    if not row:
        return api_error("Budget not found", 404)
    db.execute("DELETE FROM budgets WHERE id = %s AND user_id = %s", (budget_id, _uid()))
    audit("budget.delete", {"id": budget_id, "category_id": row["category_id"]})
    return jsonify({"ok": True})


@bp.post("/copy")
@login_required
def copy_budgets():
    """Clone one month's budgets into another; categories already budgeted in the target are skipped."""
    uid = _uid()
    data = json_body()
    src, _ = _month(data.get("from"))
    dst, _ = _month(data.get("to"))
    if src == dst:
        return api_error("Pick two different months")
    rows = db.query("SELECT category_id, amount, note FROM budgets WHERE user_id = %s AND month = %s", (uid, src)) or []
    if not rows:
        return api_error(f"No budgets in {src.strftime('%B %Y')}")
    copied = 0
    with db.transaction():
        for r in rows:
            inserted = db.execute(
                "INSERT INTO budgets (user_id, category_id, month, amount, note) VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING RETURNING id",
                (uid, r["category_id"], dst, r["amount"], r.get("note")), returning=True, commit=False,
            )
            if inserted:
                copied += 1
    audit("budget.copy", {"from": src.strftime("%Y-%m"), "to": dst.strftime("%Y-%m"), "copied": copied})
    return jsonify({"copied": copied, "skipped": len(rows) - copied})


@bp.get("/progress")
@login_required
def progress():
    uid = _uid()
    start, end = _month(request.args.get("month"))
    account_ids = parse_int_list(request.args.get("account_id")) or None
    rows = reports.by_category(uid, start, end, "sub", account_ids)
    budgets = db.query(f"SELECT {FIELDS} FROM budgets b WHERE b.user_id = %s AND b.month = %s", (uid, start)) or []
    cats = {c["id"]: c for c in (db.query("SELECT id, name, color, icon, parent_id FROM categories WHERE user_id = %s", (uid,)) or [])}
    on = reports.today()
    elapsed = budgets_lib.elapsed_fraction(start, end, on)
    out = {"month": start.strftime("%Y-%m"), **budgets_lib.month_meta(start, end, on),
           **budgets_lib.build_progress(budgets, rows, elapsed, cats)}
    return jsonify(out)
