"""Admin analytics: the numbers behind the Overview, Revenue and Engagement pages.

Everything is computed from iSpend's own tables — the revenue ledger (subscription_events,
payments), user_activity_days, users and their first-use stamps — never from Stripe and never from
anyone's transactions. Two shapes of question:

- "how many in each bucket" (signups per week, churned customers per month): per-day counts from
  SQL, summed into buckets here, so bucket edges live in one tested place (admin_range.buckets);
- "how many at a moment" (MRR, paying customers, weekly active people): evaluated at the end of
  each bucket.

Answers are cached for five minutes in metric_cache and carry `as_of`. Admin accounts are left out
of every people count: they are the operator, not customers.
"""

import json
from datetime import date, datetime, timedelta, timezone

from flask import Blueprint, jsonify, request

import activity
import admin_range
import db
from auth import admin_required
from util import api_error

bp = Blueprint("admin_metrics", __name__, url_prefix="/api/admin/metrics")

CACHE_TTL_SECONDS = 300
ACTIVATION_DAYS = 7
MAX_POINTS = 400


# ---------- cache ----------

def cached(key, compute, refresh=False, ttl=CACHE_TTL_SECONDS):
    if not refresh:
        row = db.query(
            """SELECT payload, computed_at FROM metric_cache
                WHERE key = %s AND computed_at > now() - make_interval(secs => %s)""", (key, ttl), one=True)
        if row:
            return {**row["payload"], "as_of": row["computed_at"].isoformat()}
    payload = json.loads(json.dumps(compute(), default=_json_default))
    db.execute(
        """INSERT INTO metric_cache (key, payload, computed_at) VALUES (%s, %s, now())
           ON CONFLICT (key) DO UPDATE SET payload = EXCLUDED.payload, computed_at = now()""",
        (key, json.dumps(payload)))
    return {**payload, "as_of": datetime.now(timezone.utc).isoformat()}


def _json_default(v):
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    return float(v)


# ---------- building blocks ----------

def per_bucket(daily, bkts):
    """{date: n} summed into [(start, end)] buckets."""
    out = []
    for start, end in bkts:
        total, d = 0, start
        while d < end:
            total += daily.get(d, 0)
            d += timedelta(days=1)
        out.append(total)
    return out


def ratio(num, den):
    return None if not den else num / den


def delta(value, prev):
    """(absolute, relative) change; relative is None when there is nothing to compare with."""
    if value is None or prev is None:
        return None, None
    return value - prev, (None if prev == 0 else (value - prev) / abs(prev))


def tile(key, label, value, prev=None, unit="count", good="up", spark=None, help=None, currency=None,
         drill=None, agg="last"):
    """agg says how the sparkline may be thinned: 'sum' for flows (sign-ups per day), 'last' for
    levels (MRR at the end of each day), 'mean' for rates."""
    change, pct = delta(value, prev)
    return {"key": key, "label": label, "value": value, "prev": prev, "delta": change, "delta_pct": pct,
            "unit": unit, "good": good, "spark": spark, "help": help, "currency": currency, "drill": drill,
            "agg": agg}


def _daily(sql, params):
    return {r["d"]: int(r["n"]) for r in (db.query(sql, params) or [])}


def _bounds(rng, start=None, end=None):
    tz = rng["tz"]
    return {"a": admin_range.at(start or rng["start"], tz), "b": admin_range.at(end or rng["end"], tz), "tz": tz}


SIGNUPS_DAILY = """
SELECT (created_at AT TIME ZONE %(tz)s)::date AS d, COUNT(*) AS n
  FROM users WHERE role = 'user' AND created_at >= %(a)s AND created_at < %(b)s
 GROUP BY 1
"""

# By signup day: people who imported a statement within a week of signing up, among those whose
# week is over (a signup from yesterday has not had the chance yet).
ACTIVATION_DAILY = """
SELECT (created_at AT TIME ZONE %(tz)s)::date AS d,
       COUNT(*) FILTER (WHERE created_at < now() - make_interval(days => %(days)s)) AS n,
       COUNT(*) FILTER (WHERE created_at < now() - make_interval(days => %(days)s)
                          AND first_commit_at < created_at + make_interval(days => %(days)s)) AS activated
  FROM users WHERE role = 'user' AND created_at >= %(a)s AND created_at < %(b)s
 GROUP BY 1
"""

CHURN_DAILY = """
SELECT (occurred_at AT TIME ZONE %(tz)s)::date AS d, COUNT(DISTINCT user_id) AS n
  FROM subscription_events
 WHERE mrr_from_cents > 0 AND mrr_to_cents = 0 AND occurred_at >= %(a)s AND occurred_at < %(b)s
 GROUP BY 1
"""

FAILED_PAYMENTS_DAILY = """
SELECT (failed_at AT TIME ZONE %(tz)s)::date AS d, COUNT(*) AS n
  FROM payments WHERE failed_at >= %(a)s AND failed_at < %(b)s
 GROUP BY 1
"""

# Each event holds until the same user's next one: the value "at" a moment is the interval that
# contains it.
MRR_AT = """
WITH iv AS (
  SELECT user_id, COALESCE(currency, '') AS currency, mrr_to_cents AS mrr, occurred_at AS vf,
         LEAD(occurred_at, 1, 'infinity') OVER (PARTITION BY user_id ORDER BY occurred_at, id) AS vt
    FROM subscription_events WHERE user_id IS NOT NULL
)
SELECT p.at, iv.currency, SUM(iv.mrr) AS mrr, COUNT(*) FILTER (WHERE iv.mrr > 0) AS paying
  FROM unnest(%(points)s::timestamptz[]) AS p(at)
  JOIN iv ON iv.vf <= p.at AND iv.vt > p.at
 GROUP BY 1, 2
"""

ACTIVE_AT = """
SELECT p.day, COUNT(DISTINCT a.user_id) AS n
  FROM unnest(%(days)s::date[]) AS p(day)
  LEFT JOIN (SELECT uad.user_id, uad.day FROM user_activity_days uad
               JOIN users u ON u.id = uad.user_id AND u.role = 'user'
              WHERE uad.kinds & %(mask)s <> 0) a
    ON a.day > p.day - %(window)s AND a.day <= p.day
 GROUP BY 1
"""


def primary_currency():
    row = db.query(
        """SELECT currency FROM subscriptions WHERE currency IS NOT NULL
            GROUP BY currency ORDER BY COUNT(*) DESC, currency LIMIT 1""", one=True)
    return (row or {}).get("currency") or "usd"


def mrr_at(points, currency):
    """[(mrr_cents, paying)] at each point, in one currency, plus the other currencies seen."""
    if not points:
        return [], []
    rows = db.query(MRR_AT, {"points": list(points)}) or []
    by_point, others = {}, set()
    for r in rows:
        cur = r["currency"] or currency
        if cur != currency:
            if int(r["mrr"] or 0):
                others.add(cur)
            continue
        m, p = by_point.get(r["at"], (0, 0))
        by_point[r["at"]] = (m + int(r["mrr"] or 0), p + int(r["paying"] or 0))
    return [by_point.get(p, (0, 0)) for p in points], sorted(others)


def active_at(days, window, mask):
    if not days:
        return []
    rows = db.query(ACTIVE_AT, {"days": list(days), "window": window, "mask": mask}) or []
    counts = {r["day"]: int(r["n"]) for r in rows}
    return [counts.get(d, 0) for d in days]


def _ends(bkts):
    """The last day inside each bucket (for "as at" values) and its end-of-day instant."""
    return [end - timedelta(days=1) for _start, end in bkts]


def refresh_states_if_missing():
    """Cached access states are written hourly; right after an upgrade they may not exist yet."""
    missing = db.query("SELECT 1 FROM subscriptions WHERE ent_state IS NULL LIMIT 1", one=True)
    if missing:
        import ledger
        ledger.refresh_all_ent_states()


def state_counts():
    rows = db.query(
        """SELECT s.ent_state, COUNT(*) AS n,
                  COUNT(*) FILTER (WHERE s.ent_state = 'trialing'
                                     AND s.trial_end < now() + interval '7 days') AS ending_7d
             FROM subscriptions s JOIN users u ON u.id = s.user_id
            WHERE u.status <> 'deleted' AND u.role = 'user'
            GROUP BY 1""") or []
    counts = {r["ent_state"]: int(r["n"]) for r in rows}
    counts["trials_ending_7d"] = sum(int(r["ending_7d"]) for r in rows)
    return counts


def snapshot_series(metric, days):
    """Values recorded by the nightly snapshot for metrics that cannot be rebuilt later
    (how many people were on a trial on a given day)."""
    rows = db.query("SELECT day, value FROM metric_daily WHERE metric = %s AND dim = '' AND day = ANY(%s)",
                    (metric, list(days))) or []
    got = {r["day"]: float(r["value"]) for r in rows}
    return [got.get(d) for d in days] if got else None


# ---------- the overview ----------

def overview(rng):
    tz = rng["tz"]
    refresh_states_if_missing()
    bkts = admin_range.buckets(rng["start"], rng["end"], rng["bucket"])
    ends = _ends(bkts)
    end_points = [admin_range.at(e + timedelta(days=1), tz) for e in ends]
    prev_end_point = admin_range.at(rng["prev_end"], tz)
    cur = primary_currency()

    cur_b, prev_b = _bounds(rng), _bounds(rng, rng["prev_start"], rng["prev_end"])
    signups_daily = _daily(SIGNUPS_DAILY, cur_b)
    signups = per_bucket(signups_daily, bkts)
    prev_signups = sum(_daily(SIGNUPS_DAILY, prev_b).values())

    act_params = {**cur_b, "days": ACTIVATION_DAYS}
    act_rows = db.query(ACTIVATION_DAILY, act_params) or []
    matured = {r["d"]: int(r["n"]) for r in act_rows}
    activated_daily = {r["d"]: int(r["activated"]) for r in act_rows}
    act_n, act_y = per_bucket(matured, bkts), per_bucket(activated_daily, bkts)
    prev_rows = db.query(ACTIVATION_DAILY, {**prev_b, "days": ACTIVATION_DAYS}) or []
    prev_rate = ratio(sum(int(r["activated"]) for r in prev_rows), sum(int(r["n"]) for r in prev_rows))

    mrr_series, others = mrr_at(end_points + [prev_end_point], cur)
    mrr_prev, paying_prev = mrr_series.pop()
    mrr_now, paying_now = mrr_series[-1] if mrr_series else (0, 0)

    importers = active_at(ends + [rng["prev_end"] - timedelta(days=1)], 30, activity.IMPORT)
    importers_prev = importers.pop()
    wau = active_at(ends + [rng["prev_end"] - timedelta(days=1)], 7, activity.VALUE_MASK)
    wau_prev = wau.pop()
    mau = active_at(ends, 30, activity.VALUE_MASK)

    churned = per_bucket(_daily(CHURN_DAILY, cur_b), bkts)
    churned_prev = sum(_daily(CHURN_DAILY, prev_b).values())
    failed = per_bucket(_daily(FAILED_PAYMENTS_DAILY, cur_b), bkts)
    failed_prev = sum(_daily(FAILED_PAYMENTS_DAILY, prev_b).values())

    states = state_counts()
    trial_spark = snapshot_series("ent:trialing", ends)

    tiles = [
        tile("mrr", "MRR", mrr_now, mrr_prev, unit="money", currency=cur,
             spark=[m for m, _ in mrr_series],
             help="Monthly recurring revenue: what active subscriptions bring in each month. Yearly plans "
                  "count as a twelfth; trials and complimentary accounts count as nothing."),
        tile("paying", "Paying customers", paying_now, paying_prev, spark=[p for _, p in mrr_series],
             help="People with an active or past-due paid subscription."),
        tile("trialing", "On a free trial", states.get("trialing", 0), None, spark=trial_spark,
             help="Accounts in their free trial right now."),
        tile("trials_ending", "Trials ending in 7 days", states.get("trials_ending_7d", 0), None, good="neutral",
             help="Trials that end within a week — the people to nudge."),
        tile("signups", "Sign-ups", sum(signups), prev_signups, spark=signups,
             agg="sum", help="New accounts created in this period (administrators are not counted)."),
        tile("activation", "Imported in first week", ratio(sum(act_y), sum(act_n)), prev_rate, unit="pct",
             spark=[ratio(y, n) for y, n in zip(act_y, act_n, strict=True)],
             agg="mean", help=f"Of the people who signed up in this period at least {ACTIVATION_DAYS} days ago, the share "
                  f"who imported a statement within {ACTIVATION_DAYS} days. This is the activation rate."),
        tile("importers", "Monthly importers", importers[-1] if importers else 0, importers_prev, spark=importers,
             help="People who imported at least one statement in the 30 days to the end of the period. "
                  "Statements arrive monthly, so this is the truest sign of a healthy account."),
        tile("wau", "Weekly active", wau[-1] if wau else 0, wau_prev, spark=wau,
             help="People who imported, sorted transactions or read a report in the last 7 days of the period. "
                  "Opening the app alone does not count."),
        tile("churned", "Cancelled", sum(churned), churned_prev, good="down", spark=churned,
             agg="sum", help="Paying customers whose subscription ended in this period."),
        tile("failed_payments", "Failed payments", sum(failed), failed_prev, good="down", spark=failed,
             agg="sum", help="Renewal charges that did not go through. Stripe retries them; each failure opens a grace period."),
    ]
    series = {
        "labels": [s.isoformat() for s, _e in bkts],
        "signups": signups,
        "activated": per_bucket(activated_daily, bkts),
        "wau": wau,
        "mau": mau,
        "mrr": [m for m, _ in mrr_series],
    }
    if rng["compare"]:
        pbkts = admin_range.buckets(rng["prev_start"], rng["prev_end"], rng["bucket"])[:len(bkts)]
        series["prev"] = {
            "signups": per_bucket(_daily(SIGNUPS_DAILY, prev_b), pbkts),
            "wau": active_at(_ends(pbkts), 7, activity.VALUE_MASK),
            "mrr": [m for m, _ in mrr_at([admin_range.at(e + timedelta(days=1), tz) for e in _ends(pbkts)], cur)[0]],
        }
    return {"range": _range_json(rng), "currency": cur, "other_currencies": others, "tiles": tiles,
            "series": series, "alerts": alerts()}


def _range_json(rng):
    return {k: (v.isoformat() if isinstance(v, date) else v) for k, v in rng.items() if k != "tz"}


def alerts():
    """Things that need a person, newest first. Each links to where it can be dealt with."""
    row = db.query("""
        SELECT (SELECT COUNT(*) FROM login_events WHERE new_network AND created_at > now() - interval '7 days') AS new_network,
               (SELECT COUNT(*) FROM statements WHERE status IN ('parsing', 'committing')
                  AND updated_at < now() - interval '15 minutes') AS stuck_imports,
               (SELECT COUNT(*) FROM stripe_events WHERE status = 'failed'
                  AND received_at > now() - interval '24 hours') AS webhook_failures,
               (SELECT COUNT(*) FROM email_log WHERE status = 'failed'
                  AND created_at > now() - interval '24 hours') AS email_failures,
               (SELECT COUNT(*) FROM app_errors WHERE created_at > now() - interval '24 hours') AS errors,
               (SELECT MAX(finished_at) FROM backup_jobs WHERE kind = 'backup' AND status = 'done') AS last_backup
        """, one=True) or {}
    out = []
    if row.get("new_network"):
        out.append({"kind": "new_admin_network", "level": "warn", "count": int(row["new_network"]),
                    "text": "An administrator signed in from a new network this week.", "href": "#activity?admin=1"})
    if row.get("stuck_imports"):
        out.append({"kind": "stuck_imports", "level": "warn", "count": int(row["stuck_imports"]),
                    "text": "Imports have been stuck for more than 15 minutes.", "href": "#system"})
    if row.get("webhook_failures"):
        out.append({"kind": "webhook_failures", "level": "error", "count": int(row["webhook_failures"]),
                    "text": "Stripe webhooks failed in the last day.", "href": "#system"})
    if row.get("email_failures"):
        out.append({"kind": "email_failures", "level": "warn", "count": int(row["email_failures"]),
                    "text": "Emails failed to send in the last day.", "href": "#system"})
    if row.get("errors"):
        out.append({"kind": "errors", "level": "warn", "count": int(row["errors"]),
                    "text": "Server errors were recorded in the last day.", "href": "#system"})
    last = row.get("last_backup")
    if last is None or last < datetime.now(timezone.utc) - timedelta(days=7):
        out.append({"kind": "backup_stale", "level": "info", "count": 0,
                    "text": "No backup in the last 7 days." if last else "No backup has been made yet.",
                    "href": "#backup"})
    return out


# ---------- nightly snapshot ----------

SNAPSHOT_STATES = ("trialing", "active", "grace", "read_only")


def snapshot(day):
    """Record the values of `day` that cannot be recomputed later. Idempotent."""
    states = state_counts()
    rows = [(day, f"ent:{s}", "", states.get(s, 0)) for s in SNAPSHOT_STATES]
    for cur, (mrr, paying) in _mrr_by_currency().items():
        rows.append((day, "mrr_cents", cur, mrr))
        rows.append((day, "paying", cur, paying))
    for d, metric, dim, value in rows:
        db.execute(
            """INSERT INTO metric_daily (day, metric, dim, value) VALUES (%s, %s, %s, %s)
               ON CONFLICT (day, metric, dim) DO UPDATE SET value = EXCLUDED.value, computed_at = now()""",
            (d, metric, dim, value))
    return len(rows)


def _mrr_by_currency():
    rows = db.query("""SELECT COALESCE(currency, '') AS currency, SUM(mrr_cents) AS mrr,
                              COUNT(*) FILTER (WHERE mrr_cents > 0) AS paying
                         FROM subscriptions GROUP BY 1""") or []
    return {r["currency"]: (int(r["mrr"] or 0), int(r["paying"] or 0)) for r in rows if r["currency"]}


# ---------- routes ----------

def _range_or_400():
    try:
        return admin_range.parse(request.args), None
    except admin_range.RangeError as e:
        return None, api_error(str(e))


@bp.get("/overview")
@admin_required
def overview_route():
    rng, err = _range_or_400()
    if err:
        return err
    return jsonify(cached(f"overview:{admin_range.cache_key(rng)}", lambda: overview(rng),
                          refresh=request.args.get("refresh") == "1"))
