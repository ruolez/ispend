import json
from datetime import date, datetime
from decimal import Decimal

from flask import jsonify, session

import db


def api_error(message, status=400):
    return jsonify({"error": message}), status


def audit(action, detail=None, user_id=None):
    if user_id is None:
        user_id = session.get("user_id")
    db.execute(
        "INSERT INTO audit_log (user_id, action, detail) VALUES (%s, %s, %s)",
        (user_id, action, json.dumps(detail, default=str) if detail is not None else None),
    )


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
