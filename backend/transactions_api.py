import base64
import csv
import io
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from flask import Blueprint, Response, jsonify, request, session

import categorizer
import dedupe
import merchant
import config
import db
import splits
import transfers
from auth import login_required
from util import (drop_splits, api_error, audit, clean_restore_items, csv_safe, json_body, parse_int_list, record_event, record_events,
                  row_json, rows_json, snapshot, to_int)

bp = Blueprint("transactions", __name__, url_prefix="/api/transactions")

ITEM_FIELDS = """t.id, t.account_id, t.statement_id, t.txn_date, t.posted_date, t.amount, t.currency, t.balance,
    t.description_raw, t.description_clean, t.merchant_key, t.merchant_name,
    t.category_id, t.category_status, t.category_source, t.category_rule_id, t.category_confidence,
    t.is_transfer, t.transfer_pair_id, t.is_excluded, t.notes, t.created_at, t.updated_at,
    COALESCE((SELECT array_agg(tt.tag_id ORDER BY tt.tag_id) FROM transaction_tags tt WHERE tt.transaction_id = t.id), ARRAY[]::int[]) AS tag_ids,
    (SELECT COUNT(*) FROM transaction_splits s WHERE s.transaction_id = t.id) AS split_count"""

RANGES = ("this-week", "last-week", "this-month", "last-month", "last-30", "last-90", "this-year", "last-year", "all")
STATUSES = ("all", "uncategorized", "suggested", "confirmed", "transfer", "excluded")
SORTS = {
    "-date": ("t.txn_date DESC, t.id DESC", "<"),
    "date": ("t.txn_date ASC, t.id ASC", ">"),
    "-amount": ("t.amount DESC, t.id DESC", "<"),
    "amount": ("t.amount ASC, t.id ASC", ">"),
    "merchant": ("t.merchant_name ASC, t.id ASC", ">"),
    "-merchant": ("t.merchant_name DESC, t.id DESC", "<"),
}


def today():
    return datetime.now(ZoneInfo(config.APP_TIMEZONE)).date()


def range_bounds(name, ref=None, week_start=0):
    """Named preset -> (from, to) dates (inclusive); None means unbounded. week_start: 0 = Sunday … 6 = Saturday."""
    ref = ref or today()
    if name in ("this-week", "last-week"):
        start, end = week_bounds(ref, week_start)
        return (start, end) if name == "this-week" else (start - timedelta(days=7), start - timedelta(days=1))
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
    if name and name.startswith("month:"):
        try:
            y, m = (int(x) for x in name[6:].split("-", 1))
            first = date(y, m, 1)
            last = (date(y + (m // 12), m % 12 + 1, 1) - timedelta(days=1))
            return first, last
        except ValueError:
            return None, None
    return None, None


def week_bounds(ref, week_start=0):
    """(first, last) day of ref's week; week_start counts Sunday as 0 like JavaScript's getDay()."""
    dow = (ref.weekday() + 1 - int(week_start or 0)) % 7
    start = ref - timedelta(days=dow)
    return start, start + timedelta(days=6)


def user_week_start(uid):
    row = db.query("SELECT preferences->>'week_start' AS ws FROM users WHERE id = %s", (uid,), one=True)
    try:
        return int((row or {}).get("ws") or 0) % 7
    except (TypeError, ValueError):
        return 0


def _parse_date(value):
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _parse_decimal(value):
    """Finite Decimal within Postgres numeric(14,2) reach, else None."""
    if value in (None, ""):
        return None
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not d.is_finite() or abs(d) >= Decimal("1e12"):
        return None
    return d


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
            parts.append("t.category_id = ANY(%s) OR t.category_id IN (SELECT id FROM categories WHERE parent_id = ANY(%s))"
                         " OR EXISTS (SELECT 1 FROM transaction_splits s WHERE s.transaction_id = t.id AND (s.category_id = ANY(%s)"
                         " OR s.category_id IN (SELECT id FROM categories WHERE parent_id = ANY(%s))))")
            params.extend([cat_ids, cat_ids, cat_ids, cat_ids])
        if "none" in raw_cats:
            parts.append("t.category_id IS NULL")
        if parts:
            sql += " AND (" + " OR ".join(parts) + ")"
    d_from = _parse_date(args.get("from"))
    d_to = _parse_date(args.get("to"))
    rng = args.get("range")
    if rng and (rng in RANGES or rng.startswith("month:")) and not (d_from or d_to):
        if rng in ("this-week", "last-week"):
            d_from, d_to = range_bounds(rng, week_start=user_week_start(user_id))
        else:
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
    flow = args.get("flow")
    if flow == "in":
        sql += " AND t.amount > 0"
    elif flow == "out":
        sql += " AND t.amount < 0"
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
    tag = (args.get("tag") or "").strip()
    if tag == "none":
        sql += " AND NOT EXISTS (SELECT 1 FROM transaction_tags tt WHERE tt.transaction_id = t.id)"
    elif tag:
        tag_ids = parse_int_list(tag)
        if tag_ids and args.get("tag_mode") == "all":
            for tid in tag_ids:
                sql += " AND EXISTS (SELECT 1 FROM transaction_tags tt WHERE tt.transaction_id = t.id AND tt.tag_id = %s)"
                params.append(tid)
        elif tag_ids:
            sql += " AND EXISTS (SELECT 1 FROM transaction_tags tt WHERE tt.transaction_id = t.id AND tt.tag_id = ANY(%s))"
            params.append(tag_ids)
    if args.get("split") in ("1", "true"):
        sql += " AND EXISTS (SELECT 1 FROM transaction_splits s WHERE s.transaction_id = t.id)"
    return sql, params


def _own_tag_ids(uid, raw):
    """Validated list of the user's tag ids from a body value; None when one is not theirs."""
    ids = sorted(set(parse_int_list(raw)))
    if not ids:
        return []
    own = {r["id"] for r in (db.query("SELECT id FROM tags WHERE user_id = %s AND id = ANY(%s)", (uid, ids)) or [])}
    return ids if own == set(ids) else None


def _set_tags(uid, txn_ids, tag_ids):
    """Replace the tags on txn_ids (single-row PUT) and record one event per row."""
    with db.transaction():
        db.execute("DELETE FROM transaction_tags WHERE transaction_id = ANY(%s)", (txn_ids,), commit=False)
        if tag_ids:
            db.execute_values("INSERT INTO transaction_tags (transaction_id, tag_id) VALUES %s ON CONFLICT DO NOTHING",
                              [(t, g) for t in txn_ids for g in tag_ids], commit=False)
    record_events([(t, "tag", {"tag_ids": tag_ids}, uid) for t in txn_ids])


def encode_cursor(row, sort):
    key = row["amount"] if "amount" in sort else (row["merchant_name"] if "merchant" in sort else row["txn_date"])
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
    elif "merchant" not in sort:
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
    col = "t.amount" if "amount" in sort else ("t.merchant_name" if "merchant" in sort else "t.txn_date")
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
                   COALESCE(SUM(CASE WHEN t.amount > 0 AND NOT t.is_transfer AND NOT t.is_excluded THEN t.amount END), 0) AS sum_in,
                   COALESCE(SUM(CASE WHEN t.amount < 0 AND NOT t.is_transfer AND NOT t.is_excluded THEN t.amount END), 0) AS sum_out,
                   COALESCE(SUM(abs(t.amount)) FILTER (WHERE t.is_transfer OR t.is_excluded), 0) AS sum_skipped,
                   COUNT(*) FILTER (WHERE t.is_transfer OR t.is_excluded) AS skipped,
                   COUNT(*) FILTER (WHERE t.category_id IS NULL AND NOT t.is_transfer) AS uncategorized,
                   COUNT(*) FILTER (WHERE t.category_status = 'suggested') AS suggested,
                   COUNT(*) FILTER (WHERE t.is_transfer) AS transfer,
                   COUNT(*) FILTER (WHERE t.is_excluded AND NOT t.is_transfer) AS excluded
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
    tag_facets = db.query(
        f"SELECT tt.tag_id AS id, COUNT(*) AS n FROM transactions t JOIN transaction_tags tt ON tt.transaction_id = t.id WHERE{where} GROUP BY tt.tag_id ORDER BY n DESC",
        params,
    ) or []
    currencies = [r["currency"] for r in (db.query(
        f"SELECT DISTINCT t.currency FROM transactions t WHERE{where} ORDER BY t.currency", params) or [])]
    return jsonify({
        "items": rows_json(items),
        "next_cursor": next_cursor,
        "currencies": currencies,
        "total": totals.get("total", 0),
        "sum_in": float(totals.get("sum_in") or 0),
        "sum_out": float(totals.get("sum_out") or 0),
        "skipped": {"count": totals.get("skipped") or 0, "sum": float(totals.get("sum_skipped") or 0)},
        "facets": {
            "accounts": [dict(r) for r in acct_facets],
            "categories": [dict(r) for r in cat_facets],
            "tags": [dict(r) for r in tag_facets],
            "status": {
                "uncategorized": totals.get("uncategorized", 0),
                "suggested": totals.get("suggested", 0),
                "transfer": totals.get("transfer", 0),
                "excluded": totals.get("excluded", 0),
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
                   t.is_transfer,
                   (SELECT string_agg(g.name, '; ' ORDER BY g.name) FROM transaction_tags tt JOIN tags g ON g.id = tt.tag_id WHERE tt.transaction_id = t.id) AS tags,
                   (SELECT string_agg(COALESCE(sc.name, 'Uncategorized') || ' ' || to_char(abs(s.amount), 'FM999999990.00'), ' · ' ORDER BY s.sort_order, s.id)
                    FROM transaction_splits s LEFT JOIN categories sc ON sc.id = s.category_id WHERE s.transaction_id = t.id) AS split_lines
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
        writer.writerow(["Date", "Account", "Description", "Merchant", "Category", "Amount", "Currency", "Notes", "Transfer", "Tags", "Split"])
        yield buf.getvalue()
        for r in rows:
            buf.seek(0)
            buf.truncate()
            writer.writerow([
                r["txn_date"].isoformat(), csv_safe(r["account"]), csv_safe(r["description_raw"]),
                csv_safe(r["merchant_name"]), csv_safe(r["category"] or ""), f"{r['amount']:.2f}", r["currency"],
                csv_safe(r["notes"] or ""), "yes" if r["is_transfer"] else "", csv_safe(r["tags"] or ""), csv_safe(r["split_lines"] or ""),
            ])
            yield buf.getvalue()

    return Response(generate(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename=transactions-{today().isoformat()}.csv"})


@bp.get("/transfer-candidates")
@login_required
def transfer_candidates():
    try:
        days = min(max(int(request.args.get("days") or 4), 0), 365)
    except ValueError:
        days = 4
    return jsonify(transfers.candidates(session["user_id"], days=days))


@bp.post("/pair")
@login_required
def pair_transfer():
    data = json_body()
    a_id = to_int(data.get("a_id"), "a_id", required=True)
    b_id = to_int(data.get("b_id"), "b_id", required=True)
    before = snapshot(session["user_id"], [a_id, b_id])
    try:
        out = transfers.pair(session["user_id"], a_id, b_id)
    except transfers.AlreadyPaired as e:
        return api_error(str(e), 409)
    except ValueError as e:
        return api_error(str(e))
    except LookupError as e:
        return api_error(str(e), 404)
    audit("transactions.pair", out)
    return jsonify({"ok": True, **out, "before": before})


@bp.post("/auto-pair")
@login_required
def auto_pair():
    data = json_body()
    try:
        min_confidence = min(max(float(data.get("min_confidence") or 0.9), 0.0), 1.0)
    except (TypeError, ValueError):
        return api_error("min_confidence must be a number between 0 and 1")
    uid = session["user_id"]
    legs = [t["id"] for c in transfers.candidates(uid, limit=1000) if c["confidence"] >= min_confidence for t in (c["a"], c["b"])]
    before = snapshot(uid, sorted(set(legs)))
    paired = transfers.auto_pair(uid, min_confidence=min_confidence)
    audit("transactions.auto_pair", {"paired": paired})
    return jsonify({"paired": paired, "before": before})


@bp.get("/suggest")
@login_required
def suggest():
    """Autocomplete for manual entry: merchants the user already has, with the category they
    usually land in (remembered merchant first, else the most recent categorized row)."""
    uid = session["user_id"]
    q = (request.args.get("q") or "").strip()
    if len(q) < 2:
        return jsonify([])
    limit = min(max(to_int(request.args.get("limit"), "limit") or 8, 1), 20)
    like = f"%{q}%"
    rows = db.query(
        """WITH hits AS (
             SELECT t.merchant_key,
                    (array_agg(t.merchant_name ORDER BY t.txn_date DESC, t.id DESC))[1] AS merchant_name,
                    (array_agg(t.description_raw ORDER BY t.txn_date DESC, t.id DESC))[1] AS last_description,
                    (array_agg(t.amount ORDER BY t.txn_date DESC, t.id DESC))[1] AS last_amount,
                    (array_agg(t.currency ORDER BY t.txn_date DESC, t.id DESC))[1] AS currency,
                    (array_agg(t.category_id ORDER BY (t.category_id IS NULL), t.txn_date DESC, t.id DESC))[1] AS recent_category_id,
                    MAX(t.txn_date) AS last_date, COUNT(*) AS n
             FROM transactions t
             WHERE t.user_id = %s AND (t.merchant_name ILIKE %s OR t.description_clean ILIKE %s OR t.description_raw ILIKE %s)
             GROUP BY t.merchant_key)
           SELECT h.*, COALESCE(m.category_id, h.recent_category_id) AS category_id, m.is_transfer
           FROM hits h LEFT JOIN merchant_memory m ON m.user_id = %s AND m.merchant_key = h.merchant_key
           ORDER BY (lower(h.merchant_name) LIKE lower(%s)) DESC, h.n DESC, h.last_date DESC
           LIMIT %s""",
        (uid, like, like, like, uid, f"{q}%", limit),
    ) or []
    return jsonify(rows_json(rows))


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
                    "statement": statement, "splits": _splits(txn_id)})


def _splits(txn_id):
    rows = db.query(
        """SELECT s.id, s.category_id, s.amount, s.note, s.sort_order, c.name AS category_name, c.color AS category_color,
                  c.icon AS category_icon, c.parent_id AS category_parent_id
           FROM transaction_splits s LEFT JOIN categories c ON c.id = s.category_id
           WHERE s.transaction_id = %s ORDER BY s.sort_order, s.id""",
        (txn_id,),
    ) or []
    return rows_json(rows)


@bp.put("/<int:txn_id>/splits")
@login_required
def set_splits(txn_id):
    """Replace the split lines (>= 2, same sign, summing to the amount); the first line's category
    becomes the row's primary category. The deferred DB trigger backs up this validation."""
    uid = session["user_id"]
    row = _get_own(txn_id, uid)
    if not row:
        return api_error("Transaction not found", 404)
    if row["is_transfer"]:
        return api_error("Transfers cannot be split")
    lines, problem = splits.validate_splits(row["amount"], json_body().get("lines"))
    if problem:
        return api_error(problem)
    cat_ids = sorted({ln["category_id"] for ln in lines})
    own_cats = db.query("SELECT id, kind FROM categories WHERE user_id = %s AND id = ANY(%s)", (uid, cat_ids)) or []
    if len(own_cats) != len(cat_ids):
        return api_error("Category not found", 404)
    if any(c["kind"] == "transfer" for c in own_cats):
        return api_error("Transfer categories cannot be used in a split")
    with db.transaction():
        db.execute("DELETE FROM transaction_splits WHERE transaction_id = %s", (txn_id,), commit=False)
        db.execute_values(
            "INSERT INTO transaction_splits (transaction_id, category_id, amount, note, sort_order) VALUES %s",
            [(txn_id, ln["category_id"], ln["amount"], ln["note"], i) for i, ln in enumerate(lines)], commit=False,
        )
        db.execute(
            """UPDATE transactions SET category_id = %s, category_status = 'confirmed', category_source = 'manual',
                   category_rule_id = NULL, category_confidence = 1.0, updated_at = now() WHERE id = %s""",
            (lines[0]["category_id"], txn_id), commit=False,
        )
        record_event(txn_id, "split", {"lines": len(lines), "category_ids": [ln["category_id"] for ln in lines]}, uid, commit=False)
    audit("transaction.split", {"id": txn_id, "lines": len(lines)})
    return jsonify({**row_json(_get_own(txn_id, uid)), "splits": _splits(txn_id)})


@bp.delete("/<int:txn_id>/splits")
@login_required
def clear_splits(txn_id):
    uid = session["user_id"]
    if not _get_own(txn_id, uid):
        return api_error("Transaction not found", 404)
    removed = drop_splits([txn_id], uid, "unsplit")
    audit("transaction.unsplit", {"id": txn_id})
    return jsonify({**row_json(_get_own(txn_id, uid)), "splits": [], "removed": bool(removed)})


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
    data = json_body()
    account_id = to_int(data.get("account_id"), "Account")
    account = db.query("SELECT id, currency FROM accounts WHERE id = %s AND user_id = %s", (account_id, uid), one=True)
    if not account:
        return api_error("Account not found", 404)
    txn_date = _parse_date(str(data.get("txn_date") or ""))
    amount = _parse_decimal(data.get("amount"))
    description = (data.get("description") or "").strip()
    if not txn_date or amount is None or not description:
        return api_error("Date, amount and description are required")
    amount = amount.quantize(Decimal("0.01"))
    m = merchant.normalize(description)
    key, name, clean = m.key, m.name, m.clean
    fp = dedupe.fingerprint(account["id"], txn_date, amount, clean)
    occurrence = 1 + (db.query(
        "SELECT COUNT(*) AS n FROM transactions WHERE account_id = %s AND fingerprint = %s",
        (account["id"], fp), one=True,
    ) or {}).get("n", 0)
    category_id = to_int(data.get("category_id"), "Category")
    if category_id is not None and not db.query(
            "SELECT id FROM categories WHERE id = %s AND user_id = %s", (category_id, uid), one=True):
        return api_error("Category not found", 404)
    with db.transaction():
        row = db.execute(
            """INSERT INTO transactions (user_id, account_id, txn_date, amount, currency, description_raw, description_clean,
                   merchant_key, merchant_name, category_id, category_status, category_source, category_confidence,
                   fingerprint, occurrence, notes, raw)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
            (uid, account["id"], txn_date, amount, account["currency"], description, clean, key, name,
             category_id, "confirmed" if category_id else "none", "manual" if category_id else None,
             1.0 if category_id else None, fp, occurrence, str(data.get("notes") or "").strip() or None, '{"manual": true}'),
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
    data = json_body()
    learn = bool(data.get("learn", True))
    if "category_id" in data:
        category_id = to_int(data["category_id"], "Category")
        if category_id is not None:
            cat = db.query("SELECT id FROM categories WHERE id = %s AND user_id = %s", (category_id, uid), one=True)
            if not cat:
                return api_error("Category not found", 404)
        categorizer.apply_manual(uid, [txn_id], category_id, learn_memory=learn)
    if "tag_ids" in data:
        tag_ids = _own_tag_ids(uid, data.get("tag_ids"))
        if tag_ids is None:
            return api_error("Tag not found", 404)
        _set_tags(uid, [txn_id], tag_ids)
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
    if data.get("is_transfer") is not None:
        if data["is_transfer"]:
            transfers_cat = transfers._transfer_category_id(uid, set())
            db.execute(
                """UPDATE transactions SET is_transfer = TRUE, is_excluded = TRUE,
                       category_id = COALESCE(category_id, %s),
                       category_status = CASE WHEN COALESCE(category_id, %s) IS NULL THEN category_status ELSE 'confirmed' END,
                       updated_at = now()
                   WHERE id = %s""",
                (transfers_cat, transfers_cat, txn_id),
            )
            record_event(txn_id, "transfer", {"is_transfer": True}, uid)
            drop_splits([txn_id], uid, "transfer")
        else:
            transfers.unpair(uid, txn_id)
    if data.get("is_excluded") is not None and not data.get("is_transfer"):
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
    _release_partners([txn_id])
    db.execute("DELETE FROM transactions WHERE id = %s", (txn_id,))
    audit("transaction.delete", {"id": txn_id})
    return jsonify({"ok": True})


def _release_partners(deleted_ids):
    """The other half of a transfer pair stops being a transfer unless its category says otherwise."""
    db.execute(
        """UPDATE transactions t SET transfer_pair_id = NULL,
               is_transfer = CASE WHEN c.kind = 'transfer' THEN t.is_transfer ELSE FALSE END,
               is_excluded = CASE WHEN c.kind = 'transfer' THEN t.is_excluded ELSE FALSE END,
               updated_at = now()
           FROM (SELECT p.id, COALESCE(k.kind, '') AS kind FROM transactions p
                 LEFT JOIN categories k ON k.id = p.category_id
                 WHERE p.transfer_pair_id = ANY(%s)) c
           WHERE t.id = c.id""",
        (list(deleted_ids),),
    )


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
    data = json_body()
    ids = parse_int_list(data.get("ids"))
    action = data.get("action")
    if not ids:
        return api_error("ids are required")
    own = [r["id"] for r in (db.query(
        "SELECT id FROM transactions WHERE user_id = %s AND id = ANY(%s)", (uid, ids)) or [])]
    if not own:
        return api_error("No matching transactions", 404)
    before = None if action in ("delete", "restore") else snapshot(uid, own)
    updated = 0
    if action == "categorize":
        category_id = to_int(data.get("category_id"), "Category")
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
        touched = [r["id"] for r in (db.query(
            "SELECT id FROM transactions WHERE user_id = %s AND id = ANY(%s) AND category_status = 'suggested'",
            (uid, own)) or [])]
        updated = db.execute(
            """UPDATE transactions SET category_id = NULL, category_status = 'none', category_source = NULL,
                   category_confidence = NULL, ai_rationale = NULL, updated_at = now()
               WHERE user_id = %s AND id = ANY(%s)""",
            (uid, touched),
        ) if touched else 0
        record_events([(i, "manual", {"rejected": True}, uid) for i in touched])
        drop_splits(touched, uid, "rejected")
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
        drop_splits(own, uid, "transfer")
    elif action == "unset_transfer":
        pairs = [r["transfer_pair_id"] for r in (db.query(
            "SELECT transfer_pair_id FROM transactions WHERE id = ANY(%s) AND transfer_pair_id IS NOT NULL", (own,)) or [])]
        updated = db.execute(
            """UPDATE transactions SET is_transfer = FALSE, is_excluded = FALSE, transfer_pair_id = NULL, updated_at = now()
               WHERE user_id = %s AND id = ANY(%s)""",
            (uid, own + pairs),
        )
        record_events([(i, "transfer", {"is_transfer": False}, uid) for i in own])
    elif action == "flip_sign":
        import signs
        updated = signs.flip_signs(uid, own, reason="bulk")
    elif action in ("exclude", "include"):
        flag = action == "exclude"
        updated = db.execute("UPDATE transactions SET is_excluded = %s, updated_at = now() WHERE id = ANY(%s)", (flag, own))
        record_events([(i, "excluded", {"is_excluded": flag}, uid) for i in own])
    elif action == "delete":
        _release_partners(own)
        updated = db.execute("DELETE FROM transactions WHERE id = ANY(%s)", (own,))
    elif action in ("tag", "untag"):
        tag_ids = _own_tag_ids(uid, data.get("tag_ids"))
        if tag_ids is None:
            return api_error("Tag not found", 404)
        if not tag_ids:
            return api_error("tag_ids are required")
        if action == "tag":
            db.execute_values("INSERT INTO transaction_tags (transaction_id, tag_id) VALUES %s ON CONFLICT DO NOTHING",
                              [(t, g) for t in own for g in tag_ids])
        else:
            db.execute("DELETE FROM transaction_tags WHERE transaction_id = ANY(%s) AND tag_id = ANY(%s)", (own, tag_ids))
        record_events([(t, "tag", {"tag_ids": tag_ids, "removed": action == "untag"}, uid) for t in own])
        updated = len(own)
    elif action == "restore":
        try:
            updated = _restore(uid, own, data.get("items"))
        except ValueError as e:
            return api_error(str(e))
        if updated is None:
            return api_error("items are required")
    else:
        return api_error("Unknown action")
    audit("transactions.bulk", {"action": action, "count": updated})
    body = {"updated": updated}
    if before is not None:
        body["before"] = before
    return jsonify(body)


def _restore(uid, own, items):
    """Write back a `before` snapshot (see util.SNAPSHOT_FIELDS); the client's exact Undo."""
    if not isinstance(items, list):
        return None
    cats = {r["id"] for r in (db.query("SELECT id FROM categories WHERE user_id = %s", (uid,)) or [])}
    rule_ids = {r["id"] for r in (db.query("SELECT id FROM rules WHERE user_id = %s", (uid,)) or [])}
    wanted = [it.get("transfer_pair_id") for it in items if isinstance(it, dict) and it.get("transfer_pair_id")]
    pair_ids = {r["id"] for r in (db.query("SELECT id FROM transactions WHERE user_id = %s AND id = ANY(%s)",
                                            (uid, parse_int_list(",".join(str(w) for w in wanted)))) or [])} if wanted else set()
    clean = clean_restore_items(items, set(own), cats, rule_ids, pair_ids)
    with db.transaction():
        for it in clean:
            db.execute(
                """UPDATE transactions SET category_id = %s, category_status = %s, category_source = %s, category_rule_id = %s,
                       category_confidence = %s, ai_rationale = %s, is_transfer = %s, is_excluded = %s, transfer_pair_id = %s,
                       updated_at = now()
                   WHERE id = %s AND user_id = %s""",
                (it["category_id"], it["category_status"], it["category_source"], it["category_rule_id"], it["category_confidence"],
                 it["ai_rationale"], it["is_transfer"], it["is_excluded"], it["transfer_pair_id"], it["id"], uid),
                commit=False,
            )
    record_events([(it["id"], "manual", {"restored": True, "category_id": it["category_id"], "is_transfer": it["is_transfer"],
                                         "is_excluded": it["is_excluded"]}, uid) for it in clean])
    return len(clean)
