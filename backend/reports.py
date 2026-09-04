"""Report queries. Every function takes the user id first and returns JSON-safe data.

SPENDING is the shared predicate: money out, not a transfer, not excluded.
"""
import calendar
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import config
import db
import recurring as recurring_lib
from util import row_json, rows_json

SPENDING = "t.user_id = %s AND t.amount < 0 AND NOT t.is_transfer AND NOT t.is_excluded"
CASHFLOW = "t.user_id = %s AND NOT t.is_transfer AND NOT t.is_excluded"
_NOT_TRANSFER = " AND NOT t.is_transfer AND NOT t.is_excluded"


def spending_where(include_transfers=False):
    """Money out for this user; transfers/excluded rows drop out unless include_transfers."""
    return "t.user_id = %s AND t.amount < 0" + ("" if include_transfers else _NOT_TRANSFER)


def cashflow_where(include_transfers=False):
    return "t.user_id = %s" + ("" if include_transfers else _NOT_TRANSFER)

RANGE_LABELS = {
    "this-month": "This month", "last-month": "Last month", "last-30": "Last 30 days",
    "last-90": "Last 90 days", "this-year": "This year", "last-year": "Last year",
    "all": "All time", "custom": "Custom",
}
ALL_START = date(1970, 1, 1)
ALL_END = date(2100, 12, 31)


def today():
    return datetime.now(ZoneInfo(config.APP_TIMEZONE)).date()


def month_bounds(year, month):
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


def shift_month(year, month, delta):
    idx = year * 12 + (month - 1) + delta
    return idx // 12, idx % 12 + 1


def parse_month(value, fallback=None):
    """'YYYY-MM' -> (year, month); falls back to the current month."""
    try:
        y, m = value.split("-")
        y, m = int(y), int(m)
        if 1 <= m <= 12:
            return y, m
    except (AttributeError, ValueError):
        pass
    t = fallback or today()
    return t.year, t.month


def _parse_date(s):
    try:
        return date.fromisoformat(str(s)[:10])
    except (TypeError, ValueError):
        return None


def resolve_range(range_name=None, date_from=None, date_to=None, now=None):
    """-> {name, label, start, end, prev_start, prev_end}; end is inclusive."""
    t = now or today()
    name = (range_name or "").strip() or ("custom" if (date_from or date_to) else "this-month")
    if name == "this-month":
        start, end = month_bounds(t.year, t.month)
        py, pm = shift_month(t.year, t.month, -1)
        prev_start, prev_end = month_bounds(py, pm)
    elif name == "last-month":
        py, pm = shift_month(t.year, t.month, -1)
        start, end = month_bounds(py, pm)
        ppy, ppm = shift_month(py, pm, -1)
        prev_start, prev_end = month_bounds(ppy, ppm)
    elif name in ("last-30", "last-90"):
        days = 30 if name == "last-30" else 90
        start, end = t - timedelta(days=days - 1), t
        prev_start, prev_end = start - timedelta(days=days), start - timedelta(days=1)
    elif name == "this-year":
        start, end = date(t.year, 1, 1), date(t.year, 12, 31)
        prev_start, prev_end = date(t.year - 1, 1, 1), date(t.year - 1, 12, 31)
    elif name == "last-year":
        start, end = date(t.year - 1, 1, 1), date(t.year - 1, 12, 31)
        prev_start, prev_end = date(t.year - 2, 1, 1), date(t.year - 2, 12, 31)
    elif name == "all":
        start, end, prev_start, prev_end = ALL_START, ALL_END, None, None
    else:
        name = "custom"
        start = _parse_date(date_from) or ALL_START
        end = _parse_date(date_to) or t
        if end < start:
            start, end = end, start
        length = (end - start).days + 1
        prev_end = start - timedelta(days=1)
        prev_start = prev_end - timedelta(days=length - 1)
    return {"name": name, "label": RANGE_LABELS.get(name, "Custom"), "start": start, "end": end,
            "prev_start": prev_start, "prev_end": prev_end}


def range_json(r):
    return {"name": r["name"], "label": r["label"], "start": r["start"].isoformat(), "end": r["end"].isoformat()}


def _acct(account_ids):
    if account_ids:
        return " AND t.account_id = ANY(%s)", [list(account_ids)]
    return "", []


def _f(v):
    return float(v) if v is not None else 0.0


# ---------- Summary / KPIs ----------

def summary(uid, start, end, account_ids=None, include_transfers=False):
    acct_sql, acct_params = _acct(account_ids)
    counted = "" if include_transfers else _NOT_TRANSFER
    row = db.query(
        f"""SELECT COALESCE(SUM(CASE WHEN t.amount > 0{counted} THEN t.amount END), 0) AS income,
                   COALESCE(SUM(CASE WHEN t.amount < 0{counted} THEN -t.amount END), 0) AS expenses,
                   COUNT(*) AS txn_count,
                   COUNT(*) FILTER (WHERE t.category_id IS NULL AND NOT t.is_transfer) AS uncategorized,
                   COUNT(*) FILTER (WHERE t.is_transfer) AS transfers,
                   COUNT(*) FILTER (WHERE t.category_status = 'suggested') AS suggested
            FROM transactions t
            WHERE t.user_id = %s AND t.txn_date BETWEEN %s AND %s{acct_sql}""",
        (uid, start, end, *acct_params), one=True,
    )
    income, expenses = _f(row["income"]), _f(row["expenses"])
    return {
        "start": start.isoformat(), "end": end.isoformat(),
        "income": income, "expenses": expenses, "net": round(income - expenses, 2),
        "txn_count": row["txn_count"], "uncategorized": row["uncategorized"],
        "transfers": row["transfers"], "suggested": row["suggested"],
    }


# ---------- Category breakdowns ----------

_CAT_JOIN = """
    LEFT JOIN categories c ON c.id = t.category_id
    LEFT JOIN categories p ON p.id = c.parent_id
    LEFT JOIN categories g ON g.id = COALESCE(p.id, c.id)
"""


def _cat_row(r, total_all):
    total = _f(r["total"])
    return {
        "id": r["id"], "name": r["name"] or "Uncategorized", "color": r["color"] or "muted",
        "icon": r["icon"] or ("help-circle" if r["id"] is None else "tag"), "parent_id": r.get("parent_id"),
        "total": round(total, 2), "count": r["count"],
        "pct": round(total / total_all * 100, 1) if total_all else 0.0,
    }


def by_category(uid, start, end, level="top", account_ids=None, include_transfers=False):
    acct_sql, acct_params = _acct(account_ids)
    where = spending_where(include_transfers)
    if level == "sub":
        select = "c.id, c.name, c.color, c.icon, c.parent_id"
    else:
        select = "g.id, g.name, g.color, g.icon, g.parent_id"
    rows = db.query(
        f"""SELECT {select}, SUM(-t.amount) AS total, COUNT(*) AS count
            FROM transactions t {_CAT_JOIN}
            WHERE {where} AND t.txn_date BETWEEN %s AND %s{acct_sql}
            GROUP BY {select}
            ORDER BY total DESC""",
        (uid, start, end, *acct_params),
    )
    total_all = sum(_f(r["total"]) for r in rows)
    return [_cat_row(r, total_all) for r in rows]


def _month_list(months, end=None):
    t = end or today()
    out = []
    for i in range(months - 1, -1, -1):
        y, m = shift_month(t.year, t.month, -i)
        out.append(f"{y:04d}-{m:02d}")
    return out


def monthly_by_category(uid, months=12, account_ids=None, top_n=7, include_transfers=False):
    months = max(1, min(int(months or 12), 60))
    month_keys = _month_list(months)
    fy, fm = (int(x) for x in month_keys[0].split("-"))
    first = date(fy, fm, 1)
    acct_sql, acct_params = _acct(account_ids)
    rows = db.query(
        f"""SELECT to_char(date_trunc('month', t.txn_date), 'YYYY-MM') AS month,
                   g.id AS category_id, g.name, g.color, SUM(-t.amount) AS total
            FROM transactions t {_CAT_JOIN}
            WHERE {spending_where(include_transfers)} AND t.txn_date >= %s{acct_sql}
            GROUP BY 1, 2, 3, 4""",
        (uid, first, *acct_params),
    )
    cats = {}
    for r in rows:
        cid = r["category_id"]
        entry = cats.setdefault(cid, {
            "category_id": cid, "name": r["name"] or "Uncategorized",
            "color": r["color"] or "muted", "values": {m: 0.0 for m in month_keys}, "sum": 0.0,
        })
        if r["month"] in entry["values"]:
            entry["values"][r["month"]] += _f(r["total"])
            entry["sum"] += _f(r["total"])
    ranked = sorted(cats.values(), key=lambda c: -c["sum"])
    series, other = [], None
    for i, c in enumerate(ranked):
        if i < top_n:
            series.append({"category_id": c["category_id"], "name": c["name"], "color": c["color"],
                           "values": [round(c["values"][m], 2) for m in month_keys], "total": round(c["sum"], 2)})
        else:
            if other is None:
                other = {"category_id": None, "name": "Other", "color": "muted",
                         "values": [0.0] * len(month_keys), "total": 0.0}
            for j, m in enumerate(month_keys):
                other["values"][j] = round(other["values"][j] + c["values"][m], 2)
            other["total"] = round(other["total"] + c["sum"], 2)
    if other:
        series.append(other)
    totals = [round(sum(s["values"][j] for s in series), 2) for j in range(len(month_keys))]
    return {"months": month_keys, "series": series, "totals": totals}


def trends(uid, months=12, account_ids=None, include_transfers=False):
    months = max(1, min(int(months or 12), 60))
    month_keys = _month_list(months)
    fy, fm = (int(x) for x in month_keys[0].split("-"))
    acct_sql, acct_params = _acct(account_ids)
    rows = db.query(
        f"""SELECT to_char(date_trunc('month', t.txn_date), 'YYYY-MM') AS month,
                   COALESCE(SUM(CASE WHEN t.amount > 0 THEN t.amount END), 0) AS income,
                   COALESCE(SUM(CASE WHEN t.amount < 0 THEN -t.amount END), 0) AS expenses,
                   COUNT(*) AS count
            FROM transactions t
            WHERE {cashflow_where(include_transfers)} AND t.txn_date >= %s{acct_sql}
            GROUP BY 1""",
        (uid, date(fy, fm, 1), *acct_params),
    )
    by_month = {r["month"]: r for r in rows}
    out = []
    for m in month_keys:
        r = by_month.get(m)
        income, expenses = (_f(r["income"]), _f(r["expenses"])) if r else (0.0, 0.0)
        out.append({"month": m, "income": round(income, 2), "expenses": round(expenses, 2),
                    "net": round(income - expenses, 2), "count": r["count"] if r else 0})
    return out


def top_merchants(uid, start, end, limit=20, account_ids=None, include_transfers=False):
    acct_sql, acct_params = _acct(account_ids)
    limit = max(1, min(int(limit or 20), 200))
    where = spending_where(include_transfers)
    rows = db.query(
        f"""SELECT t.merchant_key, MAX(t.merchant_name) AS merchant_name, COUNT(*) AS count,
                   SUM(-t.amount) AS total, AVG(-t.amount) AS avg, MAX(t.txn_date) AS last_date,
                   mode() WITHIN GROUP (ORDER BY t.category_id) AS category_id
            FROM transactions t
            WHERE {where} AND t.txn_date BETWEEN %s AND %s{acct_sql}
            GROUP BY t.merchant_key
            ORDER BY total DESC, count DESC
            LIMIT %s""",
        (uid, start, end, *acct_params, limit),
    )
    keys = [r["merchant_key"] for r in rows]
    sparks = {k: [0.0] * 6 for k in keys}
    if keys:
        month_keys = _month_list(6)
        fy, fm = (int(x) for x in month_keys[0].split("-"))
        srows = db.query(
            f"""SELECT t.merchant_key, to_char(date_trunc('month', t.txn_date), 'YYYY-MM') AS month, SUM(-t.amount) AS total
                FROM transactions t
                WHERE {where} AND t.txn_date >= %s AND t.merchant_key = ANY(%s){acct_sql}
                GROUP BY 1, 2""",
            (uid, date(fy, fm, 1), keys, *acct_params),
        )
        idx = {m: i for i, m in enumerate(month_keys)}
        for r in srows:
            if r["month"] in idx:
                sparks[r["merchant_key"]][idx[r["month"]]] = round(_f(r["total"]), 2)
    total_all = sum(_f(r["total"]) for r in rows)
    out = []
    for r in rows:
        total = _f(r["total"])
        out.append({
            "merchant_key": r["merchant_key"], "merchant_name": r["merchant_name"], "count": r["count"],
            "total": round(total, 2), "avg": round(_f(r["avg"]), 2),
            "last_date": r["last_date"].isoformat() if r["last_date"] else None,
            "category_id": r["category_id"], "sparkline": sparks[r["merchant_key"]],
            "pct": round(total / total_all * 100, 1) if total_all else 0.0,
        })
    return out


def month_over_month(uid, month=None, account_ids=None, include_transfers=False):
    y, m = parse_month(month)
    cur_start, cur_end = month_bounds(y, m)
    py, pm = shift_month(y, m, -1)
    prev_start, prev_end = month_bounds(py, pm)
    acct_sql, acct_params = _acct(account_ids)
    rows = db.query(
        f"""SELECT g.id, g.name, g.color, g.icon,
                   COALESCE(SUM(CASE WHEN t.txn_date BETWEEN %s AND %s THEN -t.amount END), 0) AS current,
                   COALESCE(SUM(CASE WHEN t.txn_date BETWEEN %s AND %s THEN -t.amount END), 0) AS previous
            FROM transactions t {_CAT_JOIN}
            WHERE {spending_where(include_transfers)} AND t.txn_date BETWEEN %s AND %s{acct_sql}
            GROUP BY g.id, g.name, g.color, g.icon
            ORDER BY current DESC, previous DESC""",
        (cur_start, cur_end, prev_start, prev_end, uid, prev_start, cur_end, *acct_params),
    )
    cats = []
    tot_cur = tot_prev = 0.0
    for r in rows:
        cur, prev = _f(r["current"]), _f(r["previous"])
        tot_cur += cur
        tot_prev += prev
        delta = round(cur - prev, 2)
        cats.append({
            "id": r["id"], "name": r["name"] or "Uncategorized", "color": r["color"] or "muted",
            "icon": r["icon"] or "tag", "current": round(cur, 2), "previous": round(prev, 2),
            "delta": delta, "pct": round(delta / prev * 100, 1) if prev else None,
        })
    delta = round(tot_cur - tot_prev, 2)
    return {
        "month": f"{y:04d}-{m:02d}", "previous_month": f"{py:04d}-{pm:02d}",
        "categories": cats,
        "totals": {"current": round(tot_cur, 2), "previous": round(tot_prev, 2), "delta": delta,
                   "pct": round(delta / tot_prev * 100, 1) if tot_prev else None},
    }


def largest(uid, start, end, limit=5, account_ids=None):
    acct_sql, acct_params = _acct(account_ids)
    rows = db.query(
        f"""SELECT t.id, t.txn_date, t.amount, t.merchant_name, t.description_clean, t.category_id, c.name AS category_name
            FROM transactions t LEFT JOIN categories c ON c.id = t.category_id
            WHERE {SPENDING} AND t.txn_date BETWEEN %s AND %s{acct_sql}
            ORDER BY t.amount ASC LIMIT %s""",
        (uid, start, end, *acct_params, max(1, min(int(limit or 5), 50))),
    )
    return rows_json(rows)


# ---------- Recurring / anomalies ----------

def _spend_rows(uid, since=None):
    params = [uid]
    where = ""
    if since:
        where = " AND t.txn_date >= %s"
        params.append(since)
    return db.query(
        f"""SELECT t.id, t.merchant_key, t.merchant_name, t.account_id, t.txn_date, t.amount, t.category_id
            FROM transactions t WHERE {SPENDING}{where} ORDER BY t.txn_date, t.id""",
        tuple(params),
    )


RECURRING_LOOKBACK_DAYS = 730


def recurring(uid, include_dismissed=False):
    rows = _spend_rows(uid, today() - timedelta(days=RECURRING_LOOKBACK_DAYS))
    dismissed = {r["merchant_key"] for r in db.query(
        "SELECT merchant_key FROM recurring_dismissals WHERE user_id = %s", (uid,))}
    series = recurring_lib.detect(rows, today())
    cat_names = {r["id"]: r for r in db.query(
        "SELECT id, name, color, icon FROM categories WHERE user_id = %s", (uid,))}
    out = []
    for s in series:
        s = dict(s)
        s["dismissed"] = s["merchant_key"] in dismissed
        if s["dismissed"] and not include_dismissed:
            continue
        cat = cat_names.get(s["category_id"])
        s["category_name"] = cat["name"] if cat else None
        s["category_color"] = cat["color"] if cat else "muted"
        s["last_date"] = s["last_date"].isoformat()
        s["next_expected"] = s["next_expected"].isoformat()
        out.append(s)
    return out


def anomalies(uid, lookback_days=45):
    t = today()
    rows = _spend_rows(uid, since=t - timedelta(days=400))
    dismissed = {r["transaction_id"] for r in db.query(
        "SELECT transaction_id FROM anomaly_dismissals WHERE user_id = %s", (uid,))}
    out = []
    for a in recurring_lib.anomalies(rows, t, lookback_days=lookback_days):
        if a["transaction_id"] in dismissed:
            continue
        a = dict(a)
        a["txn_date"] = a["txn_date"].isoformat()
        out.append(a)
    return out


# ---------- Dashboard aggregate ----------

def dashboard(uid, range_name=None, account_ids=None):
    r = resolve_range(range_name or "this-month")
    cur = summary(uid, r["start"], r["end"], account_ids)
    prev = summary(uid, r["prev_start"], r["prev_end"], account_ids) if r["prev_start"] else None
    cats = by_category(uid, r["start"], r["end"], "top", account_ids)
    top = cats[:6]
    rest = cats[6:]
    if rest:
        total = round(sum(c["total"] for c in rest), 2)
        top.append({"id": None, "name": "Other", "color": "muted", "icon": "more-horizontal", "parent_id": None,
                    "total": total, "count": sum(c["count"] for c in rest),
                    "pct": round(sum(c["pct"] for c in rest), 1)})
    monthly = [{"month": m["month"], "spent": m["expenses"], "income": m["income"], "net": m["net"]}
               for m in trends(uid, 12, account_ids)]
    acct_sql, acct_params = _acct(account_ids)
    recent = rows_json(db.query(
        f"""SELECT t.id, t.txn_date, t.amount, t.currency, t.merchant_name, t.description_clean, t.account_id,
                   t.category_id, t.category_status, t.is_transfer, c.name AS category_name, c.color AS category_color,
                   c.icon AS category_icon, a.name AS account_name
            FROM transactions t
            LEFT JOIN categories c ON c.id = t.category_id
            JOIN accounts a ON a.id = t.account_id
            WHERE t.user_id = %s{acct_sql}
            ORDER BY t.txn_date DESC, t.id DESC LIMIT 10""",
        (uid, *acct_params),
    ))
    review = db.query(
        """SELECT COUNT(*) FILTER (WHERE category_id IS NULL AND NOT is_transfer) AS uncategorized,
                  COUNT(*) FILTER (WHERE category_status = 'suggested') AS suggested
           FROM transactions WHERE user_id = %s""",
        (uid,), one=True,
    )
    rec = recurring_summary(uid)
    accounts = rows_json(db.query(
        """SELECT a.id, a.name, a.currency, a.account_type, a.color,
                  (SELECT t.balance FROM transactions t WHERE t.account_id = a.id AND t.balance IS NOT NULL
                     ORDER BY t.txn_date DESC, t.id DESC LIMIT 1) AS balance,
                  (SELECT MAX(t.txn_date) FROM transactions t WHERE t.account_id = a.id) AS last_txn_date
           FROM accounts a WHERE a.user_id = %s AND a.is_active ORDER BY a.name""",
        (uid,),
    ))
    return {
        "range": range_json(r),
        "kpis": {
            "spent": cur["expenses"], "spent_prev": prev["expenses"] if prev else None,
            "income": cur["income"], "income_prev": prev["income"] if prev else None,
            "net": cur["net"], "net_prev": prev["net"] if prev else None,
            "txn_count": cur["txn_count"],
        },
        "top_categories": top,
        "monthly": monthly,
        "recent": recent,
        "review_count": {"uncategorized": review["uncategorized"], "suggested": review["suggested"]},
        "recurring": rec,
        "accounts": accounts,
    }


def recurring_summary(uid):
    series = [s for s in recurring(uid) if s["is_active"]]
    t = today().isoformat()
    upcoming = sorted((s for s in series if s["next_expected"] >= t), key=lambda s: s["next_expected"])
    return {
        "count": len(series),
        "monthly_total": round(sum(s["monthly_equivalent"] for s in series), 2),
        "next": [{"merchant_name": s["merchant_name"], "merchant_key": s["merchant_key"],
                  "amount": s["median_amount"], "next_expected": s["next_expected"], "cadence": s["cadence"]}
                 for s in upcoming[:3]],
    }
