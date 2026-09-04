from flask import Blueprint, jsonify, request, session

import db
from auth import login_required
from util import api_error, audit, rows_json

bp = Blueprint("accounts", __name__, url_prefix="/api/accounts")

ACCOUNT_TYPES = ("checking", "savings", "credit_card", "line_of_credit", "loan", "investment", "cash", "other")
CURRENCIES = ("USD", "CAD")

LIST_SQL = """
SELECT a.id, a.name, a.institution, a.account_type, a.currency, a.last4, a.color, a.is_active,
       a.created_at, a.updated_at,
       COALESCE(s.txn_count, 0) AS txn_count,
       s.last_txn_date,
       s.first_txn_date,
       (SELECT t.balance FROM transactions t WHERE t.account_id = a.id AND t.balance IS NOT NULL
          ORDER BY t.txn_date DESC, t.id DESC LIMIT 1) AS balance,
       (SELECT MAX(st.created_at) FROM statements st WHERE st.account_id = a.id AND st.status = 'committed') AS last_import_at
FROM accounts a
LEFT JOIN (
  SELECT account_id, COUNT(*) AS txn_count, MAX(txn_date) AS last_txn_date, MIN(txn_date) AS first_txn_date
  FROM transactions GROUP BY account_id
) s ON s.account_id = a.id
WHERE a.user_id = %s
"""


def _validate(data, existing=None):
    name = (data.get("name") if "name" in data else (existing or {}).get("name") or "").strip()
    if not name:
        raise ValueError("Account name is required")
    account_type = data.get("account_type") or (existing or {}).get("account_type") or "checking"
    if account_type not in ACCOUNT_TYPES:
        raise ValueError("Invalid account type")
    currency = (data.get("currency") or (existing or {}).get("currency") or "USD").upper()
    if currency not in CURRENCIES:
        raise ValueError("Currency must be USD or CAD")
    last4 = (data.get("last4") if "last4" in data else (existing or {}).get("last4")) or ""
    last4 = "".join(ch for ch in str(last4) if ch.isdigit())[-4:] or None
    color = data.get("color") or (existing or {}).get("color") or "c1"
    institution = (data.get("institution") if "institution" in data else (existing or {}).get("institution")) or None
    return {
        "name": name, "account_type": account_type, "currency": currency,
        "last4": last4, "color": color, "institution": institution,
    }


@bp.get("")
@login_required
def list_accounts():
    sql = LIST_SQL
    if request.args.get("all") != "1":
        sql += " AND a.is_active"
    rows = db.query(sql + " ORDER BY a.is_active DESC, a.name", (session["user_id"],))
    return jsonify(rows_json(rows))


@bp.get("/institutions")
@login_required
def institutions():
    import bank_profiles
    return jsonify([{"key": p.key, "label": p.label, "country": p.country} for p in bank_profiles.all_profiles()])


@bp.post("")
@login_required
def create_account():
    data = request.get_json(silent=True) or {}
    try:
        f = _validate(data)
    except ValueError as e:
        return api_error(str(e))
    dup = db.query("SELECT id FROM accounts WHERE user_id = %s AND lower(name) = lower(%s)",
                   (session["user_id"], f["name"]), one=True)
    if dup:
        return api_error("An account with that name already exists")
    row = db.execute(
        """INSERT INTO accounts (user_id, name, institution, account_type, currency, last4, color)
           VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id""",
        (session["user_id"], f["name"], f["institution"], f["account_type"], f["currency"], f["last4"], f["color"]),
        returning=True,
    )
    audit("account.create", {"id": row["id"], "name": f["name"]})
    return jsonify({"id": row["id"], **f}), 201


@bp.put("/<int:account_id>")
@login_required
def update_account(account_id):
    existing = db.query("SELECT * FROM accounts WHERE id = %s AND user_id = %s",
                        (account_id, session["user_id"]), one=True)
    if not existing:
        return api_error("Account not found", 404)
    data = request.get_json(silent=True) or {}
    try:
        f = _validate(data, existing)
    except ValueError as e:
        return api_error(str(e))
    is_active = bool(data.get("is_active", existing["is_active"]))
    db.execute(
        """UPDATE accounts SET name=%s, institution=%s, account_type=%s, currency=%s, last4=%s, color=%s,
                               is_active=%s, updated_at=now() WHERE id=%s""",
        (f["name"], f["institution"], f["account_type"], f["currency"], f["last4"], f["color"], is_active, account_id),
    )
    if f["currency"] != existing["currency"]:
        db.execute("UPDATE transactions SET currency = %s WHERE account_id = %s", (f["currency"], account_id))
    audit("account.update", {"id": account_id})
    return jsonify({"ok": True})


@bp.delete("/<int:account_id>")
@login_required
def delete_account(account_id):
    existing = db.query("SELECT id FROM accounts WHERE id = %s AND user_id = %s",
                        (account_id, session["user_id"]), one=True)
    if not existing:
        return api_error("Account not found", 404)
    count = db.query("SELECT COUNT(*) AS n FROM transactions WHERE account_id = %s", (account_id,), one=True)["n"]
    if count and request.args.get("force") != "true":
        return api_error(f"This account has {count} transactions. Archive it instead, or delete with force.", 409)
    import importer
    statements = db.query("SELECT * FROM statements WHERE account_id = %s AND user_id = %s",
                          (account_id, session["user_id"])) or []
    for st in statements:
        importer.discard_statement(st, delete_transactions=True)
    db.execute("DELETE FROM accounts WHERE id = %s", (account_id,))
    audit("account.delete", {"id": account_id, "transactions": count, "statements": len(statements)})
    return jsonify({"ok": True, "deleted_transactions": count, "deleted_statements": len(statements)})
