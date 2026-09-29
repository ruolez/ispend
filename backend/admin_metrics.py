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

from flask import Blueprint, jsonify, request, session

import activity
import admin_range
import db
from auth import admin_required
from util import api_error, audit, rows_json

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
    signed_up = f"signup_from={rng['start'].isoformat()}&signup_to={(rng['end'] - timedelta(days=1)).isoformat()}"

    tiles = [
        tile("mrr", "MRR", mrr_now, mrr_prev, unit="money", currency=cur,
             spark=[m for m, _ in mrr_series],
             help="Monthly recurring revenue: what active subscriptions bring in each month. Yearly plans "
                  "count as a twelfth; trials and complimentary accounts count as nothing.", drill="#revenue"),
        tile("paying", "Paying customers", paying_now, paying_prev, spark=[p for _, p in mrr_series],
             help="People with an active or past-due paid subscription.", drill="#customers?state=active,grace"),
        tile("trialing", "On a free trial", states.get("trialing", 0), None, spark=trial_spark,
             help="Accounts in their free trial right now.", drill="#customers?state=trialing"),
        tile("trials_ending", "Trials ending in 7 days", states.get("trials_ending_7d", 0), None, good="neutral",
             help="Trials that end within a week — the people to nudge.", drill="#customers?state=trialing&trial_ending=7"),
        tile("signups", "Sign-ups", sum(signups), prev_signups, spark=signups,
             agg="sum", help="New accounts created in this period.", drill=f"#customers?{signed_up}&sort=created_at&dir=desc"),
        tile("activation", "Imported in first week", ratio(sum(act_y), sum(act_n)), prev_rate, unit="pct",
             spark=[ratio(y, n) for y, n in zip(act_y, act_n, strict=True)],
             agg="mean", help=f"Of the people who signed up in this period at least {ACTIVATION_DAYS} days ago, the share "
                  f"who imported a statement within {ACTIVATION_DAYS} days. This is the activation rate.", drill="#engagement"),
        tile("importers", "Monthly importers", importers[-1] if importers else 0, importers_prev, spark=importers,
             help="People who imported at least one statement in the 30 days to the end of the period. "
                  "Statements arrive monthly, so this is the truest sign of a healthy account.", drill="#engagement"),
        tile("wau", "Weekly active", wau[-1] if wau else 0, wau_prev, spark=wau,
             help="People who imported, sorted transactions or read a report in the last 7 days of the period. "
                  "Opening the app alone does not count.", drill="#engagement"),
        tile("churned", "Cancelled", sum(churned), churned_prev, good="down", spark=churned,
             agg="sum", help="Paying customers whose subscription ended in this period.", drill="#revenue"),
        tile("failed_payments", "Failed payments", sum(failed), failed_prev, good="down", spark=failed,
             agg="sum", help="Renewal charges that did not go through. Stripe retries them; each failure opens a grace period.",
             drill="#customers?state=grace"),
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
            "series": series}


def _range_json(rng):
    return {k: (v.isoformat() if isinstance(v, date) else v) for k, v in rng.items() if k != "tz"}


# A customer counts as started when they imported a statement within their first week.
_NOT_ACTIVATED = "NOT (u.first_commit_at IS NOT NULL AND u.first_commit_at < u.created_at + interval '7 days')"

ALERT_ORDER = {"error": 0, "warn": 1, "info": 2}
# Alerts about things that happened (a failure yesterday) rather than a state that is still true
# (a customer still in their grace period). Fixing the cause cannot clear them, so the admin can
# mark them seen: only events after that moment count again.
DISMISSIBLE = {
    "new_network": "7 days", "webhook_failures": "24 hours", "email_failures": "24 hours",
    "errors": "24 hours", "failed_imports": "24 hours",
}


def _seen_at(kind):
    return db.get_setting(f"alert_seen:{kind}") or None


def dismiss_alert(kind):
    db.set_setting(f"alert_seen:{kind}", datetime.now(timezone.utc).isoformat())


def alerts(admin_id=None):
    """The Home inbox: things that need a person, the most urgent first. Each links to the list or
    page where it can be dealt with; hash queries are turned into filters by the console's router.

    Never cached: it is what the admin checks after fixing something, so it must say so at once.
    Backups, email and errors use the System page's own checks, so the two can never disagree."""
    import admin_system
    since = {k: _seen_at(k) for k in DISMISSIBLE}
    window = {k: f"GREATEST(now() - interval '{v}', COALESCE(%({k})s::timestamptz, '-infinity'))"
              for k, v in DISMISSIBLE.items()}
    row = db.query(f"""
        SELECT (SELECT COUNT(*) FROM login_events WHERE new_network AND created_at > {window['new_network']}) AS new_network,
               (SELECT COUNT(*) FROM subscriptions s JOIN users u ON u.id = s.user_id
                 WHERE s.ent_state = 'grace' AND u.role = 'user' AND u.status <> 'deleted') AS payment_due,
               (SELECT COUNT(*) FROM subscriptions s JOIN users u ON u.id = s.user_id
                 WHERE s.ent_state = 'trialing' AND s.trial_end < now() + interval '3 days'
                   AND u.role = 'user' AND u.status = 'active' AND {_NOT_ACTIVATED}) AS trials_not_started,
               (SELECT COUNT(*) FROM statements st JOIN users u ON u.id = st.user_id
                 WHERE st.status = 'error' AND st.created_at > {window['failed_imports']} AND u.role = 'user') AS failed_imports,
               (SELECT COUNT(*) FROM statements WHERE status IN ('parsing', 'committing')
                  AND updated_at < now() - interval '15 minutes') AS stuck_imports,
               (SELECT COUNT(*) FROM stripe_events WHERE status = 'failed'
                  AND received_at > {window['webhook_failures']}) AS webhook_failures,
               (SELECT COUNT(*) FROM email_log WHERE status = 'failed'
                  AND created_at > {window['email_failures']}) AS email_failures,
               (SELECT COUNT(*) FROM app_errors WHERE created_at > {window['errors']}) AS errors,
               (SELECT COUNT(*) FROM support_reports WHERE status IN ('open', 'in_progress')) AS support_reports
        """, since, one=True) or {}
    out = []

    def add(kind, level, text, href):
        n = int(row.get(kind) or 0)
        if n:
            out.append({"kind": kind, "level": level, "count": n, "text": text(n), "href": href,
                        "dismissible": kind in DISMISSIBLE})

    def people(n):
        return "1 customer" if n == 1 else f"{n} customers"

    add("payment_due", "error", lambda n: f"{people(n)} could not be charged and are in their grace period.",
        "#customers?state=grace")
    add("webhook_failures", "error", lambda n: "Stripe could not reach iSpend in the last day.", "#system")
    add("support_reports", "warn",
        lambda n: f"{n} problem report{'s are' if n != 1 else ' is'} waiting for your reply.", "#support")
    add("trials_not_started", "warn",
        lambda n: f"{people(n)} end their trial within 3 days without having imported a statement.",
        "#customers?state=trialing&trial_ending=3&activated=0")
    add("failed_imports", "warn", lambda n: f"{n} statement{'s' if n != 1 else ''} could not be read in the last day.",
        "#imports")
    add("stuck_imports", "warn", lambda n: "Imports have been stuck for more than 15 minutes.", "#system")
    add("email_failures", "warn", lambda n: "Emails failed to send in the last day.", "#system")
    add("errors", "warn", lambda n: "Server errors were recorded in the last day.", "#system")
    add("new_network", "warn", lambda n: "You signed in from a new network this week.", "#activity?admin=1")

    backup = admin_system.backup_health()
    if backup["status"] == admin_system.ERROR:
        out.append({"kind": "backup_failed", "level": "error", "count": 0, "href": "#backup",
                    "text": "The last backup failed. Make one again from Backups."})
    elif backup["status"] == admin_system.WARN:
        out.append({"kind": "backup_stale", "level": "info", "count": 0, "href": "#backup",
                    "text": (f"No backup in the last {admin_system.BACKUP_STALE_DAYS} days."
                             if backup["last_success_at"] else "No backup has been made yet.")})
    if admin_id is not None:
        import privacy
        if any(privacy.owned_data_counts(admin_id).values()):
            out.append({"kind": "leftover_data", "level": "info", "count": 0, "href": "#settings/account",
                        "text": "Your admin account still holds finance data from before. Delete it in Settings › My account."})
    out.sort(key=lambda a: ALERT_ORDER[a["level"]])
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
    payload = cached(f"overview:{admin_range.cache_key(rng)}", lambda: overview(rng),
                     refresh=request.args.get("refresh") == "1")
    return jsonify({**payload, "alerts": alerts(session.get("user_id"))})


@bp.get("/alerts")
@admin_required
def alerts_route():
    """The inbox alone, for re-checking it without the numbers."""
    return jsonify(alerts(session.get("user_id")))


@bp.post("/alerts/<kind>/dismiss")
@admin_required
def dismiss_route(kind):
    if kind not in DISMISSIBLE:
        return api_error("That alert clears itself once the cause is fixed", 400)
    dismiss_alert(kind)
    audit("admin.alert.dismiss", {"kind": kind})
    return jsonify(alerts(session.get("user_id")))


# ---------- revenue ----------

# Each user's monthly value at the start and at the end of [a, b), and whether they had ever paid
# before a (a return after a lapse is reactivation, not new business).
BRIDGE = """
WITH iv AS (
  SELECT user_id, COALESCE(currency, '') AS currency, mrr_to_cents AS mrr, occurred_at AS vf,
         LEAD(occurred_at, 1, 'infinity') OVER (PARTITION BY user_id ORDER BY occurred_at, id) AS vt
    FROM subscription_events WHERE user_id IS NOT NULL
),
s AS (SELECT user_id, currency, mrr FROM iv WHERE vf <= %(a)s AND vt > %(a)s),
e AS (SELECT user_id, currency, mrr FROM iv WHERE vf <= %(b)s AND vt > %(b)s),
j AS (SELECT COALESCE(s.user_id, e.user_id) AS uid, COALESCE(NULLIF(e.currency, ''), NULLIF(s.currency, ''), '') AS cur,
             COALESCE(s.mrr, 0) AS m0, COALESCE(e.mrr, 0) AS m1
        FROM s FULL JOIN e ON e.user_id = s.user_id),
prior AS (SELECT DISTINCT user_id FROM subscription_events WHERE mrr_to_cents > 0 AND occurred_at < %(a)s)
SELECT j.cur,
       COALESCE(SUM(m0), 0) AS start_mrr, COALESCE(SUM(m1), 0) AS end_mrr,
       COALESCE(SUM(m1) FILTER (WHERE m0 = 0 AND m1 > 0 AND p.user_id IS NULL), 0) AS new_mrr,
       COALESCE(SUM(m1) FILTER (WHERE m0 = 0 AND m1 > 0 AND p.user_id IS NOT NULL), 0) AS reactivation_mrr,
       COALESCE(SUM(m1 - m0) FILTER (WHERE m0 > 0 AND m1 > m0), 0) AS expansion_mrr,
       COALESCE(SUM(m0 - m1) FILTER (WHERE m1 > 0 AND m1 < m0), 0) AS contraction_mrr,
       COALESCE(SUM(m0) FILTER (WHERE m0 > 0 AND m1 = 0), 0) AS churned_mrr,
       COUNT(*) FILTER (WHERE m0 > 0) AS customers_start,
       COUNT(*) FILTER (WHERE m1 > 0) AS customers_end,
       COUNT(*) FILTER (WHERE m0 = 0 AND m1 > 0) AS customers_new,
       COUNT(*) FILTER (WHERE m0 > 0 AND m1 = 0) AS customers_churned
  FROM j LEFT JOIN prior p ON p.user_id = j.uid
 GROUP BY j.cur
"""

BRIDGE_KEYS = ("start_mrr", "end_mrr", "new_mrr", "reactivation_mrr", "expansion_mrr", "contraction_mrr",
               "churned_mrr", "customers_start", "customers_end", "customers_new", "customers_churned")


def bridge(a, b, currency):
    """MRR movement between two instants, in one currency (rows in other currencies are skipped,
    never added together)."""
    rows = db.query(BRIDGE, {"a": a, "b": b}) or []
    out = {k: 0 for k in BRIDGE_KEYS}
    for r in rows:
        if (r["cur"] or currency) == currency:
            for k in BRIDGE_KEYS:
                out[k] += int(r[k] or 0)
    return out


def rates(br):
    """The ratios every revenue report quotes, from one bridge. None when there is no base."""
    start = br["start_mrr"]
    return {
        "customer_churn": ratio(br["customers_churned"], br["customers_start"]),
        "gross_revenue_churn": ratio(br["churned_mrr"] + br["contraction_mrr"], start),
        "nrr": ratio(start + br["expansion_mrr"] - br["contraction_mrr"] - br["churned_mrr"], start),
        "net_new_mrr": br["new_mrr"] + br["reactivation_mrr"] + br["expansion_mrr"] - br["contraction_mrr"] - br["churned_mrr"],
        "arpa": ratio(br["end_mrr"], br["customers_end"]),
    }


def _bridge_buckets(rng):
    """A daily money bridge over a month is mostly zeros; group it by week."""
    bucket = "week" if rng["bucket"] == "day" and rng["days"] > 14 else rng["bucket"]
    return admin_range.buckets(rng["start"], rng["end"], bucket), bucket


PAYMENTS_IN_RANGE = """
SELECT p.id, p.user_id, u.username, u.email, p.status, p.currency, p.amount_paid_cents, p.amount_refunded_cents,
       p.plan, p.billing_reason, COALESCE(p.paid_at, p.failed_at) AS at
  FROM payments p LEFT JOIN users u ON u.id = p.user_id
 WHERE COALESCE(p.paid_at, p.failed_at) >= %(a)s AND COALESCE(p.paid_at, p.failed_at) < %(b)s
 ORDER BY COALESCE(p.paid_at, p.failed_at) DESC LIMIT 25
"""

PAYMENT_TOTALS = """
SELECT COALESCE(SUM(amount_paid_cents) FILTER (WHERE currency = %(cur)s), 0) AS collected,
       COALESCE(SUM(amount_refunded_cents) FILTER (WHERE currency = %(cur)s), 0) AS refunded,
       COUNT(*) FILTER (WHERE failed_at >= %(a)s AND failed_at < %(b)s) AS failed
  FROM payments WHERE COALESCE(paid_at, failed_at) >= %(a)s AND COALESCE(paid_at, failed_at) < %(b)s
"""


def revenue(rng):
    tz = rng["tz"]
    cur = primary_currency()
    a, b = admin_range.at(rng["start"], tz), admin_range.at(rng["end"], tz)
    pa, pb = admin_range.at(rng["prev_start"], tz), admin_range.at(rng["prev_end"], tz)
    now_br, prev_br = bridge(a, b, cur), bridge(pa, pb, cur)
    now_r, prev_r = rates(now_br), rates(prev_br)
    pay = db.query(PAYMENT_TOTALS, {"a": a, "b": b, "cur": cur}, one=True) or {}
    prev_pay = db.query(PAYMENT_TOTALS, {"a": pa, "b": pb, "cur": cur}, one=True) or {}

    bkts = admin_range.buckets(rng["start"], rng["end"], rng["bucket"])
    points = [admin_range.at(e, tz) for _s, e in bkts]
    mrr_series, _others = mrr_at(points, cur)
    bb, bb_kind = _bridge_buckets(rng)
    moves = [bridge(admin_range.at(s, tz), admin_range.at(e, tz), cur) for s, e in bb]
    plan_mix = db.query(
        """SELECT COALESCE(plan, 'other') AS plan, COUNT(*) AS customers, SUM(mrr_cents) AS mrr_cents
             FROM subscriptions WHERE mrr_cents > 0 AND COALESCE(currency, %s) = %s GROUP BY 1 ORDER BY 3 DESC""",
        (cur, cur)) or []
    money = {"unit": "money", "currency": cur}
    tiles = [
        tile("mrr", "MRR", now_br["end_mrr"], prev_br["end_mrr"], spark=[m for m, _ in mrr_series], **money,
             help="Monthly recurring revenue at the end of the period."),
        tile("arr", "ARR", now_br["end_mrr"] * 12, prev_br["end_mrr"] * 12, **money,
             help="Annual run rate: MRR × 12."),
        tile("paying", "Paying customers", now_br["customers_end"], prev_br["customers_end"],
             spark=[p for _, p in mrr_series], help="Customers with an active or past-due paid subscription.",
             drill="#customers?state=active,grace"),
        tile("arpa", "Average per customer", now_r["arpa"], prev_r["arpa"], **money,
             help="MRR divided by paying customers (ARPA)."),
        tile("net_new_mrr", "Net new MRR", now_r["net_new_mrr"], prev_r["net_new_mrr"], **money,
             spark=[rates(m)["net_new_mrr"] for m in moves], agg="sum",
             help="New + returning + upgrades − downgrades − cancellations in the period."),
        tile("customer_churn", "Customer churn", now_r["customer_churn"], prev_r["customer_churn"], unit="pct",
             good="down", help="Paying customers at the start of the period who had stopped paying by its end."),
        tile("revenue_churn", "Revenue churn", now_r["gross_revenue_churn"], prev_r["gross_revenue_churn"],
             unit="pct", good="down", help="MRR lost to cancellations and downgrades, as a share of MRR at the start."),
        tile("nrr", "Net revenue retention", now_r["nrr"], prev_r["nrr"], unit="pct",
             help="What this period's starting customers pay now, as a share of what they paid at the start "
                  "(above 100% means upgrades outweigh losses)."),
        tile("collected", "Collected", int(pay.get("collected") or 0), int(prev_pay.get("collected") or 0), **money,
             help="Money actually charged in the period, before refunds."),
        tile("refunded", "Refunded", int(pay.get("refunded") or 0), int(prev_pay.get("refunded") or 0), good="down",
             **money, help="Refunds on payments made in the period."),
    ]
    since = db.query("SELECT MIN(occurred_at) AS at FROM subscription_events WHERE kind <> 'baseline'", one=True) or {}
    return {
        "range": _range_json(rng), "currency": cur, "tiles": tiles, "bridge_total": now_br,
        "history_from": since.get("at").isoformat() if since.get("at") else None,
        "series": {"labels": [s.isoformat() for s, _e in bkts], "mrr": [m for m, _ in mrr_series],
                   "paying": [p for _, p in mrr_series]},
        "bridge": {"labels": [s.isoformat() for s, _e in bb], "bucket": bb_kind,
                   **{k: [m[f"{k}_mrr"] for m in moves] for k in ("new", "reactivation", "expansion", "contraction", "churned")}},
        "plan_mix": rows_json(plan_mix),
        "payments": rows_json(db.query(PAYMENTS_IN_RANGE, {"a": a, "b": b}) or []),
    }


# ---------- trial to paid ----------

TRIAL_COHORTS = """
WITH c AS (
  SELECT u.id, u.created_at, date_trunc(%(bucket)s, u.created_at AT TIME ZONE %(tz)s)::date AS cohort
    FROM users u
   WHERE u.role = 'user' AND u.created_at >= %(a)s AND u.created_at < %(b)s
     AND NOT EXISTS (SELECT 1 FROM subscription_events e WHERE e.user_id = u.id AND e.kind = 'baseline'
                       AND COALESCE((e.detail->>'comped')::boolean, false))),
fp AS (SELECT user_id, MIN(paid_at) AS paid_at FROM payments
        WHERE amount_paid_cents > 0 AND paid_at IS NOT NULL GROUP BY 1),
card AS (SELECT user_id, MIN(occurred_at) AS at FROM subscription_events
          WHERE stripe_subscription_id IS NOT NULL OR mrr_to_cents > 0 GROUP BY 1)
SELECT c.cohort, COUNT(*) AS signups,
       COUNT(card.at) AS added_card,
       COUNT(*) FILTER (WHERE fp.paid_at < c.created_at + make_interval(days => %(window)s)) AS paid_in_window,
       COUNT(fp.paid_at) AS paid_ever,
       COUNT(*) FILTER (WHERE now() < c.created_at + make_interval(days => %(window)s)) AS maturing,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY EXTRACT(epoch FROM fp.paid_at - c.created_at) / 86400)
         FILTER (WHERE fp.paid_at IS NOT NULL) AS median_days_to_paid
  FROM c LEFT JOIN fp ON fp.user_id = c.id LEFT JOIN card ON card.user_id = c.id
 GROUP BY 1 ORDER BY 1
"""


def trial_cohorts(rng):
    import entitlement
    window = entitlement.trial_days() + 7
    bucket = "month" if rng["days"] > 92 else "week"
    rows = db.query(TRIAL_COHORTS, {**_bounds(rng), "bucket": bucket, "window": window}) or []
    out = []
    for r in rows_json(rows):
        settled = r["signups"] - r["maturing"]
        out.append({**r, "rate": ratio(r["paid_in_window"], settled) if settled else None,
                    "median_days_to_paid": None if r["median_days_to_paid"] is None else round(r["median_days_to_paid"], 1)})
    total = {k: sum(r[k] for r in out) for k in ("signups", "added_card", "paid_in_window", "paid_ever", "maturing")}
    total["rate"] = ratio(total["paid_in_window"], total["signups"] - total["maturing"])
    return {"range": _range_json(rng), "bucket": bucket, "window_days": window, "rows": out, "total": total}


# ---------- activation funnel ----------

FUNNEL = """
WITH c AS (SELECT u.id, u.created_at, u.email, u.email_verified_at AS ca, u.first_upload_at AS ua,
                  u.first_commit_at AS ma, COALESCE(sa.channel, 'unknown') AS channel
             FROM users u LEFT JOIN signup_attribution sa ON sa.user_id = u.id
            WHERE u.role = 'user' AND u.created_at >= %(a)s AND u.created_at < %(b)s),
fp AS (SELECT user_id, MIN(paid_at) AS pa FROM payments WHERE amount_paid_cents > 0 AND paid_at IS NOT NULL GROUP BY 1),
s AS (SELECT c.*, fp.pa,
             (NOT %(confirm)s OR c.ca IS NOT NULL) AS s1,
             (NOT %(confirm)s OR c.ca IS NOT NULL) AND c.ua IS NOT NULL AS s2,
             (NOT %(confirm)s OR c.ca IS NOT NULL) AND c.ua IS NOT NULL AND c.ma < c.created_at + interval '7 days' AS s3
        FROM c LEFT JOIN fp ON fp.user_id = c.id)
SELECT {group} COUNT(*) AS signed_up,
       COUNT(*) FILTER (WHERE s1) AS confirmed,
       COUNT(*) FILTER (WHERE s2) AS uploaded,
       COUNT(*) FILTER (WHERE s3) AS activated,
       COUNT(*) FILTER (WHERE s3 AND pa IS NOT NULL) AS paid,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY EXTRACT(epoch FROM ca - created_at) / 3600) FILTER (WHERE s1 AND ca IS NOT NULL) AS h_confirm,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY EXTRACT(epoch FROM ua - COALESCE(ca, created_at)) / 3600) FILTER (WHERE s2) AS h_upload,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY EXTRACT(epoch FROM ma - ua) / 3600) FILTER (WHERE s3) AS h_activate,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY EXTRACT(epoch FROM pa - ma) / 3600) FILTER (WHERE s3 AND pa IS NOT NULL) AS h_paid
  FROM s {group_by}
"""

FUNNEL_STEPS = [("signed_up", "Signed up", None), ("confirmed", "Confirmed email", "h_confirm"),
                ("uploaded", "Uploaded a statement", "h_upload"), ("activated", "Imported within a week", "h_activate"),
                ("paid", "Paid", "h_paid")]


def funnel(rng):
    import auth
    confirm = auth.verification_required_setting()
    params = {**_bounds(rng), "confirm": confirm}
    row = db.query(FUNNEL.format(group="", group_by=""), params, one=True) or {}
    steps = [s for s in FUNNEL_STEPS if confirm or s[0] != "confirmed"]
    first = int(row.get("signed_up") or 0)
    out, prev = [], None
    for key, label, hours in steps:
        n = int(row.get(key) or 0)
        out.append({"key": key, "label": label, "n": n, "pct_prev": ratio(n, prev) if prev is not None else None,
                    "pct_first": ratio(n, first), "median_hours": None if not hours or row.get(hours) is None
                    else round(float(row[hours]), 1)})
        prev = n
    by_source = db.query(FUNNEL.format(group="channel,", group_by="GROUP BY channel ORDER BY COUNT(*) DESC"), params) or []
    sources = [{"channel": r["channel"], "signups": r["signed_up"], "activated": r["activated"], "paid": r["paid"],
                "activation_rate": ratio(r["activated"], r["signed_up"]), "paid_rate": ratio(r["paid"], r["signed_up"])}
               for r in by_source]
    return {"range": _range_json(rng), "confirmation_required": confirm, "steps": out, "by_source": sources}


# ---------- engagement ----------

KIND_USE = """
SELECT COUNT(DISTINCT uad.user_id) FILTER (WHERE uad.kinds & %(value)s <> 0) AS active,
       COUNT(DISTINCT uad.user_id) FILTER (WHERE uad.kinds & 2 <> 0) AS import,
       COUNT(DISTINCT uad.user_id) FILTER (WHERE uad.kinds & 4 <> 0) AS categorize,
       COUNT(DISTINCT uad.user_id) FILTER (WHERE uad.kinds & 8 <> 0) AS report,
       COUNT(DISTINCT uad.user_id) FILTER (WHERE uad.kinds & 16 <> 0) AS dashboard,
       COUNT(DISTINCT uad.user_id) FILTER (WHERE uad.kinds & 1 <> 0) AS seen,
       COUNT(*) FILTER (WHERE uad.kinds & %(value)s <> 0) AS active_days
  FROM user_activity_days uad JOIN users u ON u.id = uad.user_id AND u.role = 'user'
 WHERE uad.day >= %(d0)s AND uad.day < %(d1)s
"""

RETENTION = """
WITH c AS (SELECT id AS user_id, date_trunc(%(unit)s, created_at AT TIME ZONE %(tz)s)::date AS cohort
             FROM users WHERE role = 'user' AND created_at >= %(a)s AND created_at < %(b)s),
sz AS (SELECT cohort, COUNT(*) AS n FROM c GROUP BY cohort),
a AS (SELECT DISTINCT user_id, date_trunc(%(unit)s, day)::date AS period
        FROM user_activity_days WHERE kinds & %(mask)s <> 0 AND day >= %(a_day)s)
SELECT c.cohort, sz.n AS size, a.period, COUNT(DISTINCT a.user_id) AS retained
  FROM c JOIN sz USING (cohort) JOIN a ON a.user_id = c.user_id AND a.period >= c.cohort
 GROUP BY 1, 2, 3 ORDER BY 1, 3
"""


def _periods_between(start, end, unit):
    if unit == "week":
        return (end - start).days // 7
    return (end.year - start.year) * 12 + end.month - start.month


def retention(rng, mode="weekly"):
    """Cohort rows (signup week or month) × periods since signup. weekly: any value event;
    monthly: imported a statement — the rhythm a monthly statement actually has."""
    unit, mask, periods = ("week", activity.VALUE_MASK, 12) if mode == "weekly" else ("month", activity.IMPORT, 12)
    tz = rng["tz"]
    today = activity.today()
    span = timedelta(weeks=periods) if unit == "week" else timedelta(days=31 * periods)
    start = max(rng["start"], today - span)
    rows = db.query(RETENTION, {"unit": unit, "tz": tz, "mask": mask, "a": admin_range.at(start, tz),
                                "b": admin_range.at(rng["end"], tz), "a_day": start}) or []
    cohorts = {}
    for r in rows:
        c = cohorts.setdefault(r["cohort"], {"cohort": r["cohort"].isoformat(), "size": int(r["size"]), "cells": {}})
        n = _periods_between(r["cohort"], r["period"], unit)
        if 0 <= n < periods:
            c["cells"][n] = int(r["retained"])
    sizes = db.query(
        """SELECT date_trunc(%(unit)s, created_at AT TIME ZONE %(tz)s)::date AS cohort, COUNT(*) AS n
             FROM users WHERE role = 'user' AND created_at >= %(a)s AND created_at < %(b)s GROUP BY 1""",
        {"unit": unit, "tz": tz, "a": admin_range.at(start, tz), "b": admin_range.at(rng["end"], tz)}) or []
    out = []
    current = admin_range._start_of(today, unit)
    for s in sorted(sizes, key=lambda r: r["cohort"]):
        c = cohorts.get(s["cohort"], {"cells": {}})
        size = int(s["n"])
        elapsed = _periods_between(s["cohort"], current, unit)
        cells = [None if n > elapsed else {"n": c["cells"].get(n, 0), "pct": ratio(c["cells"].get(n, 0), size),
                                           "partial": n == elapsed}
                 for n in range(periods)]
        out.append({"cohort": s["cohort"].isoformat(), "size": size, "cells": cells})
    return {"mode": mode, "unit": unit, "periods": periods, "rows": out}


def engagement(rng, mode="weekly"):
    bkts = admin_range.buckets(rng["start"], rng["end"], rng["bucket"])
    ends = _ends(bkts)
    prev_end = rng["prev_end"] - timedelta(days=1)
    dau = active_at(ends + [prev_end], 1, activity.VALUE_MASK)
    wau = active_at(ends + [prev_end], 7, activity.VALUE_MASK)
    mau = active_at(ends + [prev_end], 30, activity.VALUE_MASK)
    importers = active_at(ends + [prev_end], 30, activity.IMPORT)
    d_prev, w_prev, m_prev, i_prev = dau.pop(), wau.pop(), mau.pop(), importers.pop()
    use = db.query(KIND_USE, {"value": activity.VALUE_MASK, "d0": rng["start"], "d1": rng["end"]}, one=True) or {}
    paying_importing = db.query(
        """SELECT COUNT(*) FILTER (WHERE s.mrr_cents > 0) AS paying,
                  COUNT(*) FILTER (WHERE s.mrr_cents > 0 AND EXISTS (
                      SELECT 1 FROM user_activity_days a WHERE a.user_id = s.user_id AND a.kinds & 2 <> 0
                         AND a.day > %s - 30)) AS importing
             FROM subscriptions s""", (activity.today(),), one=True) or {}
    sticky = [ratio(w, m) for w, m in zip(wau, mau, strict=True)]
    tiles = [
        tile("importers", "Monthly importers", importers[-1] if importers else 0, i_prev, spark=importers,
             help="People who imported a statement in the 30 days to the end of the period — the rhythm iSpend is used at."),
        tile("wau", "Weekly active", wau[-1] if wau else 0, w_prev, spark=wau,
             help="People who imported, sorted transactions or read a report in the last 7 days."),
        tile("mau", "Monthly active", mau[-1] if mau else 0, m_prev, spark=mau,
             help="The same, over the last 30 days."),
        tile("stickiness", "Weekly ÷ monthly", sticky[-1] if sticky else None, ratio(w_prev, m_prev), unit="pct",
             spark=sticky, agg="mean", help="Of the people active this month, the share active this week. "
                                             "Daily ÷ monthly would mislead for a monthly habit."),
        tile("dau", "Active today", dau[-1] if dau else 0, d_prev, spark=dau, agg="mean",
             help="People with a value event on the last day of the period."),
        tile("paying_importing", "Paying and importing", ratio(int(paying_importing.get("importing") or 0),
                                                               int(paying_importing.get("paying") or 0)), None,
             unit="pct", help="Paying customers who imported in the last 30 days. Those who stop are at risk of cancelling."),
    ]
    return {"range": _range_json(rng), "tiles": tiles,
            "series": {"labels": [s.isoformat() for s, _e in bkts], "dau": dau, "wau": wau, "mau": mau,
                       "importers": importers},
            "by_kind": {k: int(use.get(k) or 0) for k in ("active", "import", "categorize", "report", "dashboard", "seen")},
            "active_days": int(use.get("active_days") or 0),
            "retention": retention(rng, mode)}


# ---------- routes ----------

def _metric_route(name, fn, *extra):
    rng, err = _range_or_400()
    if err:
        return err
    key = f"{name}:{admin_range.cache_key(rng)}:{':'.join(extra)}"
    return jsonify(cached(key, lambda: fn(rng, *extra) if extra else fn(rng), refresh=request.args.get("refresh") == "1"))


@bp.get("/revenue")
@admin_required
def revenue_route():
    return _metric_route("revenue", revenue)


@bp.get("/trial-cohorts")
@admin_required
def trial_cohorts_route():
    return _metric_route("trials", trial_cohorts)


@bp.get("/funnel")
@admin_required
def funnel_route():
    return _metric_route("funnel", funnel)


@bp.get("/engagement")
@admin_required
def engagement_route():
    mode = "monthly" if request.args.get("mode") == "monthly" else "weekly"
    return _metric_route("engagement", engagement, mode)
