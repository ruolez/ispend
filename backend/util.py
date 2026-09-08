import csv
import io
import json
import random
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from flask import Response, abort, jsonify, request, session

import db


def api_error(message, status=400):
    return jsonify({"error": message}), status


def json_body():
    """The request's JSON object, {} when there is no body; 400 for a non-object body."""
    data = request.get_json(silent=True)
    if data is None:
        return {}
    if not isinstance(data, dict):
        abort(400, "JSON object expected")
    return data


def to_int(value, name, lo=None, hi=None, required=False):
    """int or None from a body/query value; 400 with a user-facing message when it is not one."""
    if value is None or value == "":
        if required:
            abort(400, f"{name} is required")
        return None
    if isinstance(value, bool) or (isinstance(value, float) and not value.is_integer()):
        abort(400, f"{name} must be an integer")
    try:
        out = int(value)
    except (TypeError, ValueError):
        abort(400, f"{name} must be an integer")
    if lo is not None and out < lo:
        abort(400, f"{name} must be at least {lo}")
    if hi is not None and out > hi:
        abort(400, f"{name} must be at most {hi}")
    return out


_CONTROL_CHARS = re.compile(r"[\x00-\x09\x0b-\x1f\x7f]")
_FORMULA_PREFIXES = ("=", "+", "-", "@")


def to_money(value, name):
    """Decimal with two places from a body value; 400 with a user-facing message otherwise."""
    if value is None or value == "" or isinstance(value, bool):
        abort(400, f"{name} must be an amount")
    try:
        out = Decimal(str(value))
    except (InvalidOperation, ValueError):
        abort(400, f"{name} must be an amount")
    if not out.is_finite() or abs(out) >= Decimal("10000000000"):
        abort(400, f"{name} must be an amount")
    return out.quantize(Decimal("0.01"))


def next_color_slot(user_id, table):
    """The least-used palette slot (c1..c12) among the user's rows of `table` (tags, categories)."""
    used = db.query(f"SELECT color, COUNT(*) AS n FROM {table} WHERE user_id = %s GROUP BY color", (user_id,)) or []
    counts = {r["color"]: r["n"] for r in used}
    return min((f"c{i}" for i in range(1, 13)), key=lambda c: (counts.get(c, 0), int(c[1:])))


def csv_safe(value):
    """Text cell safe to open in a spreadsheet: control characters dropped, formula prefixes quoted."""
    if not isinstance(value, str):
        return value
    value = _CONTROL_CHARS.sub("", value)
    if value.startswith(_FORMULA_PREFIXES):
        return "'" + value
    return value


def csv_response(rows, filename, columns=None):
    """Rows -> a downloadable CSV. Shared by the reports exports and the admin activity log."""
    buf = io.StringIO()
    if rows:
        columns = columns or list(rows[0].keys())
        w = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: csv_safe("" if v is None else v) for k, v in r.items()})
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


AUDIT_RETENTION_DAYS = 180


def audit(action, detail=None, user_id=None):
    if user_id is None:
        user_id = session.get("user_id")
    db.execute(
        "INSERT INTO audit_log (user_id, action, detail) VALUES (%s, %s, %s)",
        (user_id, action, json.dumps(detail, default=str) if detail is not None else None),
    )
    if random.random() < 0.005:  # noqa: S311 - sampling, not security  # roughly one prune per 200 writes keeps the log bounded without a scheduler
        # Read the setting only here: audit() is on hot paths, so the common write path must not
        # gain a second query.
        db.execute("DELETE FROM audit_log WHERE created_at < now() - make_interval(days => %s)",
                   (audit_retention_days(),))


def audit_retention_days():
    raw = db.get_setting("audit_retention_days")
    try:
        return max(7, min(3650, int(raw)))
    except (TypeError, ValueError):
        return AUDIT_RETENTION_DAYS


def money(v):
    """Decimal/None -> float/None at the JSON boundary."""
    if v is None:
        return None
    return float(v)


def iso(v):
    if v is None:
        return None
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    return str(v)


def row_json(row):
    """Convert a RealDictRow into JSON-safe primitives."""
    out = {}
    for k, v in row.items():
        if isinstance(v, Decimal):
            out[k] = float(v)
        elif isinstance(v, (datetime, date)):
            out[k] = v.isoformat()
        else:
            out[k] = v
    return out


def rows_json(rows):
    return [row_json(r) for r in rows]


def parse_int_list(value):
    """'1,2,3' or [1,2,3] -> [1,2,3]; drops junk."""
    if value is None or value == "":
        return []
    if isinstance(value, str):
        value = value.split(",")
    out = []
    for v in value:
        try:
            out.append(int(v))
        except (TypeError, ValueError):
            continue
    return out


# Everything a bulk change can alter on a transaction row besides the amount. A bulk/resolve/pair
# response carries these values as captured *before* the change; POST /bulk {action: "restore"}
# writes them back so the client's Undo is exact (ai_rationale, rule links and pairings included).
SNAPSHOT_FIELDS = ("category_id", "category_status", "category_source", "category_rule_id", "category_confidence",
                   "ai_rationale", "is_transfer", "is_excluded", "transfer_pair_id")
RESTORE_STATUSES = ("none", "suggested", "confirmed")
RESTORE_SOURCES = ("manual", "rule", "merchant", "builtin", "ai")


def snapshot(user_id, ids):
    """Undo-relevant fields of the user's rows among ids, as JSON-safe dicts ordered by id."""
    if not ids:
        return []
    rows = db.query(
        f"SELECT id, {', '.join(SNAPSHOT_FIELDS)} FROM transactions WHERE user_id = %s AND id = ANY(%s) ORDER BY id",
        (user_id, list(ids)),
    ) or []
    return rows_json(rows)


def _opt_int(value, name):
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be an integer") from None


def clean_restore_items(items, own_ids, own_category_ids, own_rule_ids, own_pair_ids):
    """Validate a restore payload. Rows the user does not own are dropped; a foreign category, a bad
    status or source raise ValueError; a rule or partner that no longer exists is unlinked."""
    if not isinstance(items, list):
        raise ValueError("items must be a list")
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        tid = _opt_int(it.get("id"), "id")
        if tid is None or tid not in own_ids:
            continue
        cat = _opt_int(it.get("category_id"), "category_id")
        if cat is not None and cat not in own_category_ids:
            raise ValueError("Category not found")
        status = it.get("category_status") or "none"
        if status not in RESTORE_STATUSES:
            raise ValueError("Invalid category_status")
        source = it.get("category_source") or None
        if source is not None and source not in RESTORE_SOURCES:
            raise ValueError("Invalid category_source")
        rule_id = _opt_int(it.get("category_rule_id"), "category_rule_id")
        if rule_id is not None and rule_id not in own_rule_ids:
            rule_id = None
        conf = it.get("category_confidence")
        if conf is not None:
            try:
                conf = float(conf)
            except (TypeError, ValueError):
                raise ValueError("category_confidence must be a number") from None
            if not 0 <= conf <= 1:
                raise ValueError("category_confidence must be between 0 and 1")
        rationale = it.get("ai_rationale")
        rationale = str(rationale)[:4000] if rationale not in (None, "") else None
        pair = _opt_int(it.get("transfer_pair_id"), "transfer_pair_id")
        if pair is not None and pair not in own_pair_ids:
            pair = None
        out.append({
            "id": tid, "category_id": cat, "category_status": status, "category_source": source,
            "category_rule_id": rule_id, "category_confidence": conf, "ai_rationale": rationale,
            "is_transfer": bool(it.get("is_transfer")), "is_excluded": bool(it.get("is_excluded")),
            "transfer_pair_id": pair,
        })
    return out


def drop_splits(ids, user_id, reason, commit=True):
    """A recategorised, rejected or transferred parent loses its split lines; returns the ids that had some."""
    if not ids:
        return []
    had = [r["id"] for r in (db.query(
        "SELECT DISTINCT transaction_id AS id FROM transaction_splits WHERE transaction_id = ANY(%s)", (list(ids),)) or [])]
    if had:
        db.execute("DELETE FROM transaction_splits WHERE transaction_id = ANY(%s)", (had,), commit=commit)
        record_events([(i, "split", {"removed": True, "reason": reason}, user_id) for i in had], commit=commit)
    return had


def record_event(transaction_id, kind, detail=None, user_id=None, commit=True):
    """Append one row to transaction_events (history timeline in the UI)."""
    if user_id is None:
        user_id = session.get("user_id") if session else None
    db.execute(
        "INSERT INTO transaction_events (transaction_id, kind, detail, user_id) VALUES (%s, %s, %s, %s)",
        (transaction_id, kind, json.dumps(detail, default=str) if detail is not None else None, user_id),
        commit=commit,
    )


def record_events(events, commit=True):
    """Bulk variant: events = [(transaction_id, kind, detail_dict_or_None, user_id), ...]."""
    if not events:
        return
    db.execute_values(
        "INSERT INTO transaction_events (transaction_id, kind, detail, user_id) VALUES %s",
        [(t, k, json.dumps(d, default=str) if d is not None else None, u) for t, k, d, u in events],
        commit=commit,
    )
