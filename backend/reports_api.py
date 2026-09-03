import csv
import io

from flask import Blueprint, Response, jsonify, request, session

import db
import reports
from auth import login_required
from util import api_error, audit, parse_int_list

bp = Blueprint("reports", __name__, url_prefix="/api/reports")


def _uid():
    return session["user_id"]


def _accounts():
    ids = parse_int_list(request.args.get("account_id"))
    return ids or None


def _range():
    return reports.resolve_range(request.args.get("range"), request.args.get("from"), request.args.get("to"))


def _csv_response(rows, filename, columns=None):
    buf = io.StringIO()
    if rows:
        columns = columns or list(rows[0].keys())
        w = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if v is None else v) for k, v in r.items()})
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


def _wants_csv():
    return request.args.get("format") == "csv"


@bp.get("/dashboard")
@login_required
def dashboard():
    return jsonify(reports.dashboard(_uid(), request.args.get("range"), _accounts()))


@bp.get("/summary")
@login_required
def summary():
    r = _range()
    data = reports.summary(_uid(), r["start"], r["end"], _accounts())
    prev = reports.summary(_uid(), r["prev_start"], r["prev_end"], _accounts()) if r["prev_start"] else None
    out = {"range": reports.range_json(r), **data, "previous": prev}
    if _wants_csv():
        return _csv_response([data], "summary.csv")
    return jsonify(out)


@bp.get("/by-category")
@login_required
def by_category():
    r = _range()
    level = "sub" if request.args.get("level") == "sub" else "top"
    rows = reports.by_category(_uid(), r["start"], r["end"], level, _accounts())
    if _wants_csv():
        return _csv_response(rows, "spending-by-category.csv", ["name", "total", "count", "pct", "parent_id", "id"])
    return jsonify({"range": reports.range_json(r), "level": level, "categories": rows,
                    "total": round(sum(c["total"] for c in rows), 2)})


@bp.get("/monthly")
@login_required
def monthly():
    months = request.args.get("months", 12)
    try:
        months = int(months)
    except ValueError:
        return api_error("months must be an integer")
    data = reports.monthly_by_category(_uid(), months, _accounts())
    if _wants_csv():
        rows = []
        for s in data["series"]:
            row = {"category": s["name"], "total": s["total"]}
            row.update({m: v for m, v in zip(data["months"], s["values"])})
            rows.append(row)
        rows.append({"category": "Total", "total": round(sum(data["totals"]), 2),
                     **{m: v for m, v in zip(data["months"], data["totals"])}})
        return _csv_response(rows, "spending-by-month.csv", ["category", "total", *data["months"]])
    return jsonify(data)


@bp.get("/trends")
@login_required
def trends():
    try:
        months = int(request.args.get("months", 12))
    except ValueError:
        return api_error("months must be an integer")
    rows = reports.trends(_uid(), months, _accounts())
    if _wants_csv():
        return _csv_response(rows, "cash-flow.csv")
    return jsonify(rows)


@bp.get("/top-merchants")
@login_required
def top_merchants():
    r = _range()
    try:
        limit = int(request.args.get("limit", 20))
    except ValueError:
        return api_error("limit must be an integer")
    rows = reports.top_merchants(_uid(), r["start"], r["end"], limit, _accounts())
    if _wants_csv():
        return _csv_response(rows, "top-merchants.csv",
                             ["merchant_name", "count", "total", "avg", "pct", "last_date", "category_id", "merchant_key"])
    return jsonify({"range": reports.range_json(r), "merchants": rows})


@bp.get("/month-over-month")
@login_required
def month_over_month():
    data = reports.month_over_month(_uid(), request.args.get("month"), _accounts())
    if _wants_csv():
        rows = [*data["categories"], {"name": "Total", **data["totals"]}]
        return _csv_response(rows, f"compare-{data['month']}.csv", ["name", "current", "previous", "delta", "pct"])
    return jsonify(data)


@bp.get("/recurring")
@login_required
def recurring():
    rows = reports.recurring(_uid(), include_dismissed=request.args.get("include_dismissed") == "1")
    if _wants_csv():
        return _csv_response(rows, "recurring.csv",
                             ["merchant_name", "cadence", "median_amount", "monthly_equivalent", "occurrences",
                              "last_date", "next_expected", "is_active", "amount_kind", "category_name"])
    return jsonify(rows)


@bp.post("/recurring/dismiss")
@login_required
def dismiss_recurring():
    data = request.get_json(silent=True) or {}
    key = (data.get("merchant_key") or "").strip()
    if not key:
        return api_error("merchant_key is required")
    db.execute(
        "INSERT INTO recurring_dismissals (user_id, merchant_key) VALUES (%s, %s) ON CONFLICT DO NOTHING",
        (_uid(), key),
    )
    audit("recurring.dismiss", {"merchant_key": key})
    return jsonify({"ok": True})


@bp.delete("/recurring/dismiss/<path:merchant_key>")
@login_required
def undismiss_recurring(merchant_key):
    db.execute("DELETE FROM recurring_dismissals WHERE user_id = %s AND merchant_key = %s", (_uid(), merchant_key))
    audit("recurring.undismiss", {"merchant_key": merchant_key})
    return jsonify({"ok": True})
