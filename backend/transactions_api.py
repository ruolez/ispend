import base64
import csv
import io
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from flask import Blueprint, Response, jsonify, request, session

import categorizer
import config
import db
import transfers
from auth import login_required
from util import api_error, audit, parse_int_list, record_event, record_events, row_json, rows_json

bp = Blueprint("transactions", __name__, url_prefix="/api/transactions")

ITEM_FIELDS = """t.id, t.account_id, t.statement_id, t.txn_date, t.posted_date, t.amount, t.currency, t.balance,
    t.description_raw, t.description_clean, t.merchant_key, t.merchant_name,
    t.category_id, t.category_status, t.category_source, t.category_rule_id, t.category_confidence,
    t.is_transfer, t.transfer_pair_id, t.is_excluded, t.notes, t.created_at, t.updated_at"""

RANGES = ("this-month", "last-month", "last-30", "last-90", "this-year", "last-year", "all")
STATUSES = ("all", "uncategorized", "suggested", "confirmed", "transfer", "excluded")
SORTS = {
    "-date": ("t.txn_date DESC, t.id DESC", "<"),
    "date": ("t.txn_date ASC, t.id ASC", ">"),
    "-amount": ("t.amount DESC, t.id DESC", "<"),
    "amount": ("t.amount ASC, t.id ASC", ">"),
    "merchant": ("t.merchant_name ASC, t.id ASC", ">"),
}


def today():
    return datetime.now(ZoneInfo(config.APP_TIMEZONE)).date()


def range_bounds(name, ref=None):
    """Named preset -> (from, to) dates (inclusive); None means unbounded."""
    ref = ref or today()
    if name == "this-month":
        return ref.replace(day=1), None
    if name == "last-month":
        first_this = ref.replace(day=1)
        last_prev = first_this - timedelta(days=1)
        return last_prev.replace(day=1), last_prev
    if name == "last-30":
        return ref - timedelta(days=29), None
    if name == "last-90":
        return ref - timedelta(days=89), None
    if name == "this-year":
        return date(ref.year, 1, 1), None
    if name == "last-year":
        return date(ref.year - 1, 1, 1), date(ref.year - 1, 12, 31)
    return None, None


def _parse_date(value):
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _parse_decimal(value):
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def build_filters(args, user_id):
    """Query-string filters -> (sql_fragment starting with ' AND ...', params)."""
    sql, params = " t.user_id = %s", [user_id]
    account_ids = parse_int_list(args.get("account_id"))
    if account_ids:
        sql += " AND t.account_id = ANY(%s)"
        params.append(account_ids)
    raw_cats = [c.strip() for c in (args.get("category_id") or "").split(",") if c.strip()]
    if raw_cats:
        cat_ids = parse_int_list([c for c in raw_cats if c != "none"])
        parts = []
        if cat_ids:
            parts.append("t.category_id = ANY(%s) OR t.category_id IN (SELECT id FROM categories WHERE parent_id = ANY(%s))")
            params.extend([cat_ids, cat_ids])
        if "none" in raw_cats:
            parts.append("t.category_id IS NULL")
        sql += " AND (" + " OR ".join(parts) + ")"
    d_from = _parse_date(args.get("from"))
    d_to = _parse_date(args.get("to"))
    rng = args.get("range")
    if rng in RANGES and not (d_from or d_to):
        d_from, d_to = range_bounds(rng)
    if d_from:
        sql += " AND t.txn_date >= %s"
        params.append(d_from)
    if d_to:
        sql += " AND t.txn_date <= %s"
        params.append(d_to)
    q = (args.get("q") or "").strip()
    if q:
        like = f"%{q}%"
        sql += " AND (t.description_raw ILIKE %s OR t.description_clean ILIKE %s OR t.merchant_name ILIKE %s OR t.notes ILIKE %s)"
        params.extend([like, like, like, like])
    status = args.get("status") or "all"
    if status == "uncategorized":
        sql += " AND t.category_id IS NULL AND NOT t.is_transfer"
    elif status == "suggested":
        sql += " AND t.category_status = 'suggested'"
    elif status == "confirmed":
        sql += " AND t.category_status = 'confirmed'"
    elif status == "transfer":
        sql += " AND t.is_transfer"
    elif status == "excluded":
        sql += " AND t.is_excluded"
    transfers_mode = args.get("transfers") or "include"
    if transfers_mode == "exclude":
        sql += " AND NOT t.is_transfer"
    elif transfers_mode == "only":
        sql += " AND t.is_transfer"
    amin = _parse_decimal(args.get("min"))
    amax = _parse_decimal(args.get("max"))
    if amin is not None:
        sql += " AND abs(t.amount) >= %s"
        params.append(amin)
    if amax is not None:
        sql += " AND abs(t.amount) <= %s"
        params.append(amax)
    statement_id = args.get("statement_id")
    if statement_id and statement_id.isdigit():
        sql += " AND t.statement_id = %s"
        params.append(int(statement_id))
    merchant_key = args.get("merchant_key")
    if merchant_key:
        sql += " AND t.merchant_key = %s"
        params.append(merchant_key)
    return sql, params


def encode_cursor(row, sort):
    key = row["amount"] if "amount" in sort else (row["merchant_name"] if sort == "merchant" else row["txn_date"])
    return base64.urlsafe_b64encode(f"{key}|{row['id']}".encode()).decode()


def decode_cursor(cursor, sort):
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        value, id_str = raw.rsplit("|", 1)
        txn_id = int(id_str)
    except Exception:
        return None
    if "amount" in sort:
        value = _parse_decimal(value)
    elif sort != "merchant":
        value = _parse_date(value)
    if value is None:
        return None
    return value, txn_id


def cursor_clause(sort, cursor):
    """Keyset pagination predicate for the given sort, or ('', [])."""
    decoded = decode_cursor(cursor, sort) if cursor else None
    if not decoded:
        return "", []
    value, txn_id = decoded
    col = "t.amount" if "amount" in sort else ("t.merchant_name" if sort == "merchant" else "t.txn_date")
    op = SORTS[sort][1]
    return f" AND ({col}, t.id) {op} (%s, %s)", [value, txn_id]


@bp.get("")
@login_required
def list_transactions():
    args = request.args
    uid = session["user_id"]
    where, params = build_filters(args, uid)
    sort = args.get("sort") if args.get("sort") in SORTS else "-date"
    try:
        limit = min(max(int(args.get("limit") or 100), 1), 500)
    except ValueError:
        limit = 100
    cur_sql, cur_params = cursor_clause(sort, args.get("cursor"))
    items = db.query(
        f"SELECT {ITEM_FIELDS} FROM transactions t WHERE{where}{cur_sql} ORDER BY {SORTS[sort][0]} LIMIT %s",
        params + cur_params + [limit + 1],
    ) or []
    next_cursor = None
    if len(items) > limit:
        items = items[:limit]
        next_cursor = encode_cursor(items[-1], sort)
    totals = db.query(
        f"""SELECT COUNT(*) AS total,
                   COALESCE(SUM(CASE WHEN t.amount > 0 THEN t.amount END), 0) AS sum_in,
                   COALESCE(SUM(CASE WHEN t.amount < 0 THEN t.amount END), 0) AS sum_out,
                   COUNT(*) FILTER (WHERE t.category_id IS NULL AND NOT t.is_transfer) AS uncategorized,
                   COUNT(*) FILTER (WHERE t.category_status = 'suggested') AS suggested,
                   COUNT(*) FILTER (WHERE t.is_transfer) AS transfer
            FROM transactions t WHERE{where}""",
        params, one=True,
    ) or {}
    acct_facets = db.query(
        f"SELECT t.account_id AS id, COUNT(*) AS n FROM transactions t WHERE{where} GROUP BY t.account_id ORDER BY n DESC",
        params,
    ) or []
    cat_facets = db.query(
        f"SELECT t.category_id AS id, COUNT(*) AS n FROM transactions t WHERE{where} GROUP BY t.category_id ORDER BY n DESC",
        params,
    ) or []
    return jsonify({
        "items": rows_json(items),
        "next_cursor": next_cursor,
        "total": totals.get("total", 0),
        "sum_in": float(totals.get("sum_in") or 0),
        "sum_out": float(totals.get("sum_out") or 0),
        "facets": {
            "accounts": [dict(r) for r in acct_facets],
            "categories": [dict(r) for r in cat_facets],
            "status": {
                "uncategorized": totals.get("uncategorized", 0),
                "suggested": totals.get("suggested", 0),
                "transfer": totals.get("transfer", 0),
            },
        },
    })


@bp.get("/export")
@login_required
def export_csv():
    uid = session["user_id"]
    where, params = build_filters(request.args, uid)
    rows = db.query(
        f"""SELECT t.txn_date, a.name AS account, t.description_raw, t.merchant_name,
                   COALESCE(p.name || ' > ' || c.name, c.name) AS category, t.amount, t.currency, t.notes,
                   t.is_transfer
            FROM transactions t
            JOIN accounts a ON a.id = t.account_id
            LEFT JOIN categories c ON c.id = t.category_id
            LEFT JOIN categories p ON p.id = c.parent_id
            WHERE{where} ORDER BY t.txn_date DESC, t.id DESC""",
        params,
    ) or []

    def generate():
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["Date", "Account", "Description", "Merchant", "Category", "Amount", "Currency", "Notes", "Transfer"])
        yield buf.getvalue()
        for r in rows:
            buf.seek(0)
            buf.truncate()
            writer.writerow([
                r["txn_date"].isoformat(), r["account"], r["description_raw"], r["merchant_name"],
                r["category"] or "", f"{r['amount']:.2f}", r["currency"], r["notes"] or "",
                "yes" if r["is_transfer"] else "",
            ])
            yield buf.getvalue()

    return Response(generate(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename=transactions-{today().isoformat()}.csv"})


@bp.get("/transfer-candidates")
@login_required
def transfer_candidates():
    try:
        days = int(request.args.get("days") or 4)
    except ValueError:
        days = 4
    return jsonify(transfers.candidates(session["user_id"], days=days))


@bp.post("/pair")
@login_required
def pair_transfer():
    data = request.get_json(silent=True) or {}
    try:
        out = transfers.pair(session["user_id"], int(data.get("a_id")), int(data.get("b_id")))
    except (TypeError, ValueError) as e:
        return api_error(str(e) or "a_id and b_id are required")
    except LookupError as e:
        return api_error(str(e), 404)
    audit("transactions.pair", out)
    return jsonify({"ok": True, **out})


@bp.post("/auto-pair")
@login_required
def auto_pair():
    data = request.get_json(silent=True) or {}
    paired = transfers.auto_pair(session["user_id"], min_confidence=float(data.get("min_confidence") or 0.9))
    audit("transactions.auto_pair", {"paired": paired})
    return jsonify({"paired": paired})


@bp.get("/merchant/<path:merchant_key>")
@login_required
def by_merchant(merchant_key):
    try:
        limit = min(int(request.args.get("limit") or 50), 500)
    except ValueError:
        limit = 50
    rows = db.query(
        f"SELECT {ITEM_FIELDS} FROM transactions t WHERE t.user_id = %s AND t.merchant_key = %s ORDER BY t.txn_date DESC, t.id DESC LIMIT %s",
        (session["user_id"], merchant_key, limit),
    ) or []
    return jsonify(rows_json(rows))


def _get_own(txn_id, uid):
    return db.query(f"SELECT {ITEM_FIELDS} FROM transactions t WHERE t.id = %s AND t.user_id = %s", (txn_id, uid), one=True)


def _events(txn_id):
    rows = db.query(
        """SELECT e.id, e.kind, e.detail, e.user_id, u.username, e.created_at
           FROM transaction_events e LEFT JOIN users u ON u.id = e.user_id
           WHERE e.transaction_id = %s ORDER BY e.created_at, e.id""",
        (txn_id,),
    ) or []
    return rows_json(rows)


@bp.get("/<int:txn_id>")
@login_required
def get_transaction(txn_id):
    uid = session["user_id"]
    row = _get_own(txn_id, uid)
    if not row:
        return api_error("Transaction not found", 404)
    others = db.query(
        f"""SELECT {ITEM_FIELDS} FROM transactions t
            WHERE t.user_id = %s AND t.merchant_key = %s AND t.id <> %s
            ORDER BY t.txn_date DESC, t.id DESC LIMIT 10""",
        (uid, row["merchant_key"], txn_id),
    ) or []
    statement = None
    if row["statement_id"]:
        statement = db.query("SELECT id, original_filename, bank_profile FROM statements WHERE id = %s",
                             (row["statement_id"],), one=True)
    return jsonify({**row_json(row), "events": _events(txn_id), "merchant_others": rows_json(others),
                    "statement": statement})


@bp.get("/<int:txn_id>/events")
@login_required
def get_events(txn_id):
    if not _get_own(txn_id, session["user_id"]):
        return api_error("Transaction not found", 404)
    return jsonify(_events(txn_id))


@bp.post("")
@login_required
def create_transaction():
    uid = session["user_id"]
    data = request.get_json(silent=True) or {}
    account = db.query("SELECT id, currency FROM accounts WHERE id = %s AND user_id = %s",
                       (data.get("account_id"), uid), one=True)
    if not account:
        return api_error("Account not found", 404)
    txn_date = _parse_date(str(data.get("txn_date") or ""))
    amount = _parse_decimal(data.get("amount"))
    description = (data.get("description") or "").strip()
    if not txn_date or amount is None or not description:
        return api_error("Date, amount and description are required")
    amount = amount.quantize(Decimal("0.01"))
    try:
        import merchant as merchant_mod
        m = merchant_mod.normalize(description)
        key, name, clean = m.key, m.name, m.clean
    except Exception:
        clean = " ".join(description.upper().split())
        key = " ".join(clean.split()[:3])
        name = key.title()
    try:
        import dedupe
        fp = dedupe.fingerprint(account["id"], txn_date, amount, clean)
    except Exception:
        import hashlib
        fp = hashlib.sha1(f"{account['id']}|{txn_date.isoformat()}|{amount:.2f}|{clean}".encode()).hexdigest()
    occurrence = 1 + (db.query(
        "SELECT COUNT(*) AS n FROM transactions WHERE account_id = %s AND fingerprint = %s",
        (account["id"], fp), one=True,
    ) or {}).get("n", 0)
    category_id = data.get("category_id")
    row = db.execute(
        """INSERT INTO transactions (user_id, account_id, txn_date, amount, currency, description_raw, description_clean,
               merchant_key, merchant_name, category_id, category_status, category_source, category_confidence,
               fingerprint, occurrence, notes, raw)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
        (uid, account["id"], txn_date, amount, account["currency"], description, clean, key, name,
         category_id, "confirmed" if category_id else "none", "manual" if category_id else None,
         1.0 if category_id else None, fp, occurrence, (data.get("notes") or "").strip() or None, '{"manual": true}'),
        returning=True,
    )
    record_event(row["id"], "imported", {"manual": True}, uid)
    if category_id:
        record_event(row["id"], "manual", {"category_id": category_id}, uid)
        if data.get("learn", True):
            categorizer.learn(uid, key, category_id, display_name=name)
    audit("transaction.create", {"id": row["id"]})
    return jsonify(row_json(_get_own(row["id"], uid))), 201


@bp.put("/<int:txn_id>")
@login_required
def update_transaction(txn_id):
    uid = session["user_id"]
    row = _get_own(txn_id, uid)
    if not row:
        return api_error("Transaction not found", 404)
    data = request.get_json(silent=True) or {}
    learn = bool(data.get("learn", True))
    if "category_id" in data:
        category_id = data["category_id"]
        if category_id is not None:
            cat = db.query("SELECT id FROM categories WHERE id = %s AND user_id = %s", (category_id, uid), one=True)
            if not cat:
                return api_error("Category not found", 404)
        categorizer.apply_manual(uid, [txn_id], category_id, learn_memory=learn)
    if "notes" in data:
        notes = (data.get("notes") or "").strip() or None
        db.execute("UPDATE transactions SET notes = %s, updated_at = now() WHERE id = %s", (notes, txn_id))
        record_event(txn_id, "note", {"notes": notes}, uid)
    if "merchant_name" in data:
        name = (data.get("merchant_name") or "").strip()
        if name:
            if data.get("rename_all"):
                db.execute("UPDATE transactions SET merchant_name = %s WHERE user_id = %s AND merchant_key = %s",
                           (name, uid, row["merchant_key"]))
                db.execute("UPDATE merchant_memory SET display_name = %s WHERE user_id = %s AND merchant_key = %s",
                           (name, uid, row["merchant_key"]))
            else:
                db.execute("UPDATE transactions SET merchant_name = %s, updated_at = now() WHERE id = %s", (name, txn_id))
    if "is_transfer" in data:
        if data["is_transfer"]:
            transfers_cat = transfers._transfer_category_id(uid, set())
            db.execute(
                """UPDATE transactions SET is_transfer = TRUE, is_excluded = TRUE,
                       category_id = COALESCE(category_id, %s),
                       category_status = CASE WHEN category_id IS NULL AND %s IS NOT NULL THEN 'confirmed' ELSE category_status END,
                       updated_at = now()
                   WHERE id = %s""",
                (transfers_cat, transfers_cat, txn_id),
            )
            record_event(txn_id, "transfer", {"is_transfer": True}, uid)
        else:
            transfers.unpair(uid, txn_id)
    if "is_excluded" in data and not data.get("is_transfer"):
        db.execute("UPDATE transactions SET is_excluded = %s, updated_at = now() WHERE id = %s",
                   (bool(data["is_excluded"]), txn_id))
        record_event(txn_id, "excluded", {"is_excluded": bool(data["is_excluded"])}, uid)
    audit("transaction.update", {"id": txn_id, "fields": sorted(k for k in data if k != "learn")})
    return jsonify(row_json(_get_own(txn_id, uid)))


@bp.delete("/<int:txn_id>")
@login_required
def delete_transaction(txn_id):
    uid = session["user_id"]
    row = _get_own(txn_id, uid)
    if not row:
        return api_error("Transaction not found", 404)
    if row["transfer_pair_id"]:
        db.execute("UPDATE transactions SET transfer_pair_id = NULL WHERE id = %s", (row["transfer_pair_id"],))
    db.execute("DELETE FROM transactions WHERE id = %s", (txn_id,))
    audit("transaction.delete", {"id": txn_id})
    return jsonify({"ok": True})


@bp.post("/<int:txn_id>/rule-draft")
@login_required
def rule_draft(txn_id):
    row = _get_own(txn_id, session["user_id"])
    if not row:
        return api_error("Transaction not found", 404)
    return jsonify({
        "name": f"{row['merchant_name']}",
        "match_type": "equals",
        "match_field": "merchant_key",
        "pattern": row["merchant_key"],
        "alt_pattern": row["description_clean"],
        "category_id": row["category_id"],
        "account_id": None,
        "case_sensitive": False,
    })


@bp.post("/<int:txn_id>/unpair")
@login_required
def unpair_transfer(txn_id):
    try:
        n = transfers.unpair(session["user_id"], txn_id)
    except LookupError as e:
        return api_error(str(e), 404)
    audit("transactions.unpair", {"id": txn_id})
    return jsonify({"ok": True, "updated": n})


@bp.post("/bulk")
@login_required
def bulk():
    uid = session["user_id"]
    data = request.get_json(silent=True) or {}
    ids = parse_int_list(data.get("ids"))
    action = data.get("action")
    if not ids:
        return api_error("ids are required")
    own = [r["id"] for r in (db.query(
        "SELECT id FROM transactions WHERE user_id = %s AND id = ANY(%s)", (uid, ids)) or [])]
    if not own:
        return api_error("No matching transactions", 404)
    updated = 0
    if action == "categorize":
        category_id = data.get("category_id")
        if category_id is None:
            return api_error("category_id is required")
        cat = db.query("SELECT id FROM categories WHERE id = %s AND user_id = %s", (category_id, uid), one=True)
        if not cat:
            return api_error("Category not found", 404)
        updated = categorizer.apply_manual(uid, own, category_id, learn_memory=bool(data.get("learn", True)))
    elif action == "uncategorize":
        updated = categorizer.apply_manual(uid, own, None, learn_memory=False)
    elif action == "accept_suggestion":
        rows = db.query(
            """SELECT id, merchant_key, merchant_name, category_id FROM transactions
               WHERE user_id = %s AND id = ANY(%s) AND category_status = 'suggested' AND category_id IS NOT NULL""",
            (uid, own),
        ) or []
        if rows:
            sids = [r["id"] for r in rows]
            db.execute(
                """UPDATE transactions SET category_status = 'confirmed', category_confidence = 1.0, updated_at = now()
                   WHERE id = ANY(%s)""",
                (sids,),
            )
            record_events([(r["id"], "manual", {"accepted": True, "category_id": r["category_id"]}, uid) for r in rows])
            if data.get("learn", True):
                seen = {}
                for r in rows:
                    seen.setdefault((r["merchant_key"], r["category_id"]), r["merchant_name"])
                for (key, cid), name in seen.items():
                    categorizer.learn(uid, key, cid, display_name=name)
            updated = len(sids)
    elif action == "reject_suggestion":
        updated = db.execute(
            """UPDATE transactions SET category_id = NULL, category_status = 'none', category_source = NULL,
                   category_confidence = NULL, ai_rationale = NULL, updated_at = now()
               WHERE user_id = %s AND id = ANY(%s) AND category_status = 'suggested'""",
            (uid, own),
        )
        record_events([(i, "manual", {"rejected": True}, uid) for i in own])
    elif action == "set_transfer":
        transfers_cat = transfers._transfer_category_id(uid, set())
        updated = db.execute(
            """UPDATE transactions SET is_transfer = TRUE, is_excluded = TRUE,
                   category_id = COALESCE(category_id, %s),
                   category_status = CASE WHEN category_id IS NULL AND %s IS NOT NULL THEN 'confirmed' ELSE category_status END,
                   updated_at = now()
               WHERE id = ANY(%s)""",
            (transfers_cat, transfers_cat, own),
        )
        record_events([(i, "transfer", {"is_transfer": True}, uid) for i in own])
    elif action == "unset_transfer":
        pairs = [r["transfer_pair_id"] for r in (db.query(
            "SELECT transfer_pair_id FROM transactions WHERE id = ANY(%s) AND transfer_pair_id IS NOT NULL", (own,)) or [])]
        updated = db.execute(
            """UPDATE transactions SET is_transfer = FALSE, is_excluded = FALSE, transfer_pair_id = NULL, updated_at = now()
               WHERE user_id = %s AND id = ANY(%s)""",
            (uid, own + pairs),
        )
        record_events([(i, "transfer", {"is_transfer": False}, uid) for i in own])
    elif action in ("exclude", "include"):
        flag = action == "exclude"
        updated = db.execute("UPDATE transactions SET is_excluded = %s, updated_at = now() WHERE id = ANY(%s)", (flag, own))
        record_events([(i, "excluded", {"is_excluded": flag}, uid) for i in own])
    elif action == "delete":
        db.execute("UPDATE transactions SET transfer_pair_id = NULL WHERE transfer_pair_id = ANY(%s)", (own,))
        updated = db.execute("DELETE FROM transactions WHERE id = ANY(%s)", (own,))
    else:
        return api_error("Unknown action")
    audit("transactions.bulk", {"action": action, "count": updated})
    return jsonify({"updated": updated})
