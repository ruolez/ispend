"""Black-box API suite for iSpend (http://localhost:5559).

Run:  <qa-venv>/bin/pytest qa/e2e/test_api.py -v
Users qa_api1 / qa_api2 are created through the admin account and deleted permanently at the end
(set QA_KEEP_USERS=1 to keep them). admin's own data is only ever read.
"""
import csv
import io
import json
import os
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from conftest import (Api, FIXTURES, QA_USERS, add_txn, api_login, cat_by_slug, create_account, import_fixture,
                      list_all, upload, wait_status)

TODAY = datetime.now(ZoneInfo("America/Chicago")).date()


def money(v):
    return Decimal(str(v)).quantize(Decimal("0.01"))


def sums(items):
    out = sum((money(t["amount"]) for t in items if money(t["amount"]) < 0), Decimal(0))
    inn = sum((money(t["amount"]) for t in items if money(t["amount"]) >= 0), Decimal(0))
    return out, inn


# ======================================================================================
# 1. Auth
# ======================================================================================

GET_ROUTES = [
    "/api/auth/me", "/api/settings", "/api/settings/client", "/api/settings/openrouter/models", "/api/users",
    "/api/accounts", "/api/accounts/institutions", "/api/categories", "/api/categories?flat=1",
    "/api/statements", "/api/statements/1", "/api/statements/1/file",
    "/api/transactions", "/api/transactions/export", "/api/transactions/transfer-candidates",
    "/api/transactions/suggest?q=ab", "/api/transactions/merchant/x", "/api/transactions/1",
    "/api/transactions/1/events", "/api/review/count", "/api/review", "/api/rules", "/api/rules/1", "/api/merchants",
    "/api/reports/dashboard", "/api/reports/summary", "/api/reports/by-category", "/api/reports/monthly",
    "/api/reports/trends", "/api/reports/top-merchants", "/api/reports/month-over-month", "/api/reports/recurring",
    "/api/ai/status", "/api/insights", "/api/budgets", "/api/budgets/progress", "/api/tags",
]
MUTATING_ROUTES = [
    ("PUT", "/api/auth/me/preferences"), ("PUT", "/api/auth/me/password"), ("PUT", "/api/settings"),
    ("POST", "/api/settings/openrouter/test"), ("POST", "/api/users"), ("PUT", "/api/users/1"),
    ("DELETE", "/api/users/1"), ("PUT", "/api/users/1/password"), ("POST", "/api/accounts"),
    ("PUT", "/api/accounts/1"), ("DELETE", "/api/accounts/1"), ("POST", "/api/categories"),
    ("PUT", "/api/categories/1"), ("PUT", "/api/categories/reorder"), ("POST", "/api/categories/1/merge"),
    ("DELETE", "/api/categories/1"), ("POST", "/api/categories/reset-defaults"), ("POST", "/api/statements"),
    ("PUT", "/api/statements/1/mapping"), ("PUT", "/api/statements/1/account"), ("PUT", "/api/statements/1/rows"),
    ("POST", "/api/statements/1/commit"), ("POST", "/api/statements/1/reparse"), ("DELETE", "/api/statements/1"),
    ("POST", "/api/statements/1/flip-signs"), ("POST", "/api/statements/1/ai-extract"),
    ("POST", "/api/transactions"), ("PUT", "/api/transactions/1"), ("DELETE", "/api/transactions/1"),
    ("POST", "/api/transactions/1/rule-draft"), ("POST", "/api/transactions/1/unpair"),
    ("POST", "/api/transactions/pair"), ("POST", "/api/transactions/auto-pair"), ("POST", "/api/transactions/bulk"),
    ("POST", "/api/review/resolve"), ("POST", "/api/review/suggest"), ("POST", "/api/rules"),
    ("PUT", "/api/rules/reorder"), ("POST", "/api/rules/preview"), ("POST", "/api/rules/run"),
    ("PUT", "/api/rules/1"), ("DELETE", "/api/rules/1"), ("POST", "/api/rules/1/apply"),
    ("PUT", "/api/merchants/x"), ("DELETE", "/api/merchants/x"), ("POST", "/api/merchants/renormalize"),
    ("POST", "/api/merchants/learn"), ("POST", "/api/reports/recurring/dismiss"),
    ("DELETE", "/api/reports/recurring/dismiss/x"), ("POST", "/api/ai/categorize"), ("POST", "/api/insights/generate"),
    ("POST", "/api/insights/anomalies/1/dismiss"), ("DELETE", "/api/insights/anomalies/1/dismiss"),
    ("POST", "/api/budgets"), ("PUT", "/api/budgets/1"), ("DELETE", "/api/budgets/1"), ("POST", "/api/budgets/copy"),
    ("POST", "/api/tags"), ("PUT", "/api/tags/1"), ("DELETE", "/api/tags/1"),
]


class TestAuth:
    def test_health_is_public(self, anon):
        r = anon.get("/api/health")
        assert (r.status_code, r.json()) == (200, {"status": "ok"})

    def test_login_ok_returns_me_payload(self, qa_users):
        s = Api()
        r = s.login("qa_api1", QA_USERS["qa_api1"])
        assert r.status_code == 200
        body = r.json()
        assert body["username"] == "qa_api1" and body["role"] == "user" and body["id"] == qa_users["qa_api1"]
        assert isinstance(body["preferences"], dict)
        assert "session" in s.cookies.get_dict()

    def test_login_bad_password(self):
        r = Api().login("qa_api1", "wrong-password")
        assert (r.status_code, r.json()) == (401, {"error": "Invalid username or password"})

    def test_login_unknown_user(self):
        r = Api().login("qa_nobody_xyz", "whatever")
        assert (r.status_code, r.json()) == (401, {"error": "Invalid username or password"})

    def test_login_missing_body(self):
        r = Api().post("/api/auth/login")
        assert (r.status_code, r.json()) == (401, {"error": "Invalid username or password"})

    def test_login_wrong_content_type(self):
        r = Api().post("/api/auth/login", data="username=admin&password=admin",
                       headers={"Content-Type": "application/x-www-form-urlencoded"})
        assert r.status_code == 401

    def test_me_matches_login_and_session_persists(self, u1, qa_users):
        a = u1.get("/api/auth/me").json()
        b = u1.get("/api/auth/me").json()
        assert a == b and a["id"] == qa_users["qa_api1"] and a["role"] == "user"

    def test_logout_invalidates_session(self, qa_users):
        s = api_login("qa_api2", QA_USERS["qa_api2"])
        assert s.post("/api/auth/logout").json() == {"ok": True}
        assert s.get("/api/auth/me").status_code == 401

    @pytest.mark.parametrize("route", GET_ROUTES)
    def test_unauthenticated_get_is_401(self, anon, route):
        r = anon.get(route)
        assert (r.status_code, r.json()) == (401, {"error": "Not authenticated"}), route

    @pytest.mark.parametrize("method,route", MUTATING_ROUTES)
    def test_unauthenticated_mutation_is_401(self, anon, method, route):
        r = anon.request(method, route, json={})
        assert (r.status_code, r.json()) == (401, {"error": "Not authenticated"}), (method, route)

    def test_unknown_route_is_json_404(self, u1):
        r = u1.get("/api/does-not-exist")
        assert (r.status_code, r.json()) == (404, {"error": "Not found"})

    @pytest.mark.parametrize("method,route", [
        ("GET", "/api/users"), ("POST", "/api/users"), ("PUT", "/api/users/1"), ("DELETE", "/api/users/1"),
        ("PUT", "/api/users/1/password"),
    ])
    def test_non_admin_user_routes_403(self, u1, method, route):
        r = u1.request(method, route, json={"username": "x", "password": "yyyyyy"})
        assert (r.status_code, r.json()) == (403, {"error": "Admin access required"})

    def test_non_admin_settings_put_is_per_user_and_cannot_publish_shared(self, u1, admin):
        before = admin.get("/api/settings").json()
        assert before["shared_available"] is False, "precondition: no shared key configured on this instance"
        r = u1.put("/api/settings", json={"openrouter_model": "qa/model", "shared": True, "clear_shared": True})
        assert r.status_code == 200
        after = admin.get("/api/settings").json()
        assert (after["shared_available"], after["shared_model"]) == (False, "")
        mine = u1.get("/api/settings").json()
        assert mine["openrouter_model"] == "qa/model" and mine["is_admin"] is False
        u1.put("/api/settings", json={"openrouter_model": ""})

    def test_admin_cannot_delete_or_demote_self(self, admin):
        me = admin.get("/api/auth/me").json()
        r = admin.delete(f"/api/users/{me['id']}")
        assert (r.status_code, r.json()) == (400, {"error": "You cannot delete your own account"})
        r = admin.put(f"/api/users/{me['id']}", json={"is_active": False})
        assert (r.status_code, r.json()) == (400, {"error": "You cannot deactivate your own account"})
        r = admin.put(f"/api/users/{me['id']}", json={"role": "user"})
        assert (r.status_code, r.json()) == (400, {"error": "You cannot remove your own admin role"})
        assert admin.get("/api/auth/me").json()["role"] == "admin"

    def test_admin_user_validation(self, admin):
        r = admin.post("/api/users", json={"username": "qa_short", "password": "123456789"})
        assert r.status_code == 400 and "10 characters" in r.json()["error"]
        r = admin.post("/api/users", json={"username": "qa_api1", "password": "abcdefghij"})
        assert (r.status_code, r.json()) == (400, {"error": "Username already exists"})
        r = admin.put("/api/users/999999", json={"role": "user"})
        assert (r.status_code, r.json()) == (404, {"error": "User not found"})
        r = admin.put("/api/users/999999/password", json={"password": "123"})
        assert r.status_code == 400

    def test_inactive_user_cannot_login_and_live_session_dies(self, admin):
        r = admin.post("/api/users", json={"username": "qa_api_inactive", "password": "inactive-pass1"})
        assert r.status_code == 201
        uid = r.json()["id"]
        try:
            s = api_login("qa_api_inactive", "inactive-pass1")
            assert s.get("/api/accounts").status_code == 200
            r = admin.delete(f"/api/users/{uid}")  # soft delete = deactivate
            assert r.json() == {"ok": True}
            assert any(u["id"] == uid and not u["is_active"] for u in admin.get("/api/users").json())
            assert Api().login("qa_api_inactive", "inactive-pass1").status_code == 401
            assert s.get("/api/auth/me").status_code == 401, "an already-issued session must stop working"
            assert s.get("/api/accounts").status_code == 401, "every route must re-check is_active, not just /me"
            r = s.post("/api/accounts", json={"name": "QA ghost", "account_type": "checking", "currency": "USD"})
            assert r.status_code == 401, r.text
            r = admin.put(f"/api/users/{uid}", json={"is_active": True})
            assert r.status_code == 200
            assert Api().login("qa_api_inactive", "inactive-pass1").status_code == 200
        finally:
            admin.delete(f"/api/users/{uid}?permanent=true")

    def test_demoted_admin_loses_admin_routes_at_once(self, admin):
        r = admin.post("/api/users", json={"username": "qa_api_admin2", "password": "admin2-pass1", "role": "admin"})
        assert r.status_code == 201
        uid = r.json()["id"]
        try:
            s = api_login("qa_api_admin2", "admin2-pass1")
            assert s.get("/api/users").status_code == 200
            assert admin.put(f"/api/users/{uid}", json={"role": "user"}).status_code == 200
            r = s.get("/api/users")
            assert (r.status_code, r.json()) == (403, {"error": "Admin access required"})
            assert s.get("/api/auth/me").json()["role"] == "user"
            assert s.get("/api/accounts").status_code == 200, "a demoted admin keeps a working user session"
        finally:
            admin.delete(f"/api/users/{uid}?permanent=true")

    def test_permanent_delete_removes_per_user_settings(self, admin):
        r = admin.post("/api/users", json={"username": "qa_api_gone", "password": "gone-pass-123"})
        uid = r.json()["id"]
        s = api_login("qa_api_gone", "gone-pass-123")
        assert s.put("/api/settings", json={"openrouter_model": "qa/gone-model"}).status_code == 200
        assert s.get("/api/settings").json()["openrouter_model"] == "qa/gone-model"
        assert admin.delete(f"/api/users/{uid}?permanent=true").json() == {"ok": True}
        r = admin.post("/api/users", json={"username": "qa_api_gone", "password": "gone-pass-123"})
        try:
            s2 = api_login("qa_api_gone", "gone-pass-123")
            assert s2.get("/api/settings").json()["openrouter_model"] == "", "settings must not survive a permanent delete"
        finally:
            admin.delete(f"/api/users/{r.json()['id']}?permanent=true")

    def test_security_headers_on_pages_and_api(self, anon):
        for path in ("/login.html", "/api/health"):
            h = anon.get(path).headers
            assert h.get("X-Frame-Options") == "DENY", (path, dict(h))
            assert h.get("X-Content-Type-Options") == "nosniff", path
            assert "frame-ancestors 'none'" in h.get("Content-Security-Policy", ""), path
            assert h.get("Referrer-Policy") == "same-origin", path

    def test_password_change_rules(self, qa_users):
        s = api_login("qa_api2", QA_USERS["qa_api2"])
        r = s.put("/api/auth/me/password", json={"current_password": "nope", "password": "newpass123"})
        assert (r.status_code, r.json()) == (400, {"error": "Current password is incorrect"})
        r = s.put("/api/auth/me/password", json={"current_password": QA_USERS["qa_api2"], "password": "short-one"})
        assert (r.status_code, r.json()) == (400, {"error": "New password must be at least 10 characters"})
        r = s.put("/api/auth/me/password", json={"current_password": QA_USERS["qa_api2"], "password": "newpass123"})
        assert r.json() == {"ok": True}
        assert Api().login("qa_api2", QA_USERS["qa_api2"]).status_code == 401
        assert Api().login("qa_api2", "newpass123").status_code == 200
        r = s.put("/api/auth/me/password", json={"current_password": "newpass123", "password": QA_USERS["qa_api2"]})
        assert r.json() == {"ok": True}

    def test_preferences_only_known_keys(self, u1):
        r = u1.put("/api/auth/me/preferences", json={"theme": "dark", "density": "compact", "evil": "x", "currency": "CAD"})
        assert r.status_code == 200
        prefs = r.json()
        assert prefs["theme"] == "dark" and prefs["density"] == "compact" and prefs["currency"] == "CAD"
        assert "evil" not in prefs
        assert u1.get("/api/auth/me").json()["preferences"]["theme"] == "dark"

    def test_preferences_saved_views_skips_and_onboarding(self, u1):
        view = {"id": "v_big", "name": "Big Amazon", "query": "?q=amazon&min=100&range=this-year", "pinned": True}
        r = u1.put("/api/auth/me/preferences", json={"saved_views": [view], "review_skips": ["AMAZON", "t12", "AMAZON"],
                                                     "onboarding": {"dismissed": True}})
        assert r.status_code == 200, r.text
        prefs = r.json()
        assert prefs["saved_views"] == [{**view, "page": "transactions"}]
        assert prefs["review_skips"] == ["AMAZON", "t12"] and prefs["onboarding"] == {"dismissed": True}
        me = u1.get("/api/auth/me").json()["preferences"]
        assert me["saved_views"] == prefs["saved_views"] and me["review_skips"] == ["AMAZON", "t12"]
        r = u1.put("/api/auth/me/preferences", json={"saved_views": [{**view, "query": "?open=5"}]})
        assert r.status_code == 400 and "unsupported keys" in r.json()["error"]
        r = u1.put("/api/auth/me/preferences", json={"saved_views": [{**view, "id": "no spaces"}]})
        assert (r.status_code, r.json()) == (400, {"error": "Invalid view id"})
        assert u1.put("/api/auth/me/preferences", json={"saved_views": [], "review_skips": [], "onboarding": {"dismissed": False}}).status_code == 200


# ======================================================================================
# 2. Authorization / IDOR: qa_api2 attacks qa_api1's objects
# ======================================================================================

@pytest.fixture(scope="session")
def victim(u1, seeded):
    """Snapshot of qa_api1 objects an attacker will target."""
    items, _ = list_all(u1, account_id=seeded["checking"])
    txn = items[0]
    cats = u1.get("/api/categories?flat=1").json()
    own_cat = next(c for c in cats if c["id"] == seeded["rule_category"])
    merchants = u1.get("/api/merchants").json()
    budget = u1.post("/api/budgets", json={"category_id": own_cat["id"], "amount": 123.45, "month": "2030-01"})
    assert budget.status_code == 201, budget.text
    tag = u1.post("/api/tags", json={"name": "QA victim tag"})
    assert tag.status_code == 201, tag.text
    return {
        "budget": budget.json()["id"], "tag": tag.json()["id"], "txn": txn, "txn_ids": [t["id"] for t in items], "statement": seeded["checking_statement"],
        "rule": seeded["rule_id"], "category": own_cat, "account": seeded["checking"],
        "merchant_key": txn["merchant_key"], "memory_keys": [m["merchant_key"] for m in merchants],
        "cat_order": [(c["id"], c["sort_order"]) for c in cats],
    }


def _txn_snapshot(session, txn_id):
    r = session.get(f"/api/transactions/{txn_id}")
    assert r.status_code == 200
    body = r.json()
    return {k: body[k] for k in ("amount", "category_id", "notes", "is_transfer", "is_excluded", "merchant_name")}


class TestIsolation:
    @pytest.mark.parametrize("method,path,body", [
        ("GET", "/api/transactions/{txn}", None),
        ("GET", "/api/transactions/{txn}/events", None),
        ("PUT", "/api/transactions/{txn}", {"notes": "pwned"}),
        ("PUT", "/api/transactions/{txn}", {"category_id": None}),
        ("DELETE", "/api/transactions/{txn}", None),
        ("POST", "/api/transactions/{txn}/rule-draft", {}),
        ("POST", "/api/transactions/{txn}/unpair", {}),
        ("POST", "/api/insights/anomalies/{txn}/dismiss", {}),
        ("GET", "/api/statements/{st}", None),
        ("GET", "/api/statements/{st}/file", None),
        ("PUT", "/api/statements/{st}/mapping", {"mapping": {}}),
        ("PUT", "/api/statements/{st}/account", {"account_id": None}),
        ("PUT", "/api/statements/{st}/rows", {"all": True, "include": False}),
        ("POST", "/api/statements/{st}/commit", {}),
        ("POST", "/api/statements/{st}/reparse", {}),
        ("POST", "/api/statements/{st}/flip-signs", {}),
        ("POST", "/api/statements/{st}/ai-extract", {}),
        ("DELETE", "/api/statements/{st}?with_transactions=true", None),
        ("GET", "/api/rules/{rule}", None),
        ("PUT", "/api/rules/{rule}", {"name": "pwned"}),
        ("DELETE", "/api/rules/{rule}", None),
        ("POST", "/api/rules/{rule}/apply", {}),
        ("PUT", "/api/categories/{cat}", {"name": "pwned"}),
        ("DELETE", "/api/categories/{cat}", None),
        ("PUT", "/api/accounts/{acct}", {"name": "pwned"}),
        ("DELETE", "/api/accounts/{acct}?force=true", None),
        ("PUT", "/api/budgets/{budget}", {"amount": 1}),
        ("DELETE", "/api/budgets/{budget}", None),
        ("PUT", "/api/tags/{tag}", {"name": "pwned"}),
        ("DELETE", "/api/tags/{tag}", None),
        ("PUT", "/api/transactions/{txn}", {"tag_ids": ["{tag}"]}),
    ])
    def test_foreign_object_by_id_is_404(self, u2, victim, method, path, body):
        url = path.format(txn=victim["txn"]["id"], st=victim["statement"], rule=victim["rule"],
                          cat=victim["category"]["id"], acct=victim["account"], budget=victim["budget"], tag=victim["tag"])
        if body and "tag_ids" in body:
            body = {"tag_ids": [victim["tag"]]}
        r = u2.request(method, url, json=body)
        assert r.status_code == 404, (method, url, r.status_code, r.text)
        assert "pwned" not in r.text

    def test_foreign_objects_not_modified_or_deleted(self, u1, u2, victim):
        # runs after the parametrized attacks above (pytest keeps file order)
        assert u1.get(f"/api/transactions/{victim['txn']['id']}").status_code == 200
        assert u1.get(f"/api/statements/{victim['statement']}").json()["status"] == "committed"
        assert u1.get(f"/api/rules/{victim['rule']}").json()["name"] == "QA wholefds rule"
        cats = u1.get("/api/categories?flat=1").json()
        assert next(c for c in cats if c["id"] == victim["category"]["id"])["name"] == victim["category"]["name"]
        accts = u1.get("/api/accounts").json()
        assert any(a["id"] == victim["account"] and a["name"] == "QA Chase Checking" for a in accts)
        mine = u1.get("/api/budgets", params={"month": "2030-01"}).json()["budgets"]
        assert any(b["id"] == victim["budget"] and b["amount"] == 123.45 for b in mine)
        assert u2.get("/api/budgets", params={"month": "2030-01"}).json()["budgets"] == []
        assert any(t["id"] == victim["tag"] and t["name"] == "QA victim tag" for t in u1.get("/api/tags").json())
        assert not any(t["id"] == victim["tag"] for t in u2.get("/api/tags").json())

    def test_foreign_ids_in_bulk_review_pair(self, u2, u1, victim):
        before = _txn_snapshot(u1, victim["txn"]["id"])
        ids = victim["txn_ids"][:3]
        r = u2.post("/api/transactions/bulk", json={"ids": ids, "action": "delete"})
        assert (r.status_code, r.json()) == (404, {"error": "No matching transactions"})
        r = u2.post("/api/transactions/bulk", json={"ids": ids, "action": "categorize",
                                                    "category_id": victim["category"]["id"]})
        assert r.status_code == 404
        r = u2.post("/api/review/resolve", json={"ids": ids, "category_id": victim["category"]["id"]})
        assert (r.status_code, r.json()) == (404, {"error": "No matching transactions"})
        r = u2.post("/api/review/resolve", json={"ids": ids, "mark_transfer": True})
        assert r.status_code == 404
        r = u2.post("/api/transactions/pair", json={"a_id": ids[0], "b_id": ids[1]})
        assert (r.status_code, r.json()) == (404, {"error": "Transaction not found"})
        assert _txn_snapshot(u1, victim["txn"]["id"]) == before
        assert len(list_all(u1, account_id=victim["account"])[0]) == len(victim["txn_ids"])

    def test_foreign_category_and_account_references_rejected(self, u2, victim):
        acct2 = create_account(u2, "QA u2 acct")
        t = add_txn(u2, acct2, "2024-01-05", "-10.00", "QA U2 THING")
        r = u2.put(f"/api/transactions/{t['id']}", json={"category_id": victim["category"]["id"]})
        assert (r.status_code, r.json()) == (404, {"error": "Category not found"})
        r = u2.post("/api/transactions/bulk", json={"ids": [t["id"]], "action": "categorize",
                                                    "category_id": victim["category"]["id"]})
        assert r.status_code == 404
        r = u2.post("/api/rules", json={"pattern": "x", "category_id": victim["category"]["id"]})
        assert (r.status_code, r.json()) == (400, {"error": "Category not found"})
        r = u2.post("/api/rules", json={"pattern": "x", "set_excluded": True, "account_id": victim["account"]})
        assert (r.status_code, r.json()) == (400, {"error": "Account not found"})
        r = u2.post("/api/transactions", json={"account_id": victim["account"], "txn_date": "2024-01-01",
                                              "amount": "-1.00", "description": "pwned"})
        assert (r.status_code, r.json()) == (404, {"error": "Account not found"})
        r = u2.post("/api/transactions", json={"account_id": acct2, "txn_date": "2024-01-01", "amount": "-1.00",
                                              "description": "pwned cat", "category_id": victim["category"]["id"]})
        assert (r.status_code, r.json()) == (404, {"error": "Category not found"})
        r = upload(u2, os.path.join(FIXTURES, "td.csv"), account_id=victim["account"])
        assert (r.status_code, r.json()) == (404, {"error": "Account not found"})
        r = u2.put(f"/api/merchants/{victim['merchant_key']}", json={"category_id": victim["category"]["id"]})
        assert (r.status_code, r.json()) == (404, {"error": "Category not found"})
        cats2 = u2.get("/api/categories?flat=1").json()
        mine = next(c for c in cats2 if c["slug"] == "groceries")
        r = u2.post(f"/api/categories/{mine['id']}/merge", json={"into": victim["category"]["id"]})
        assert r.status_code in (400, 404) and "moved" not in r.json()
        r = u2.post(f"/api/categories/{victim['category']['id']}/merge", json={"into": mine["id"]})
        assert r.status_code in (400, 404) and "moved" not in r.json()
        r = u2.delete(f"/api/categories/{mine['id']}?reassign_to={victim['category']['id']}")
        assert r.status_code == 404
        u2.delete(f"/api/accounts/{acct2}?force=true")

    def test_foreign_ids_in_unscoped_writes_are_noops(self, u1, u2, victim):
        r = u2.put("/api/categories/reorder", json={"ids": [c[0] for c in reversed(victim["cat_order"])]})
        assert r.status_code == 200
        cats = u1.get("/api/categories?flat=1").json()
        assert [(c["id"], c["sort_order"]) for c in cats] == victim["cat_order"]
        r = u2.put("/api/rules/reorder", json={"ids": [victim["rule"]]})
        assert r.status_code == 200
        assert u1.get(f"/api/rules/{victim['rule']}").json()["priority"] == 10
        r = u2.delete(f"/api/merchants/{victim['memory_keys'][0]}") if victim["memory_keys"] else None
        if r is not None:
            assert r.status_code == 200
            assert [m["merchant_key"] for m in u1.get("/api/merchants").json()] == victim["memory_keys"]
        r = u2.post("/api/reports/recurring/dismiss", json={"merchant_key": victim["merchant_key"]})
        assert r.status_code == 200
        r = u2.delete(f"/api/insights/anomalies/{victim['txn']['id']}/dismiss")
        assert r.status_code == 200

    def test_lists_and_filters_never_leak(self, u2, victim):
        assert u2.get("/api/statements").json() == []
        items, first = list_all(u2, statement_id=victim["statement"])
        assert items == [] and first["total"] == 0
        assert u2.get(f"/api/transactions/merchant/{victim['merchant_key']}").json() == []
        assert u2.get("/api/transactions/suggest", params={"q": victim["txn"]["merchant_name"][:4]}).json() == []
        assert u2.get("/api/merchants").json() == []
        assert u2.get("/api/rules").json() == []
        assert u2.get("/api/review/count").json() == {"uncategorized": 0, "suggested": 0, "total": 0}
        assert u2.get("/api/reports/recurring").json() == []
        assert u2.get("/api/transactions/transfer-candidates").json() == []
        ids = {c["id"] for c in u2.get("/api/categories?flat=1").json()}
        assert victim["category"]["id"] not in ids


# ======================================================================================
# 3. Import pipeline — every fixture
# ======================================================================================

# name -> (bank_profile, valid rows, charges sum, payments sum, first date, last date, invalid rows)
# Sums computed by hand from backend/tests/fixtures/*.csv using the app's convention (charges negative).
FIXTURE_EXPECT = {
    "amex.csv": ("amex", 5, "-531.21", "600.00", "2024-08-02", "2024-08-12", 0),
    "bmo_card.csv": ("bmo", 3, "-58.59", "500.00", "2024-08-01", "2024-08-06", 0),
    "bmo_chequing.csv": ("bmo", 3, "-66.40", "3100.00", "2024-08-01", "2024-08-06", 0),
    "bofa_card.csv": ("bofa", 3, "-30.55", "350.00", "2024-08-03", "2024-08-09", 0),
    "bofa_checking.csv": ("bofa", 5, "-812.34", "2500.00", "2024-08-02", "2024-08-20", 1),
    "capital_one_360.csv": ("capital_one", 3, "-120.50", "1500.00", "2024-08-01", "2024-08-09", 0),
    "capital_one_card.csv": ("capital_one", 4, "-94.45", "300.00", "2024-08-01", "2024-08-08", 0),
    "chase_card.csv": ("chase", 6, "-144.73", "450.00", "2024-08-01", "2024-08-15", 0),
    "chase_checking.csv": ("chase", 8, "-939.99", "2500.00", "2024-08-01", "2024-08-12", 0),
    "citi.csv": ("citi", 4, "-166.77", "520.00", "2024-08-02", "2024-08-11", 0),
    "discover.csv": ("discover", 3, "-92.55", "200.00", "2024-08-01", "2024-08-09", 0),
    "generic_headerless.csv": (None, 3, "-124.50", "3000.00", "2024-08-01", "2024-08-03", 0),
    "generic_semicolon.csv": (None, 3, "-124.50", "3000.00", "2024-08-13", "2024-08-15", 0),
    "generic.xlsx": (None, 3, "-124.50", "3000.00", "2024-08-01", "2024-08-03", 0),
    "generic.xls": (None, 3, "-124.50", "3000.00", "2024-08-01", "2024-08-03", 0),
    "pnc_activity.csv": ("pnc", 3, "-184.12", "2500.00", "2024-08-02", "2024-08-12", 0),
    "pnc_checking.csv": ("pnc", 5, "-649.61", "2500.00", "2024-08-01", "2024-08-12", 0),
    "pnc_classic.csv": ("pnc", 3, "-184.12", "2500.00", "2024-08-02", "2024-08-12", 0),
    "rbc.csv": ("rbc", 5, "-335.65", "3200.00", "2024-08-01", "2024-08-09", 0),
    "scotiabank.csv": ("wells_fargo", 3, "-100.65", "3200.00", "2024-08-01", "2024-08-05", 0),  # shares WF shape
    "td.csv": ("td", 4, "-250.65", "3200.00", "2024-08-01", "2024-08-07", 0),
    "wells_fargo.csv": ("wells_fargo", 5, "-1321.50", "2500.00", "2024-08-01", "2024-08-15", 0),
    # no account at upload time -> no auto flip: the file's own signs are kept
    "generic_positive_charges.csv": (None, 6, "-200.00", "226.90", "2024-08-01", "2024-08-06", 0),
}
IN_FILE_DUPES = {"chase_checking.csv": 1}


def _valid_rows(preview):
    return [r for r in preview["rows"] if r["is_valid"]]


class TestImportPipeline:
    @pytest.mark.parametrize("name", sorted(FIXTURE_EXPECT))
    def test_fixture_roundtrip(self, u1, name):
        profile, n, charges, payments, d0, d1, invalid = FIXTURE_EXPECT[name]
        acct = create_account(u1, f"QA fx {name}", "checking")
        r = upload(u1, os.path.join(FIXTURES, name))
        assert r.status_code == 201, r.text
        up = r.json()
        assert up["status"] == "parsing" and up["original_filename"] == name  # duplicate_of covered separately
        assert up["file_kind"] == name.rsplit(".", 1)[1]
        sid = up["id"]
        pv = wait_status(u1, sid, ("previewed", "error"))
        assert pv["status"] == "previewed", pv.get("error_message")
        assert pv["bank_profile"] == profile
        if profile:
            assert float(pv["profile_confidence"]) >= 0.6
        else:
            assert any("Bank not recognised" in w for w in pv["warnings"])
        rows = _valid_rows(pv)
        assert len(rows) == n and pv["summary"]["valid"] == n and pv["summary"]["invalid"] == invalid
        assert pv["summary"]["rows_total"] == n + invalid and pv["summary"]["dupes_existing"] == 0
        assert pv["summary"]["dupes_in_file"] == IN_FILE_DUPES.get(name, 0)
        dates = sorted(r["txn_date"] for r in rows)
        assert (dates[0], dates[-1]) == (d0, d1)
        assert all(date.fromisoformat(r["txn_date"]) for r in rows)
        out, inn = sums(rows)
        assert (out, inn) == (money(charges), money(payments))
        assert (money(pv["summary"]["charges"]["sum"]), money(pv["summary"]["payments"]["sum"])) == (out, inn)
        assert pv["summary"]["included"] == n
        assert all(r["description"] for r in rows)
        assert pv["suggested_account_id"] is None or isinstance(pv["suggested_account_id"], int)
        # commit into the account
        c = u1.post(f"/api/statements/{sid}/commit", json={"account_id": acct})
        assert c.status_code == 200, c.text
        res = c.json()
        assert (res["imported"], res["skipped_duplicates"], res["skipped_invalid"], res["excluded_by_user"]) == (n, 0, invalid, 0)
        assert sum(res["categorized"].values()) == n
        items, first = list_all(u1, account_id=acct)
        assert first["total"] == n and len(items) == n
        assert sums(items) == (out, inn)
        assert sorted(t["txn_date"] for t in items)[0] == d0
        assert all(t["statement_id"] == sid and t["account_id"] == acct and t["currency"] == "USD" for t in items)
        st = u1.get(f"/api/statements/{sid}").json()
        assert st["status"] == "committed" and st["stats"]["imported"] == n and st["account_id"] == acct
        assert "rows" not in st
        # re-upload the same file: every row is a known duplicate, commit imports nothing
        r2 = upload(u1, os.path.join(FIXTURES, name), account_id=acct)
        assert r2.status_code == 201 and r2.json()["duplicate_of"] == sid
        sid2 = r2.json()["id"]
        pv2 = wait_status(u1, sid2, ("previewed", "error"))
        assert pv2["status"] == "previewed"
        assert pv2["summary"]["dupes_existing"] == n and pv2["summary"]["included"] == 0
        assert all(r["duplicate_of"] and r["duplicate_txn"]["amount"] == r["amount"] for r in _valid_rows(pv2))
        c2 = u1.post(f"/api/statements/{sid2}/commit", json={"account_id": acct}).json()
        assert c2["imported"] == 0
        assert list_all(u1, account_id=acct)[1]["total"] == n
        # deleting the re-upload (0 transactions) needs no confirmation; the original needs the flag
        assert u1.delete(f"/api/statements/{sid2}").json() == {"ok": True, "deleted_transactions": 0}
        r = u1.delete(f"/api/statements/{sid}")
        assert r.status_code == 409 and f"imported {n} transactions" in r.json()["error"]
        assert list_all(u1, account_id=acct)[1]["total"] == n, "a refused delete must not touch anything"
        r = u1.delete(f"/api/statements/{sid}?with_transactions=true")
        assert r.json() == {"ok": True, "deleted_transactions": n}
        assert list_all(u1, account_id=acct)[1]["total"] == 0
        assert u1.get(f"/api/statements/{sid}").status_code == 404
        assert u1.delete(f"/api/accounts/{acct}").json()["ok"] is True

    def test_commit_reports_known_duplicates_as_skipped(self, u1):
        """Re-importing a file whose rows already exist must say so in the commit result."""
        acct = create_account(u1, "QA fx dupcount", "checking")
        import_fixture(u1, "discover.csv", acct)
        r = upload(u1, os.path.join(FIXTURES, "discover.csv"), account_id=acct)
        pv = wait_status(u1, r.json()["id"])
        assert pv["summary"]["dupes_existing"] == 3
        res = u1.post(f"/api/statements/{pv['id']}/commit", json={"account_id": acct}).json()
        u1.delete(f"/api/accounts/{acct}?force=true")
        assert (res["imported"], res["skipped_duplicates"]) == (0, 3), res

    def test_statement_file_download(self, u1, seeded):
        r = u1.get(f"/api/statements/{seeded['checking_statement']}/file")
        assert r.status_code == 200
        assert "chase_checking.csv" in r.headers.get("Content-Disposition", "")
        with open(os.path.join(FIXTURES, "chase_checking.csv"), "rb") as f:
            assert r.content == f.read()

    def test_statement_list_shape(self, u1, seeded):
        rows = u1.get("/api/statements").json()
        mine = next(s for s in rows if s["id"] == seeded["checking_statement"])
        assert mine["txn_count"] == 8 and mine["status"] == "committed" and mine["account_name"] == "QA Chase Checking"
        assert "stored_path" not in mine and "ocr_path" not in mine
        assert u1.get("/api/statements", params={"status": "committed", "account_id": seeded["checking"]}).json()[0]["id"] == mine["id"]
        assert u1.get("/api/statements", params={"status": "error"}).json() == []

    def test_generic_positive_charges_auto_flip_for_card_account(self, u1):
        card = create_account(u1, "QA fx autoflip card", "credit_card")
        chk = create_account(u1, "QA fx autoflip chk", "checking")
        # uploading straight into a card account flips at parse time
        r = upload(u1, os.path.join(FIXTURES, "generic_positive_charges.csv"), account_id=card)
        pv = wait_status(u1, r.json()["id"], ("previewed", "error"))
        assert pv["status"] == "previewed" and pv["mapping"]["flip_sign"] is True
        assert any(w.startswith("Amounts were flipped automatically") for w in pv["warnings"])
        assert sums(_valid_rows(pv)) == (money("-226.90"), money("200.00"))
        # a checking account keeps the file's signs and warns instead
        r2 = upload(u1, os.path.join(FIXTURES, "generic_positive_charges.csv"), account_id=chk)
        pv2 = wait_status(u1, r2.json()["id"], ("previewed", "error"))
        assert pv2["status"] == "previewed" and not pv2["mapping"]["flip_sign"]
        assert any("Most amounts are positive" in w for w in pv2["warnings"])
        assert sums(_valid_rows(pv2)) == (money("-200.00"), money("226.90"))
        # choosing the card account after parsing flips too (recompute_dupes path)
        r3 = upload(u1, os.path.join(FIXTURES, "generic_positive_charges.csv"), filename="positive_again.csv")
        pv3 = wait_status(u1, r3.json()["id"], ("previewed", "error"))
        a = u1.put(f"/api/statements/{pv3['id']}/account", json={"account_id": card})
        assert a.status_code == 200 and a.json()["mapping"]["flip_sign"] is True
        assert sums(_valid_rows(a.json())) == (money("-226.90"), money("200.00"))
        assert a.json()["account_id"] == card
        for sid in (pv["id"], pv2["id"], pv3["id"]):
            assert u1.delete(f"/api/statements/{sid}").status_code == 200
        u1.delete(f"/api/accounts/{card}")
        u1.delete(f"/api/accounts/{chk}")

    def test_flip_signs_on_committed_statement(self, u1):
        acct = create_account(u1, "QA fx flip acct", "checking")
        pv, res = import_fixture(u1, "td.csv", acct)
        sid = pv["id"]
        r = u1.post(f"/api/statements/{sid}/flip-signs")
        assert r.json() == {"flipped": 4}
        items, _ = list_all(u1, account_id=acct)
        assert sums(items) == (money("-3200.00"), money("250.65"))
        assert u1.get(f"/api/statements/{sid}").json()["mapping"]["flip_sign"] is True
        events = u1.get(f"/api/transactions/{items[0]['id']}/events").json()
        assert any(e["kind"] == "manual" and e["detail"].get("flip_sign") for e in events)
        assert u1.post(f"/api/statements/{sid}/flip-signs").json() == {"flipped": 4}
        items, _ = list_all(u1, account_id=acct)
        assert sums(items) == (money("-250.65"), money("3200.00"))
        assert u1.get(f"/api/statements/{sid}").json()["mapping"]["flip_sign"] is False
        assert u1.delete(f"/api/accounts/{acct}?force=true").json()["deleted_transactions"] == 4

    def test_flip_signs_requires_committed(self, u1):
        r = upload(u1, os.path.join(FIXTURES, "td.csv"), filename="td_preview.csv")
        pv = wait_status(u1, r.json()["id"], ("previewed", "error"))
        f = u1.post(f"/api/statements/{pv['id']}/flip-signs")
        assert (f.status_code, f.json()) == (409, {"error": "Only imported statements can be flipped"})
        u1.delete(f"/api/statements/{pv['id']}")

    def test_mapping_edit_on_generic(self, u1):
        r = upload(u1, os.path.join(FIXTURES, "generic_headerless.csv"))
        pv = wait_status(u1, r.json()["id"], ("previewed", "error"))
        sid = pv["id"]
        m = pv["mapping"]
        assert (m["date"], m["amount"], m["balance"], m["description"], m["has_header"]) == (0, 2, 3, [1], False)
        # flip via the mapping
        r = u1.put(f"/api/statements/{sid}/mapping", json={"mapping": {**m, "flip_sign": True}})
        assert r.status_code == 200, r.text
        assert sums(_valid_rows(r.json())) == (money("-3000.00"), money("124.50"))
        # description from the balance column (silly but legal) and skip first row
        r = u1.put(f"/api/statements/{sid}/mapping", json={"mapping": {**m, "description": [3], "skip_rows": 1}})
        body = r.json()
        rows = _valid_rows(body)
        assert len(rows) == 2 and rows[0]["description"] == "3995.50"
        # a mapping without a date column yields only invalid rows -> still previewed, with a warning
        r = u1.put(f"/api/statements/{sid}/mapping", json={"mapping": {**m, "date": None, "amount": None}})
        assert r.status_code == 200 and r.json()["summary"]["valid"] == 0
        assert any("No valid transactions" in w for w in r.json()["warnings"])
        r = u1.put(f"/api/statements/{sid}/mapping", json={"mapping": "not-a-dict"})
        assert (r.status_code, r.json()) == (400, {"error": "mapping object is required"})
        r = u1.put(f"/api/statements/{sid}/mapping", json={"mapping": {**m, "date": "abc"}})
        assert r.status_code in (400, 409), r.text
        u1.delete(f"/api/statements/{sid}")

    def test_bank_profile_relabel_and_reparse(self, u1):
        r = upload(u1, os.path.join(FIXTURES, "scotiabank.csv"))
        pv = wait_status(u1, r.json()["id"], ("previewed", "error"))
        sid = pv["id"]
        assert pv["bank_profile"] == "wells_fargo"
        assert {p["key"] for p in pv["profile_options"]} >= {"scotiabank", "wells_fargo", "chase", "pnc"}
        r = u1.put(f"/api/statements/{sid}/mapping", json={"mapping": pv["mapping"], "bank_profile": "scotiabank"})
        assert r.status_code == 200 and r.json()["bank_profile"] == "scotiabank"
        assert sums(_valid_rows(r.json())) == (money("-100.65"), money("3200.00"))
        # plain reparse forgets the override; keep_mapping keeps it
        r = u1.post(f"/api/statements/{sid}/reparse", json={"keep_mapping": True})
        assert r.json() == {"id": sid, "status": "parsing"}
        pv2 = wait_status(u1, sid, ("previewed", "error"))
        assert pv2["status"] == "previewed" and pv2["bank_profile"] == "scotiabank"
        r = u1.post(f"/api/statements/{sid}/reparse", json={})
        pv3 = wait_status(u1, sid, ("previewed", "error"))
        assert pv3["bank_profile"] == "wells_fargo" and pv3["summary"] == pv["summary"]
        u1.delete(f"/api/statements/{sid}")

    def test_rows_include_toggle_and_preview_category(self, u1):
        acct = create_account(u1, "QA fx rows acct", "checking")
        r = upload(u1, os.path.join(FIXTURES, "rbc.csv"), account_id=acct)
        pv = wait_status(u1, r.json()["id"], ("previewed", "error"))
        sid = pv["id"]
        rows = _valid_rows(pv)
        bell = next(x for x in rows if "BELL" in x["description"])
        r = u1.put(f"/api/statements/{sid}/rows", json={"row_ids": [bell["id"]], "include": False})
        assert r.status_code == 200 and r.json()["updated"] == 1 and r.json()["summary"]["included"] == 4
        cat = cat_by_slug(u1, "pets")
        tims = next(x for x in rows if "TIM HORTONS" in x["description"])
        r = u1.put(f"/api/statements/{sid}/rows", json={"row_ids": [tims["id"]], "category_id": cat["id"]})
        assert r.status_code == 200 and r.json()["updated"] == 1
        assert r.json()["stats"]["predicted"]["manual"] == 1
        r = u1.put(f"/api/statements/{sid}/rows", json={"row_ids": [tims["id"]], "category_id": 999999999})
        assert r.status_code == 404
        r = u1.put(f"/api/statements/{sid}/rows", json={"category_id": cat["id"]})
        assert (r.status_code, r.json()) == (400, {"error": "row_ids is required"})
        res = u1.post(f"/api/statements/{sid}/commit", json={}).json()  # account chosen at upload time
        assert (res["imported"], res["excluded_by_user"], res["categorized"]["manual"]) == (4, 1, 1)
        items, _ = list_all(u1, account_id=acct)
        t = next(x for x in items if "TIM HORTONS" in x["description_raw"])
        assert (t["category_id"], t["category_status"], t["category_source"]) == (cat["id"], "confirmed", "manual")
        assert not any("BELL" in x["description_raw"] for x in items)
        assert any(m["merchant_key"] == t["merchant_key"] and m["category_id"] == cat["id"]
                   for m in u1.get("/api/merchants").json()), "a preview category must be learned"
        r = u1.put(f"/api/statements/{sid}/rows", json={"all": True, "include": False})
        assert r.status_code == 409
        r = u1.post(f"/api/statements/{sid}/commit", json={"account_id": acct})
        assert r.status_code == 409 and "not ready" in r.json()["error"]
        u1.delete(f"/api/merchants/{t['merchant_key']}")
        u1.delete(f"/api/accounts/{acct}?force=true")

    def test_commit_requires_account(self, u1):
        r = upload(u1, os.path.join(FIXTURES, "citi.csv"), filename="citi_noacct.csv")
        pv = wait_status(u1, r.json()["id"], ("previewed", "error"))
        c = u1.post(f"/api/statements/{pv['id']}/commit", json={})
        assert (c.status_code, c.json()) == (400, {"error": "Choose an account to import into"})
        c = u1.post(f"/api/statements/{pv['id']}/commit", json={"account_id": 999999999})
        assert (c.status_code, c.json()) == (400, {"error": "Choose an account to import into"})
        assert u1.get(f"/api/statements/{pv['id']}").json()["status"] == "previewed"
        u1.delete(f"/api/statements/{pv['id']}")

    def test_reject_empty_and_missing_file(self, u1):
        r = u1.post("/api/statements", data={"account_id": ""})
        assert (r.status_code, r.json()) == (400, {"error": "No file uploaded (field name 'file')"})
        r = upload(u1, None, filename="empty.csv", content=b"")
        assert (r.status_code, r.json()) == (400, {"error": "The uploaded file is empty"})

    def test_reject_unsupported_binary(self, u1):
        r = upload(u1, None, filename="virus.bin", content=b"\x00\x01\x02\xff\xfe" * 100)
        assert r.status_code == 400 and "Unsupported file type" in r.json()["error"]

    def test_exe_renamed_csv_never_500_and_never_imports(self, u1):
        content = b"MZ\x90\x00\x03\x00\x00\x00\x04\x00\x00\x00\xff\xff" + os.urandom(2048)
        r = upload(u1, None, filename="setup.exe.csv", content=content)
        assert r.status_code in (201, 400), r.text
        if r.status_code == 201:
            pv = wait_status(u1, r.json()["id"], ("previewed", "error"))
            assert pv["status"] in ("previewed", "error")
            if pv["status"] == "previewed":
                assert pv["summary"]["valid"] == 0
            u1.delete(f"/api/statements/{pv['id']}")

    def test_unicode_and_traversal_filenames(self, u1):
        r = upload(u1, os.path.join(FIXTURES, "td.csv"), filename="relevé août 🧾.csv")
        assert r.status_code == 201 and r.json()["original_filename"] == "relevé août 🧾.csv"
        pv = wait_status(u1, r.json()["id"], ("previewed", "error"))
        assert pv["status"] == "previewed" and pv["original_filename"] == "relevé août 🧾.csv"
        d = u1.get(f"/api/statements/{pv['id']}/file")
        assert d.status_code == 200 and "relev" in d.headers["Content-Disposition"]
        u1.delete(f"/api/statements/{pv['id']}")
        r = upload(u1, os.path.join(FIXTURES, "td.csv"), filename="../../../etc/passwd.csv")
        assert r.status_code == 201 and r.json()["original_filename"] == "passwd.csv"
        u1.delete(f"/api/statements/{r.json()['id']}")

    def test_oversized_upload_is_413(self, u1):
        big = b"Date,Description,Amount\n" + b"x" * (25 * 1024 * 1024 + 1)
        r = upload(u1, None, filename="huge.csv", content=big)
        assert r.status_code == 413, (r.status_code, r.text[:200])
        assert r.json()["error"].startswith("File is too large")

    def test_upload_duplicate_flag_for_second_identical_upload(self, u1):
        a = upload(u1, os.path.join(FIXTURES, "discover.csv"), filename="d1.csv").json()
        b = upload(u1, os.path.join(FIXTURES, "discover.csv"), filename="d2.csv").json()
        assert b["duplicate_of"] == a["id"]
        wait_status(u1, a["id"]); wait_status(u1, b["id"])
        # the shared stored file must survive deleting one of the two
        assert u1.delete(f"/api/statements/{a['id']}").status_code == 200
        assert u1.get(f"/api/statements/{b['id']}/file").status_code == 200
        u1.delete(f"/api/statements/{b['id']}")

    def test_statement_row_paging(self, u1):
        r = upload(u1, os.path.join(FIXTURES, "chase_checking.csv"), filename="paging.csv")
        sid = r.json()["id"]
        wait_status(u1, sid)
        p = u1.get(f"/api/statements/{sid}", params={"offset": 2, "limit": 3}).json()
        assert (len(p["rows"]), p["rows_offset"], p["rows_limit"]) == (3, 2, 3)
        assert [x["row_index"] for x in p["rows"]] == [3, 4, 5]
        p = u1.get(f"/api/statements/{sid}", params={"offset": -5, "limit": 5000}).json()
        assert (p["rows_offset"], p["rows_limit"]) == (0, 2000)
        u1.delete(f"/api/statements/{sid}")


# ======================================================================================
# 4. Transactions API
# ======================================================================================

@pytest.fixture(scope="session")
def manual(u1):
    """Deterministic hand-made data for report maths (all in Jun-Sep of the current app year)."""
    y = TODAY.year if TODAY.month >= 9 else TODAY.year - 1  # keep every date in the past
    acct = create_account(u1, "QA Manual", "checking")
    sav = create_account(u1, "QA Manual Savings", "savings")
    cat_a = u1.post("/api/categories", json={"name": "QA Cat A", "kind": "expense", "color": "c1"}).json()
    cat_b = u1.post("/api/categories", json={"name": "QA Cat B", "kind": "expense", "color": "c2"}).json()
    salary = cat_by_slug(u1, "income.salary")
    rows = [
        (f"{y}-07-01", "900.00", "QA EMPLOYER INC", salary["id"]),
        (f"{y}-07-05", "-40.00", "QA GYM CLUB", cat_a["id"]),
        (f"{y}-07-10", "-10.00", "QA BOOKSTORE", cat_b["id"]),
        (f"{y}-08-01", "1000.00", "QA EMPLOYER INC", salary["id"]),
        (f"{y}-08-05", "-40.00", "QA GYM CLUB", cat_a["id"]),
        (f"{y}-08-10", "-25.50", "QA BOOKSTORE", cat_b["id"]),
        (f"{y}-08-15", "-7.25", "QA UNCATEGORIZED THING", None),
        (f"{y}-08-20", "-250.00", "QA BIG PURCHASE", cat_b["id"]),
        (f"{y}-09-05", "-40.00", "QA GYM CLUB", cat_a["id"]),
    ]
    txns = [add_txn(u1, acct, d, a, desc, cid) for d, a, desc, cid in rows]
    out_leg = add_txn(u1, acct, f"{y}-06-22", "-300.00", "QA TRANSFER TO SAVINGS")
    in_leg = add_txn(u1, sav, f"{y}-06-23", "300.00", "QA TRANSFER FROM CHECKING")
    return {"acct": acct, "sav": sav, "cat_a": cat_a, "cat_b": cat_b, "salary": salary, "txns": txns,
            "year": y, "out_leg": out_leg, "in_leg": in_leg}


class TestTransactions:
    def test_create_manual_transaction_shape(self, u1, manual):
        t = manual["txns"][1]
        assert (money(t["amount"]), t["category_status"], t["category_source"]) == (money("-40"), "confirmed", "manual")
        assert t["merchant_key"] and t["merchant_name"] and t["description_raw"] == "QA GYM CLUB"
        assert t["statement_id"] is None and t["currency"] == "USD"
        u = manual["txns"][6]
        assert (u["category_id"], u["category_status"], u["category_source"]) == (None, "none", None)

    def test_create_validation(self, u1, manual):
        a = manual["acct"]
        r = u1.post("/api/transactions", json={"account_id": a, "txn_date": "nope", "amount": "1", "description": "x"})
        assert (r.status_code, r.json()) == (400, {"error": "Date, amount and description are required"})
        r = u1.post("/api/transactions", json={"account_id": a, "txn_date": "2024-01-01", "amount": "abc", "description": "x"})
        assert r.status_code == 400
        r = u1.post("/api/transactions", json={"account_id": a, "txn_date": "2024-01-01", "amount": "1", "description": "  "})
        assert r.status_code == 400
        r = u1.post("/api/transactions", json={"account_id": 999999, "txn_date": "2024-01-01", "amount": "1", "description": "x"})
        assert r.status_code == 404

    def test_list_filters_from_to_account_category(self, u1, manual):
        y, a = manual["year"], manual["acct"]
        items, first = list_all(u1, account_id=a, **{"from": f"{y}-08-01", "to": f"{y}-08-31"})
        assert first["total"] == 5 and len(items) == 5
        assert all(f"{y}-08-01" <= t["txn_date"] <= f"{y}-08-31" and t["account_id"] == a for t in items)
        assert (money(first["sum_in"]), money(first["sum_out"])) == (money("1000"), money("-322.75"))
        assert first["skipped"] == {"count": 0, "sum": 0.0}
        assert first["facets"]["status"]["uncategorized"] == 1 and first["currencies"] == ["USD"]
        acct_facet = {f["id"]: f["n"] for f in first["facets"]["accounts"]}
        assert acct_facet == {a: 5}
        cat_facet = {f["id"]: f["n"] for f in first["facets"]["categories"]}
        assert cat_facet == {manual["cat_a"]["id"]: 1, manual["cat_b"]["id"]: 2, manual["salary"]["id"]: 1, None: 1}
        items, _ = list_all(u1, account_id=a, category_id=manual["cat_b"]["id"])
        assert sorted(money(t["amount"]) for t in items) == [money("-250"), money("-25.5"), money("-10")]
        # parent category id includes its children
        income = cat_by_slug(u1, "income")
        items, _ = list_all(u1, account_id=a, category_id=income["id"])
        assert sorted(money(t["amount"]) for t in items) == [money("900"), money("1000")]
        items, _ = list_all(u1, account_id=a, category_id="none")
        assert [t["description_raw"] for t in items] == ["QA TRANSFER TO SAVINGS", "QA UNCATEGORIZED THING"] or \
            sorted(t["description_raw"] for t in items) == ["QA TRANSFER TO SAVINGS", "QA UNCATEGORIZED THING"]
        items, _ = list_all(u1, account_id=a, category_id=f"none,{manual['cat_a']['id']}")
        assert len(items) == 5

    def test_list_status_flow_q_min_max_sort(self, u1, manual):
        a = manual["acct"]
        items, _ = list_all(u1, account_id=a, status="uncategorized")
        assert {t["description_raw"] for t in items} == {"QA TRANSFER TO SAVINGS", "QA UNCATEGORIZED THING"}
        items, _ = list_all(u1, account_id=a, status="confirmed")
        assert len(items) == 8
        items, _ = list_all(u1, account_id=a, flow="in")
        assert [money(t["amount"]) for t in items] == [money("1000"), money("900")]
        items, _ = list_all(u1, account_id=a, flow="out", min="100")
        assert {money(t["amount"]) for t in items} == {money("-250"), money("-300")}
        items, _ = list_all(u1, account_id=a, min="7", max="10.5")
        assert {money(t["amount"]) for t in items} == {money("-7.25"), money("-10")}
        items, _ = list_all(u1, account_id=a, q="bookstore")
        assert len(items) == 2 and all("BOOKSTORE" in t["description_raw"] for t in items)
        items, _ = list_all(u1, account_id=a, q="%")  # LIKE wildcard must be harmless
        assert len(items) == 10
        for sort, key, rev in (("date", "txn_date", False), ("-date", "txn_date", True),
                               ("amount", "amount", False), ("-amount", "amount", True),
                               ("merchant", "merchant_name", False)):
            items, _ = list_all(u1, account_id=a, sort=sort)
            vals = [money(t[key]) if key == "amount" else t[key] for t in items]
            assert vals == sorted(vals, reverse=rev), sort
        r = u1.get("/api/transactions", params={"account_id": a, "sort": "evil; DROP TABLE"})
        assert r.status_code == 200 and [t["txn_date"] for t in r.json()["items"]] == \
            sorted((t["txn_date"] for t in r.json()["items"]), reverse=True), "invalid sort falls back to -date"

    def test_list_range_presets(self, u1, manual):
        for rng in ("this-week", "last-week", "this-month", "last-month", "last-30", "last-90", "this-year", "last-year", "all",
                    f"month:{manual['year']}-08", "month:2024-13", "bogus"):
            r = u1.get("/api/transactions", params={"range": rng, "account_id": manual["acct"]})
            assert r.status_code == 200, rng
        y = manual["year"]
        items, _ = list_all(u1, account_id=manual["acct"], range=f"month:{y}-07")
        assert sorted(t["txn_date"] for t in items) == [f"{y}-07-01", f"{y}-07-05", f"{y}-07-10"]
        # explicit from/to wins over the preset
        items, _ = list_all(u1, account_id=manual["acct"], range="last-year", **{"from": f"{y}-08-20", "to": f"{y}-08-20"})
        assert [t["description_raw"] for t in items] == ["QA BIG PURCHASE"]
        last_year = TODAY.year - 1
        items, _ = list_all(u1, account_id=manual["acct"], range="last-year")
        assert all(t["txn_date"].startswith(str(last_year)) for t in items)
        # the week presets honour the account's week_start preference (0 = Sunday)
        import datetime as _dt
        assert u1.put("/api/auth/me/preferences", json={"week_start": 1}).status_code == 200
        monday = TODAY - _dt.timedelta(days=TODAY.weekday())
        wk = add_txn(u1, manual["acct"], monday.isoformat(), "-3.00", "QA WEEK MARKER")
        items, _ = list_all(u1, account_id=manual["acct"], range="this-week")
        assert any(t["id"] == wk["id"] for t in items)
        summary = u1.get("/api/reports/summary", params={"range": "this-week"}).json()
        assert summary["range"]["start"] == monday.isoformat()
        assert u1.put("/api/auth/me/preferences", json={"week_start": 0}).status_code == 200
        u1.delete(f"/api/transactions/{wk['id']}")

    def test_list_limit_and_cursor_extremes(self, u1, manual):
        a = manual["acct"]
        for lim, expect in (("0", 1), ("-1", 1), ("100000", 10), ("abc", 10), ("2", 2)):
            r = u1.get("/api/transactions", params={"account_id": a, "limit": lim})
            assert r.status_code == 200, lim
            assert len(r.json()["items"]) == expect and r.json()["total"] == 10, lim
        r = u1.get("/api/transactions", params={"account_id": a, "limit": 3, "sort": "amount"})
        page1 = r.json()
        assert page1["next_cursor"]
        r2 = u1.get("/api/transactions", params={"account_id": a, "limit": 3, "sort": "amount", "cursor": page1["next_cursor"]})
        page2 = r2.json()
        ids1, ids2 = {t["id"] for t in page1["items"]}, {t["id"] for t in page2["items"]}
        assert not ids1 & ids2 and money(page2["items"][0]["amount"]) >= money(page1["items"][-1]["amount"])
        r = u1.get("/api/transactions", params={"account_id": a, "cursor": "!!!garbage!!!", "offset": "99999999"})
        assert r.status_code == 200 and r.json()["total"] == 10

    def test_totals_consistent_with_summary(self, u1, manual):
        y, a = manual["year"], manual["acct"]
        _, first = list_all(u1, account_id=a, **{"from": f"{y}-07-01", "to": f"{y}-08-31"})
        s = u1.get("/api/reports/summary", params={"account_id": a, "from": f"{y}-07-01", "to": f"{y}-08-31"}).json()
        assert (money(s["income"]), money(s["expenses"])) == (money(first["sum_in"]), -money(first["sum_out"]))
        assert s["txn_count"] == first["total"] == 8

    def test_get_single_and_events(self, u1, manual):
        t = manual["txns"][1]
        r = u1.get(f"/api/transactions/{t['id']}")
        assert r.status_code == 200
        body = r.json()
        assert body["id"] == t["id"] and body["statement"] is None
        assert {e["kind"] for e in body["events"]} == {"imported", "manual"}
        assert all(e["username"] == "qa_api1" for e in body["events"])
        others = {o["id"] for o in body["merchant_others"]}
        assert others == {manual["txns"][4]["id"], manual["txns"][8]["id"]}
        assert u1.get("/api/transactions/999999999").status_code == 404
        ev = u1.get(f"/api/transactions/{t['id']}/events").json()
        assert ev == body["events"]

    def test_update_note_category_merchant_and_events(self, u1, manual):
        t = manual["txns"][6]  # uncategorized
        r = u1.put(f"/api/transactions/{t['id']}", json={"notes": "  hello note  "})
        assert r.status_code == 200 and r.json()["notes"] == "hello note"
        r = u1.put(f"/api/transactions/{t['id']}", json={"category_id": manual["cat_a"]["id"], "learn": False})
        assert (r.json()["category_id"], r.json()["category_status"], r.json()["category_source"]) == \
            (manual["cat_a"]["id"], "confirmed", "manual")
        r = u1.put(f"/api/transactions/{t['id']}", json={"category_id": 999999999})
        assert (r.status_code, r.json()) == (404, {"error": "Category not found"})
        r = u1.put(f"/api/transactions/{t['id']}", json={"merchant_name": "QA Renamed"})
        assert r.json()["merchant_name"] == "QA Renamed"
        r = u1.put(f"/api/transactions/{t['id']}", json={"is_excluded": True})
        assert r.json()["is_excluded"] is True
        kinds = [e["kind"] for e in u1.get(f"/api/transactions/{t['id']}/events").json()]
        assert kinds == ["imported", "note", "manual", "excluded"]
        r = u1.put(f"/api/transactions/{t['id']}", json={"category_id": None, "notes": None, "is_excluded": False})
        body = r.json()
        assert (body["category_id"], body["notes"], body["is_excluded"], body["category_status"]) == (None, None, False, "none")
        u1.put(f"/api/transactions/{t['id']}", json={"merchant_name": t["merchant_name"]})

    def test_transfer_flag_uses_transfer_category(self, u1, manual):
        t = manual["txns"][2]
        r = u1.put(f"/api/transactions/{t['id']}", json={"is_transfer": True})
        body = r.json()
        assert body["is_transfer"] and body["is_excluded"] and body["category_id"] == manual["cat_b"]["id"], \
            "an existing category is kept"
        r = u1.put(f"/api/transactions/{t['id']}", json={"is_transfer": False})
        assert (r.json()["is_transfer"], r.json()["is_excluded"]) == (False, False)

    def test_bulk_actions(self, u1, manual):
        ids = [manual["txns"][2]["id"], manual["txns"][5]["id"]]  # the two bookstore rows
        r = u1.post("/api/transactions/bulk", json={"ids": ids, "action": "categorize", "category_id": manual["cat_a"]["id"], "learn": False})
        assert r.json()["updated"] == 2
        assert all(u1.get(f"/api/transactions/{i}").json()["category_id"] == manual["cat_a"]["id"] for i in ids)
        assert u1.post("/api/transactions/bulk", json={"ids": ids, "action": "exclude"}).json()["updated"] == 2
        assert u1.get(f"/api/transactions/{ids[0]}").json()["is_excluded"] is True
        assert u1.post("/api/transactions/bulk", json={"ids": ids, "action": "include"}).json()["updated"] == 2
        assert u1.post("/api/transactions/bulk", json={"ids": ids, "action": "set_transfer"}).json()["updated"] == 2
        assert u1.get(f"/api/transactions/{ids[0]}").json()["is_transfer"] is True
        assert u1.post("/api/transactions/bulk", json={"ids": ids, "action": "unset_transfer"}).json()["updated"] == 2
        assert u1.get(f"/api/transactions/{ids[0]}").json()["is_transfer"] is False
        assert u1.post("/api/transactions/bulk", json={"ids": ids, "action": "flip_sign"}).json()["updated"] == 2
        assert money(u1.get(f"/api/transactions/{ids[0]}").json()["amount"]) == money("10")
        assert u1.post("/api/transactions/bulk", json={"ids": ids, "action": "flip_sign"}).json()["updated"] == 2
        assert u1.post("/api/transactions/bulk", json={"ids": ids, "action": "accept_suggestion"}).json()["updated"] == 0
        assert u1.post("/api/transactions/bulk", json={"ids": ids, "action": "reject_suggestion"}).json()["updated"] == 0
        r = u1.post("/api/transactions/bulk", json={"ids": ids, "action": "categorize", "category_id": manual["cat_b"]["id"], "learn": False})
        assert r.json()["updated"] == 2
        assert u1.post("/api/transactions/bulk", json={"ids": ids, "action": "uncategorize"}).json()["updated"] == 2
        r = u1.post("/api/transactions/bulk", json={"ids": ids, "action": "categorize", "category_id": manual["cat_b"]["id"], "learn": False})
        assert r.json()["updated"] == 2
        r = u1.post("/api/transactions/bulk", json={"ids": ids, "action": "categorize"})
        assert (r.status_code, r.json()) == (400, {"error": "category_id is required"})
        r = u1.post("/api/transactions/bulk", json={"ids": ids, "action": "nonsense"})
        assert (r.status_code, r.json()) == (400, {"error": "Unknown action"})
        r = u1.post("/api/transactions/bulk", json={"ids": [], "action": "delete"})
        assert (r.status_code, r.json()) == (400, {"error": "ids are required"})
        r = u1.post("/api/transactions/bulk", json={"ids": "1,abc,,2", "action": "delete"})
        assert r.status_code == 404  # junk dropped, nothing owned

    def test_bulk_before_and_restore_roundtrip(self, u1, manual):
        ids = [manual["txns"][2]["id"], manual["txns"][5]["id"]]
        r = u1.post("/api/transactions/bulk", json={"ids": ids, "action": "exclude"}).json()
        assert r["updated"] == 2 and sorted(b["id"] for b in r["before"]) == sorted(ids)
        assert all(b["is_excluded"] is False and b["category_id"] == manual["cat_b"]["id"] for b in r["before"])
        assert set(r["before"][0]) == {"id", "category_id", "category_status", "category_source", "category_rule_id",
                                       "category_confidence", "ai_rationale", "is_transfer", "is_excluded", "transfer_pair_id"}
        assert u1.get(f"/api/transactions/{ids[0]}").json()["is_excluded"] is True
        r2 = u1.post("/api/transactions/bulk", json={"ids": ids, "action": "restore", "items": r["before"]}).json()
        assert r2 == {"updated": 2}
        got = u1.get(f"/api/transactions/{ids[0]}").json()
        assert got["is_excluded"] is False and got["category_id"] == manual["cat_b"]["id"]
        assert any(e["kind"] == "manual" and (e.get("detail") or {}).get("restored") for e in got["events"])
        # set_transfer files the row under Transfers; restoring puts the old category back and clears the flag
        r = u1.post("/api/transactions/bulk", json={"ids": ids, "action": "set_transfer"}).json()
        assert u1.get(f"/api/transactions/{ids[0]}").json()["is_transfer"] is True
        assert u1.post("/api/transactions/bulk", json={"ids": ids, "action": "restore", "items": r["before"]}).json() == {"updated": 2}
        got = u1.get(f"/api/transactions/{ids[0]}").json()
        assert (got["is_transfer"], got["is_excluded"], got["category_id"]) == (False, False, manual["cat_b"]["id"])
        # delete carries no snapshot; restore needs items
        assert "before" not in u1.post("/api/transactions/bulk", json={"ids": ids, "action": "restore", "items": []}).json()
        r = u1.post("/api/transactions/bulk", json={"ids": ids, "action": "restore"})
        assert (r.status_code, r.json()) == (400, {"error": "items are required"})

    def test_pair_returns_before_and_restore_unpairs(self, u1, manual):
        a, b = manual["out_leg"], manual["in_leg"]
        r = u1.post("/api/transactions/pair", json={"a_id": a["id"], "b_id": b["id"]}).json()
        assert r["ok"] and sorted(x["id"] for x in r["before"]) == sorted([a["id"], b["id"]])
        assert all(x["transfer_pair_id"] is None and x["is_transfer"] is False for x in r["before"])
        assert u1.get(f"/api/transactions/{a['id']}").json()["transfer_pair_id"] == b["id"]
        assert u1.post("/api/transactions/bulk", json={"ids": [a["id"], b["id"]], "action": "restore", "items": r["before"]}).json() == {"updated": 2}
        got = u1.get(f"/api/transactions/{a['id']}").json()
        assert (got["is_transfer"], got["transfer_pair_id"], got["category_id"]) == (False, None, None)
        r = u1.post("/api/transactions/auto-pair", json={"min_confidence": 0.5}).json()
        assert set(r) == {"paired", "before"} and isinstance(r["before"], list)
        if r["paired"]:
            u1.post("/api/transactions/bulk", json={"ids": [x["id"] for x in r["before"]], "action": "restore", "items": r["before"]})

    def test_list_facets_count_excluded_rows(self, u1, manual):
        tid = manual["txns"][6]["id"]
        assert u1.post("/api/transactions/bulk", json={"ids": [tid], "action": "exclude"}).json()["updated"] == 1
        st = u1.get("/api/transactions", params={"range": "all", "account_id": manual["acct"]}).json()["facets"]["status"]
        assert st["excluded"] == 1 and set(st) == {"uncategorized", "suggested", "transfer", "excluded"}
        assert u1.post("/api/transactions/bulk", json={"ids": [tid], "action": "include"}).json()["updated"] == 1
        assert u1.get("/api/transactions", params={"range": "all", "account_id": manual["acct"]}).json()["facets"]["status"]["excluded"] == 0

    def test_bulk_delete_and_single_delete(self, u1, manual):
        a = manual["acct"]
        t1 = add_txn(u1, a, f"{manual['year']}-05-01", "-1.00", "QA TEMP ONE")
        t2 = add_txn(u1, a, f"{manual['year']}-05-02", "-2.00", "QA TEMP TWO")
        assert u1.post("/api/transactions/bulk", json={"ids": [t1["id"]], "action": "delete"}).json() == {"updated": 1}
        assert u1.get(f"/api/transactions/{t1['id']}").status_code == 404
        assert u1.delete(f"/api/transactions/{t2['id']}").json() == {"ok": True}
        assert u1.delete(f"/api/transactions/{t2['id']}").status_code == 404

    def test_export_csv(self, u1, manual):
        y, a = manual["year"], manual["acct"]
        r = u1.get("/api/transactions/export", params={"account_id": a, "from": f"{y}-08-01", "to": f"{y}-08-31"})
        assert r.status_code == 200 and r.headers["Content-Type"].startswith("text/csv")
        assert "attachment; filename=transactions-" in r.headers["Content-Disposition"]
        rows = list(csv.reader(io.StringIO(r.text)))
        assert rows[0] == ["Date", "Account", "Description", "Merchant", "Category", "Amount", "Currency", "Notes", "Transfer", "Tags"]
        items, first = list_all(u1, account_id=a, **{"from": f"{y}-08-01", "to": f"{y}-08-31"})
        assert len(rows) - 1 == first["total"] == 5
        assert sorted(money(x[5]) for x in rows[1:]) == sorted(money(t["amount"]) for t in items)
        assert all(x[1] == "QA Manual" and x[6] == "USD" for x in rows[1:])
        big = next(x for x in rows[1:] if x[2] == "QA BIG PURCHASE")
        assert big[4] == "QA Cat B" and big[0] == f"{y}-08-20"
        sub = next(x for x in rows[1:] if x[2] == "QA EMPLOYER INC")
        assert sub[4] == "Income > Salary"

    def test_export_csv_escapes_formula_cells(self, u1, manual):
        """Cells starting with = + - @ are executed by Excel/Sheets when the CSV is opened."""
        t = add_txn(u1, manual["acct"], f"{manual['year']}-05-03", "-3.00", "=HYPERLINK(\"http://evil\",\"click\")",
                    notes="@SUM(A1:A9)")
        try:
            r = u1.get("/api/transactions/export", params={"account_id": manual["acct"], "q": "HYPERLINK"})
            row = next(x for x in csv.reader(io.StringIO(r.text)) if "HYPERLINK" in x[2])
            assert not row[2].startswith("=") and not row[7].startswith("@"), row
        finally:
            u1.delete(f"/api/transactions/{t['id']}")

    def test_suggest_and_by_merchant(self, u1, manual):
        r = u1.get("/api/transactions/suggest", params={"q": "gym"})
        assert r.status_code == 200 and r.json() and r.json()[0]["category_id"] == manual["cat_a"]["id"]
        assert r.json()[0]["n"] == 3
        assert u1.get("/api/transactions/suggest", params={"q": "g"}).json() == []
        key = manual["txns"][1]["merchant_key"]
        rows = u1.get(f"/api/transactions/merchant/{key}").json()
        assert len(rows) == 3 and all(r["merchant_key"] == key for r in rows)
        rows = u1.get(f"/api/transactions/merchant/{key}", params={"limit": 1}).json()
        assert len(rows) == 1

    def test_rule_draft(self, u1, manual):
        t = manual["txns"][1]
        r = u1.post(f"/api/transactions/{t['id']}/rule-draft")
        assert r.status_code == 200
        d = r.json()
        assert (d["match_type"], d["match_field"], d["pattern"], d["category_id"]) == \
            ("equals", "merchant_key", t["merchant_key"], manual["cat_a"]["id"])

    def test_transfer_candidates_pair_unpair_auto(self, u1, manual):
        out_leg, in_leg = manual["out_leg"], manual["in_leg"]
        cands = u1.get("/api/transactions/transfer-candidates").json()
        ours = next(c for c in cands if c["a"]["id"] == out_leg["id"])
        assert ours["b"]["id"] == in_leg["id"] and ours["confidence"] == 0.9
        r = u1.post("/api/transactions/pair", json={"a_id": out_leg["id"], "b_id": out_leg["id"]})
        assert (r.status_code, r.json()) == (400, {"error": "Pick two different transactions"})
        r = u1.post("/api/transactions/pair", json={"a_id": out_leg["id"], "b_id": manual["txns"][1]["id"]})
        assert (r.status_code, r.json()) == (400, {"error": "Both transactions are in the same account"})
        r = u1.post("/api/transactions/pair", json={})
        assert r.status_code == 400
        r = u1.post("/api/transactions/pair", json={"a_id": out_leg["id"], "b_id": in_leg["id"]})
        assert r.status_code == 200, r.text
        r = u1.post("/api/transactions/pair", json={"a_id": out_leg["id"], "b_id": manual["txns"][1]["id"]})
        assert r.status_code in (400, 409) and "already paired" in r.json()["error"] or r.status_code == 400, r.text
        r = u1.post("/api/transactions/pair", json={"a_id": out_leg["id"], "b_id": in_leg["id"]})
        assert r.status_code == 200 and r.json()["ok"] is True
        internal = cat_by_slug(u1, "transfers.internal")
        assert r.json()["category_id"] == internal["id"]
        a = u1.get(f"/api/transactions/{out_leg['id']}").json()
        b = u1.get(f"/api/transactions/{in_leg['id']}").json()
        assert (a["transfer_pair_id"], b["transfer_pair_id"]) == (in_leg["id"], out_leg["id"])
        assert a["is_transfer"] and b["is_transfer"] and a["is_excluded"] and a["category_id"] == internal["id"]
        assert not any(c["a"]["id"] == out_leg["id"] for c in u1.get("/api/transactions/transfer-candidates").json())
        r = u1.post(f"/api/transactions/{out_leg['id']}/unpair")
        assert r.json() == {"ok": True, "updated": 2}
        a = u1.get(f"/api/transactions/{out_leg['id']}").json()
        b = u1.get(f"/api/transactions/{in_leg['id']}").json()
        assert not a["is_transfer"] and not b["is_transfer"] and a["transfer_pair_id"] is None
        r = u1.post("/api/transactions/auto-pair", json={"min_confidence": 0.9})
        assert r.status_code == 200 and r.json()["paired"] >= 1
        a = u1.get(f"/api/transactions/{out_leg['id']}").json()
        assert a["transfer_pair_id"] == in_leg["id"] and a["is_transfer"]
        assert u1.post(f"/api/transactions/{in_leg['id']}/unpair").json()["updated"] == 2
        r = u1.post("/api/transactions/transfer-candidates".replace("transfer-candidates", "transfer-candidates?days=abc"))
        assert r.status_code in (200, 405)

    def test_delete_one_leg_releases_partner(self, u1, manual):
        y, a, s = manual["year"], manual["acct"], manual["sav"]
        o = add_txn(u1, a, f"{y}-05-10", "-55.00", "QA TEMP XFER OUT")
        i = add_txn(u1, s, f"{y}-05-11", "55.00", "QA TEMP XFER IN")
        assert u1.post("/api/transactions/pair", json={"a_id": o["id"], "b_id": i["id"]}).status_code == 200
        assert u1.delete(f"/api/transactions/{o['id']}").json() == {"ok": True}
        partner = u1.get(f"/api/transactions/{i['id']}").json()
        # partner keeps its transfer category, so the trigger keeps it a transfer, but the pair link is gone
        assert partner["transfer_pair_id"] is None
        u1.delete(f"/api/transactions/{i['id']}")


# ======================================================================================
# 5. Categories
# ======================================================================================

class TestCategories:
    def test_seeded_tree_and_flat(self, u1):
        tree = u1.get("/api/categories").json()
        flat = u1.get("/api/categories?flat=1").json()
        roots = {c["slug"] for c in tree}
        assert {"income", "transfers", "groceries", "dining", "transport", "housing", "fees", "taxes"} <= roots
        dining = next(c for c in tree if c["slug"] == "dining")
        assert [c["slug"] for c in dining["children"]] == ["dining.restaurants", "dining.coffee", "dining.delivery", "dining.bars"]
        assert all(c["is_system"] for c in flat if not c["name"].startswith("QA"))
        assert sum(len(c["children"]) for c in tree) + len(tree) == len(flat)
        assert all("txn_count" in c for c in tree)

    def test_create_rename_reorder_delete(self, u1):
        r = u1.post("/api/categories", json={"name": "QA Temp Root", "color": "c5", "icon": "star"})
        assert r.status_code == 201
        root = r.json()
        assert (root["slug"], root["kind"], root["color"], root["icon"], root["parent_id"]) == \
            ("qa_temp_root", "expense", "c5", "star", None)
        r = u1.post("/api/categories", json={"name": "QA Temp Child", "parent_id": root["id"]})
        child = r.json()
        assert r.status_code == 201 and (child["slug"], child["color"], child["kind"]) == ("qa_temp_root.qa_temp_child", "c5", "expense")
        r = u1.post("/api/categories", json={"name": "Grandchild", "parent_id": child["id"]})
        assert (r.status_code, r.json()) == (400, {"error": "Only two levels of categories are supported"})
        r = u1.post("/api/categories", json={"name": "qa temp CHILD", "parent_id": root["id"]})
        assert (r.status_code, r.json()) == (400, {"error": "A category with that name already exists here"})
        r = u1.post("/api/categories", json={"name": "QA Temp Root"})
        assert r.status_code == 400
        r = u1.post("/api/categories", json={"name": "QA Temp Child"})  # same name, different parent is fine
        assert r.status_code == 201
        twin = r.json()
        assert twin["slug"] == "qa_temp_child"  # slug unique across the user
        r = u1.post("/api/categories", json={"name": "QA Temp Child 2", "parent_id": root["id"]})
        assert r.json()["slug"] == "qa_temp_root.qa_temp_child_2"
        r = u1.post("/api/categories", json={"name": ""})
        assert (r.status_code, r.json()) == (400, {"error": "Name is required"})
        r = u1.post("/api/categories", json={"name": "Bad", "kind": "nope"})
        assert (r.status_code, r.json()) == (400, {"error": "Invalid kind"})
        r = u1.post("/api/categories", json={"name": "Bad", "color": "#ff0000"})
        assert (r.status_code, r.json()) == (400, {"error": "Invalid color slot"})
        r = u1.post("/api/categories", json={"name": "Bad", "parent_id": 999999999})
        assert (r.status_code, r.json()) == (404, {"error": "Parent category not found"})
        # rename + recolor with propagation
        r = u1.put(f"/api/categories/{root['id']}", json={"name": "QA Temp Root 2", "color": "c9", "propagate_color": True})
        assert r.json() == {"ok": True}
        flat = {c["id"]: c for c in u1.get("/api/categories?flat=1").json()}
        assert flat[root["id"]]["name"] == "QA Temp Root 2" and flat[child["id"]]["color"] == "c9"
        assert flat[root["id"]]["slug"] == "qa_temp_root", "slug is stable across renames"
        r = u1.put(f"/api/categories/{root['id']}", json={"name": "Dining"})
        assert r.status_code == 400
        r = u1.put(f"/api/categories/{root['id']}", json={"parent_id": root["id"]})
        assert (r.status_code, r.json()) == (400, {"error": "A category cannot be its own parent"})
        r = u1.put(f"/api/categories/{root['id']}", json={"parent_id": twin["id"]})
        assert (r.status_code, r.json()) == (400, {"error": "A category with subcategories cannot be nested"})
        r = u1.put(f"/api/categories/{twin['id']}", json={"parent_id": child["id"]})
        assert (r.status_code, r.json()) == (400, {"error": "Invalid parent category"})
        # kind change on the parent cascades to children
        r = u1.put(f"/api/categories/{root['id']}", json={"kind": "income"})
        assert r.status_code == 200
        flat = {c["id"]: c for c in u1.get("/api/categories?flat=1").json()}
        assert flat[child["id"]]["kind"] == "income"
        # reorder
        ids = [c["id"] for c in u1.get("/api/categories").json()]
        new_order = list(reversed(ids))
        assert u1.put("/api/categories/reorder", json={"ids": new_order}).json() == {"ok": True}
        assert [c["id"] for c in u1.get("/api/categories").json()] == new_order
        assert u1.put("/api/categories/reorder", json={"ids": ids}).json() == {"ok": True}
        assert [c["id"] for c in u1.get("/api/categories").json()] == ids
        # delete root cascades to children
        r = u1.delete(f"/api/categories/{root['id']}")
        assert r.status_code == 200 and r.json()["references"] == {"transactions": 0, "rules": 0, "merchants": 0}
        flat = {c["id"] for c in u1.get("/api/categories?flat=1").json()}
        assert root["id"] not in flat and child["id"] not in flat
        assert u1.delete(f"/api/categories/{twin['id']}").status_code == 200
        assert u1.delete(f"/api/categories/{twin['id']}").status_code == 404

    def test_kind_mismatch_child_under_income_parent(self, u1):
        income = cat_by_slug(u1, "income")
        r = u1.post("/api/categories", json={"name": "QA Expense Under Income", "parent_id": income["id"], "kind": "expense"})
        try:
            if r.status_code == 201:
                assert r.json()["kind"] == "income", "a child must inherit its parent's kind"
            else:
                assert r.status_code == 400
        finally:
            if r.status_code == 201:
                u1.delete(f"/api/categories/{r.json()['id']}")

    def test_delete_in_use_requires_reassign_and_merge(self, u1, manual):
        acct = manual["acct"]
        y = manual["year"]
        src = u1.post("/api/categories", json={"name": "QA Merge Src"}).json()
        dst = u1.post("/api/categories", json={"name": "QA Merge Dst"}).json()
        t = add_txn(u1, acct, f"{y}-05-20", "-9.00", "QA MERGE ME", src["id"], learn=True)
        rule = u1.post("/api/rules", json={"pattern": "qa merge", "category_id": src["id"]}).json()
        r = u1.delete(f"/api/categories/{src['id']}")
        assert r.status_code == 409
        assert r.json()["references"] == {"transactions": 1, "rules": 1, "merchants": 1}
        assert "1 transaction, 1 rule, 1 remembered merchant still use this category" in r.json()["error"]
        assert u1.get(f"/api/transactions/{t['id']}").json()["category_id"] == src["id"]
        r = u1.delete(f"/api/categories/{src['id']}?reassign_to={src['id']}")
        assert r.status_code == 404
        r = u1.post(f"/api/categories/{src['id']}/merge", json={"into": src["id"]})
        assert r.status_code == 400
        r = u1.post(f"/api/categories/{src['id']}/merge", json={"into": dst["id"]})
        assert r.json() == {"ok": True, "moved": 1}
        assert u1.get(f"/api/transactions/{t['id']}").json()["category_id"] == dst["id"]
        assert u1.get(f"/api/rules/{rule['id']}").json()["category_id"] == dst["id"]
        assert any(m["merchant_key"] == t["merchant_key"] and m["category_id"] == dst["id"]
                   for m in u1.get("/api/merchants").json())
        assert u1.get("/api/categories?flat=1").json() and src["id"] not in {c["id"] for c in u1.get("/api/categories?flat=1").json()}
        # delete with reassignment
        other = u1.post("/api/categories", json={"name": "QA Merge Other"}).json()
        r = u1.delete(f"/api/categories/{dst['id']}?reassign_to={other['id']}")
        assert r.status_code == 200 and r.json()["references"]["transactions"] == 1
        assert u1.get(f"/api/transactions/{t['id']}").json()["category_id"] == other["id"]
        u1.delete(f"/api/transactions/{t['id']}")
        u1.delete(f"/api/rules/{rule['id']}")
        u1.delete(f"/api/merchants/{t['merchant_key']}")
        assert u1.delete(f"/api/categories/{other['id']}").status_code == 200

    def test_system_category_deletion_and_reset_defaults(self, u1):
        taxes = cat_by_slug(u1, "taxes")
        assert taxes["is_system"] is True
        r = u1.delete(f"/api/categories/{taxes['id']}")
        # the API has no is_system guard: deletion of an unused system category succeeds
        assert r.status_code == 200
        with pytest.raises(KeyError):
            cat_by_slug(u1, "taxes")
        tree = u1.post("/api/categories/reset-defaults").json()
        assert any(c["slug"] == "taxes" for c in tree)
        assert cat_by_slug(u1, "taxes")["is_system"] is True


# ======================================================================================
# 6. Rules
# ======================================================================================

class TestRules:
    def test_rule_drove_categorization_on_commit(self, u1, seeded):
        res = seeded["checking_commit"]
        assert res["categorized"]["rule"] == 2
        items, _ = list_all(u1, account_id=seeded["checking"], q="WHOLEFDS")
        assert len(items) == 2
        assert all((t["category_id"], t["category_source"], t["category_status"], t["category_rule_id"]) ==
                   (seeded["rule_category"], "rule", "confirmed", seeded["rule_id"]) for t in items)
        rule = u1.get(f"/api/rules/{seeded['rule_id']}").json()
        assert rule["hit_count"] >= 2 and rule["last_hit_at"]  # other tests re-import the same file
        ev = u1.get(f"/api/transactions/{items[0]['id']}/events").json()
        assert [e["kind"] for e in ev] == ["imported", "rule"]

    @pytest.mark.parametrize("match_type,pattern,field,hits", [
        ("contains", "bookstore", "description_clean", 2),
        ("starts_with", "QA GYM", "description_raw", 3),
        ("equals", "QA BIG PURCHASE", "description_raw", 1),
        ("regex", r"^QA (GYM|BIG)\b", "description_raw", 4),
        ("regex", r"^qa\s+employer", "description_clean", 2),
    ])
    def test_match_types_via_preview(self, u1, manual, match_type, pattern, field, hits):
        r = u1.post("/api/rules/preview", json={"match_type": match_type, "match_field": field, "pattern": pattern,
                                                "account_id": manual["acct"]})
        assert r.status_code == 200, r.text
        assert r.json()["count"] == hits and len(r.json()["sample"]) == hits

    def test_validation(self, u1, manual):
        r = u1.post("/api/rules", json={"match_type": "regex", "pattern": "(unclosed", "category_id": manual["cat_a"]["id"]})
        assert r.status_code == 400 and r.json()["error"].startswith("Invalid regular expression")
        r = u1.post("/api/rules/preview", json={"match_type": "regex", "pattern": "[z-a]"})
        assert r.status_code == 400
        r = u1.post("/api/rules", json={"pattern": "x"})
        assert (r.status_code, r.json()) == (400, {"error": "Choose a category, or mark the rule as transfer/excluded"})
        r = u1.post("/api/rules", json={"pattern": "", "category_id": manual["cat_a"]["id"]})
        assert (r.status_code, r.json()) == (400, {"error": "Pattern is required"})
        r = u1.post("/api/rules", json={"pattern": "x", "match_type": "fuzzy", "category_id": manual["cat_a"]["id"]})
        assert r.status_code == 400 and "Match type" in r.json()["error"]
        r = u1.post("/api/rules", json={"pattern": "x", "match_field": "notes", "category_id": manual["cat_a"]["id"]})
        assert r.status_code == 400 and "Match field" in r.json()["error"]
        r = u1.post("/api/rules", json={"pattern": "x", "category_id": manual["cat_a"]["id"], "amount_min": "10", "amount_max": "5"})
        assert (r.status_code, r.json()) == (400, {"error": "Minimum amount must not exceed maximum amount"})
        r = u1.post("/api/rules", json={"pattern": "x", "category_id": manual["cat_a"]["id"], "amount_min": "abc"})
        assert (r.status_code, r.json()) == (400, {"error": "Minimum amount must be a number"})
        r = u1.post("/api/rules", json={"pattern": "x", "category_id": "abc"})
        assert (r.status_code, r.json()) == (400, {"error": "Category must be an integer"})
        r = u1.post("/api/rules", json={"pattern": "x", "category_id": manual["cat_a"]["id"], "priority": "high"})
        assert (r.status_code, r.json()) == (400, {"error": "Priority must be an integer"})
        assert u1.get("/api/rules/999999999").status_code == 404

    def test_create_apply_update_reorder_run_delete(self, u1, manual):
        y, acct = manual["year"], manual["acct"]
        t1 = add_txn(u1, acct, f"{y}-05-21", "-12.00", "QA RULEBAIT ALPHA")
        t2 = add_txn(u1, acct, f"{y}-05-22", "-13.00", "QA RULEBAIT BETA")
        t3 = add_txn(u1, acct, f"{y}-05-23", "-14.00", "QA RULEBAIT GAMMA", manual["cat_b"]["id"])
        r = u1.post("/api/rules", json={"name": "QA bait", "pattern": "rulebait", "category_id": manual["cat_a"]["id"],
                                        "apply_existing": True})
        assert r.status_code == 201 and r.json()["applied"] == 2, "only uncategorized rows by default"
        rid = r.json()["id"]
        got = u1.get(f"/api/rules/{rid}").json()
        assert (got["name"], got["match_type"], got["match_field"], got["is_active"], got["hit_count"]) == \
            ("QA bait", "contains", "description_clean", True, 2)
        assert got["priority"] > 0
        for t in (t1, t2):
            b = u1.get(f"/api/transactions/{t['id']}").json()
            assert (b["category_id"], b["category_source"], b["category_rule_id"]) == (manual["cat_a"]["id"], "rule", rid)
        assert u1.get(f"/api/transactions/{t3['id']}").json()["category_id"] == manual["cat_b"]["id"]
        r = u1.post(f"/api/rules/{rid}/apply", json={"only_uncategorized": False})
        assert r.json() == {"updated": 3}
        assert u1.get(f"/api/transactions/{t3['id']}").json()["category_id"] == manual["cat_a"]["id"]
        r = u1.put(f"/api/rules/{rid}", json={"pattern": "rulebait gam", "is_active": False})
        assert r.json() == {"ok": True, "applied": 0}
        got = u1.get(f"/api/rules/{rid}").json()
        assert got["pattern"] == "rulebait gam" and got["is_active"] is False
        r = u1.put(f"/api/rules/{rid}", json={"match_type": "regex", "pattern": "(("})
        assert r.status_code == 400
        r2 = u1.post("/api/rules", json={"name": "QA bait 2", "pattern": "rulebait", "set_excluded": True}).json()
        ids = [r["id"] for r in u1.get("/api/rules").json()]
        assert ids.index(rid) < ids.index(r2["id"])
        assert u1.put("/api/rules/reorder", json={"ids": [r2["id"], rid]}).json() == {"ok": True}
        rules = u1.get("/api/rules").json()
        pri = {r["id"]: r["priority"] for r in rules}
        assert pri[r2["id"]] == 0 and pri[rid] == 10
        assert [r["id"] for r in rules].index(r2["id"]) < [r["id"] for r in rules].index(rid)
        u1.post("/api/transactions/bulk", json={"ids": [t1["id"], t2["id"], t3["id"]], "action": "uncategorize"})
        r = u1.post("/api/rules/run", json={})
        assert r.status_code == 200 and r.json()["updated"] >= 3
        b = u1.get(f"/api/transactions/{t1['id']}").json()
        assert b["is_excluded"] is True and b["category_rule_id"] == r2["id"] and b["category_id"] is None
        assert u1.delete(f"/api/rules/{rid}").json() == {"ok": True}
        assert u1.delete(f"/api/rules/{r2['id']}").json() == {"ok": True}
        assert u1.delete(f"/api/rules/{rid}").status_code == 404
        b = u1.get(f"/api/transactions/{t1['id']}").json()
        assert b["category_rule_id"] is None, "rule FK is SET NULL on delete"
        u1.post("/api/transactions/bulk", json={"ids": [t1["id"], t2["id"], t3["id"]], "action": "delete"})


# ======================================================================================
# 7. Review
# ======================================================================================

class TestReview:
    def test_count_queue_resolve_learn(self, u1, manual):
        y, acct = manual["year"], manual["acct"]
        a = add_txn(u1, acct, f"{y}-05-24", "-21.00", "QA REVIEWBAIT SHOP")
        b = add_txn(u1, acct, f"{y}-05-25", "-22.00", "QA REVIEWBAIT SHOP")
        assert a["merchant_key"] == b["merchant_key"]
        count = u1.get("/api/review/count").json()
        assert count["uncategorized"] >= 2 and count["total"] == count["uncategorized"] + count["suggested"]
        q = u1.get("/api/review", params={"account_id": acct}).json()
        grp = next(g for g in q["groups"] if g["key"] == a["merchant_key"])
        assert grp["count"] == 2 and money(grp["total"]) == money("-43") and sorted(grp["ids"]) == sorted([a["id"], b["id"]])
        assert grp["suggestion"] is None and grp["currency"] == "USD"
        assert (grp["first"], grp["last"]) == (f"{y}-05-24", f"{y}-05-25")
        assert q["remaining"] >= 1 and q["remaining_items"] >= 2
        single = u1.get("/api/review", params={"mode": "single", "account_id": acct, "limit": 1}).json()
        assert len(single["items"]) == 1 and "raw" not in single["items"][0] and "fingerprint" not in single["items"][0]
        assert single["remaining"] >= 2
        r = u1.post("/api/review/resolve", json={"ids": [a["id"]]})
        assert (r.status_code, r.json()) == (400, {"error": "Choose a category or mark as transfer"})
        r = u1.post("/api/review/resolve", json={"ids": [], "category_id": manual["cat_a"]["id"]})
        assert (r.status_code, r.json()) == (400, {"error": "ids are required"})
        r = u1.post("/api/review/resolve", json={"ids": grp["ids"], "category_id": manual["cat_a"]["id"],
                                                 "create_rule": {"pattern": "reviewbait", "name": "QA review rule"}})
        assert r.status_code == 200 and r.json()["updated"] == 2 and r.json()["rule_id"]
        assert sorted(r.json()["ids"]) == sorted(grp["ids"]) and all(b["category_id"] is None for b in r.json()["before"])
        rid = r.json()["rule_id"]
        for t in (a, b):
            got = u1.get(f"/api/transactions/{t['id']}").json()
            assert (got["category_id"], got["category_status"], got["category_rule_id"]) == (manual["cat_a"]["id"], "confirmed", rid)
        rule = u1.get(f"/api/rules/{rid}").json()
        assert (rule["pattern"], rule["hit_count"], rule["category_id"]) == ("reviewbait", 2, manual["cat_a"]["id"])
        mem = next(m for m in u1.get("/api/merchants").json() if m["merchant_key"] == a["merchant_key"])
        assert mem["category_id"] == manual["cat_a"]["id"] and mem["txn_count"] == 2
        assert not any(g["key"] == a["merchant_key"] for g in u1.get("/api/review", params={"account_id": acct}).json()["groups"])
        # mark as transfer path
        c = add_txn(u1, acct, f"{y}-05-26", "-23.00", "QA REVIEWBAIT XFER")
        r = u1.post("/api/review/resolve", json={"ids": [c["id"]], "mark_transfer": True, "learn": False})
        assert r.json()["updated"] == 1
        got = u1.get(f"/api/transactions/{c['id']}").json()
        assert got["is_transfer"] and got["category_id"] == cat_by_slug(u1, "transfers.internal")["id"]
        # learn from history rebuilds memory without error
        r = u1.post("/api/merchants/learn")
        assert r.status_code == 200 and isinstance(r.json(), dict)
        r = u1.post("/api/merchants/renormalize")
        assert r.status_code == 200 and isinstance(r.json(), dict)
        u1.delete(f"/api/rules/{rid}")
        u1.delete(f"/api/merchants/{a['merchant_key']}")
        u1.post("/api/transactions/bulk", json={"ids": [a["id"], b["id"], c["id"]], "action": "delete"})

    def test_suggest_without_ai_is_502_not_500(self, u1, manual):
        r = u1.post("/api/review/suggest", json={"ids": [manual["txns"][6]["id"]]})
        assert r.status_code == 502 and "not enabled" in r.json()["error"]
        r = u1.post("/api/ai/categorize", json={"ids": [manual["txns"][6]["id"]]})
        assert r.status_code == 502
        s = u1.get("/api/ai/status").json()
        suggested = list_all(u1, status="suggested")[1]["total"]
        assert (s["enabled"], s["configured"], s["pending_suggestions"]) == (False, False, suggested)
        r = u1.post("/api/insights/generate", json={"month": f"{manual['year']}-08"})
        assert r.status_code == 502

    def test_merchant_memory_endpoints(self, u1, manual):
        key = manual["txns"][1]["merchant_key"]
        r = u1.put(f"/api/merchants/{key}", json={"category_id": manual["cat_b"]["id"], "apply_existing": True,
                                                  "display_name": "QA Gym Renamed", "rename_all": True})
        assert r.json() == {"ok": True, "applied": 3}
        got = u1.get(f"/api/transactions/{manual['txns'][1]['id']}").json()
        assert (got["category_id"], got["category_source"], got["merchant_name"]) == (manual["cat_b"]["id"], "merchant", "QA Gym Renamed")
        mem = next(m for m in u1.get("/api/merchants", params={"q": "gym"}).json() if m["merchant_key"] == key)
        assert mem["display_name"] == "QA Gym Renamed" and mem["txn_count"] == 3 and money(mem["total"]) == money("-120")
        r = u1.put(f"/api/merchants/{key}", json={})
        assert (r.status_code, r.json()) == (400, {"error": "category_id or display_name is required"})
        # restore category A on the gym rows for the report tests
        r = u1.put(f"/api/merchants/{key}", json={"category_id": manual["cat_a"]["id"], "apply_existing": True})
        assert r.json()["applied"] == 3
        assert u1.delete(f"/api/merchants/{key}").json() == {"ok": True}
        assert not any(m["merchant_key"] == key for m in u1.get("/api/merchants").json())
        got = u1.get(f"/api/transactions/{manual['txns'][1]['id']}").json()
        assert got["category_id"] == manual["cat_a"]["id"]


# ======================================================================================
# Accounts
# ======================================================================================

class TestBudgets:
    def test_crud_progress_and_copy(self, u1, manual):
        y = manual["year"]
        cat_a, cat_b, m = manual["cat_a"]["id"], manual["cat_b"]["id"], f"{y}-08"
        r = u1.post("/api/budgets", json={"category_id": cat_a, "month": m, "amount": "100", "note": " gym "})
        assert r.status_code == 201, r.text
        b = r.json()
        assert (b["category_id"], b["month"], b["amount"], b["note"]) == (cat_a, m, 100.0, "gym")
        r = u1.post("/api/budgets", json={"category_id": cat_a, "month": m, "amount": 80})
        assert r.status_code == 200 and r.json()["id"] == b["id"] and r.json()["amount"] == 80.0 and r.json()["note"] is None
        assert u1.post("/api/budgets", json={"category_id": cat_b, "month": m, "amount": 300}).status_code == 201
        listed = u1.get("/api/budgets", params={"month": m}).json()
        assert sorted(x["category_id"] for x in listed["budgets"]) == sorted([cat_a, cat_b]) and m in listed["months_with_budgets"]
        p = u1.get("/api/budgets/progress", params={"month": m}).json()
        by = {i["category_id"]: i for i in p["items"]}
        assert (by[cat_a]["budget"], by[cat_a]["spent"], by[cat_a]["remaining"], by[cat_a]["pct"]) == (80.0, 40.0, 40.0, 50.0)
        assert (by[cat_b]["budget"], by[cat_b]["spent"], by[cat_b]["pace_status"]) == (300.0, 275.5, "on_track")
        assert p["totals"] == {"budget": 380.0, "spent": 315.5, "remaining": 64.5, "pct": 83.0, "pace_status": "on_track"}
        assert (p["days_in_month"], p["days_elapsed"], p["elapsed_pct"], p["month"]) == (31, 31, 100.0, m)
        assert p["unbudgeted"]["count"] == 0 and p["unbudgeted"]["spent"] == 0.0  # 7.25 is uncategorized, not a category
        assert [i["category_id"] for i in p["items"]] == [cat_b, cat_a]  # highest percent used first
        p2 = u1.get("/api/budgets/progress", params={"month": m, "account_id": manual["sav"]}).json()
        assert p2["totals"]["spent"] == 0.0 and all(i["spent"] == 0.0 for i in p2["items"])
        # validation
        r = u1.post("/api/budgets", json={"category_id": cat_a, "month": m, "amount": 0})
        assert (r.status_code, r.json()) == (400, {"error": "amount must be greater than zero"})
        r = u1.post("/api/budgets", json={"category_id": cat_by_slug(u1, "transfers.internal")["id"], "month": m, "amount": 5})
        assert (r.status_code, r.json()) == (400, {"error": "Transfers cannot be budgeted"})
        assert u1.post("/api/budgets", json={"category_id": cat_a, "month": "2026-13", "amount": 5}).status_code == 400
        assert u1.get("/api/budgets/progress", params={"month": "nope"}).status_code == 400
        # parent / child overlap
        sub = u1.post("/api/categories", json={"name": "QA Sub A", "parent_id": cat_a}).json()
        r = u1.post("/api/budgets", json={"category_id": sub["id"], "month": m, "amount": 5})
        assert r.status_code == 400 and "not both" in r.json()["error"]
        # copy forward: existing target rows are skipped
        nxt = f"{y}-09"
        assert u1.post("/api/budgets", json={"category_id": cat_a, "month": nxt, "amount": 1}).status_code == 201
        assert u1.post("/api/budgets/copy", json={"from": m, "to": nxt}).json() == {"copied": 1, "skipped": 1}
        assert u1.post("/api/budgets/copy", json={"from": m, "to": m}).status_code == 400
        assert u1.post("/api/budgets/copy", json={"from": "2001-01", "to": nxt}).status_code == 400
        # update / delete
        r = u1.put(f"/api/budgets/{b['id']}", json={"amount": "55.5", "note": "x"})
        assert (r.json()["amount"], r.json()["note"]) == (55.5, "x")
        assert u1.put(f"/api/budgets/{b['id']}", json={"amount": -1}).status_code == 400
        assert u1.delete(f"/api/budgets/{b['id']}").json() == {"ok": True}
        assert u1.delete(f"/api/budgets/{b['id']}").status_code == 404
        for x in u1.get("/api/budgets", params={"month": m}).json()["budgets"] + u1.get("/api/budgets", params={"month": nxt}).json()["budgets"]:
            u1.delete(f"/api/budgets/{x['id']}")
        u1.delete(f"/api/categories/{sub['id']}")

    def test_empty_user_progress_is_zero(self, u2):
        p = u2.get("/api/budgets/progress").json()
        assert p["items"] == [] and p["totals"]["budget"] == 0.0 and p["unbudgeted"] == {"spent": 0.0, "count": 0, "categories": []}
        assert u2.get("/api/budgets").json()["budgets"] == []


class TestTags:
    def test_crud_filters_facets_and_export(self, u1, manual):
        acct = manual["acct"]
        a, b = manual["txns"][2], manual["txns"][5]
        r = u1.post("/api/tags", json={"name": "  QA Trip "})
        assert r.status_code == 201, r.text
        trip = r.json()
        assert trip["name"] == "QA Trip" and trip["color"] in {f"c{i}" for i in range(1, 13)} and trip["txn_count"] == 0
        r = u1.post("/api/tags", json={"name": "qa trip"})
        assert (r.status_code, r.json()) == (400, {"error": "A tag with that name already exists"})
        work = u1.post("/api/tags", json={"name": "QA Work", "color": "c9"}).json()
        assert u1.post("/api/tags", json={"name": "x", "color": "pink"}).status_code == 400
        # attach through PUT, then bulk
        got = u1.put(f"/api/transactions/{a['id']}", json={"tag_ids": [trip["id"], work["id"], trip["id"]]}).json()
        assert got["tag_ids"] == sorted([trip["id"], work["id"]])
        assert any(e["kind"] == "tag" for e in u1.get(f"/api/transactions/{a['id']}").json()["events"])
        assert u1.post("/api/transactions/bulk", json={"ids": [b["id"]], "action": "tag", "tag_ids": [trip["id"]]}).json()["updated"] == 1
        assert u1.get(f"/api/transactions/{b['id']}").json()["tag_ids"] == [trip["id"]]
        # filters and facets
        items, first = list_all(u1, account_id=acct, range="all", tag=trip["id"])
        assert sorted(t["id"] for t in items) == sorted([a["id"], b["id"]])
        assert {f["id"]: f["n"] for f in first["facets"]["tags"]} == {trip["id"]: 2, work["id"]: 1}
        items, _ = list_all(u1, account_id=acct, range="all", tag=f"{trip['id']},{work['id']}", tag_mode="all")
        assert [t["id"] for t in items] == [a["id"]]
        items, _ = list_all(u1, account_id=acct, range="all", tag="none")
        assert not any(t["id"] in (a["id"], b["id"]) for t in items)
        listed = {t["id"]: t for t in u1.get("/api/tags").json()}
        assert (listed[trip["id"]]["txn_count"], listed[work["id"]]["txn_count"]) == (2, 1)
        # export carries the names
        rows = list(csv.reader(io.StringIO(u1.get("/api/transactions/export", params={"account_id": acct, "range": "all", "tag": trip["id"]}).text)))
        assert rows[0][-1] == "Tags" and sorted(r[-1] for r in rows[1:]) == ["QA Trip", "QA Trip; QA Work"]
        # rename / recolour / foreign tag / untag / delete cascades
        assert u1.put(f"/api/tags/{work['id']}", json={"name": "QA Office", "color": "c3"}).json()["name"] == "QA Office"
        assert u1.put(f"/api/transactions/{a['id']}", json={"tag_ids": [999999]}).status_code == 404
        assert u1.post("/api/transactions/bulk", json={"ids": [a["id"]], "action": "untag", "tag_ids": [work["id"]]}).json()["updated"] == 1
        assert u1.get(f"/api/transactions/{a['id']}").json()["tag_ids"] == [trip["id"]]
        assert u1.post("/api/transactions/bulk", json={"ids": [a["id"]], "action": "tag", "tag_ids": []}).status_code == 400
        assert u1.delete(f"/api/tags/{trip['id']}").json() == {"ok": True, "removed_from": 2}
        assert u1.get(f"/api/transactions/{a['id']}").json()["tag_ids"] == []
        assert u1.delete(f"/api/tags/{work['id']}").json()["ok"] is True
        assert u1.delete(f"/api/tags/{work['id']}").status_code == 404


class TestAccounts:
    def test_crud_and_validation(self, u1):
        r = u1.post("/api/accounts", json={"name": "QA Acct X", "account_type": "credit_card", "currency": "cad",
                                           "last4": "12-34-5678", "institution": "td", "color": "c4"})
        assert r.status_code == 201
        body = r.json()
        assert (body["currency"], body["last4"], body["account_type"], body["institution"]) == ("CAD", "5678", "credit_card", "td")
        aid = body["id"]
        r = u1.post("/api/accounts", json={"name": "qa acct x"})
        assert (r.status_code, r.json()) == (400, {"error": "An account with that name already exists"})
        r = u1.post("/api/accounts", json={"name": "QA Bad", "currency": "EUR"})
        assert (r.status_code, r.json()) == (400, {"error": "Currency must be USD or CAD"})
        r = u1.post("/api/accounts", json={"name": "QA Bad", "account_type": "crypto"})
        assert (r.status_code, r.json()) == (400, {"error": "Invalid account type"})
        r = u1.post("/api/accounts", json={"name": "   "})
        assert (r.status_code, r.json()) == (400, {"error": "Account name is required"})
        listed = next(a for a in u1.get("/api/accounts").json() if a["id"] == aid)
        assert (listed["txn_count"], listed["balance"], listed["last_import_at"], listed["is_active"]) == (0, None, None, True)
        t = add_txn(u1, aid, "2024-02-02", "-5.00", "QA ACCT X THING")
        r = u1.put(f"/api/accounts/{aid}", json={"name": "QA Acct X2", "currency": "USD", "is_active": False})
        assert r.json() == {"ok": True}
        assert u1.get(f"/api/transactions/{t['id']}").json()["currency"] == "USD", "currency change propagates"
        assert not any(a["id"] == aid for a in u1.get("/api/accounts").json())
        archived = next(a for a in u1.get("/api/accounts?all=1").json() if a["id"] == aid)
        assert archived["is_active"] is False and archived["name"] == "QA Acct X2" and archived["txn_count"] == 1
        r = u1.delete(f"/api/accounts/{aid}")
        assert r.status_code == 409 and "1 transactions" in r.json()["error"]
        r = u1.delete(f"/api/accounts/{aid}?force=true")
        assert r.json() == {"ok": True, "deleted_transactions": 1, "deleted_statements": 0}
        assert u1.get(f"/api/transactions/{t['id']}").status_code == 404
        assert u1.put(f"/api/accounts/{aid}", json={"name": "x"}).status_code == 404

    def test_institutions(self, u1):
        inst = u1.get("/api/accounts/institutions").json()
        keys = {i["key"] for i in inst}
        assert {"chase", "amex", "capital_one", "bofa", "citi", "discover", "wells_fargo", "rbc", "td", "bmo", "scotiabank", "pnc"} == keys
        assert all(i["country"] in ("US", "CA") and i["label"] for i in inst)

    def test_account_delete_removes_its_statements(self, u1):
        acct = create_account(u1, "QA Acct Del", "checking")
        pv, _ = import_fixture(u1, "bmo_card.csv", acct)
        r = u1.delete(f"/api/accounts/{acct}?force=true")
        assert r.json() == {"ok": True, "deleted_transactions": 3, "deleted_statements": 1}
        assert u1.get(f"/api/statements/{pv['id']}").status_code == 404


# ======================================================================================
# 8. Reports — expected numbers derived by hand from the `manual` dataset
# ======================================================================================

class TestReports:
    def _aug(self, manual):
        y = manual["year"]
        return {"account_id": manual["acct"], "from": f"{y}-08-01", "to": f"{y}-08-31"}

    def test_summary(self, u1, manual):
        y = manual["year"]
        s = u1.get("/api/reports/summary", params=self._aug(manual)).json()
        assert {k: s[k] for k in ("income", "expenses", "net", "txn_count", "uncategorized", "transfers", "suggested")} == \
            {"income": 1000.0, "expenses": 322.75, "net": 677.25, "txn_count": 5, "uncategorized": 1, "transfers": 0, "suggested": 0}
        assert (s["range"]["name"], s["range"]["start"], s["range"]["end"]) == ("custom", f"{y}-08-01", f"{y}-08-31")
        assert (s["range"]["prev_start"], s["range"]["prev_end"]) == (f"{y}-07-01", f"{y}-07-31")
        assert {k: s["previous"][k] for k in ("income", "expenses", "net", "txn_count")} == \
            {"income": 900.0, "expenses": 50.0, "net": 850.0, "txn_count": 3}
        s2 = u1.get("/api/reports/summary", params={**self._aug(manual), "include_transfers": "1"}).json()
        assert (s2["income"], s2["expenses"]) == (1000.0, 322.75)

    def test_summary_on_fixture_data_matches_csv(self, u1, seeded):
        # chase_checking.csv: +2500 in, 939.99 out (incl. the 450 card payment) — include_transfers makes it exact
        s = u1.get("/api/reports/summary", params={"account_id": seeded["checking"], "from": "2024-08-01",
                                                   "to": "2024-08-31", "include_transfers": "1"}).json()
        assert (s["income"], s["expenses"], s["net"], s["txn_count"]) == (2500.0, 939.99, 1560.01, 8)
        s = u1.get("/api/reports/summary", params={"account_id": seeded["checking"], "range": "all"}).json()
        items, _ = list_all(u1, account_id=seeded["checking"])
        skipped_out = -sum((money(t["amount"]) for t in items if money(t["amount"]) < 0 and (t["is_transfer"] or t["is_excluded"])), Decimal(0))
        assert money(s["expenses"]) + skipped_out == money("939.99")
        assert s["transfers"] == sum(1 for t in items if t["is_transfer"])

    def test_by_category(self, u1, manual):
        r = u1.get("/api/reports/by-category", params=self._aug(manual)).json()
        assert r["total"] == 322.75 and r["level"] == "top" and r["flow"] == "spending"
        got = [(c["name"], c["total"], c["count"], c["pct"]) for c in r["categories"]]
        assert got == [("QA Cat B", 275.5, 2, 85.4), ("QA Cat A", 40.0, 1, 12.4), ("Uncategorized", 7.25, 1, 2.2)]
        unc = r["categories"][-1]
        assert (unc["id"], unc["color"], unc["icon"]) == (None, "muted", "help-circle")
        inc = u1.get("/api/reports/by-category", params={**self._aug(manual), "flow": "income", "level": "sub"}).json()
        assert [(c["name"], c["total"], c["pct"], c["parent_id"]) for c in inc["categories"]] == \
            [("Salary", 1000.0, 100.0, manual["salary"]["parent_id"])]
        inc = u1.get("/api/reports/by-category", params={**self._aug(manual), "flow": "income"}).json()
        assert [(c["name"], c["total"]) for c in inc["categories"]] == [("Income", 1000.0)]
        csvr = u1.get("/api/reports/by-category", params={**self._aug(manual), "format": "csv"})
        rows = list(csv.reader(io.StringIO(csvr.text)))
        assert csvr.headers["Content-Type"].startswith("text/csv") and rows[0] == ["name", "total", "count", "pct", "parent_id", "id"]
        assert [(x[0], x[1]) for x in rows[1:]] == [("QA Cat B", "275.5"), ("QA Cat A", "40.0"), ("Uncategorized", "7.25")]

    def test_monthly(self, u1, manual):
        y = manual["year"]
        r = u1.get("/api/reports/monthly", params={"account_id": manual["acct"], "months": 12}).json()
        assert len(r["months"]) == 12 and r["flow"] == "spending" and r["parent_id"] is None
        idx = {m: i for i, m in enumerate(r["months"])}
        assert f"{y}-08" in idx and f"{y}-07" in idx
        series = {s["name"]: s for s in r["series"]}
        a, b, unc = series["QA Cat A"], series["QA Cat B"], series["Uncategorized"]
        assert (a["values"][idx[f"{y}-07"]], a["values"][idx[f"{y}-08"]], a["values"][idx[f"{y}-09"]]) == (40.0, 40.0, 40.0)
        assert (b["values"][idx[f"{y}-07"]], b["values"][idx[f"{y}-08"]], b["total"]) == (10.0, 275.5, 285.5)
        assert unc["values"][idx[f"{y}-08"]] == 7.25 and unc["category_id"] is None
        assert r["totals"][idx[f"{y}-08"]] == 322.75 and r["totals"][idx[f"{y}-07"]] == 50.0
        assert a["category_id"] == manual["cat_a"]["id"] and a["color"] == "c1"
        sub = u1.get("/api/reports/monthly", params={"account_id": manual["acct"], "months": 3, "parent_id": manual["cat_a"]["id"]}).json()
        assert [s["name"] for s in sub["series"]] == ["Directly in QA Cat A"] and sub["series"][0]["total"] == 120.0
        r = u1.get("/api/reports/monthly", params={"account_id": manual["acct"], "months": 999}).json()
        assert len(r["months"]) == 60
        r = u1.get("/api/reports/monthly", params={"account_id": manual["acct"], "months": -3}).json()
        assert len(r["months"]) == 1
        c = u1.get("/api/reports/monthly", params={"account_id": manual["acct"], "months": 2, "format": "csv"})
        rows = list(csv.reader(io.StringIO(c.text)))
        assert rows[0][:2] == ["category", "total"] and len(rows[0]) == 4 and rows[-1][0] == "Total"

    def test_trends(self, u1, manual):
        y = manual["year"]
        rows = u1.get("/api/reports/trends", params={"account_id": manual["acct"], "months": 12}).json()
        assert len(rows) == 12
        by = {r["month"]: r for r in rows}
        assert by[f"{y}-08"] == {"month": f"{y}-08", "income": 1000.0, "expenses": 322.75, "net": 677.25, "count": 5}
        assert by[f"{y}-07"] == {"month": f"{y}-07", "income": 900.0, "expenses": 50.0, "net": 850.0, "count": 3}
        c = u1.get("/api/reports/trends", params={"account_id": manual["acct"], "months": 2, "format": "csv"})
        rows = list(csv.reader(io.StringIO(c.text)))
        assert rows[0] == ["month", "income", "expenses", "net", "count"] and len(rows) == 3

    def test_top_merchants(self, u1, manual):
        y = manual["year"]
        r = u1.get("/api/reports/top-merchants", params=self._aug(manual)).json()
        m = r["merchants"]
        assert [(x["total"], x["count"], x["avg"], x["pct"], x["last_date"]) for x in m] == [
            (250.0, 1, 250.0, 77.5, f"{y}-08-20"), (40.0, 1, 40.0, 12.4, f"{y}-08-05"),
            (25.5, 1, 25.5, 7.9, f"{y}-08-10"), (7.25, 1, 7.25, 2.2, f"{y}-08-15")]
        assert m[0]["category_id"] == manual["cat_b"]["id"] and m[3]["category_id"] is None
        assert all(len(x["sparkline"]) == 6 and x["merchant_key"] and x["merchant_name"] for x in m)
        r = u1.get("/api/reports/top-merchants", params={**self._aug(manual), "limit": 2}).json()
        assert len(r["merchants"]) == 2
        r = u1.get("/api/reports/top-merchants", params={**self._aug(manual), "flow": "income"}).json()
        assert [(x["total"], x["count"], x["pct"]) for x in r["merchants"]] == [(1000.0, 1, 100.0)]
        c = u1.get("/api/reports/top-merchants", params={**self._aug(manual), "format": "csv"})
        rows = list(csv.reader(io.StringIO(c.text)))
        assert rows[0] == ["merchant_name", "count", "total", "avg", "pct", "last_date", "category_id", "merchant_key"] and len(rows) == 5

    def test_month_over_month(self, u1, manual):
        y = manual["year"]
        r = u1.get("/api/reports/month-over-month", params={"account_id": manual["acct"], "month": f"{y}-08"}).json()
        assert (r["month"], r["previous_month"], r["flow"]) == (f"{y}-08", f"{y}-07", "spending")
        assert r["totals"] == {"current": 322.75, "previous": 50.0, "delta": 272.75, "pct": 545.5}
        cats = {c["name"]: c for c in r["categories"]}
        assert (cats["QA Cat B"]["current"], cats["QA Cat B"]["previous"], cats["QA Cat B"]["delta"], cats["QA Cat B"]["pct"]) == (275.5, 10.0, 265.5, 2655.0)
        assert (cats["QA Cat A"]["current"], cats["QA Cat A"]["previous"], cats["QA Cat A"]["delta"], cats["QA Cat A"]["pct"]) == (40.0, 40.0, 0.0, 0.0)
        assert (cats["Uncategorized"]["current"], cats["Uncategorized"]["previous"], cats["Uncategorized"]["pct"]) == (7.25, 0.0, None)
        assert [c["name"] for c in r["categories"]] == ["QA Cat B", "QA Cat A", "Uncategorized"]
        r2 = u1.get("/api/reports/month-over-month", params={"account_id": manual["acct"], "month": f"{y}-08", "vs": f"{y}-08"}).json()
        assert r2["previous_month"] == f"{y}-07", "comparing a month with itself falls back to the month before"
        r3 = u1.get("/api/reports/month-over-month", params={"account_id": manual["acct"], "month": f"{y}-08", "vs": f"{y}-06"}).json()
        assert r3["previous_month"] == f"{y}-06"
        c = u1.get("/api/reports/month-over-month", params={"account_id": manual["acct"], "month": f"{y}-08", "format": "csv"})
        rows = list(csv.reader(io.StringIO(c.text)))
        assert rows[0] == ["name", "current", "previous", "delta", "pct"] and rows[-1][:2] == ["Total", "322.75"]

    def test_recurring_and_dismiss(self, u1, manual):
        y = manual["year"]
        rows = u1.get("/api/reports/recurring").json()
        gym = next(r for r in rows if r["merchant_key"] == manual["txns"][1]["merchant_key"])
        expected_next = (date(y, 9, 5) + timedelta(days=30)).isoformat()
        assert {k: gym[k] for k in ("cadence", "occurrences", "median_amount", "last_amount", "monthly_equivalent",
                                    "amount_kind", "is_active", "last_date", "next_expected", "category_id",
                                    "category_name", "account_ids", "dismissed")} == {
            "cadence": "monthly", "occurrences": 3, "median_amount": 40.0, "last_amount": 40.0,
            "monthly_equivalent": 40.0, "amount_kind": "fixed", "is_active": True, "last_date": f"{y}-09-05",
            "next_expected": expected_next, "category_id": manual["cat_a"]["id"], "category_name": "QA Cat A",
            "account_ids": [manual["acct"]], "dismissed": False}
        assert sorted(gym["ids"]) == sorted(manual["txns"][i]["id"] for i in (1, 4, 8))
        assert not any(r["merchant_key"] == manual["txns"][2]["merchant_key"] for r in rows), "2 bookstore visits are not recurring"
        r = u1.post("/api/reports/recurring/dismiss", json={"merchant_key": gym["merchant_key"]})
        assert r.json() == {"ok": True}
        assert not any(r["merchant_key"] == gym["merchant_key"] for r in u1.get("/api/reports/recurring").json())
        again = next(r for r in u1.get("/api/reports/recurring?include_dismissed=1").json() if r["merchant_key"] == gym["merchant_key"])
        assert again["dismissed"] is True
        r = u1.post("/api/reports/recurring/dismiss", json={})
        assert (r.status_code, r.json()) == (400, {"error": "merchant_key is required"})
        assert u1.delete(f"/api/reports/recurring/dismiss/{gym['merchant_key']}").json() == {"ok": True}
        assert any(r["merchant_key"] == gym["merchant_key"] for r in u1.get("/api/reports/recurring").json())
        c = u1.get("/api/reports/recurring?format=csv")
        assert c.headers["Content-Type"].startswith("text/csv") and c.text.startswith("merchant_name,cadence,median_amount")

    def test_dashboard(self, u1, manual):
        y = manual["year"]
        d = u1.get("/api/reports/dashboard", params=self._aug(manual)).json()
        assert d["kpis"] == {"spent": 322.75, "spent_prev": 50.0, "income": 1000.0, "income_prev": 900.0,
                             "net": 677.25, "net_prev": 850.0, "txn_count": 5}
        assert [(c["name"], c["total"]) for c in d["top_categories"]] == [("QA Cat B", 275.5), ("QA Cat A", 40.0), ("Uncategorized", 7.25)]
        assert len(d["monthly"]) == 12 and {m["month"]: m for m in d["monthly"]}[f"{y}-08"] == \
            {"month": f"{y}-08", "spent": 322.75, "income": 1000.0, "net": 677.25}
        assert d["income"]["total"] == 1000.0 and d["income"]["previous"] == 900.0
        assert [(s["total"], s["count"]) for s in d["income"]["sources"]] == [(1000.0, 1)]
        assert len(d["recent"]) == 10 and all(t["account_id"] == manual["acct"] for t in d["recent"])
        assert d["recent"][0]["txn_date"] >= d["recent"][-1]["txn_date"]
        assert d["recurring"]["count"] >= 1 and d["recurring"]["monthly_total"] >= 40.0
        assert any(a["id"] == manual["acct"] for a in d["accounts"])
        assert set(d["review_count"]) == {"uncategorized", "suggested"}
        assert set(d["setup"]) == {"accounts", "statements", "transactions", "needs_review", "rules", "ai_configured"}
        assert d["setup"]["transactions"] >= 9 and d["setup"]["accounts"] >= 2 and d["setup"]["ai_configured"] is False
        assert d["range"]["name"] == "custom"
        d2 = u1.get("/api/reports/dashboard").json()
        assert d2["range"]["name"] == "this-month" and d2["range"]["start"] == TODAY.replace(day=1).isoformat()

    def test_insights_anomalies_and_dismiss(self, u1, manual):
        y = manual["year"]
        r = u1.get("/api/insights", params={"month": f"{y}-08"}).json()
        assert r["period"] == {"start": f"{y}-08-01", "end": f"{y}-08-31", "month": f"{y}-08"}
        assert r["ai"]["status"] == "disabled" and r["ai"]["content"] is None
        big = manual["txns"][7]
        an = next(a for a in r["anomalies"] if a["transaction_id"] == big["id"])
        assert (an["kind"], an["amount"], an["txn_date"], an["text"]) == ("new_merchant", 250.0, f"{y}-08-20", "First charge from this merchant")
        assert not any(a["transaction_id"] == manual["txns"][6]["id"] for a in r["anomalies"]), "7.25 is below the new-merchant threshold"
        assert any(s["merchant_key"] == manual["txns"][1]["merchant_key"] for s in r["recurring"])
        assert u1.post(f"/api/insights/anomalies/{big['id']}/dismiss").json() == {"ok": True}
        assert u1.post(f"/api/insights/anomalies/{big['id']}/dismiss").json() == {"ok": True}  # idempotent
        assert not any(a["transaction_id"] == big["id"] for a in u1.get("/api/insights").json()["anomalies"])
        assert u1.delete(f"/api/insights/anomalies/{big['id']}/dismiss").json() == {"ok": True}
        assert any(a["transaction_id"] == big["id"] for a in u1.get("/api/insights").json()["anomalies"])
        r = u1.get("/api/insights", params={"month": "garbage"}).json()
        assert r["period"]["month"] == TODAY.strftime("%Y-%m")

    def test_empty_user_all_zeros(self, u2):
        s = u2.get("/api/reports/summary", params={"range": "all"}).json()
        assert {k: s[k] for k in ("income", "expenses", "net", "txn_count", "uncategorized", "transfers", "suggested")} == \
            {"income": 0.0, "expenses": 0.0, "net": 0.0, "txn_count": 0, "uncategorized": 0, "transfers": 0, "suggested": 0}
        assert s["previous"] is None
        d = u2.get("/api/reports/dashboard").json()
        assert d["kpis"]["spent"] == 0.0 and d["kpis"]["txn_count"] == 0 and d["top_categories"] == [] and d["recent"] == []
        assert len(d["monthly"]) == 12 and all(m["spent"] == 0.0 for m in d["monthly"])
        assert d["recurring"] == {"count": 0, "monthly_total": 0.0, "next": []}
        bc = u2.get("/api/reports/by-category", params={"range": "all"}).json()
        assert bc["categories"] == [] and bc["total"] == 0
        m = u2.get("/api/reports/monthly").json()
        assert m["series"] == [] and m["totals"] == [0.0] * 12
        assert all(t["count"] == 0 and t["net"] == 0.0 for t in u2.get("/api/reports/trends").json())
        assert u2.get("/api/reports/top-merchants", params={"range": "all"}).json()["merchants"] == []
        mom = u2.get("/api/reports/month-over-month").json()
        assert mom["categories"] == [] and mom["totals"] == {"current": 0.0, "previous": 0.0, "delta": 0.0, "pct": None}
        assert u2.get("/api/reports/recurring").json() == []
        ins = u2.get("/api/insights").json()
        assert ins["recurring"] == [] and ins["anomalies"] == []
        assert u2.get("/api/reports/summary", params={"format": "csv"}).status_code == 200
        assert u2.get("/api/reports/by-category", params={"format": "csv"}).text == ""

    @pytest.mark.parametrize("path,params", [
        ("/api/reports/summary", {"from": "2024-12-31", "to": "2024-01-01"}),
        ("/api/reports/summary", {"range": "month:2024-13"}),
        ("/api/reports/summary", {"range": "month:abc"}),
        ("/api/reports/summary", {"range": "nonsense"}),
        ("/api/reports/summary", {"account_id": "abc,1e3"}),
        ("/api/reports/dashboard", {"from": "2024-12-31", "to": "2024-01-01"}),
        ("/api/reports/by-category", {"level": "bogus", "flow": "bogus"}),
        ("/api/reports/monthly", {"months": "0"}),
        ("/api/reports/top-merchants", {"limit": "-5"}),
        ("/api/reports/month-over-month", {"month": "2024-13", "vs": "xx"}),
    ])
    def test_lenient_params_never_500(self, u1, path, params):
        r = u1.get(path, params=params)
        assert r.status_code == 200, (path, params, r.status_code, r.text)
        if path == "/api/reports/summary":
            assert r.json()["range"]["start"] <= r.json()["range"]["end"]

    @pytest.mark.parametrize("path,params,msg", [
        ("/api/reports/summary", {"from": "garbage", "to": "2024-99-99"}, "from must be a date (YYYY-MM-DD)"),
        ("/api/reports/monthly", {"months": "abc"}, "months must be an integer"),
        ("/api/reports/monthly", {"parent_id": "abc"}, "parent_id must be an integer"),
        ("/api/reports/trends", {"months": "12.5"}, "months must be an integer"),
        ("/api/reports/top-merchants", {"limit": "many"}, "limit must be an integer"),
    ])
    def test_invalid_params_400(self, u1, path, params, msg):
        r = u1.get(path, params=params)
        assert (r.status_code, r.json()) == (400, {"error": msg})

    def test_reversed_dates_are_swapped_not_rejected(self, u1, manual):
        y = manual["year"]
        r = u1.get("/api/reports/summary", params={"account_id": manual["acct"], "from": f"{y}-08-31", "to": f"{y}-08-01"}).json()
        assert (r["range"]["start"], r["range"]["end"], r["expenses"]) == (f"{y}-08-01", f"{y}-08-31", 322.75)


# ======================================================================================
# 9. Settings
# ======================================================================================

class TestSettings:
    def test_get_masks_secret_and_client_shape(self, u1):
        secret = "sk-or-qa-super-secret-0123456789"
        try:
            r = u1.put("/api/settings", json={"openrouter_api_key": secret, "openrouter_model": "qa/model-x", "evil": "x"})
            assert r.json() == {"ok": True}
            s = u1.get("/api/settings")
            assert secret not in s.text
            body = s.json()
            assert body["openrouter_api_key"] == "••••••••" and body["openrouter_model"] == "qa/model-x"
            assert "evil" not in body and body["is_admin"] is False and "shared_api_key" not in body
            assert set(body) == {"openrouter_api_key", "openrouter_model", "ai_categorize_enabled", "ai_insights_enabled",
                                 "shared_available", "shared_model", "is_admin"}
            c = u1.get("/api/settings/client").json()
            assert c == {"ai_configured": True, "ai_categorize_enabled": False, "ai_insights_enabled": False, "ai_model": "qa/model-x"}
            assert secret not in u1.get("/api/ai/status").text
            r = u1.put("/api/settings", json={"ai_categorize_enabled": True, "ai_insights_enabled": "1", "openrouter_api_key": "••••••••"})
            assert r.status_code == 200
            assert u1.get("/api/settings").json()["ai_categorize_enabled"] == "1"
            c = u1.get("/api/settings/client").json()
            assert (c["ai_configured"], c["ai_categorize_enabled"], c["ai_insights_enabled"]) == (True, True, True), \
                "sending the mask back must keep the stored key"
            r = u1.put("/api/settings", json={"openrouter_api_key": ""})
            assert u1.get("/api/settings/client").json()["ai_configured"] is False
            assert u1.get("/api/settings").json()["openrouter_api_key"] == ""
        finally:
            u1.put("/api/settings", json={"openrouter_api_key": "", "openrouter_model": "", "ai_categorize_enabled": False,
                                          "ai_insights_enabled": False})

    def test_admin_sees_shared_key_mask_only(self, admin):
        s = admin.get("/api/settings").json()
        assert s["is_admin"] is True and "shared_api_key" in s and s["shared_api_key"] in ("", "••••••••")


# ======================================================================================
# 10. Robustness — none of these may return 500
# ======================================================================================

@pytest.fixture(scope="session")
def probe_ids(u1, manual):
    acct = create_account(u1, "QA Probe", "checking")
    t = add_txn(u1, acct, "2024-03-03", "-3.00", "QA PROBE TXN")
    r = upload(u1, os.path.join(FIXTURES, "citi.csv"), filename="probe_preview.csv")
    pv = wait_status(u1, r.json()["id"])
    cat = u1.post("/api/categories", json={"name": "QA Probe Cat"}).json()
    yield {"acct": acct, "txn": t["id"], "st": pv["id"], "cat": cat["id"], "key": t["merchant_key"]}
    u1.delete(f"/api/statements/{pv['id']}")
    u1.delete(f"/api/categories/{cat['id']}?reassign_to={cat['id']}")
    u1.delete(f"/api/categories/{cat['id']}")
    u1.delete(f"/api/accounts/{acct}?force=true")


NON_OBJECT_BODIES = ["[1,2,3]", '"just a string"', "12345", "true"]
JSON_ENDPOINTS = [
    ("POST", "/api/auth/login"), ("PUT", "/api/auth/me/preferences"), ("PUT", "/api/auth/me/password"),
    ("PUT", "/api/settings"), ("POST", "/api/accounts"), ("PUT", "/api/accounts/{acct}"), ("POST", "/api/categories"),
    ("PUT", "/api/categories/{cat}"), ("PUT", "/api/categories/reorder"), ("POST", "/api/categories/{cat}/merge"),
    ("PUT", "/api/statements/{st}/mapping"), ("PUT", "/api/statements/{st}/account"), ("PUT", "/api/statements/{st}/rows"),
    ("POST", "/api/statements/{st}/commit"), ("POST", "/api/statements/{st}/reparse"), ("POST", "/api/transactions"),
    ("PUT", "/api/transactions/{txn}"), ("POST", "/api/transactions/pair"), ("POST", "/api/transactions/auto-pair"),
    ("POST", "/api/transactions/bulk"), ("POST", "/api/review/resolve"), ("POST", "/api/review/suggest"),
    ("POST", "/api/rules"), ("PUT", "/api/rules/reorder"), ("POST", "/api/rules/preview"), ("POST", "/api/rules/run"),
    ("PUT", "/api/merchants/{key}"), ("POST", "/api/reports/recurring/dismiss"), ("POST", "/api/ai/categorize"),
    ("POST", "/api/insights/generate"),
]


def _fmt(path, ids):
    return path.format(**ids)


class TestRobustness:
    @pytest.mark.parametrize("method,path", JSON_ENDPOINTS)
    def test_non_object_json_body_never_500(self, u1, probe_ids, method, path):
        for body in NON_OBJECT_BODIES:
            r = u1.request(method, _fmt(path, probe_ids), data=body, headers={"Content-Type": "application/json"})
            assert r.status_code != 500, (method, path, body, r.text)

    @pytest.mark.parametrize("method,path", JSON_ENDPOINTS)
    def test_malformed_json_and_wrong_content_type_never_500(self, u1, probe_ids, method, path):
        url = _fmt(path, probe_ids)
        r = u1.request(method, url, data="{not json", headers={"Content-Type": "application/json"})
        assert r.status_code != 500, (method, path, r.text)
        r = u1.request(method, url, data='{"a": 1}', headers={"Content-Type": "text/plain"})
        assert r.status_code != 500, (method, path, r.text)
        assert r.headers["Content-Type"].startswith("application/json")

    @pytest.mark.parametrize("method,path,body", [
        ("PUT", "/api/transactions/{txn}", {"category_id": None, "notes": None, "merchant_name": None, "is_transfer": None,
                                            "is_excluded": None, "learn": None, "rename_all": None}),
        ("POST", "/api/transactions", {"account_id": None, "txn_date": None, "amount": None, "description": None,
                                       "category_id": None, "notes": None, "learn": None}),
        ("POST", "/api/accounts", {"name": None, "institution": None, "account_type": None, "currency": None,
                                   "last4": None, "color": None}),
        ("PUT", "/api/accounts/{acct}", {"name": None, "institution": None, "account_type": None, "currency": None,
                                         "last4": None, "color": None, "is_active": None}),
        ("POST", "/api/categories", {"name": None, "parent_id": None, "kind": None, "color": None, "icon": None}),
        ("PUT", "/api/categories/{cat}", {"name": None, "parent_id": None, "kind": None, "color": None, "icon": None,
                                          "propagate_color": None}),
        ("POST", "/api/categories/{cat}/merge", {"into": None}),
        ("PUT", "/api/categories/reorder", {"ids": None}),
        ("POST", "/api/rules", {"name": None, "match_type": None, "match_field": None, "pattern": None,
                                "case_sensitive": None, "amount_min": None, "amount_max": None, "account_id": None,
                                "category_id": None, "set_transfer": None, "set_excluded": None, "priority": None,
                                "is_active": None}),
        ("POST", "/api/rules/preview", {"pattern": None, "match_type": None}),
        ("PUT", "/api/rules/reorder", {"ids": None}),
        ("POST", "/api/transactions/bulk", {"ids": None, "action": None, "category_id": None, "learn": None}),
        ("POST", "/api/transactions/bulk", {"ids": ["{txn}"], "action": "restore", "items": None}),
        ("POST", "/api/transactions/bulk", {"ids": ["{txn}"], "action": "restore", "items": [None, 1, {"id": "{txn}", "category_id": "x"}]}),
        ("POST", "/api/transactions/pair", {"a_id": None, "b_id": None}),
        ("POST", "/api/transactions/auto-pair", {"min_confidence": None}),
        ("POST", "/api/review/resolve", {"ids": None, "category_id": None, "mark_transfer": None, "learn": None,
                                         "create_rule": None}),
        ("PUT", "/api/merchants/{key}", {"category_id": None, "display_name": None, "apply_existing": None,
                                         "rename_all": None, "is_transfer": None}),
        ("PUT", "/api/statements/{st}/mapping", {"mapping": None, "bank_profile": None}),
        ("PUT", "/api/statements/{st}/account", {"account_id": None}),
        ("PUT", "/api/statements/{st}/rows", {"row_ids": None, "include": None, "all": None, "only": None,
                                              "category_id": None, "confirm_suggestions": None}),
        ("POST", "/api/statements/{st}/commit", {"account_id": None}),
        ("POST", "/api/statements/{st}/reparse", {"keep_mapping": None, "bank_profile": None}),
        ("PUT", "/api/settings", {"openrouter_api_key": None, "openrouter_model": None, "ai_categorize_enabled": None}),
        ("PUT", "/api/auth/me/preferences", {"theme": None, "density": None, "default_account_id": None}),
        ("POST", "/api/reports/recurring/dismiss", {"merchant_key": None}),
        ("POST", "/api/auth/login", {"username": None, "password": None}),
    ])
    def test_nulls_in_every_field_never_500(self, u1, probe_ids, method, path, body):
        r = u1.request(method, _fmt(path, probe_ids), json=body)
        assert r.status_code != 500, (method, path, r.text)

    @pytest.mark.parametrize("method,path,body,params", [
        ("PUT", "/api/transactions/{txn}", {"category_id": "abc"}, None),
        ("PUT", "/api/transactions/{txn}", {"category_id": 99999999999}, None),
        ("PUT", "/api/transactions/{txn}", {"category_id": 1.5}, None),
        ("POST", "/api/transactions/bulk", {"ids": ["{txn}"], "action": "categorize", "category_id": "abc"}, None),
        ("POST", "/api/transactions", {"account_id": "abc", "txn_date": "2024-01-01", "amount": "1", "description": "x"}, None),
        ("POST", "/api/transactions", {"account_id": "{acct}", "txn_date": "2024-01-01", "amount": "1", "description": "x", "category_id": "abc"}, None),
        ("POST", "/api/categories", {"name": "QA type probe", "parent_id": "abc"}, None),
        ("PUT", "/api/categories/{cat}", {"parent_id": "abc"}, None),
        ("POST", "/api/categories/{cat}/merge", {"into": "abc"}, None),
        ("DELETE", "/api/categories/{cat}", None, {"reassign_to": "abc"}),
        ("PUT", "/api/merchants/{key}", {"category_id": "abc"}, None),
        ("POST", "/api/review/resolve", {"ids": ["{txn}"], "category_id": "abc"}, None),
        ("PUT", "/api/statements/{st}/account", {"account_id": "abc"}, None),
        ("PUT", "/api/statements/{st}/rows", {"row_ids": [1], "category_id": "abc"}, None),
        ("POST", "/api/statements/{st}/commit", {"account_id": "abc"}, None),
        ("POST", "/api/statements", None, None),
        ("POST", "/api/transactions/auto-pair", {"min_confidence": "abc"}, None),
        ("POST", "/api/transactions/pair", {"a_id": "abc", "b_id": 1}, None),
        ("GET", "/api/statements", None, {"limit": "abc"}),
        ("GET", "/api/statements", None, {"account_id": "abc"}),
        ("GET", "/api/statements", None, {"limit": "99999999999999999999"}),
        ("GET", "/api/statements/{st}", None, {"offset": "abc", "limit": "abc"}),
        ("GET", "/api/transactions/suggest", None, {"q": "ab", "limit": "abc"}),
        ("GET", "/api/transactions", None, {"category_id": "abc"}),
        ("GET", "/api/transactions", None, {"category_id": "99999999999"}),
        ("GET", "/api/transactions", None, {"account_id": "99999999999"}),
        ("GET", "/api/transactions", None, {"min": "NaN", "max": "Infinity"}),
        ("GET", "/api/transactions", None, {"min": "1e99999"}),
        ("GET", "/api/transactions", None, {"limit": "99999999999999999999"}),
        ("GET", "/api/transactions", None, {"from": "0001-01-01", "to": "9999-12-31"}),
        ("GET", "/api/transactions", None, {"range": "month:99999-01"}),
        ("GET", "/api/transactions", None, {"statement_id": "99999999999"}),
        ("GET", "/api/transactions/99999999999", None, None),
        ("GET", "/api/review", None, {"limit": "abc", "account_id": "99999999999", "mode": "bogus"}),
        ("GET", "/api/transactions/merchant/{key}", None, {"limit": "abc"}),
        ("GET", "/api/transactions/transfer-candidates", None, {"days": "abc"}),
        ("GET", "/api/transactions/transfer-candidates", None, {"days": "-99999999999"}),
        ("GET", "/api/reports/summary", None, {"account_id": "99999999999"}),
        ("GET", "/api/reports/monthly", None, {"months": "99999999999999999999"}),
        ("GET", "/api/reports/monthly", None, {"parent_id": "99999999999"}),
        ("GET", "/api/insights", None, {"month": "99999-01"}),
        ("POST", "/api/insights/anomalies/99999999999/dismiss", {}, None),
    ])
    def test_type_confusion_never_500(self, u1, probe_ids, method, path, body, params):
        url = _fmt(path, probe_ids)
        if body is not None:
            body = json.loads(json.dumps(body).replace('"{txn}"', str(probe_ids["txn"])).replace('"{acct}"', str(probe_ids["acct"])))
        r = u1.request(method, url, json=body, params=params)
        assert r.status_code != 500, (method, url, body, params, r.status_code, r.text)

    def test_huge_sqlish_unicode_strings(self, u1, probe_ids):
        big = "x" * 10000
        sqlish = "Robert'); DROP TABLE transactions; -- \" OR 1=1"
        emoji = "Café ☕ 🧾 東京 — ünïcödé"
        r = u1.put(f"/api/transactions/{probe_ids['txn']}", json={"notes": big, "merchant_name": sqlish})
        assert r.status_code == 200 and len(r.json()["notes"]) == 10000 and r.json()["merchant_name"] == sqlish
        for q in ("x" * 1500, sqlish, emoji, "%", "_", "\\", "'"):
            r = u1.get("/api/transactions", params={"q": q})
            assert r.status_code == 200, q[:40]
        r = u1.get("/api/transactions", params={"q": sqlish})
        assert r.json()["total"] == 1
        t = add_txn(u1, probe_ids["acct"], "2024-03-04", "-4.00", emoji + " " + sqlish, notes=big)
        assert emoji in t["description_raw"] and len(t["notes"]) == 10000
        r = u1.post("/api/accounts", json={"name": big, "institution": emoji})
        assert r.status_code == 201
        aid = r.json()["id"]
        assert u1.delete(f"/api/accounts/{aid}").status_code == 200
        r = u1.post("/api/categories", json={"name": emoji + sqlish})
        assert r.status_code == 201 and r.json()["slug"]
        cid = r.json()["id"]
        r = u1.post("/api/categories", json={"name": big})
        assert r.status_code == 201
        assert u1.delete(f"/api/categories/{cid}").status_code == 200
        assert u1.delete(f"/api/categories/{r.json()['id']}").status_code == 200
        r = u1.post("/api/rules", json={"pattern": big, "category_id": probe_ids["cat"], "name": big})
        assert (r.status_code, r.json()) == (400, {"error": "Pattern must be at most 200 characters"})
        r = u1.post("/api/rules", json={"pattern": "x" * 200, "category_id": probe_ids["cat"], "name": big})
        assert r.status_code == 201 and len(u1.get(f"/api/rules/{r.json()['id']}").json()["name"]) == 200
        u1.delete(f"/api/rules/{r.json()['id']}")
        r = u1.post("/api/rules/preview", json={"match_type": "regex", "pattern": "(a+)+$" + "a" * 50})
        assert r.status_code in (200, 400)
        for key in ("a/b/c", "x'y", "%27", "..%2F..%2Fetc", emoji):
            r = u1.get(f"/api/transactions/merchant/{key}")
            assert r.status_code in (200, 404), key
            r = u1.delete(f"/api/merchants/{key}")
            assert r.status_code in (200, 404), key
        r = u1.put("/api/auth/me/preferences", json={"theme": big, "currency": emoji})
        assert r.status_code == 400, "unknown theme/currency values are rejected, never stored"
        u1.put("/api/auth/me/preferences", json={"theme": "light", "currency": ""})
        u1.delete(f"/api/transactions/{t['id']}")
        u1.put(f"/api/transactions/{probe_ids['txn']}", json={"notes": None, "merchant_name": "QA Probe Txn"})

    def test_upload_field_name_and_form_garbage(self, u1):
        r = u1.post("/api/statements", files={"upload": ("x.csv", b"Date,Amount\n2024-01-01,1\n")})
        assert (r.status_code, r.json()) == (400, {"error": "No file uploaded (field name 'file')"})
        r = u1.post("/api/statements", files={"file": ("x.csv", b"Date,Description,Amount\n2024-01-01,A,-1\n")},
                    data={"account_id": "abc"})
        assert r.status_code != 500, r.text
        if r.status_code == 201:
            u1.delete(f"/api/statements/{r.json()['id']}")
