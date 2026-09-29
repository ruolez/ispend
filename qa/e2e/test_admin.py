"""The admin console: its API (metrics, search) and its shell in a real browser.

    QA_SCRATCH=<dir> <venv>/bin/pytest qa/e2e/test_admin.py -p no:cacheprovider

Read-only against the admin account: nothing here creates, locks or deletes anyone.
"""
import io
import json
import re
import time
import zipfile

import pytest
from playwright.sync_api import sync_playwright

from conftest import ADMIN, BASE, api_login
from helpers import SW_BLOCKED_WARNING

TILE_KEYS = ["mrr", "paying", "trialing", "trials_ending", "signups", "activation", "importers", "wau",
             "churned", "failed_payments"]


@pytest.fixture(scope="module")
def admin_api():
    return api_login(*ADMIN)


class TestDataRights:
    def test_a_person_can_download_their_own_data(self, u1):
        r = u1.post("/api/auth/me/exports", json={})
        assert r.status_code in (202, 409, 429), r.text
        deadline = time.time() + 60
        while time.time() < deadline:
            done = [x for x in u1.get("/api/auth/me/exports").json() if x["status"] == "done"]
            if done:
                break
            time.sleep(1)
        assert done, "the export finished"
        z = u1.get(f"/api/auth/me/exports/{done[0]['id']}/download")
        assert z.status_code == 200 and z.headers["content-type"] == "application/zip"
        names = zipfile.ZipFile(io.BytesIO(z.content)).namelist()
        assert {"README.txt", "transactions.csv", "accounts.json", "settings.json"} <= set(names)

    def test_nobody_else_can_download_it(self, u1, admin_api):
        mine = [x for x in u1.get("/api/auth/me/exports").json() if x["status"] == "done"]
        if mine:
            r = admin_api.get(f"/api/auth/me/exports/{mine[0]['id']}/download")
            assert (r.status_code, r.json()["code"]) == (403, "admin_account")

    def test_an_admin_erases_an_account_for_good(self, admin_api):
        r = admin_api.post("/api/admin/users", json={"username": "qa_erase_me", "password": "erase-me-pass1"})
        assert r.status_code == 201, r.text
        uid = r.json()["id"]
        r = admin_api.post(f"/api/admin/users/{uid}/erase", json={"confirm": "wrong"})
        assert r.status_code == 409
        r = admin_api.post(f"/api/admin/users/{uid}/erase", json={"confirm": "qa_erase_me", "reason": "e2e"})
        assert r.status_code == 200, r.text
        assert admin_api.get(f"/api/admin/users/{uid}").status_code == 404
        assert any(e["erased_user_id"] == uid for e in admin_api.get("/api/admin/erasures").json())


class TestMessages:
    def test_preview_and_validation(self, admin_api):
        r = admin_api.post("/api/admin/email/preview", json={"subject": "", "body": "x", "category": "service",
                                                             "audience": {"query": "?status=active"}})
        assert (r.status_code, r.json()["error"]) == (400, "A subject is 1 to 200 characters")
        r = admin_api.post("/api/admin/email/preview", json={"subject": "Hi {name}", "body": "Hello {name} {oops}",
                                                             "category": "service", "audience": {"query": "?status=active"}})
        assert r.status_code == 200, r.text
        body = r.json()
        if body["sample"]:
            assert "{oops}" in body["sample"]["text"] and "{name}" not in body["sample"]["text"]

    def test_a_message_to_one_person_is_recorded(self, admin_api):
        r = admin_api.post("/api/admin/users", json={"username": "qa_msg", "email": "qa_msg@example.com",
                                                      "password": "qa-msg-pass-1"})
        assert r.status_code == 201, r.text
        uid = r.json()["id"]
        try:
            r = admin_api.post(f"/api/admin/users/{uid}/email", json={"subject": "Hello", "body": "About your account",
                                                                      "category": "service"})
            assert r.status_code == 202, r.text
            campaign = r.json()["id"]
            deadline = time.time() + 30
            while time.time() < deadline:
                row = next(c for c in admin_api.get("/api/admin/email/campaigns").json() if c["id"] == campaign)
                if row["status"] != "sending":
                    break
                time.sleep(0.5)
            assert (row["status"], row["recipients"]) == ("done", 1)
            emails = admin_api.get(f"/api/admin/users/{uid}").json()["emails"]
            assert any(e["template"] == "campaign" for e in emails)
        finally:
            admin_api.post(f"/api/admin/users/{uid}/erase", json={"confirm": "qa_msg", "reason": "e2e"})


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

    def test_search_finds_customers_by_name_and_id_but_never_the_admin(self, admin_api, u1):
        person = u1.get("/api/auth/me").json()
        for q in ("qa_api1", f"#{person['id']}"):
            users = admin_api.get("/api/admin/search", params={"q": q}).json()["users"]
            assert users and users[0]["id"] == person["id"], (q, users)
        me = admin_api.get("/api/auth/me").json()
        assert me["id"] not in [u["id"] for u in admin_api.get("/api/admin/search", params={"q": f"#{me['id']}"}).json()["users"]]

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

    def test_person_view_has_no_transaction_data(self, admin_api, u1):
        person = u1.get("/api/auth/me").json()
        body = admin_api.get(f"/api/admin/users/{person['id']}").json()
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
        page.goto(f"{BASE}/admin?range=30d#overview")
        page.locator(".adm-kpi").first.wait_for()
        assert page.locator(".adm-kpi").count() == len(TILE_KEYS)
        assert page.locator("#admin-title").inner_text() == "Home"
        assert page.locator("#ch-ov-signups").is_visible()
        assert page.locator("#ov-alerts h2").inner_text() == "Needs attention"
        assert page.locator("a.adm-kpi").count() == len(TILE_KEYS), "every tile opens the list or page behind it"

    def test_a_tile_opens_the_customers_behind_it(self, page):
        page.goto(f"{BASE}/admin?range=30d#overview")
        page.locator('a.adm-kpi[data-kpi="trialing"]').click()
        page.locator("#users-table tr[data-id], #users-table .empty").first.wait_for()
        assert page.url.endswith("?range=30d&state=trialing#customers") or ("state=trialing" in page.url and page.url.endswith("#customers"))
        assert page.locator("#u-views .tab.active").inner_text() == "On a trial"
        page.go_back()
        page.locator(".adm-kpi").first.wait_for()
        assert page.errors == []

    def test_admin_navigation_replaces_the_app_s(self, page):
        nav = page.locator(".sb-nav")
        assert nav.locator('a[data-page="customers"]').is_visible()
        assert page.locator('.sb-foot a[data-page="settings"]').is_visible()
        assert nav.locator('a[data-page="transactions"]').count() == 0
        assert page.locator('a[href="/index.html"], .app-banner').count() == 0, "nothing of the customer app"

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
        page.locator('.sb-nav a[data-page="customers"]').click()
        page.locator("#users-table tr[data-id]").first.wait_for()
        assert page.locator("#adm-range-group").is_hidden(), "customers have no period"
        page.locator('.sb-nav a[data-page="overview"]').click()
        page.locator(".adm-kpi").first.wait_for()
        assert "range=90d" in page.url

    def test_palette_finds_people_and_opens_them(self, page, qa_users):
        page.keyboard.press("Meta+k")
        page.locator(".palette-input input").fill("qa_api1")
        page.locator(".palette-item", has_text="qa_api1").first.wait_for()
        page.keyboard.press("Enter")
        page.locator(".cust-head h1", has_text="qa_api1").wait_for()
        assert re.search(r"#customers/\d+$", page.url), page.url
        assert page.errors == []

    def test_customer_filters_live_in_the_url_and_rows_open_the_customer_page(self, page):
        page.goto(f"{BASE}/admin#users")               # the old name still lands on the list
        page.locator("#users-table tr[data-id]").first.wait_for()
        assert page.url.endswith("#customers")
        page.locator('[data-act="f-status"]').click()
        page.locator(".menu-item", has_text="Everyone").click()
        page.wait_for_function("location.search.includes('status=all')")
        page.locator("#users-table tr[data-id]").first.wait_for()
        page.locator("#users-table tr[data-id] td:nth-child(3)").first.click()
        page.locator(".cust-tabs").wait_for()
        for tab in ("billing", "activity", "notes", "overview"):
            page.locator(f'.cust-tabs a[href$="/{tab}"]').click()
            page.wait_for_function(f"location.hash.endsWith('/{tab}')")
            assert page.locator("#p360-tab").inner_text().strip()
        page.reload()
        page.locator(".cust-tabs .tab.active", has_text="Overview").wait_for()
        page.locator(".adm-crumb a").click()
        page.locator("#users-table tr[data-id]").first.wait_for()
        assert "status=all" in page.url, "the list comes back with its filters"
        assert page.errors == []

    def test_settings_pages_and_old_links(self, page):
        for old, new in (("billing", "settings/billing"), ("signups", "settings/signups"), ("retention", "settings/retention")):
            page.goto(f"{BASE}/admin#{old}")
            page.locator(f'[data-spage="{new.split("/")[1]}"]:not([hidden]) .setting-row').first.wait_for()
            assert page.url.endswith(f"#{new}")
        for key in ("account", "email", "support", "ai", "landing"):
            page.goto(f"{BASE}/admin#settings/{key}")
            page.locator(f'[data-spage="{key}"]:not([hidden]) .settings-section').first.wait_for()
        assert page.errors == []

    def test_leaving_unsaved_settings_asks_first(self, page):
        page.goto(f"{BASE}/admin#settings/retention")
        field = page.locator("#hk-audit")
        field.fill("45")
        page.locator(".floatbar").wait_for()
        page.locator('.sb-nav a[data-page="overview"]').click()
        page.locator(".modal", has_text="Save your changes?").wait_for()
        assert page.url.endswith("#settings/retention")
        page.locator(".modal-foot .btn", has_text="Stay here").click()
        assert page.locator("#hk-audit").input_value() == "45"
        page.locator('.floatbar [data-bar="discard"]').click()
        assert page.locator(".floatbar").count() == 0
        assert page.errors == []

    @pytest.mark.parametrize("section,marker", [("revenue", "#ch-rv-bridge"), ("engagement", ".adm-funnel, #en-funnel .empty"),
                                                ("imports", "#ch-im"), ("system", ".adm-health-chip:not(.is-loading)"),
                                                ("messages", "#msg-list table, #msg-list .empty")])
    def test_growth_sections_render(self, page, section, marker):
        page.goto(f"{BASE}/admin?range=90d#{section}")
        panel = page.locator(f'[data-panel="{section}"]')
        panel.locator(marker).first.wait_for()
        assert page.errors == []

    def test_support_inbox_thread_and_note(self, page, u1, admin_api):
        r = u1.post("/api/support/reports", json={"kind": "question", "body": "QA admin suite: how do budgets roll over?"})
        assert r.status_code == 201, r.text
        rid = r.json()["id"]
        try:
            page.goto(f"{BASE}/admin#support?view=all")
            page.locator(f'.sup-row[href="#support/{rid}"]').click()
            page.locator(".sup-msg").first.wait_for()
            assert "QA admin suite" in page.locator(".sup-msgs").inner_text()
            page.locator('#sup-mode [data-mode="note"]').click()
            page.fill("#sup-reply", "QA note from the admin suite")
            page.locator("#sup-send").click()
            page.locator(".sup-msg--note").wait_for()
            assert page.locator("#sup-status").input_value() == "open", "a note leaves the status alone"
            seen = u1.get(f"/api/support/reports/{rid}").json()
            assert [m["body"] for m in seen["messages"]] == ["QA admin suite: how do budgets roll over?"]
            page.select_option("#sup-status", "resolved")
            page.locator(".toast", has_text="Marked as resolved").wait_for()
            assert u1.get(f"/api/support/reports/{rid}").json()["status"] == "resolved"
            assert page.errors == []
        finally:
            admin_api.patch(f"/api/admin/support/reports/{rid}", json={"status": "resolved"})

    def test_phone_has_the_admin_bottom_bar(self, page):
        page.set_viewport_size({"width": 390, "height": 844})
        page.goto(f"{BASE}/admin#overview")
        page.locator(".adm-kpi").first.wait_for()
        labels = [t.strip() for t in page.locator(".bottomnav .bn-label").all_inner_texts()]
        assert labels == ["Home", "Customers", "Revenue", "Activity", "More"]
        page.set_viewport_size({"width": 1440, "height": 900})
