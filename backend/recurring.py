"""Pure recurring-charge and anomaly detection over transaction dicts.

No database access. Inputs are plain dicts:
  {id, merchant_key, merchant_name, account_id, txn_date (date), amount (negative = spend), category_id}
"""
from collections import defaultdict
from datetime import timedelta
from statistics import median, pstdev

# (name, nominal days, tolerance days, per-month factor)
CADENCES = [
    ("weekly", 7, 2, 52 / 12),
    ("biweekly", 14, 3, 26 / 12),
    ("monthly", 30, 5, 1.0),
    ("quarterly", 91, 10, 1 / 3),
    ("yearly", 365, 20, 1 / 12),
]

MIN_INTERVAL_SHARE = 0.7
FIXED_CV = 0.1
VARIABLE_CV = 0.4
NEW_MERCHANT_MIN_AMOUNT = 200
UNUSUAL_MULTIPLIER = 2.0
UNUSUAL_MIN_PRIOR = 3


def _spend(t):
    return -float(t["amount"])


def _cadence_for(median_interval):
    for name, days, tol, factor in CADENCES:
        if abs(median_interval - days) <= tol:
            return name, days, tol, factor
    return None


def detect(txns, today, min_occurrences=3):
    groups = defaultdict(list)
    for t in txns:
        if float(t["amount"]) >= 0:
            continue
        groups[t["merchant_key"]].append(t)

    out = []
    for key, rows in groups.items():
        if len(rows) < min_occurrences:
            continue
        rows.sort(key=lambda r: (r["txn_date"], r.get("id") or 0))
        dates = sorted({r["txn_date"] for r in rows})
        if len(dates) < min_occurrences:
            continue
        intervals = [(b - a).days for a, b in zip(dates, dates[1:], strict=False)]
        cadence = _cadence_for(median(intervals))
        if not cadence:
            continue
        name, days, tol, factor = cadence
        within = sum(1 for i in intervals if abs(i - days) <= tol)
        if within / len(intervals) < MIN_INTERVAL_SHARE:
            continue
        amounts = [_spend(r) for r in rows]
        med_amt = median(amounts)
        if med_amt <= 0:
            continue
        cv = pstdev(amounts) / med_amt
        if cv <= FIXED_CV:
            amount_kind = "fixed"
        elif cv <= VARIABLE_CV:
            amount_kind = "variable"
        else:
            continue
        last = rows[-1]
        last_date = dates[-1]
        next_expected = last_date + timedelta(days=days)
        is_active = last_date >= today - timedelta(days=2 * days + tol)
        cat_counts = defaultdict(int)
        for r in rows:
            if r.get("category_id") is not None:
                cat_counts[r["category_id"]] += 1
        category_id = max(cat_counts, key=cat_counts.get) if cat_counts else None
        out.append({
            "merchant_key": key,
            "merchant_name": last.get("merchant_name") or key,
            "cadence": name,
            "occurrences": len(rows),
            "median_amount": round(med_amt, 2),
            "last_amount": round(_spend(last), 2),
            "last_date": last_date,
            "next_expected": next_expected,
            "days_until_next": (next_expected - today).days,
            "is_active": is_active,
            "amount_kind": amount_kind,
            "category_id": category_id,
            "account_ids": sorted({r["account_id"] for r in rows if r.get("account_id") is not None}),
            "ids": [r.get("id") for r in rows],
            "monthly_equivalent": round(med_amt * factor, 2),
        })
    out.sort(key=lambda s: (not s["is_active"], -s["monthly_equivalent"]))
    return out


def anomalies(txns, today, lookback_days=45):
    spend = [t for t in txns if float(t["amount"]) < 0]
    spend.sort(key=lambda r: (r["txn_date"], r.get("id") or 0))
    cutoff = today - timedelta(days=lookback_days)
    by_merchant = defaultdict(list)
    for t in spend:
        by_merchant[t["merchant_key"]].append(t)

    out = []
    for key, rows in by_merchant.items():
        first = rows[0]
        if first["txn_date"] >= cutoff and _spend(first) >= NEW_MERCHANT_MIN_AMOUNT:
            out.append({
                "kind": "new_merchant",
                "transaction_id": first.get("id"),
                "merchant_key": key,
                "merchant_name": first.get("merchant_name") or key,
                "amount": round(_spend(first), 2),
                "txn_date": first["txn_date"],
                "delta": None,
                "text": "First charge from this merchant",
            })
        for idx, t in enumerate(rows):
            if t["txn_date"] < cutoff:
                continue
            prior = [_spend(p) for p in rows[:idx]]
            if len(prior) >= UNUSUAL_MIN_PRIOR:
                med = median(prior)
                amt = _spend(t)
                if med > 0 and amt > UNUSUAL_MULTIPLIER * med:
                    out.append({
                        "kind": "unusual_amount",
                        "transaction_id": t.get("id"),
                        "merchant_key": key,
                        "merchant_name": t.get("merchant_name") or key,
                        "amount": round(amt, 2),
                        "txn_date": t["txn_date"],
                        "delta": round(amt - med, 2),
                        "text": f"{amt / med:.1f}x the usual amount ({med:.2f})",
                    })
        seen = {}
        for t in rows:
            if t["txn_date"] < cutoff:
                continue
            sig = (t["txn_date"], round(_spend(t), 2))
            if sig in seen and seen[sig].get("id") != t.get("id"):
                out.append({
                    "kind": "duplicate_charge",
                    "transaction_id": t.get("id"),
                    "merchant_key": key,
                    "merchant_name": t.get("merchant_name") or key,
                    "amount": round(_spend(t), 2),
                    "txn_date": t["txn_date"],
                    "delta": None,
                    "text": "Same merchant and amount charged twice on the same day",
                    "duplicate_of": seen[sig].get("id"),
                })
            else:
                seen.setdefault(sig, t)
    out.sort(key=lambda a: (a["txn_date"], a.get("transaction_id") or 0), reverse=True)
    return out
