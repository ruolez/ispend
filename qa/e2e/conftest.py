"""Shared fixtures for the black-box API suite (see qa/CONTEXT.md).

Creates two isolated users (qa_api1, qa_api2) through the admin account, gives each test
a logged-in requests.Session, and deletes both users permanently at the end of the session
(ON DELETE CASCADE removes every account/statement/transaction/category/rule they own).
"""
import os
import time

import pytest
import requests

from ratelimit import login_with_retry

BASE = os.environ.get("ISPEND_BASE_URL", "http://localhost:5559")
ADMIN = ("admin", "admin")
QA_USERS = {"qa_api1": "qa-api1-pass", "qa_api2": "qa-api2-pass"}
FIXTURES = os.path.join(os.path.dirname(__file__), "..", "..", "backend", "tests", "fixtures")


class Api(requests.Session):
    """requests.Session bound to BASE with a JSON-friendly call helper."""

    def __init__(self, username=None):
        super().__init__()
        self.username = username

    def request(self, method, url, **kwargs):
        if url.startswith("/"):
            url = BASE + url
        kwargs.setdefault("timeout", 60)
        return super().request(method, url, **kwargs)

    def login(self, username, password):
        r = login_with_retry(lambda: self.post("/api/auth/login", json={"username": username, "password": password}))
        self.username = username
        return r


def api_login(username, password):
    s = Api()
    r = s.login(username, password)
    assert r.status_code == 200, (username, r.status_code, r.text)
    return s


def wait_status(session, statement_id, wanted=("previewed", "error", "committed"), timeout=90):
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = session.get(f"/api/statements/{statement_id}")
        assert r.status_code == 200, r.text
        body = r.json()
        if body["status"] in wanted:
            return body
        time.sleep(0.3)
    raise AssertionError(f"statement {statement_id} did not reach {wanted} within {timeout}s: {body}")


def upload(session, path, account_id=None, filename=None, content=None):
    name = filename or os.path.basename(path)
    if content is None:
        with open(path, "rb") as f:
            content = f.read()
    data = {"account_id": str(account_id)} if account_id else {}
    return session.post("/api/statements", files={"file": (name, content)}, data=data)


def import_fixture(session, name, account_id, filename=None):
    """Upload + wait + commit a fixture; returns (statement preview json, commit result json)."""
    r = upload(session, os.path.join(FIXTURES, name), account_id=account_id, filename=filename)
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    preview = wait_status(session, sid, ("previewed", "error"))
    assert preview["status"] == "previewed", preview.get("error_message")
    c = session.post(f"/api/statements/{sid}/commit", json={"account_id": account_id})
    assert c.status_code == 200, c.text
    return preview, c.json()


def create_account(session, name, account_type="checking", currency="USD", **extra):
    r = session.post("/api/accounts", json={"name": name, "account_type": account_type, "currency": currency, **extra})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def add_txn(session, account_id, txn_date, amount, description, category_id=None, notes=None, learn=False):
    body = {"account_id": account_id, "txn_date": txn_date, "amount": amount, "description": description,
            "learn": learn}
    if category_id is not None:
        body["category_id"] = category_id
    if notes is not None:
        body["notes"] = notes
    r = session.post("/api/transactions", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def cat_by_slug(session, slug):
    cats = session.get("/api/categories?flat=1").json()
    for c in cats:
        if c["slug"] == slug:
            return c
    raise KeyError(slug)


def list_all(session, **params):
    """Follow next_cursor and return every item plus the first page's totals."""
    params = {"limit": 500, **params}
    r = session.get("/api/transactions", params=params)
    assert r.status_code == 200, r.text
    first = r.json()
    items = list(first["items"])
    cursor = first.get("next_cursor")
    while cursor:
        r = session.get("/api/transactions", params={**params, "cursor": cursor})
        assert r.status_code == 200, r.text
        page = r.json()
        items.extend(page["items"])
        cursor = page.get("next_cursor")
    return items, first


@pytest.fixture(scope="session")
def admin():
    return api_login(*ADMIN)


def _purge_user(admin_session, user_id, username, required=True):
    """Purging is deliberately two steps: a user must be in the trash before they can be destroyed.
    The server also refuses while one of their imports is still parsing, so a suite that left a
    background job running gets a short retry rather than a flaky failure."""
    admin_session.delete(f"/api/admin/users/{user_id}")
    deadline = time.time() + 60
    while True:
        r = admin_session.delete(f"/api/admin/users/{user_id}?permanent=true&confirm={username}")
        if r.status_code == 200 or time.time() > deadline:
            break
        time.sleep(2)
    if required:
        assert r.status_code == 200, r.text
    return r


def _ensure_user(admin_session, username, password):
    users = admin_session.get("/api/admin/users?status=all").json()["items"]
    existing = next((u for u in users if u["username"] == username), None)
    if existing:
        # a previous run was interrupted: start from a clean slate
        _purge_user(admin_session, existing["id"], username)
    r = admin_session.post("/api/admin/users", json={"username": username, "password": password, "role": "user"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


@pytest.fixture(scope="session")
def qa_users(admin):
    ids = {name: _ensure_user(admin, name, pw) for name, pw in QA_USERS.items()}
    yield ids
    if os.environ.get("QA_KEEP_USERS") != "1":
        for name, uid in ids.items():
            _purge_user(admin, uid, name, required=False)


@pytest.fixture(scope="session")
def u1(qa_users):
    return api_login("qa_api1", QA_USERS["qa_api1"])


@pytest.fixture(scope="session")
def u2(qa_users):
    return api_login("qa_api2", QA_USERS["qa_api2"])


@pytest.fixture(scope="session")
def anon():
    return Api()


@pytest.fixture(scope="session")
def seeded(u1):
    """qa_api1's fixture-derived data: chase_checking -> checking account, chase_card -> credit card."""
    checking = create_account(u1, "QA Chase Checking", "checking", institution="chase", last4="9876")
    card = create_account(u1, "QA Chase Card", "credit_card", institution="chase", last4="5432")
    groceries = u1.post("/api/categories", json={"name": "QA Rule Groceries", "kind": "expense", "color": "c3"})
    assert groceries.status_code == 201, groceries.text
    rule = u1.post("/api/rules", json={"name": "QA wholefds rule", "match_type": "contains",
                                       "match_field": "description_raw", "pattern": "WHOLEFDS",
                                       "category_id": groceries.json()["id"]})
    assert rule.status_code == 201, rule.text
    chk_preview, chk_commit = import_fixture(u1, "chase_checking.csv", checking)
    card_preview, card_commit = import_fixture(u1, "chase_card.csv", card)
    return {
        "checking": checking, "card": card, "rule_category": groceries.json()["id"], "rule_id": rule.json()["id"],
        "checking_statement": chk_preview["id"], "card_statement": card_preview["id"],
        "checking_commit": chk_commit, "card_commit": card_commit,
    }
