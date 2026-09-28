"""The populated customer the browser suites look at.

Admin accounts own no finance data, so the account "with data" is a customer of its own: `qa_data`,
created through the admin API and filled from the fixture statements the first time any suite needs
it. Later runs reuse it as it is. Read-only by convention, like the admin used to be: suites look
at it, qa_tester and the qa_api* users are the ones they change.

    python qa/e2e/data_persona.py        # create or check it by hand
"""
import os
import re
from datetime import date

import requests

from ratelimit import login_with_retry

BASE = os.environ.get("ISPEND_BASE_URL", "http://localhost:5559")
ADMIN = ("admin", os.environ.get("ISPEND_ADMIN_PASSWORD", "admin"))
DATA_USER = ("qa_data", "qa-data-pass1")
FIXTURES = os.path.join(os.path.dirname(__file__), "..", "..", "backend", "tests", "fixtures")

# (account name, type, fixture) — two banks and two cards, each imported once for every one of the
# last MONTHS months with its dates moved into that month, so "this month", trends and budgets all
# have something to show whatever day the suite runs.
MONTHS = 4
STATEMENTS = [
    ("QA Chase Checking", "checking", "chase_checking.csv"),
    ("QA Chase Card", "credit_card", "chase_card.csv"),
    ("QA Amex", "credit_card", "amex.csv"),
    ("QA Wells Fargo", "checking", "wells_fargo.csv"),
]


def _session(username, password):
    s = requests.Session()
    r = login_with_retry(lambda: s.post(f"{BASE}/api/auth/login", json={"username": username, "password": password}, timeout=60))
    return s, r


def _wait(s, sid, timeout=120):
    import time
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = s.get(f"{BASE}/api/statements/{sid}", timeout=60).json()
        if body["status"] in ("previewed", "error", "committed"):
            return body
        time.sleep(0.4)
    raise AssertionError(f"statement {sid} was not read within {timeout}s")


_DATE = re.compile(r"\b(\d{2})/(\d{2})/(\d{4})\b")


def _moved(text, months_back, today=None):
    """The fixture with every MM/DD/YYYY moved into the month `months_back` before this one. Days in
    the current month stay before today; nothing lands on a day the month does not have."""
    today = today or date.today()
    y, m = today.year, today.month - months_back
    while m < 1:
        y, m = y - 1, m + 12
    last = today.day - 1 if months_back == 0 else 28

    def move(match):
        day = max(1, min(int(match.group(2)), last, 28))
        return f"{m:02d}/{day:02d}/{y}"
    return _DATE.sub(move, text)


def _fill(s):
    have = {a["name"]: a["id"] for a in s.get(f"{BASE}/api/accounts?all=1", timeout=60).json()}
    for name, kind, fixture in STATEMENTS:
        account_id = have.get(name)
        if account_id is None:
            acct = s.post(f"{BASE}/api/accounts", json={"name": name, "account_type": kind, "currency": "USD"}, timeout=60)
            assert acct.status_code == 201, acct.text
            account_id = acct.json()["id"]
        with open(os.path.join(FIXTURES, fixture), encoding="utf-8") as f:
            text = f.read()
        for back in range(MONTHS - 1, -1, -1):
            up = s.post(f"{BASE}/api/statements", files={"file": (f"m{back}-{fixture}", _moved(text, back).encode())},
                        data={"account_id": str(account_id)}, timeout=120)
            assert up.status_code == 201, up.text
            preview = _wait(s, up.json()["id"])
            assert preview["status"] == "previewed", preview.get("error_message")
            c = s.post(f"{BASE}/api/statements/{preview['id']}/commit", json={"account_id": account_id}, timeout=120)
            assert c.status_code == 200, c.text
    budget_cat = next((c for c in s.get(f"{BASE}/api/categories", timeout=60).json() if c.get("kind") == "expense"), None)
    if budget_cat and not s.get(f"{BASE}/api/budgets", timeout=60).json():
        s.post(f"{BASE}/api/budgets", json={"category_id": budget_cat["id"], "amount": 400}, timeout=60)


def ensure_data_user():
    """Log in as qa_data, creating and filling the account first if it is not there yet."""
    s, r = _session(*DATA_USER)
    if r.status_code != 200:
        admin, ar = _session(*ADMIN)
        assert ar.status_code == 200, f"admin login failed: {ar.text}"
        made = admin.post(f"{BASE}/api/admin/users", json={"username": DATA_USER[0], "password": DATA_USER[1],
                                                            "access": "comped"}, timeout=60)
        assert made.status_code == 201, made.text
        s, r = _session(*DATA_USER)
        assert r.status_code == 200, r.text
    recent = s.get(f"{BASE}/api/transactions?limit=1&summary=0&range=last-90", timeout=60).json().get("items")
    if not recent:
        _fill(s)
    return s


if __name__ == "__main__":
    sess = ensure_data_user()
    print("qa_data ready:", len(sess.get(f"{BASE}/api/accounts?all=1", timeout=60).json()), "accounts")
