import csv
import io

from flask import Blueprint, Response, jsonify, request, session

import db
import reports
from auth import login_required
from util import api_error, audit, csv_safe, json_body, parse_int_list

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
            w.writerow({k: csv_safe("" if v is None else v) for k, v in r.items()})
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


def _wants_csv():
    return request.args.get("format") == "csv"


def _inc():
    """include_transfers=1 counts transfers and excluded rows as ordinary money in/out."""
    return request.args.get("include_transfers") in ("1", "true")


def _flow():
    """flow=income reports money in by category/source instead of spending."""
    return "income" if request.args.get("flow") == "income" else "spending"


@bp.get("/dashboard")
@login_required
def dashboard():
    return jsonify(reports.dashboard(_uid(), request.args.get("range"), _accounts(),
                                     request.args.get("from"), request.args.get("to")))


@bp.get("/summary")
@login_required
def summary():
    r = _range()
    data = reports.summary(_uid(), r["start"], r["end"], _accounts(), include_transfers=_inc())
    prev = reports.summary(_uid(), r["prev_start"], r["prev_end"], _accounts(), include_transfers=_inc()) if r["prev_start"] else None
    out = {"range": reports.range_json(r), **data, "previous": prev}
    if _wants_csv():
        return _csv_response([data], "summary.csv")
    return jsonify(out)


@bp.get("/by-category")
@login_required
def by_category():
    r = _range()
    level = "sub" if request.args.get("level") == "sub" else "top"
    flow = _flow()
    rows = reports.by_category(_uid(), r["start"], r["end"], level, _accounts(), include_transfers=_inc(), flow=flow)
    if _wants_csv():
        return _csv_response(rows, f"{flow}-by-category.csv", ["name", "total", "count", "pct", "parent_id", "id"])
    return jsonify({"range": reports.range_json(r), "level": level, "flow": flow, "categories": rows,
                    "total": round(sum(c["total"] for c in rows), 2)})


@bp.get("/monthly")
@login_required
def monthly():
    months = request.args.get("months", 12)
    try:
        months = int(months)
    except ValueError:
        return api_error("months must be an integer")
    parent_id = request.args.get("parent_id")
    try:
        parent_id = int(parent_id) if parent_id else None
    except ValueError:
        return api_error("parent_id must be an integer")
    data = reports.monthly_by_category(_uid(), months, _accounts(), include_transfers=_inc(), parent_id=parent_id,
                                       flow=_flow())
    if _wants_csv():
        rows = []
        for s in data["series"]:
            row = {"category": s["name"], "total": s["total"]}
            row.update({m: v for m, v in zip(data["months"], s["values"])})
            rows.append(row)
        rows.append({"category": "Total", "total": round(sum(data["totals"]), 2),
                     **{m: v for m, v in zip(data["months"], data["totals"])}})
        return _csv_response(rows, f"{data['flow']}-by-month.csv", ["category", "total", *data["months"]])
    return jsonify(data)


@bp.get("/trends")
@login_required
def trends():
    try:
        months = int(request.args.get("months", 12))
    except ValueError:
        return api_error("months must be an integer")
    rows = reports.trends(_uid(), months, _accounts(), include_transfers=_inc())
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
    flow = _flow()
    rows = reports.top_merchants(_uid(), r["start"], r["end"], limit, _accounts(), include_transfers=_inc(), flow=flow)
    if _wants_csv():
        return _csv_response(rows, "income-sources.csv" if flow == "income" else "top-merchants.csv",
                             ["merchant_name", "count", "total", "avg", "pct", "last_date", "category_id", "merchant_key"])
    return jsonify({"range": reports.range_json(r), "flow": flow, "merchants": rows})


@bp.get("/month-over-month")
@login_required
def month_over_month():
    data = reports.month_over_month(_uid(), request.args.get("month"), _accounts(), include_transfers=_inc(),
                                    vs=request.args.get("vs"), flow=_flow())
    if _wants_csv():
        rows = [*data["categories"], {"name": "Total", **data["totals"]}]
        return _csv_response(rows, f"compare-{data['month']}-vs-{data['previous_month']}.csv",
                             ["name", "current", "previous", "delta", "pct"])
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
    data = json_body()
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
