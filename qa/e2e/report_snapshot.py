"""Byte-level snapshot of every /api/reports/* payload for one user, so schema or aggregation
changes (budgets, tags, splits) can prove they left existing numbers untouched.

    <venv>/bin/python qa/e2e/report_snapshot.py capture --user admin --dir /path/outside/repo
    <venv>/bin/python qa/e2e/report_snapshot.py verify  --user admin --dir /path/outside/repo
    <venv>/bin/python qa/e2e/report_snapshot.py capture --user qa_flows --dir qa/fixtures/report-snapshots/qa_flows

Every URL uses explicit from/to dates, a fixed `end` month and explicit `vs` months, so the
matrix is deterministic across days. `trends` has no explicit window parameter and is anchored
to today: it is included but reported separately when only its month labels moved.
"""
import argparse
import json
import os
import pathlib
import sys

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ratelimit import login_with_retry  # noqa: E402

BASE = os.environ.get("ISPEND_BASE_URL", "http://localhost:5559")
USERS = {
    "admin": ("admin", "admin"),
    "qa_flows": ("qa_flows", "qa-flows-pass1"),
    "qa_tester": ("qa_tester", "qa-tester-pass1"),
}
SPAN = ("2015-01-01", "2035-12-31")


def login(user):
    name, pw = USERS[user]
    s = requests.Session()
    r = login_with_retry(lambda: s.post(f"{BASE}/api/auth/login", json={"username": name, "password": pw}, timeout=30))
    if r.status_code != 200:
        sys.exit(f"login as {user} failed: {r.status_code} {r.text[:200]}")
    return s


def data_months(s):
    """Newest and oldest transaction month ('YYYY-MM') or (None, None) for an empty account."""
    out = []
    for sort in ("-date", "date"):
        r = s.get(f"{BASE}/api/transactions", params={"range": "all", "limit": 1, "sort": sort}, timeout=60)
        r.raise_for_status()
        items = r.json().get("items") or []
        out.append(items[0]["txn_date"][:7] if items else None)
    return out[0], out[1]


def month_list(newest, count):
    y, m = int(newest[:4]), int(newest[5:7])
    months = []
    for _ in range(count):
        months.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return months


def matrix(s):
    """Return [(slug, path, params)] covering every report endpoint deterministically."""
    newest, _oldest = data_months(s)
    accounts = [a["id"] for a in s.get(f"{BASE}/api/accounts?all=1", timeout=60).json()]
    cats = s.get(f"{BASE}/api/categories", timeout=60).json()
    parents = [c["id"] for c in cats if c.get("children")]
    span = {"from": SPAN[0], "to": SPAN[1]}
    urls = []

    def add(slug, path, **params):
        urls.append((slug, path, params))

    for inc in ("0", "1"):
        add(f"summary-inc{inc}", "/api/reports/summary", **span, include_transfers=inc)
        for level in ("top", "sub"):
            for flow in ("spending", "income"):
                add(f"bycat-{level}-{flow}-inc{inc}", "/api/reports/by-category", **span, level=level, flow=flow,
                    include_transfers=inc)
        for flow in ("spending", "income"):
            add(f"merchants-{flow}-inc{inc}", "/api/reports/top-merchants", **span, limit=200, flow=flow,
                include_transfers=inc)
    add("bycat-top-csv", "/api/reports/by-category", **span, level="top", format="csv")
    add("bycat-sub-csv", "/api/reports/by-category", **span, level="sub", format="csv")
    add("merchants-csv", "/api/reports/top-merchants", **span, limit=200, format="csv")
    add("summary-csv", "/api/reports/summary", **span, format="csv")
    for acct in accounts:
        add(f"acct{acct}-summary", "/api/reports/summary", **span, account_id=acct)
        add(f"acct{acct}-bycat-sub", "/api/reports/by-category", **span, level="sub", account_id=acct)
    if newest:
        for months in (6, 12, 24):
            add(f"monthly-{months}", "/api/reports/monthly", months=months, end=newest)
        add("monthly-12-income", "/api/reports/monthly", months=12, end=newest, flow="income")
        add("monthly-12-inc1", "/api/reports/monthly", months=12, end=newest, include_transfers="1")
        add("monthly-12-csv", "/api/reports/monthly", months=12, end=newest, format="csv")
        for pid in parents:
            add(f"monthly-12-parent{pid}", "/api/reports/monthly", months=12, end=newest, parent_id=pid)
        recent = month_list(newest, 7)
        for cur, prev in zip(recent[:6], recent[1:7], strict=False):
            for flow in ("spending", "income"):
                add(f"mom-{cur}-{flow}", "/api/reports/month-over-month", month=cur, vs=prev, flow=flow)
            add(f"mom-{cur}-inc1", "/api/reports/month-over-month", month=cur, vs=prev, include_transfers="1")
        add(f"mom-{recent[0]}-csv", "/api/reports/month-over-month", month=recent[0], vs=recent[1], format="csv")
    add("trends-24", "/api/reports/trends", months=24)
    add("trends-24-inc1", "/api/reports/trends", months=24, include_transfers="1")
    return urls


def fetch(s, path, params):
    r = s.get(f"{BASE}{path}", params=params, timeout=120)
    if r.status_code != 200:
        sys.exit(f"{path} {params} -> {r.status_code} {r.text[:200]}")
    return r.content


def capture(user, out_dir):
    s = login(user)
    out = pathlib.Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*.snap"):
        old.unlink()
    manifest = []
    for slug, path, params in matrix(s):
        body = fetch(s, path, params)
        (out / f"{slug}.snap").write_bytes(body)
        manifest.append({"slug": slug, "path": path, "params": params, "bytes": len(body)})
    (out / "manifest.json").write_text(json.dumps({"user": user, "base": BASE, "urls": manifest}, indent=1) + "\n")
    print(f"captured {len(manifest)} payloads for {user} into {out}")


def _diff_hint(a, b):
    try:
        ja, jb = json.loads(a), json.loads(b)
    except ValueError:
        return "csv/bytes differ"
    if isinstance(ja, dict) and isinstance(jb, dict):
        keys = [k for k in set(ja) | set(jb) if ja.get(k) != jb.get(k)]
        return "keys differ: " + ", ".join(sorted(keys)[:8])
    return "json differs"


def verify(user, snap_dir):
    out = pathlib.Path(snap_dir)
    manifest_path = out / "manifest.json"
    if not manifest_path.exists():
        sys.exit(f"no snapshot manifest in {out}; run capture first")
    manifest = json.loads(manifest_path.read_text())
    if manifest["user"] != user:
        sys.exit(f"snapshot was captured for {manifest['user']}, not {user}")
    s = login(user)
    failures, trends_only = [], []
    for entry in manifest["urls"]:
        expected = (out / f"{entry['slug']}.snap").read_bytes()
        actual = fetch(s, entry["path"], entry["params"])
        if actual != expected:
            hint = _diff_hint(expected, actual)
            (trends_only if entry["slug"].startswith("trends") else failures).append(f"{entry['slug']}: {hint}")
    checked = len(manifest["urls"])
    if trends_only:
        print(f"note: {len(trends_only)} trends payload(s) differ (today-anchored window): " + "; ".join(trends_only))
    if failures:
        print(f"MISMATCH {len(failures)}/{checked} payloads for {user}:")
        for f in failures:
            print("  " + f)
        return False
    print(f"identical: {checked - len(trends_only)}/{checked} payloads for {user}")
    return True


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["capture", "verify"])
    ap.add_argument("--user", default="qa_flows", choices=sorted(USERS))
    ap.add_argument("--dir", required=True)
    args = ap.parse_args()
    if args.mode == "capture":
        capture(args.user, args.dir)
    elif not verify(args.user, args.dir):
        sys.exit(1)


if __name__ == "__main__":
    main()
