"""The admin console: its API (metrics, search) and its shell in a real browser.

    QA_SCRATCH=<dir> <venv>/bin/pytest qa/e2e/test_admin.py -p no:cacheprovider

Read-only against the admin account: nothing here creates, locks or deletes anyone.
"""
import json
import re

import pytest
from playwright.sync_api import sync_playwright

from conftest import ADMIN, BASE, api_login
from helpers import SW_BLOCKED_WARNING

TILE_KEYS = ["mrr", "paying", "trialing", "trials_ending", "signups", "activation", "importers", "wau",
             "churned", "failed_payments"]


@pytest.fixture(scope="module")
def admin_api():
    return api_login(*ADMIN)


class TestMetricsApi:
    @pytest.mark.parametrize("rng,points", [("7d", 7), ("30d", 30), ("90d", 13), ("12m", 13)])
    def test_overview_shape(self, admin_api, rng, points):
        r = admin_api.get(f"/api/admin/metrics/overview?range={rng}&refresh=1")
        assert r.status_code == 200, r.text
        d = r.json()
        assert [t["key"] for t in d["tiles"]] == TILE_KEYS
        assert {k: len(v) for k, v in d["series"].items()} == {k: len(d["series"]["labels"]) for k in d["series"]}
        assert abs(len(d["series"]["labels"]) - points) <= 1, "12 months may straddle 13 calendar months"
        assert d["as_of"] and d["range"]["key"] == rng

    def test_revenue_bridge_adds_up(self, admin_api):
        d = admin_api.get("/api/admin/metrics/revenue?range=90d&refresh=1").json()
        b = d["bridge_total"]
        assert b["start_mrr"] + b["new_mrr"] + b["reactivation_mrr"] + b["expansion_mrr"] \
            - b["contraction_mrr"] - b["churned_mrr"] == b["end_mrr"]
        assert len(d["bridge"]["new"]) == len(d["bridge"]["labels"])

    @pytest.mark.parametrize("path", ["trial-cohorts?range=12m", "funnel?range=90d", "engagement?range=90d",
                                      "engagement?range=12m&mode=monthly"])
    def test_growth_endpoints(self, admin_api, path):
        r = admin_api.get(f"/api/admin/metrics/{path}&refresh=1")
        assert r.status_code == 200, r.text
        d = r.json()
        if "steps" in d:
            counts = [s["n"] for s in d["steps"]]
            assert counts == sorted(counts, reverse=True), "each step is a subset of the one before"
        if "retention" in d:
            for row in d["retention"]["rows"]:
                assert all(c is None or c["n"] <= row["size"] for c in row["cells"])

    def test_system_health_has_every_part(self, admin_api):
        d = admin_api.get("/api/admin/system/health").json()
        assert set(d) == {"imports", "email", "stripe", "backups", "storage", "errors", "status"}
        assert all(d[k]["status"] in ("ok", "warn", "error") for k in d if k != "status")

    @pytest.mark.parametrize("path", ["imports", "ai"])
    def test_imports_and_ai_reports(self, admin_api, path):
        r = admin_api.get(f"/api/admin/{path}?range=30d")
        assert r.status_code == 200, r.text
        assert not re.search(r"original_filename|\.pdf\b|\.csv\b", r.text)

    def test_compare_adds_the_previous_period(self, admin_api):
        d = admin_api.get("/api/admin/metrics/overview?range=7d&compare=1").json()
        assert set(d["series"]["prev"]) == {"signups", "wau", "mrr"}

    def test_bad_range_is_a_400(self, admin_api):
        r = admin_api.get("/api/admin/metrics/overview?range=custom&from=2026-02-01&to=2026-01-01")
        assert (r.status_code, r.json()["error"]) == (400, "to must be on or after from")

    def test_no_money_from_anyone_s_transactions(self, admin_api):
        """The overview is built from subscriptions and usage counts; nothing a user typed or spent."""
        text = admin_api.get("/api/admin/metrics/overview?range=90d").text
        assert not re.search(r"merchant|description|txn_|\.pdf|\.csv", text)

    def test_search_finds_the_admin_by_name_and_id(self, admin_api):
        me = admin_api.get("/api/auth/me").json()
        for q in ("admin", f"#{me['id']}"):
            users = admin_api.get("/api/admin/search", params={"q": q}).json()["users"]
            assert users and users[0]["id"] == me["id"], (q, users)

    def test_people_list_pages_and_filters(self, admin_api):
        body = admin_api.get("/api/admin/users", params={"per_page": 1}).json()
        assert (body["per_page"], len(body["items"])) == (1, 1) and body["total"] >= 1
        me = admin_api.get("/api/auth/me").json()
        mine = admin_api.get("/api/admin/users", params={"q": f"#{me['id']}", "state": "admin_exempt,active,trialing,grace,read_only"}).json()
        assert [u["id"] for u in mine["items"]] in ([me["id"]], []), mine
        facets = admin_api.get("/api/admin/users/facets").json()
        assert set(facets) == {"sources", "tags"}

    def test_people_export_needs_a_recent_password(self):
        s = api_login(*ADMIN)
        s.auto_step_up = False
        r = s.get("/api/admin/users?format=csv")
        assert (r.status_code, r.json()["code"]) == (403, "step_up_required")

    def test_person_view_has_no_transaction_data(self, admin_api):
        me = admin_api.get("/api/auth/me").json()
        body = admin_api.get(f"/api/admin/users/{me['id']}").json()
        assert {"subscription", "activation", "timeline", "logins", "notes", "tags", "emails"} <= set(body)
        assert not re.search(r"description_|merchant_key|\.pdf\b|\.csv\b", json.dumps(body))

    def test_non_admins_are_refused(self, u1):
        for path in ("/api/admin/metrics/overview", "/api/admin/search?q=a", "/api/admin/users/facets"):
            assert u1.get(path).status_code == 403


@pytest.fixture(scope="module")
def page(admin_api):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        ctx.add_cookies([{"name": c.name, "value": c.value, "url": BASE} for c in admin_api.cookies])
        pg = ctx.new_page()
        pg.errors = []
        pg.on("pageerror", lambda e: pg.errors.append(str(e)))
        pg.on("console", lambda m: pg.errors.append(m.text) if m.type == "error" and m.text != SW_BLOCKED_WARNING
              and "Failed to load resource" not in m.text and "fetching the script" not in m.text else None)
        yield pg
        browser.close()


class TestShell:
    def test_overview_renders_tiles_and_charts(self, page):
        page.goto(f"{BASE}/admin.html?range=30d#overview")
        page.locator(".adm-kpi").first.wait_for()
        assert page.locator(".adm-kpi").count() == len(TILE_KEYS)
        assert page.locator("#admin-title").inner_text() == "Overview"
        assert page.locator("#ch-ov-mrr").is_visible()
        assert page.errors == []

    def test_admin_navigation_replaces_the_app_s(self, page):
        nav = page.locator(".sb-nav")
        assert nav.locator('a[data-page="users"]').is_visible()
        assert nav.locator('a[data-page="transactions"]').count() == 0
        assert page.locator('.sb-foot a[href="/index.html"]').inner_text().strip() == "Back to app"

    def test_period_and_compare_live_in_the_url(self, page):
        page.locator("#adm-range").click()
        page.locator('[data-range="90d"]').click()
        page.locator("#adm-compare").click()
        page.wait_for_function("location.search.includes('cmp=1')")
        assert "range=90d" in page.url and "cmp=1" in page.url
        page.reload()
        page.locator(".adm-kpi").first.wait_for()
        assert page.locator("#adm-range").inner_text().strip() == "Last 90 days"
        assert page.locator("#adm-compare").get_attribute("aria-pressed") == "true"

    def test_sections_switch_without_losing_the_period(self, page):
        page.locator('.sb-nav a[data-page="users"]').click()
        page.locator("#users-table tr[data-id]").first.wait_for()
        assert page.locator("#adm-range-group").is_hidden(), "users has no period"
        page.locator('.sb-nav a[data-page="overview"]').click()
        page.locator(".adm-kpi").first.wait_for()
        assert "range=90d" in page.url

    def test_palette_finds_people_and_opens_them(self, page):
        page.keyboard.press("Meta+k")
        page.locator(".palette-input input").fill("admin")
        page.locator(".palette-item", has_text="admin").first.wait_for()
        page.keyboard.press("Enter")
        page.locator(".drawer").wait_for()
        assert page.url.endswith("#users")
        page.keyboard.press("Escape")
        assert page.errors == []

    def test_people_filters_live_in_the_url_and_rows_open_the_person(self, page):
        page.goto(f"{BASE}/admin.html#users")
        page.locator("#users-table tr[data-id]").first.wait_for()
        page.locator('[data-act="f-status"]').click()
        page.locator(".menu-item", has_text="Everyone").click()
        page.wait_for_function("location.search.includes('status=all')")
        page.locator("#users-table tr[data-id]").first.wait_for()
        page.locator("#users-table tr[data-id] td:nth-child(3)").first.click()
        page.locator(".drawer .p360-tabs").wait_for()
        for tab in ("billing", "history", "notes", "summary"):
            page.locator(f'[data-p360-tab="{tab}"]').click()
            assert page.locator("#p360-tab").inner_text().strip()
        page.keyboard.press("Escape")
        assert page.errors == []

    @pytest.mark.parametrize("section,marker", [("revenue", "#ch-rv-bridge"), ("engagement", ".adm-funnel, #en-funnel .empty"),
                                                ("imports", "#ch-im"), ("system", ".adm-health-chip:not(.is-loading)")])
    def test_growth_sections_render(self, page, section, marker):
        page.goto(f"{BASE}/admin.html?range=90d#{section}")
        panel = page.locator(f'[data-panel="{section}"]')
        panel.locator(marker).first.wait_for()
        assert page.errors == []

    def test_phone_has_the_admin_bottom_bar(self, page):
        page.set_viewport_size({"width": 390, "height": 844})
        page.goto(f"{BASE}/admin.html#overview")
        page.locator(".adm-kpi").first.wait_for()
        labels = [t.strip() for t in page.locator(".bottomnav .bn-label").all_inner_texts()]
        assert labels[0] == "Overview" and labels[-1] == "More" and "Users" in labels
        page.set_viewport_size({"width": 1440, "height": 900})
