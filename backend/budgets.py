"""Budget maths (pure): elapsed fraction of a month, category roll-ups, pace, and the progress payload
served by GET /api/budgets/progress. No database access here."""
from datetime import date

AHEAD_MARGIN = 0.10  # spent share may exceed the elapsed share by this much before a budget is "ahead of pace"


def elapsed_fraction(start, end, on):
    """Share of the period [start, end] that has passed by `on` (inclusive days): 0.0 before, 1.0 after."""
    if on < start:
        return 0.0
    if on > end:
        return 1.0
    days = (end - start).days + 1
    return round(((on - start).days + 1) / days, 4)


def rollup(rows):
    """/api/reports/by-category?level=sub rows -> {category_id: total}. A parent's total is its own row
    plus every child's row; the uncategorized row (id None) is left out."""
    totals = {}
    for r in rows:
        if r.get("id") is None:
            continue
        totals[r["id"]] = totals.get(r["id"], 0.0) + float(r.get("total") or 0)
    for r in rows:
        pid = r.get("parent_id")
        if r.get("id") is None or pid is None:
            continue
        totals[pid] = totals.get(pid, 0.0) + float(r.get("total") or 0)
    return {k: round(v, 2) for k, v in totals.items()}


def pace_status(spent, budget, elapsed):
    """'over' past the budget, 'ahead' when spending runs ahead of the calendar, else 'on_track'."""
    if budget <= 0:
        return "on_track"
    if spent > budget + 0.004:
        return "over"
    if elapsed < 1 and spent / budget > elapsed + AHEAD_MARGIN:
        return "ahead"
    return "on_track"


def projected(spent, elapsed):
    """Month-end projection at the current pace; None at the very start or once the month is over."""
    if elapsed <= 0 or elapsed >= 1:
        return None
    return round(spent / elapsed, 2)


def build_progress(budgets, rows, elapsed, categories=None):
    """budgets: [{id, category_id, amount, note}], rows: by_category sub rows for the month,
    categories: {id: {name, color, icon, parent_id}}. Items are sorted by percent used (highest first)."""
    spent_by = rollup(rows)
    cats = categories or {}
    items = []
    for b in budgets:
        cid = b["category_id"]
        budget = float(b["amount"])
        spent = float(spent_by.get(cid, 0.0))
        meta = cats.get(cid, {})
        items.append({
            "budget_id": b["id"], "category_id": cid, "name": meta.get("name") or "Category",
            "color": meta.get("color") or "c1", "icon": meta.get("icon") or "tag", "parent_id": meta.get("parent_id"),
            "budget": round(budget, 2), "spent": round(spent, 2), "remaining": round(budget - spent, 2),
            "pct": round(spent / budget * 100, 1) if budget else 0.0,
            "projected": projected(spent, elapsed), "pace_status": pace_status(spent, budget, elapsed),
            "note": b.get("note"),
        })
    items.sort(key=lambda x: (-x["pct"], x["name"]))
    tb = round(sum(i["budget"] for i in items), 2)
    ts = round(sum(i["spent"] for i in items), 2)
    totals = {"budget": tb, "spent": ts, "remaining": round(tb - ts, 2),
              "pct": round(ts / tb * 100, 1) if tb else 0.0, "pace_status": pace_status(ts, tb, elapsed) if tb else "on_track"}
    budgeted = {i["category_id"] for i in items}
    parents = {}
    for r in rows:
        if r.get("id") is None:
            continue
        if r.get("parent_id") is None:
            parents[r["id"]] = r
        elif r["parent_id"] not in parents:
            m = cats.get(r["parent_id"], {})
            parents[r["parent_id"]] = {"id": r["parent_id"], "name": m.get("name") or "Other", "color": m.get("color") or "muted",
                                       "icon": m.get("icon") or "tag"}
    unb = []
    for pid, r in parents.items():
        if pid in budgeted:
            continue
        budgeted_children = sum(spent_by.get(cid, 0.0) for cid, m in cats.items() if m.get("parent_id") == pid and cid in budgeted)
        t = spent_by.get(pid, 0.0) - budgeted_children
        if t > 0.004:
            unb.append({"id": pid, "name": r.get("name") or "Category", "color": r.get("color") or "muted",
                        "icon": r.get("icon") or "tag", "total": round(t, 2)})
    unb.sort(key=lambda x: -x["total"])
    return {"items": items, "totals": totals,
            "unbudgeted": {"spent": round(sum(x["total"] for x in unb), 2), "count": len(unb), "categories": unb[:3]}}


def month_meta(start, end, on):
    days = (end - start).days + 1
    elapsed_days = min(max((on - start).days + 1, 0), days)
    return {"start": start.isoformat(), "end": end.isoformat(), "days_in_month": days, "days_elapsed": elapsed_days,
            "elapsed_pct": round(elapsed_days / days * 100, 1)}


__all__ = ["elapsed_fraction", "rollup", "pace_status", "projected", "build_progress", "month_meta", "date"]
