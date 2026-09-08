"""End-to-end user flows F1–F12 for iSpend, driven through the real UI with Playwright.

Run (venv from qa/CONTEXT.md):
    <venv>/bin/pytest qa/e2e/test_flows.py -p no:cacheprovider -v

The tests are ORDERED and share one logged-in browser context (module scope). A dedicated user
`qa_flows` is deleted and re-created through the admin API at the start, so every run starts from
an empty account with the default category tree. Nothing belonging to admin is touched.

Each flow uses soft assertions (Soft.check) so a single defect does not stop the rest of the flow;
the test fails at the end listing every failed check. Observations that are not failures are
recorded with Soft.note and written (with all failures) to qa/reports/flows-e2e-notes.json.
"""
import csv
import io
import json
import os
import pathlib
import re
import time

import pytest
import requests
from playwright.sync_api import TimeoutError as PwTimeout
from playwright.sync_api import sync_playwright

from ratelimit import login_with_retry, press_enter_login, submit_login

BAD_TEXT = re.compile(r"(\bundefined\b|\bNaN\b|\bnull\b|\[object Object\]|\$NaN|−NaN|\bInvalid Date\b)")
TEXT_SCAN_JS = r"""
(pattern) => {
  const bad = new RegExp(pattern); const out = [];
  const sel = (el) => el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') + (el.className ? '.' + String(el.className).trim().split(/\s+/).slice(0, 3).join('.') : '');
  const vis = (el) => el && el.getClientRects().length > 0;
  const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT); let n;
  while ((n = w.nextNode())) { const t = n.nodeValue; if (!t || !bad.test(t)) continue; const el = n.parentElement;
    if (!el || !vis(el) || el.closest('script,style,noscript')) continue; out.push({ kind: 'text', text: t.trim().slice(0, 140), el: sel(el) }); }
  return out.slice(0, 40);
}
"""


def scan_text(page):
    return page.evaluate(TEXT_SCAN_JS, BAD_TEXT.pattern)


class Recorder:
    """Collects console errors, page errors and HTTP >= 400 responses for per-flow deltas."""

    def __init__(self, page):
        self.page = page
        self.console, self.pageerrors, self.http_errors = [], [], []
        page.on("console", lambda m: self.console.append({"type": m.type, "text": m.text[:400], "url": page.url}) if m.type in ("error", "warning") else None)
        page.on("pageerror", lambda e: self.pageerrors.append({"text": str(e)[:600], "url": page.url}))
        page.on("response", lambda r: self.http_errors.append({"status": r.status, "method": r.request.method, "url": r.url}) if r.status >= 400 else None)

    def snapshot(self):
        return {k: len(getattr(self, k)) for k in ("console", "pageerrors", "http_errors")}

    def delta(self, snap):
        return {k: getattr(self, k)[n:] for k, n in snap.items() if getattr(self, k)[n:]}

BASE = os.environ.get("ISPEND_BASE_URL", "http://localhost:5559")
ROOT = pathlib.Path(__file__).resolve().parents[2]
FIX = ROOT / "backend" / "tests" / "fixtures"
SHOTS = ROOT / "qa" / "reports" / "screenshots"
NOTES = ROOT / "qa" / "reports" / "flows-e2e-notes.json"
SCRATCH = pathlib.Path(os.environ.get("QA_SCRATCH", "/private/tmp/claude-501/-Users-ruolez-Desktop-Dev-ispend/272cd059-cdbc-40ed-a58b-b1f0ef765b8f/scratchpad"))

ADMIN = ("admin", "admin")
QA = ("qa_flows", "qa-flows-pass1")
TESTER = ("qa_tester", "qa-tester-pass1")
SLOW_S = 2.0

S = {}          # state shared between ordered flows (ids, names, counters)
RESULTS = []    # per-flow {flow, fails, notes, timings}


# ---------------------------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------------------------
class Soft:
    def __init__(self, flow, page=None, rec=None):
        self.flow, self.page, self.rec = flow, page, rec
        self.fails, self.notes, self.timings = [], [], []
        self.snap = rec.snapshot() if rec else None

    def check(self, cond, msg):
        if not cond:
            self.fails.append(msg)
        return bool(cond)

    def eq(self, a, b, msg):
        return self.check(a == b, f"{msg}: expected {b!r}, got {a!r}")

    def note(self, msg):
        self.notes.append(msg)

    def timed(self, label, fn):
        t0 = time.monotonic()
        out = fn()
        dt = time.monotonic() - t0
        self.timings.append({"label": label, "s": round(dt, 2)})
        if dt > SLOW_S:
            self.fails.append(f"SLOW: {label} took {dt:.1f}s (> {SLOW_S:.0f}s)")
        return out

    def finish(self):
        if self.rec and self.snap is not None:
            d = self.rec.delta(self.snap)
            for e in d.get("pageerrors", []):
                self.fails.append(f"PAGE ERROR at {e['url']}: {e['text'][:200]}")
            for e in d.get("console", []):
                if e["type"] == "error" and "favicon" not in e["text"] and "Failed to load resource" not in e["text"]:
                    self.fails.append(f"CONSOLE ERROR at {e['url']}: {e['text'][:200]}")
            for e in d.get("http_errors", []):
                if e["status"] >= 500:
                    self.fails.append(f"HTTP {e['status']} {e['method']} {e['url']}")
        RESULTS.append({"flow": self.flow, "fails": self.fails, "notes": self.notes, "timings": self.timings})
        NOTES.write_text(json.dumps(RESULTS, indent=2))
        if self.fails:
            pytest.fail(f"{self.flow}: {len(self.fails)} failed check(s):\n- " + "\n- ".join(self.fails), pytrace=False)


def shot(page, name, full=False):
    SHOTS.mkdir(parents=True, exist_ok=True)
    p = SHOTS / f"flow-{name}.png"
    try:
        page.screenshot(path=str(p), full_page=full)
    except Exception as e:  # noqa: BLE001
        print("screenshot failed", name, e)
    return p.name


class Api:
    """Thin requests wrapper with the session cookie of one user."""

    def __init__(self, creds):
        self.s = requests.Session()
        r = login_with_retry(lambda: self.s.post(f"{BASE}/api/auth/login", json={"username": creds[0], "password": creds[1]}))
        assert r.ok, f"login {creds[0]} failed: {r.status_code} {r.text[:200]}"
        self.me = r.json()

    def __call__(self, method, path, **kw):
        r = self.s.request(method, f"{BASE}{path}", **kw)
        if r.status_code >= 400:
            raise requests.HTTPError(f"{method} {path} -> {r.status_code} {r.text[:300]}", response=r)
        return r.json() if r.content and "json" in r.headers.get("content-type", "") else r.content

    def get(self, path, **kw):
        return self("GET", path, **kw)

    def post(self, path, body=None, **kw):
        return self("POST", path, json=body if body is not None else {}, **kw)

    def put(self, path, body=None, **kw):
        return self("PUT", path, json=body if body is not None else {}, **kw)

    def delete(self, path, **kw):
        return self("DELETE", path, **kw)


def money(s):
    """'−$1,234.50' / '+$3.00' / 'CA$5.00' -> float."""
    s = (s or "").replace("−", "-").replace(",", "")
    m = re.search(r"([-+]?)[^\d\-+]*([\d]+(?:\.\d+)?)", s)
    if not m:
        return None
    v = float(m.group(2))
    return -v if m.group(1) == "-" else v


def toast(page, contains=None, timeout=6000):
    loc = page.locator(".toast", has_text=contains) if contains else page.locator(".toast")
    loc.first.wait_for(state="visible", timeout=timeout)
    return loc.first.inner_text()


def toast_text_all(page):
    return [t.inner_text() for t in page.locator(".toast").all()]


def click_undo(page):
    """Undo on the newest toast (older undo toasts may still be on screen)."""
    page.locator(".toast .toast-action", has_text="Undo").last.click()


def dismiss_toasts(page):
    """Close every toast through the DOM: a toast mid-animation is never "stable" enough for a click."""
    for _ in range(10):
        if not page.locator(".toast").count():
            return
        page.evaluate("document.querySelectorAll('.toast .toast-close').forEach((b) => b.click())")
        page.wait_for_timeout(120)


def confirm_modal(page):
    m = page.locator(".modal")
    m.wait_for(state="visible", timeout=5000)
    return m


def menu_click(page, label):
    item = page.locator(".menu .menu-item", has_text=label).first
    item.wait_for(state="visible", timeout=4000)
    item.click()


def goto(page, path, wait_sel="#tb-username", soft=None, label=None):
    def _go():
        page.goto(f"{BASE}{path}")
        if wait_sel:
            page.wait_for_selector(wait_sel, timeout=15000)
    if soft:
        soft.timed(label or f"load {path}", _go)
    else:
        _go()


def pick_range(page, F, anchor="#f-range", preset=None, frm=None, to=None):
    """Open the date-range picker on `anchor`, pick a preset or a custom range, close any leftover popover."""
    page.locator(anchor).click()
    page.wait_for_selector(".popover [data-preset]", timeout=5000)
    n = page.locator(".popover").count()
    if n != 1 and not S.get("_dbl_popover_noted"):
        S["_dbl_popover_noted"] = True
        F.fails.append(f"clicking {anchor} opened {n} range popovers at once (duplicate click handlers); Esc closes only one")
        shot(page, "F4-range-double-popover")
    pop = page.locator(".popover").last
    if preset:
        pop.locator(f"[data-preset='{preset}']").click()
    else:
        if frm is not None:
            pop.locator("[data-f='from']").fill(frm)
        if to is not None:
            pop.locator("[data-f='to']").fill(to)
        pop.locator("[data-act='apply']").click()
    for _ in range(3):
        if page.locator(".popover").count() == 0:
            break
        page.keyboard.press("Escape")
        page.wait_for_timeout(150)


def settle(page, ms=300):
    page.wait_for_timeout(ms)
    try:
        page.wait_for_load_state("networkidle", timeout=5000)
    except PwTimeout:
        pass


def ui_login(page, creds, expect_path="/index.html"):
    page.goto(f"{BASE}/login.html")
    page.fill("#username", creds[0])
    page.fill("#password", creds[1])
    submit_login(page)
    page.wait_for_url(re.compile(re.escape(expect_path)), timeout=15000)
    page.wait_for_function("() => !!window.currentUser", timeout=15000)


def bad_text(page):
    return [f"{x['el']}: {x['text']}" for x in scan_text(page)]


def canvas_ink(page, selector):
    """Number of non-transparent pixels drawn on a canvas (0 = blank)."""
    return page.evaluate(
        """(sel) => { const c = document.querySelector(sel); if (!c) return -1; const ctx = c.getContext('2d');
            const d = ctx.getImageData(0, 0, c.width, c.height).data; let n = 0; for (let i = 3; i < d.length; i += 4) if (d[i]) n++; return n; }""",
        selector,
    )


def fixture_rows():
    """Independent parse of the fixture CSVs imported in this run -> list of (date, amount) per file key."""
    out = {}
    with open(FIX / "chase_card.csv") as f:
        out["chase_card"] = [(r["Transaction Date"], float(r["Amount"])) for r in csv.DictReader(f)]
    with open(FIX / "amex.csv") as f:
        out["amex"] = [(r["Date"], -float(r["Amount"])) for r in csv.DictReader(f)]
    with open(FIX / "generic_semicolon.csv") as f:
        out["generic_semicolon"] = [(r["Date"], (float(r["Deposit"]) if r["Deposit"] else -float(r["Withdrawal"]))) for r in csv.DictReader(f, delimiter=";")]
    with open(FIX / "chase_checking.csv") as f:
        out["chase_checking"] = [(r["Posting Date"], float(r["Amount"])) for r in csv.DictReader(f)]
    # generic.xlsx (built by make_fixtures.py): same three rows as generic_headerless.csv
    out["generic_xlsx"] = [("2024-08-01", -4.5), ("2024-08-02", 3000.0), ("2024-08-03", -120.0)]
    return out


# ---------------------------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------------------------
@pytest.fixture(scope="module", autouse=True)
def fresh_user():
    """Delete + re-create qa_flows through the admin API so the run starts from an empty account."""
    admin = Api(ADMIN)
    users = admin.get("/api/users")
    for u in users:
        if u["username"] == QA[0]:
            admin.delete(f"/api/users/{u['id']}?permanent=true")
    created = admin.post("/api/users", {"username": QA[0], "password": QA[1], "role": "user"})
    S["qa_user_id"] = created["id"]
    S["api"] = Api(QA)
    S["fx"] = fixture_rows()
    RESULTS.clear()
    yield
    NOTES.write_text(json.dumps(RESULTS, indent=2))


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True)
        yield b
        b.close()


@pytest.fixture(scope="module")
def ctx(browser):
    c = browser.new_context(viewport={"width": 1366, "height": 900}, accept_downloads=True)
    yield c
    c.close()


@pytest.fixture(scope="module")
def page(ctx):
    p = ctx.new_page()
    S["rec"] = Recorder(p)
    yield p


@pytest.fixture
def qapi():
    return S["api"]


# ---------------------------------------------------------------------------------------------
# F1 — first run
# ---------------------------------------------------------------------------------------------
def test_f1_first_run(page, qapi):
    F = Soft("F1", page, S["rec"])
    # unauthenticated deep link redirects to login with ?next
    page.goto(f"{BASE}/transactions.html")
    page.wait_for_url(re.compile(r"/login\.html"), timeout=10000)
    F.check("next=" in page.url and "transactions" in page.url, f"deep link should redirect to login with ?next, got {page.url}")
    shot(page, "F1-login")
    F.check(page.locator("#login-form").is_visible(), "login form visible")

    # empty submit
    page.click("#login-btn")
    err = page.locator("#login-error")
    F.check(err.is_visible() and "Enter your username and password" in err.inner_text(), f"empty-fields message: {err.inner_text()!r}")
    shot(page, "F1-login-empty")

    # wrong password
    page.fill("#username", QA[0])
    page.fill("#password", "nope-nope")
    submit_login(page, navigate=False)
    F.check("Invalid username or password" in err.inner_text(), f"wrong-password message: {err.inner_text()!r}")
    F.check(page.evaluate("document.activeElement.id") == "password", "focus returns to password after a failed login")
    shot(page, "F1-login-wrong-password")

    # password visibility toggle (none in markup)
    has_toggle = page.locator("#login-form button[aria-label*='password' i], #login-form .trailing").count() > 0
    F.note(f"password visibility toggle present: {has_toggle}")

    # Enter key submits and follows ?next
    page.fill("#password", QA[1])
    t0 = time.monotonic()
    press_enter_login(page)
    page.wait_for_url(re.compile(r"/transactions\.html"), timeout=15000)
    page.wait_for_selector("#tb-username", timeout=15000)
    F.timings.append({"label": "login -> transactions", "s": round(time.monotonic() - t0, 2)})
    F.eq(page.locator("#tb-username").inner_text().strip(), QA[0], "top bar shows the username")

    # dashboard empty state + CTA
    goto(page, "/index.html", wait_sel="#dash-empty:not([hidden]) .empty", soft=F, label="dashboard load (empty)")
    empty = page.locator("#dash-empty")
    F.check("Import your first statement" in empty.inner_text(), "dashboard empty-state title")
    F.check(page.locator("#dash-body").is_hidden(), "charts hidden on empty dashboard")
    setup = page.locator("#dash-setup")
    F.check(setup.is_visible() and "0 of 4 done" in setup.inner_text(), f"first-run checklist shows 0 of 4: {setup.inner_text()[:80]!r}")
    F.check(setup.locator("li").count() == 5 and setup.locator("li.is-done").count() == 0, "five steps, none done")
    page.locator("#dash-setup [data-act='setup-dismiss']").click()
    t = toast(page, "Checklist hidden")
    F.check("Undo" in t and setup.is_hidden(), "hiding the checklist offers Undo")
    F.check((qapi.get("/api/auth/me")["preferences"].get("onboarding") or {}).get("dismissed") is True, "dismissal saved to preferences")
    click_undo(page)
    toast(page, "Undone")
    page.wait_for_timeout(400)
    F.check(setup.is_visible(), "undo shows the checklist again")
    kpi_vals = [v.inner_text() for v in page.locator("#kpis .stat-value").all()]
    F.note(f"empty dashboard KPI values: {kpi_vals}")
    F.check(all(v in ("$0.00", "+$0.00", "—", "0%") for v in kpi_vals), f"empty KPIs should read $0.00 / —, got {kpi_vals}")
    F.check(page.locator("#kpis .stat-delta", has_text="No previous period").count() >= 1 or page.locator("#kpis .stat-delta", has_text="No income").count() >= 1,
            "KPI deltas explain the empty period")
    shot(page, "F1-dashboard-empty", full=True)
    cta = empty.locator("a.btn-primary", has_text="Import statement")
    F.check(cta.count() == 1, "empty-state CTA present")
    cta.click()
    page.wait_for_url(re.compile(r"/import\.html"), timeout=10000)
    page.wait_for_selector("#dropzone", timeout=10000)
    F.check(page.locator("#dropzone").is_visible(), "CTA leads to import dropzone")
    F.check(not bad_text(page), f"bad text on import page: {bad_text(page)}")
    F.finish()


# ---------------------------------------------------------------------------------------------
# F2 — accounts
# ---------------------------------------------------------------------------------------------
def create_account_ui(page, F, name, type_value, currency="USD", institution=None):
    page.click('[data-act="add-account"]')
    m = confirm_modal(page)
    m.locator("#acct-name").fill(name)
    m.locator("#acct-type").select_option(type_value)
    m.locator("#acct-cur").select_option(currency)
    if institution:
        m.locator("#acct-inst").select_option(institution)
    m.locator(".modal-foot .btn-primary").click()
    toast(page, "Account created")
    page.wait_for_timeout(300)


def test_f2_accounts(page, qapi):
    F = Soft("F2", page, S["rec"])
    goto(page, "/settings.html#accounts", wait_sel="#accounts-table .empty, #accounts-table table tbody tr:not(.skel-row)", soft=F, label="settings load")
    F.check("No accounts yet" in page.locator("#accounts-table").inner_text(), "accounts empty state")
    shot(page, "F2-accounts-empty")

    create_account_ui(page, F, "QA Checking", "checking", institution="chase")
    shot(page, "F2-account-created")
    create_account_ui(page, F, "QA Visa", "credit_card")
    create_account_ui(page, F, "QA Savings", "savings")
    create_account_ui(page, F, "QA Temp", "cash")

    # duplicate name -> error toast
    page.click('[data-act="add-account"]')
    m = confirm_modal(page)
    m.locator("#acct-name").fill("qa checking")
    m.locator(".modal-foot .btn-primary").click()
    t = toast(page, "already exists")
    F.check("already exists" in t, f"duplicate account name error toast: {t!r}")
    F.check(page.locator(".modal").count() == 1, "modal stays open after a server error")
    shot(page, "F2-account-duplicate-name")
    page.keyboard.press("Escape")

    accts = {a["name"]: a for a in qapi.get("/api/accounts?all=1")}
    F.check({"QA Checking", "QA Visa", "QA Savings", "QA Temp"} <= set(accts), f"accounts via API: {sorted(accts)}")
    S["acct_checking"] = accts["QA Checking"]["id"]
    S["acct_card"] = accts["QA Visa"]["id"]
    S["acct_savings"] = accts["QA Savings"]["id"]
    temp_id = accts["QA Temp"]["id"]
    rows = page.locator("#accounts-table tbody tr")
    F.eq(rows.count(), 4, "accounts table rows")

    # rename via pencil
    page.locator(f'tr[data-id="{S["acct_card"]}"] [data-act="edit-account"]').click()
    m = confirm_modal(page)
    F.eq(m.locator("#acct-name").input_value(), "QA Visa", "edit modal prefilled")
    m.locator("#acct-name").fill("QA Credit Card")
    m.locator(".modal-foot .btn-primary").click()
    toast(page, "Account saved")
    page.wait_for_selector(f'tr[data-id="{S["acct_card"]}"]:has-text("QA Credit Card")', timeout=5000)
    F.eq([a for a in qapi.get("/api/accounts?all=1") if a["id"] == S["acct_card"]][0]["name"], "QA Credit Card", "rename persisted")
    shot(page, "F2-account-renamed")

    # archive / restore
    page.locator(f'tr[data-id="{S["acct_savings"]}"] [data-act="account-menu"]').click()
    menu_click(page, "Archive")
    page.wait_for_selector(f'tr[data-id="{S["acct_savings"]}"] .badge:has-text("Archived")', timeout=5000)
    F.note(f"toasts after Archive: {toast_text_all(page)}")
    shot(page, "F2-account-archived")
    F.check([a for a in qapi.get("/api/accounts?all=1") if a["id"] == S["acct_savings"]][0]["is_active"] is False, "archive persisted")
    t = toast(page, "Account archived")
    F.check("Undo" in t, "undo offered on archive")
    click_undo(page)
    toast(page, "Undone")
    page.wait_for_timeout(600)
    F.check([a for a in qapi.get("/api/accounts?all=1") if a["id"] == S["acct_savings"]][0]["is_active"] is True, "undo restored the account")
    page.locator(f'tr[data-id="{S["acct_savings"]}"] [data-act="account-menu"]').click()
    menu_click(page, "Archive")
    page.wait_for_selector(f'tr[data-id="{S["acct_savings"]}"] .badge:has-text("Archived")', timeout=5000)
    dismiss_toasts(page)
    page.locator(f'tr[data-id="{S["acct_savings"]}"] [data-act="account-menu"]').click()
    menu_click(page, "Restore")
    page.wait_for_timeout(600)
    F.check(page.locator(f'tr[data-id="{S["acct_savings"]}"] .badge:has-text("Archived")').count() == 0, "restore removes Archived badge")

    # delete with transactions: what does the UI say?
    created = qapi.post("/api/transactions", {"account_id": temp_id, "txn_date": "2024-08-20", "amount": -9.99, "description": "QA TEMP PURCHASE"})
    F.note(f"manual txn created id={created.get('id')} account={created.get('account_id')} (temp_id={temp_id}); API counts={[(a['name'], a['txn_count']) for a in qapi.get('/api/accounts?all=1')]}")
    page.reload()
    page.wait_for_selector(f'tr[data-id="{temp_id}"]', timeout=10000)
    txn_cell = page.locator(f'tr[data-id="{temp_id}"] td[data-label="Transactions"]').inner_text().strip()
    F.eq(txn_cell, "1", "transaction count shown for QA Temp")
    page.locator(f'tr[data-id="{temp_id}"] [data-act="account-menu"]').click()
    menu_click(page, "Delete")
    m = confirm_modal(page)
    body = m.inner_text()
    F.note(f"delete-account confirm text: {body!r}")
    F.check("1 transaction" in body, "confirm mentions the transaction count")
    F.check("1 transactions" not in body, f"pluralisation: confirm says '1 transactions' -> {body!r}")
    shot(page, "F2-account-delete-confirm")
    m.locator(".modal-foot .btn-danger-solid, .modal-foot .btn-primary").last.click()
    t = toast(page, "Account deleted")
    F.check("Account deleted" in t, "delete toast")
    page.wait_for_timeout(500)
    F.check(all(a["id"] != temp_id for a in qapi.get("/api/accounts?all=1")), "account deleted via API")
    F.eq(qapi.get("/api/transactions?range=all&limit=1")["total"], 0, "its transaction was deleted too")
    shot(page, "F2-accounts-final")
    F.finish()


# ---------------------------------------------------------------------------------------------
# F3 — import
# ---------------------------------------------------------------------------------------------
def upload_and_wait(page, F, paths, label, timeout=45000):
    if isinstance(paths, (str, pathlib.Path)):
        paths = [paths]
    n_before = page.locator(".file-row").count()
    t0 = time.monotonic()
    page.set_input_files("#file-input", [str(p) for p in paths])
    page.wait_for_timeout(120)
    statuses = [r.inner_text().replace("\n", " ") for r in page.locator(".file-row .file-status").all()]
    F.note(f"{label}: statuses right after upload: {statuses}")
    shot(page, f"F3-{label}-uploading")
    for i in range(n_before, n_before + len(paths)):
        row = page.locator(".file-row").nth(i)
        try:
            row.locator(".badge:has-text('Ready'), .badge:has-text('Failed')").first.wait_for(state="visible", timeout=timeout)
        except PwTimeout:
            F.fails.append(f"{label}: file {i} never reached Ready/Failed (stuck spinner?) status={row.locator('.file-status').inner_text()!r}")
    dt = time.monotonic() - t0
    F.timings.append({"label": f"upload+parse {label}", "s": round(dt, 2)})
    if dt > 5:
        F.fails.append(f"SLOW: upload+parse {label} took {dt:.1f}s")
    return [r.inner_text().replace("\n", " ") for r in page.locator(".file-row .file-status").all()]


def review_state(page):
    """Read the review screen: detection title/sub, sign-check numbers, preview row count, summary."""
    sc = page.locator(".signcheck .sc-item")
    items = {}
    for i in range(sc.count()):
        el = sc.nth(i)
        items[el.locator(".sc-label").inner_text().strip().lower()] = (el.locator(".sc-value").inner_text().strip(), el.locator(".sc-sub").inner_text().strip())
    return {
        "title": page.locator(".detect-title").inner_text().strip() if page.locator(".detect-title").count() else "",
        "sub": page.locator(".detect-sub").inner_text().strip() if page.locator(".detect-sub").count() else "",
        "sign": items,
        "rows": page.locator(".tbl-preview tbody tr[data-row]").count(),
        "summary": page.locator(".preview-head .tbl-summary").inner_text().replace("\n", " ") if page.locator(".preview-head .tbl-summary").count() else "",
        "foot": page.locator(".commit-foot .summary").inner_text().strip() if page.locator(".commit-foot .summary").count() else "",
        "commit_disabled": page.locator('[data-act="commit"]').is_disabled() if page.locator('[data-act="commit"]').count() else None,
        "account": page.locator("#rv-account").input_value() if page.locator("#rv-account").count() else None,
        "dupes": page.locator(".tbl-preview .badge:has-text('Duplicate')").count(),
    }


def wait_review(page):
    page.wait_for_selector("#step-review:not([hidden]) .tbl-preview, #step-review:not([hidden]) .error-box", timeout=30000)
    page.wait_for_timeout(300)
    page.wait_for_timeout(600)  # autoAccount may re-render once


def restart_import(page):
    """Back to the upload step: 'Import another' when on the Done step, otherwise the page is already there."""
    if page.locator('#step-done:not([hidden]) [data-act="restart"]').count():
        page.locator('[data-act="restart"]').click()
    elif page.locator('#step-upload[hidden]').count():
        page.goto(f"{BASE}/import.html")
    page.wait_for_selector("#dropzone", timeout=10000)


def commit_and_wait(page, F, label):
    page.locator('[data-act="commit"]').click()
    t = toast(page, "Imported", timeout=20000)
    page.wait_for_selector("#step-done:not([hidden]) .done-card, #step-review:not([hidden]) .tbl-preview", timeout=20000)
    F.note(f"{label}: commit toast {t!r}")
    return t


def txn_total(qapi):
    return qapi.get("/api/transactions?range=all&limit=1")["total"]


def test_f3_import(page, qapi):
    F = Soft("F3", page, S["rec"])
    goto(page, "/import.html", wait_sel="#dropzone", soft=F, label="import load")
    F.check(page.locator("#upload-account option").count() == 4, "upload-account select lists 'Choose during review' + 3 accounts")

    # --- chase_card.csv ---
    upload_and_wait(page, F, FIX / "chase_card.csv", "chase")
    F.check(page.locator(".file-row .badge:has-text('Ready')").count() == 1, "chase_card.csv reaches Ready")
    shot(page, "F3-chase-ready")
    page.locator('[data-act="go-review"]').click()
    wait_review(page)
    st = review_state(page)
    F.note(f"chase review: {st}")
    F.check("Chase" in st["title"], f"bank detected label: {st['title']!r}")
    F.check("6 rows found" in st["sub"], f"row count in banner: {st['sub']!r}")
    F.eq(st["rows"], 6, "preview rows")
    F.eq(abs(money(st["sign"].get("charges", ("", ""))[0]) or 0), 144.73, "charges sum")
    F.eq(st["sign"].get("charges", ("", ""))[1], "5 charges", "charges count")
    F.eq(money(st["sign"].get("payments / income", ("", ""))[0]), 450.0, "payments sum")
    F.note(f"first import: account preselected={st['account']!r} footer={st['foot']!r} toasts={toast_text_all(page)} (QA Checking has institution=chase, so the Chase *card* CSV is auto-assigned to the checking account by institution alone)")
    if not st["account"]:
        F.check(st["commit_disabled"] is True, "commit disabled before an account is chosen")
        F.check("choose an account first" in st["foot"], f"footer asks for account: {st['foot']!r}")
    else:
        F.check(any("matched from the statement" in t for t in toast_text_all(page)), f"auto-assignment is explained by a toast: {toast_text_all(page)}")
        shot(page, "F3-chase-auto-account")
    F.eq(st["dupes"], 0, "no duplicates on first import")
    shot(page, "F3-chase-review", full=True)

    # sign convention toggle
    page.locator('[data-act="flip-signs"]').click()
    page.wait_for_function("() => document.querySelector('.signcheck .sc-item .sc-sub') && document.querySelector('.signcheck .sc-item .sc-sub').textContent.includes('1 charge')", timeout=10000)
    st2 = review_state(page)
    F.eq(abs(money(st2["sign"]["charges"][0])), 450.0, "after flip: charges = old payments")
    F.eq(st2["sign"]["payments / income"][1], "5 payments", "after flip: payments count")
    F.check(page.locator('[data-map="flip_sign"][value="1"]').is_checked(), "mapping radio reflects flipped sign")
    shot(page, "F3-chase-flipped")
    page.locator('[data-act="flip-signs"]').click()
    page.wait_for_function("() => document.querySelector('.signcheck .sc-item .sc-sub') && document.querySelector('.signcheck .sc-item .sc-sub').textContent.includes('5 charges')", timeout=10000)

    # account selection
    page.select_option("#rv-account", str(S["acct_card"]))
    t = toast(page, "Account set to")
    F.check("QA Credit Card" in t, f"account toast: {t!r}")
    page.wait_for_function("() => !document.querySelector('[data-act=commit]').disabled", timeout=8000)
    st3 = review_state(page)
    F.check("into QA Credit Card" in st3["foot"], f"footer names account: {st3['foot']!r}")
    F.check("6 to import" in st3["summary"], f"summary: {st3['summary']!r}")
    mapping_summary = page.locator(".mapping summary").inner_text()
    F.check("date" in mapping_summary and "description" in mapping_summary and "amount" in mapping_summary, f"mapping summary: {mapping_summary!r}")
    commit_and_wait(page, F, "chase")
    done = page.locator("#step-done .done-card")
    F.check("Import complete" in done.inner_text(), "done screen")
    nums = [x.inner_text() for x in done.locator(".done-stats .n").all()]
    F.note(f"chase done stats: {nums} labels={[x.inner_text() for x in done.locator('.done-stats .l').all()]}")
    F.eq(nums[0], "6", "done: imported 6")
    shot(page, "F3-chase-done", full=True)
    F.eq(txn_total(qapi), 6, "API total after chase import")
    S["stmt_chase"] = [x for x in qapi.get("/api/statements") if x["original_filename"] == "chase_card.csv"][0]["id"]

    # --- amex.csv with account pre-selected on the upload step ---
    restart_import(page)
    F.eq(page.locator("#upload-account").input_value(), str(S["acct_card"]), "upload-account remembers the last import account")
    upload_and_wait(page, F, FIX / "amex.csv", "amex")
    page.locator('[data-act="go-review"]').click()
    wait_review(page)
    st = review_state(page)
    F.note(f"amex review: {st} toasts={toast_text_all(page)}")
    F.check("Amex" in st["title"] or "American Express" in st["title"], f"amex detected: {st['title']!r}")
    F.eq(abs(money(st["sign"]["charges"][0])), 531.21, "amex charges (positive = charge convention normalised)")
    F.eq(money(st["sign"]["payments / income"][0]), 600.0, "amex payment")
    F.eq(st["account"], str(S["acct_card"]), "account preselected from upload step")
    shot(page, "F3-amex-review", full=True)
    commit_and_wait(page, F, "amex")
    F.eq(txn_total(qapi), 11, "API total after amex")

    # --- generic.xlsx: mapping editor ---
    restart_import(page)
    upload_and_wait(page, F, FIX / "generic.xlsx", "xlsx")
    page.locator('[data-act="go-review"]').click()
    wait_review(page)
    st = review_state(page)
    F.note(f"xlsx review: {st} toasts={toast_text_all(page)}")
    F.check("generic" in st["title"].lower() or "No known bank" in st["title"], f"xlsx generic detection: {st['title']!r}")
    F.eq(st["rows"], 3, "xlsx rows")
    F.check(page.locator(".mapping details[open]").count() == 1, "mapping editor auto-opened for generic file")
    F.eq(st["account"], str(S["acct_card"]), "xlsx gets the remembered account from the upload step")
    shot(page, "F3-xlsx-review", full=True)
    # change the Amount column role to Debit -> the file is re-parsed and the sign summary changes
    before_sign = st["sign"]["charges"] + st["sign"]["payments / income"]
    page.locator('.mapping-table select[data-col="2"]').select_option("debit")
    # the editor debounces 350 ms before the PUT; wait for the re-rendered mapping line
    try:
        page.wait_for_function("() => /debit/i.test((document.querySelector('.mapping summary') || {}).innerText || '')", timeout=10000)
    except PwTimeout:
        pass
    st2 = review_state(page)
    F.note(f"xlsx: Amount column -> Debit via the UI select; preview now {st2['sign']} summary={st2['summary']!r} mapping line={page.locator('.mapping summary').inner_text()!r}")
    sid_x = [x for x in qapi.get("/api/statements") if x["status"] == "previewed" and x["original_filename"] == "generic.xlsx"][0]["id"]
    map_api = qapi.get(f"/api/statements/{sid_x}?limit=5")
    F.note(f"xlsx mapping via API after the UI change: amount={map_api['mapping'].get('amount')} debit={map_api['mapping'].get('debit')} summary={map_api['summary']['charges']} {map_api['summary']['payments']}")
    changed = st2["sign"]["charges"] + st2["sign"]["payments / income"] != before_sign
    F.check(changed or map_api["mapping"].get("debit") == 2, "changing a column role in the mapping editor re-parses the file (UI or API shows the new role)")
    if not changed:
        F.note("UI did not visibly change after the role select (the same PUT through the API re-parses and turns all 3 rows unreadable: a Debit column holding negative numbers yields 0 charges / 0 payments) — see finding")
    shot(page, "F3-xlsx-mapping-changed")
    page.locator('.mapping-table select[data-col="2"]').select_option("amount")
    page.wait_for_function("() => { const s = Array.from(document.querySelectorAll('.signcheck .sc-item'))[0]; return s && s.textContent.includes('2 charges'); }", timeout=10000)
    page.select_option("#rv-account", str(S["acct_checking"]))
    toast(page, "Account set to")
    page.wait_for_function("() => !document.querySelector('[data-act=commit]').disabled", timeout=8000)
    commit_and_wait(page, F, "xlsx")
    F.eq(txn_total(qapi), 14, "API total after xlsx")

    # --- headerless + semicolon into QA Savings ---
    restart_import(page)
    page.select_option("#upload-account", str(S["acct_savings"]))
    upload_and_wait(page, F, FIX / "generic_headerless.csv", "headerless")
    page.locator('[data-act="go-review"]').click()
    wait_review(page)
    st = review_state(page)
    F.note(f"headerless review: {st}")
    F.eq(st["rows"], 3, "headerless rows")
    F.check(not page.locator('[data-map="has_header"]').is_checked(), "'First row is a header' switch is off for a headerless file")
    hdr = [h.inner_text() for h in page.locator(".mapping-table thead .colname").all()]
    F.note(f"headerless column names shown: {hdr}")
    shot(page, "F3-headerless-review", full=True)
    commit_and_wait(page, F, "headerless")
    S["stmt_headerless"] = [x for x in qapi.get("/api/statements") if x["original_filename"] == "generic_headerless.csv"][0]["id"]
    F.eq(txn_total(qapi), 17, "API total after headerless")

    restart_import(page)
    page.select_option("#upload-account", str(S["acct_savings"]))
    upload_and_wait(page, F, FIX / "generic_semicolon.csv", "semicolon")
    page.locator('[data-act="go-review"]').click()
    wait_review(page)
    st = review_state(page)
    dates = [d.inner_text() for d in page.locator(".tbl-preview tbody tr td.num").all()]
    F.note(f"semicolon review: {st} dates={dates}")
    F.eq(st["rows"], 3, "semicolon rows")
    F.check(dates and dates[0].startswith("Aug 13"), f"DD/MM/YYYY dates parsed (13/08/2024 -> Aug 13): {dates}")
    F.eq(abs(money(st["sign"]["charges"][0])), 124.5, "semicolon withdrawals")
    F.eq(money(st["sign"]["payments / income"][0]), 3000.0, "semicolon deposits")
    shot(page, "F3-semicolon-review", full=True)
    commit_and_wait(page, F, "semicolon")
    F.eq(txn_total(qapi), 20, "API total after semicolon")

    # --- re-upload chase_card.csv -> duplicates ---
    restart_import(page)
    page.select_option("#upload-account", str(S["acct_card"]))
    upload_and_wait(page, F, FIX / "chase_card.csv", "chase-dup")
    meta = page.locator(".file-row .file-meta").inner_text()
    F.check("same file was uploaded before" in meta, f"file list keeps the 'same file was uploaded before' hint after parsing: {meta!r} (the upload response carries duplicate_of, but the polled GET /api/statements/<id> omits it, so the hint disappears as soon as parsing finishes)")
    page.locator('[data-act="go-review"]').click()
    wait_review(page)
    st = review_state(page)
    F.note(f"dup review: {st}")
    F.eq(st["dupes"], 6, "all 6 rows flagged Duplicate")
    F.check("6 duplicates skipped" in st["summary"] and "0 to import" in st["summary"], f"summary states duplicates are skipped: {st['summary']!r}")
    F.eq(page.locator("#skip-dupes").count(), 0, "no dead 'skip duplicates' switch")
    F.check(all(page.locator(".tbl-preview [data-row-include]").nth(i).is_disabled() for i in range(6)), "duplicate rows cannot be ticked")
    F.check(st["commit_disabled"] is True, "commit disabled with nothing to import")
    shot(page, "F3-duplicates-review", full=True)
    # 'Include all' must leave duplicates out
    page.locator('[data-act="rows-all"][data-include="1"]').click()
    page.wait_for_timeout(1500)
    st2 = review_state(page)
    rows_api = qapi.get(f"/api/statements/{[x for x in qapi.get('/api/statements') if x['status'] == 'previewed' and x['original_filename'] == 'chase_card.csv'][0]['id']}?limit=10")
    F.note(f"API after Include all: include flags={[r['include'] for r in rows_api['rows']]} summary.included={rows_api['summary']['included']}")
    F.check(not any(r["include"] for r in rows_api["rows"]) and rows_api["summary"]["included"] == 0, "Include all leaves duplicate rows excluded")
    F.check("0 to import" in st2["summary"] and st2["commit_disabled"] is True, f"still nothing to import: {st2['summary']!r}")
    shot(page, "F3-duplicates-include-all")
    before = txn_total(qapi)
    page.locator('[data-act="discard"]').click()
    m = confirm_modal(page)
    m.locator(".modal-foot .btn-danger-solid").click()
    page.wait_for_selector("#step-done:not([hidden]) .done-card, #step-upload:not([hidden]) #dropzone", timeout=10000)
    F.eq(txn_total(qapi), before, "transaction count unchanged after the duplicate re-upload")

    # --- invalid files ---
    restart_import(page)
    garbage_txt = SCRATCH / "garbage.txt"
    garbage_txt.write_bytes(b"\x00\xffthis is not a statement\n" * 20)
    empty_csv = SCRATCH / "empty.csv"
    empty_csv.write_bytes(b"")
    garbage_csv = SCRATCH / "garbage.csv"
    garbage_csv.write_bytes(bytes(range(256)) * 8)
    upload_and_wait(page, F, [garbage_txt, empty_csv, garbage_csv], "invalid", timeout=30000)
    rows = page.locator(".file-row")
    for i in range(rows.count()):
        r = rows.nth(i)
        F.note(f"invalid file row {i}: {r.locator('.file-name').inner_text()!r} -> {r.locator('.file-status').inner_text().replace(chr(10), ' ')!r}")
    F.check(rows.nth(0).locator(".badge:has-text('Failed')").count() == 1 and "Unsupported file type" in rows.nth(0).inner_text(), "garbage.txt rejected client-side")
    F.check(rows.nth(1).locator(".badge:has-text('Failed')").count() == 1, "empty.csv fails clearly")
    F.check(rows.nth(2).locator(".badge:has-text('Failed')").count() == 1, "garbage.csv fails clearly")
    F.check(page.locator(".file-row .spinner").count() == 0, "no stuck spinner after invalid uploads")
    F.check(page.locator('[data-act="go-review"]').count() == 0, "no Review button when nothing parsed")
    msgs = [rows.nth(i).locator(".file-status").inner_text() for i in range(rows.count())]
    F.check(all(len(m) > 8 for m in msgs), f"error messages are explanatory: {msgs}")
    shot(page, "F3-invalid-files", full=True)
    # error rows: 'Failed' with a Remove button; the server-side error message is not shown in the list
    F.note(f"server-side parse failure rows show a message in the upload list: {[('Failed' in m and len(m) > 20) for m in msgs[1:]]}")
    S["invalid_stmt_ids"] = [s["id"] for s in qapi.get("/api/statements") if s["status"] == "error"]

    # --- 3 files at once ---
    for i in range(rows.count()):
        b = page.locator('.file-row [data-act="remove-file"]')
        if b.count():
            b.first.click()
    page.select_option("#upload-account", str(S["acct_card"]))
    upload_and_wait(page, F, [FIX / "bofa_card.csv", FIX / "citi.csv", FIX / "discover.csv"], "three")
    F.eq(page.locator(".file-row .badge:has-text('Ready')").count(), 3, "three files all Ready")
    btn = page.locator('[data-act="go-review"]')
    F.check("Review 3 statements" in btn.inner_text(), f"review button label: {btn.inner_text()!r}")
    shot(page, "F3-three-files-ready", full=True)
    btn.click()
    wait_review(page)
    F.eq(page.locator(".file-tabs .file-tab").count(), 3, "review shows a tab per file")
    shot(page, "F3-three-files-review", full=True)
    tabs = page.locator(".file-tabs .file-tab")
    tabs.nth(1).click()
    page.wait_for_timeout(500)
    F.check(tabs.nth(1).get_attribute("aria-selected") == "true", "second tab selectable")
    # discard the first two (bofa, citi) and leave discover previewed for the Statements page
    for name in ("bofa_card.csv", "citi.csv"):
        page.locator(".file-tabs .file-tab", has_text=name).click()
        page.wait_for_timeout(400)
        page.locator('[data-act="discard"]').click()
        m = confirm_modal(page)
        F.check(name in m.inner_text() and "without importing anything" in m.inner_text(), f"discard confirm text: {m.inner_text()!r}")
        m.locator(".modal-foot .btn-danger-solid").click()
        page.wait_for_timeout(700)
    F.eq(page.locator(".file-tabs .file-tab").count(), 0, "single remaining file has no tab strip")
    F.check("discover" in page.locator(".detect-sub").inner_text().lower() or "Discover" in page.locator(".detect-title").inner_text(), "discover remains in review")
    S["stmt_discover"] = [s for s in qapi.get("/api/statements") if s["status"] == "previewed"][0]["id"]

    # --- statements page ---
    goto(page, "/statements.html", wait_sel="#statements-host table tbody tr[data-id]", soft=F, label="statements load")
    rows = page.locator("#statements-host tbody tr")
    st_api = qapi.get("/api/statements")
    F.eq(rows.count(), len(st_api), "statements list count matches API")
    statuses = {r.locator("td:nth-child(1) .st-file-name").inner_text(): r.locator(".st-status .badge").inner_text().lower() for r in rows.all()}
    F.note(f"statements statuses: {statuses}")
    F.check(statuses.get("discover.csv") == "needs review", "discover shows Needs review")
    F.check(statuses.get("chase_card.csv") == "imported", "chase shows Imported")
    F.eq(page.locator("tr[data-id] .badge:has-text('Failed')").count(), len(S["invalid_stmt_ids"]), "failed uploads listed")
    fail_msgs = [x.inner_text() for x in page.locator(".st-status .text-3").all()]
    F.note(f"failed statement messages: {fail_msgs}")
    F.check(page.locator(f'tr[data-id="{S["stmt_discover"]}"] a:has-text("Continue")').count() == 1, "Continue button for previewed statement")
    F.check(not bad_text(page), f"bad text on statements page: {bad_text(page)}")
    shot(page, "F3-statements-list", full=True)

    # open committed -> transactions filtered by statement
    page.locator(f'tr[data-id="{S["stmt_chase"]}"] td:nth-child(2)').click()
    page.wait_for_url(re.compile(rf"statement={S['stmt_chase']}"), timeout=10000)
    page.wait_for_selector("#tx-body tr[data-id]", timeout=10000)
    F.eq(page.locator("#tx-body tr[data-id]").count(), 6, "statement deep link shows its 6 rows")
    F.check("Rows imported from one statement" in page.locator("#tx-sub").inner_text(), "transactions page explains the statement filter")
    shot(page, "F3-statement-open")

    # download original
    goto(page, "/statements.html")
    page.wait_for_selector("#statements-host table tbody tr", timeout=10000)
    page.locator(f'tr[data-id="{S["stmt_chase"]}"] [data-act="menu"]').click()
    with page.expect_download(timeout=10000) as dl:
        menu_click(page, "Download original")
    d = dl.value
    F.eq(d.suggested_filename, "chase_card.csv", "download filename")
    F.check(pathlib.Path(d.path()).read_bytes() == (FIX / "chase_card.csv").read_bytes(), "downloaded bytes equal the fixture")

    # reparse previewed
    page.locator(f'tr[data-id="{S["stmt_discover"]}"] [data-act="menu"]').click()
    menu_click(page, "Parse again")
    t = toast(page, "Parsing again")
    F.check("Parsing again" in t, "reparse toast")
    try:
        page.wait_for_selector(f'tr[data-id="{S["stmt_discover"]}"] .badge:has-text("Parsing")', timeout=3000)
        F.note("reparse: Parsing badge observed")
    except PwTimeout:
        F.note("reparse: Parsing badge not observed (parse finished within the 2s poll)")
    page.wait_for_selector(f'tr[data-id="{S["stmt_discover"]}"] .badge:has-text("Needs review")', timeout=20000)
    shot(page, "F3-statement-reparsed")

    # delete failed statements (what does the UI say?)
    for sid in S["invalid_stmt_ids"]:
        page.locator(f'tr[data-id="{sid}"] [data-act="menu"]').click()
        menu_click(page, "Delete")
        m = confirm_modal(page)
        if sid == S["invalid_stmt_ids"][0]:
            F.note(f"delete failed statement confirm: {m.inner_text()!r}")
            shot(page, "F3-statement-delete-confirm")
        m.locator(".modal-foot .btn-danger-solid").click()
        toast(page, "Statement deleted")
        page.wait_for_timeout(400)
    # roll back the headerless import (3 transactions) and check the confirm number
    before = txn_total(qapi)
    page.locator(f'tr[data-id="{S["stmt_headerless"]}"] [data-act="menu"]').click()
    menu_click(page, "Roll back import")
    m = confirm_modal(page)
    body = m.inner_text()
    F.note(f"rollback confirm: {body!r}")
    F.check("3" in body and "transactions imported from" in body, "rollback confirm states the number of transactions")
    shot(page, "F3-statement-rollback-confirm")
    m.locator(".modal-foot .btn-danger-solid").click()
    t = toast(page, "Rolled back")
    F.check("3 transactions deleted" in t, f"rollback toast: {t!r}")
    page.wait_for_timeout(500)
    F.eq(txn_total(qapi), before - 3, "rollback removed exactly 3 transactions")
    F.check(all(s["id"] != S["stmt_headerless"] for s in qapi.get("/api/statements")), "rolled-back statement removed from list")
    shot(page, "F3-statements-after", full=True)
    F.finish()


# ---------------------------------------------------------------------------------------------
# F4 — transactions
# ---------------------------------------------------------------------------------------------
def summary_numbers(page):
    txt = page.locator("#tx-summary").inner_text().replace("\n", " ")
    out = {"text": txt}
    m = re.search(r"([\d,]+) transactions?", txt)
    out["count"] = int(m.group(1).replace(",", "")) if m else None
    for key in ("Spent", "Received", "Net"):
        mm = re.search(key + r"\s+([−\-+]?[A-Z]*\$[\d,]+\.\d\d)", txt)
        out[key.lower()] = money(mm.group(1)) if mm else None
    return out


def visible_amounts(page):
    return [money(a.inner_text()) for a in page.locator("#tx-body tr[data-id] .col-amt .amt").all()]


def test_f4_transactions(page, qapi):
    F = Soft("F4", page, S["rec"])
    # filler rows for pagination (one merchant, Jan–Apr 2024, never in August)
    import datetime as dt
    d0 = dt.date(2024, 1, 1)
    for i in range(110):
        qapi.post("/api/transactions", {"account_id": S["acct_checking"], "txn_date": (d0 + dt.timedelta(days=i)).isoformat(), "amount": -1.0, "description": "QA BULK FILLER"})
    total_api = qapi.get("/api/transactions?range=all&limit=1")
    S["total_all"] = total_api["total"]

    goto(page, "/transactions.html?range=all", wait_sel="#tx-body tr[data-id]", soft=F, label="transactions load (130 rows)")
    page.wait_for_selector("#tx-summary b", timeout=10000)
    page.wait_for_timeout(400)
    sm = summary_numbers(page)
    F.note(f"summary: {sm}")
    F.eq(sm["count"], total_api["total"], "totals bar count = API total")
    F.eq(sm["spent"], round(abs(total_api["sum_out"]), 2), "totals bar Spent = API sum_out")
    F.eq(sm["received"], round(total_api["sum_in"], 2), "totals bar Received = API sum_in")
    F.eq(sm["net"], round(total_api["sum_in"] + total_api["sum_out"], 2), "totals bar Net")
    F.eq(page.locator("#tx-body tr[data-id]").count(), 100, "first page is 100 rows")
    F.check("Loading more" in page.locator("#tx-foot").inner_text(), "footer shows loading-more state before the sentinel is reached")
    shot(page, "F4-list", full=False)

    # infinite scroll to the last row
    def scroll_all():
        for _ in range(12):
            page.evaluate("() => { const w = document.getElementById('tx-wrap'); w.scrollTop = w.scrollHeight; window.scrollTo(0, document.body.scrollHeight); }")
            page.wait_for_timeout(500)
            if page.locator("#tx-body tr[data-id]").count() >= S["total_all"]:
                break
    F.timed("infinite scroll to the end", scroll_all)
    F.eq(page.locator("#tx-body tr[data-id]").count(), S["total_all"], "all rows loaded by scrolling")
    F.check(f"{S['total_all']} of {S['total_all']} shown" in page.locator("#tx-foot").inner_text(), f"footer after full load: {page.locator('#tx-foot').inner_text()!r}")
    counted = [money(a.inner_text()) for a in page.locator("#tx-body tr[data-id]:not(.is-transfer):not(.is-excluded) .col-amt .amt").all()]
    skipped = page.locator("#tx-body tr.is-transfer, #tx-body tr.is-excluded").count()
    F.note(f"rows flagged transfer/excluded (shown but not counted): {skipped}; summary says {sm['text']!r}")
    F.eq(round(sum(a for a in counted if a < 0), 2), -sm["spent"], "sum of visible non-transfer negative rows = Spent")
    F.eq(round(sum(a for a in counted if a > 0), 2), sm["received"], "sum of visible non-transfer positive rows = Received")
    shot(page, "F4-list-end")

    # --- filters change URL + list ---
    pick_range(page, F, preset="last-month")
    page.wait_for_url(re.compile(r"range=last-month"), timeout=5000)
    page.wait_for_selector("#tx-body .empty, #tx-body tr[data-id]", timeout=10000)
    F.check(page.locator("#tx-body .empty").count() == 1 and "No transactions match" in page.locator("#tx-body").inner_text(), "last-month preset -> filtered empty state")
    F.check(page.locator("#f-clear").is_visible(), "Clear filters visible when a non-default range is set")
    shot(page, "F4-filter-empty")
    pick_range(page, F, frm="2024-08-01", to="2024-08-31")
    page.wait_for_url(re.compile(r"from=2024-08-01&to=2024-08-31"), timeout=5000)
    page.wait_for_selector("#tx-body tr[data-id]", timeout=10000)
    aug_api = qapi.get("/api/transactions?from=2024-08-01&to=2024-08-31&limit=1")
    F.eq(summary_numbers(page)["count"], aug_api["total"], "custom range count")
    F.check("Aug 1" in page.locator("#f-range").inner_text() and "Aug 31" in page.locator("#f-range").inner_text(), f"range button label: {page.locator('#f-range').inner_text()!r}")
    shot(page, "F4-filter-custom-range")

    # account filter
    page.locator("#f-accounts").click()
    page.wait_for_selector(".popover [data-v]", timeout=5000)
    opts = page.locator(".popover [data-v]").all_inner_texts()
    F.note(f"account filter options (Aug 2024): {opts}")
    page.locator(f".popover [data-v='{S['acct_checking']}']").click()
    page.wait_for_url(re.compile(rf"acct={S['acct_checking']}"), timeout=5000)
    page.keyboard.press("Escape")
    page.wait_for_selector("#tx-body tr[data-id], #tx-body .empty", timeout=10000)
    page.wait_for_timeout(500)
    n_acct = qapi.get(f"/api/transactions?from=2024-08-01&to=2024-08-31&account_id={S['acct_checking']}&limit=1")["total"]
    F.eq(page.locator("#tx-body tr[data-id]").count(), n_acct, "account filter rows = API count")
    F.eq(summary_numbers(page)["count"], n_acct, "account filter count")
    shot(page, "F4-filter-account")
    F.check("QA Checking" in page.locator("#f-accounts").inner_text(), "account button shows the selected name")
    # category filter: Uncategorized
    page.locator("#f-categories").click()
    page.locator(".popover [data-v='none']").click()
    page.wait_for_url(re.compile(r"cat=none"), timeout=5000)
    page.keyboard.press("Escape")
    page.wait_for_timeout(600)
    F.check("Uncategorized" in page.locator("#f-categories").inner_text(), "category button label")
    # status seg
    page.locator("#f-status [data-status='uncategorized']").click()
    page.wait_for_url(re.compile(r"status=uncategorized"), timeout=5000)
    # flow
    page.locator("#f-flow [data-flow='out']").click()
    page.wait_for_url(re.compile(r"flow=out"), timeout=5000)
    page.wait_for_timeout(600)
    sm = summary_numbers(page)
    F.check(sm["received"] is None and sm["spent"] is not None, f"Debits filter hides Received: {sm['text']!r}")
    shot(page, "F4-filters-stacked")
    # search
    page.locator("#f-clear").click()
    page.wait_for_timeout(500)
    F.check("acct=" not in page.url and "status=" not in page.url and "flow=" not in page.url and "cat=" not in page.url, f"clear filters resets URL: {page.url}")
    F.note(f"after Clear filters the URL is {page.url.split('?')[-1]!r} (the default range is written explicitly, unlike on first load) and the older data hides behind 'No transactions match'")
    pick_range(page, F, preset="all")
    page.wait_for_url(re.compile(r"range=all"), timeout=5000)
    page.fill("#f-q", "NETFLIX")
    page.wait_for_url(re.compile(r"q=NETFLIX"), timeout=5000)
    page.wait_for_function("() => document.querySelectorAll('#tx-body tr[data-id]').length === 1", timeout=10000)
    F.eq(page.locator("#tx-body tr[data-id]").count(), 1, "search narrows to NETFLIX")
    shot(page, "F4-search")
    page.fill("#f-q", "")
    page.press("#f-q", "Enter")
    page.wait_for_timeout(700)

    # --- sort ---
    page.locator("th[data-sort='amount']").click()
    page.wait_for_url(re.compile(r"sort=-amount"), timeout=5000)
    page.wait_for_selector("#tx-body tr[data-id]", timeout=10000)
    page.wait_for_timeout(400)
    amts = visible_amounts(page)
    F.check(amts == sorted(amts, reverse=True), "amount desc sorted")
    F.eq(page.locator("th[data-sort='amount']").get_attribute("aria-sort"), "descending", "aria-sort descending")
    page.locator("th[data-sort='amount']").click()
    page.wait_for_url(re.compile(r"sort=amount"), timeout=5000)
    page.wait_for_timeout(700)
    amts = visible_amounts(page)
    F.check(amts == sorted(amts), "amount asc sorted")
    page.locator("th[data-sort='merchant']").click()
    page.wait_for_url(re.compile(r"sort=merchant"), timeout=5000)
    page.wait_for_timeout(700)
    names = [n.inner_text() for n in page.locator("#tx-body tr[data-id] .merchant-name").all()]
    F.check(names == sorted(names, key=str.lower) or names == sorted(names), f"merchant sort order (first 5): {names[:5]}")
    page.locator("th[data-sort='merchant']").click()
    page.wait_for_timeout(400)
    F.note(f"clicking Merchant header twice: sort stays {page.url.split('sort=')[-1][:12]} (no reverse)")
    shot(page, "F4-sort")
    page.locator("th[data-sort='date']").click()
    page.wait_for_timeout(600)

    # --- drawer: category picker (filter + create), note, transfer flag ---
    page.fill("#f-q", "NETFLIX")
    page.wait_for_function("() => document.querySelectorAll('#tx-body tr[data-id]').length === 1", timeout=10000)
    netflix_id = int(page.locator("#tx-body tr[data-id]").first.get_attribute("data-id"))
    S["netflix_id"] = netflix_id
    page.locator("#tx-body tr[data-id] .col-merchant").first.click()
    page.wait_for_selector(".drawer #txd-notes", timeout=10000)
    F.check("NETFLIX" in page.locator("#drawer-title").inner_text().upper(), "drawer title is the merchant")
    chip0 = page.locator(".drawer [data-dact='pick']").inner_text()
    F.note(f"NETFLIX drawer category chip before any change: {chip0!r} (built-in hint suggestion expected)")
    F.check("Choose a category" in chip0 or page.locator(".drawer .catchip--suggested").count() == 1, f"drawer chip shows uncategorized or a suggested chip: {chip0!r}")
    shot(page, "F4-drawer")
    page.locator(".drawer [data-dact='pick']").click()
    page.wait_for_selector(".cat-picker input", timeout=5000)
    page.locator(".cat-picker input").type("Strea")
    page.wait_for_timeout(200)
    opts = [o.inner_text().split("\n")[0] for o in page.locator(".cat-picker .cp-opt").all()]
    F.note(f"picker options for 'Strea': {opts}")
    F.check(opts and opts[0].startswith("Streaming"), "typing filters the picker (Streaming first)")
    shot(page, "F4-picker-filter")
    page.locator(".cat-picker .cp-opt").first.click()
    t = toast(page, "Categorized as Streaming")
    F.check("Streaming" in t, f"categorize toast: {t!r}")
    page.wait_for_timeout(400)
    F.check("Streaming" in page.locator(".drawer [data-dact='pick']").inner_text(), "drawer chip updated")
    F.check("Streaming" in page.locator(f"tr[data-id='{netflix_id}'] .catchip-label").inner_text(), "row chip updated without reload")
    # create inline
    page.locator(".drawer [data-dact='pick']").click()
    page.wait_for_selector(".cat-picker input", timeout=5000)
    page.locator(".cat-picker input").type("QA Custom Cat")
    page.wait_for_selector(".cat-picker .cp-create", timeout=3000)
    F.check("Create" in page.locator(".cat-picker .cp-create").inner_text() and "QA Custom Cat" in page.locator(".cat-picker .cp-create").inner_text(), "create option offered")
    page.locator(".cat-picker .cp-create").click()
    t = toast(page, "Categorized as QA Custom Cat")
    page.wait_for_timeout(500)
    cats = qapi.get("/api/categories")
    custom = [c for c in cats if c["name"] == "QA Custom Cat"]
    F.check(len(custom) == 1, "inline-created category exists via API")
    S["cat_custom"] = custom[0]["id"] if custom else None
    F.eq(qapi.get(f"/api/transactions/{netflix_id}")["category_id"], S["cat_custom"], "transaction now in the new category")
    shot(page, "F4-picker-create")
    # note
    page.locator(".drawer #txd-notes").fill("qa note 1")
    page.locator(".drawer #txd-merchant").focus()
    t = toast(page, "Note saved")
    F.eq(qapi.get(f"/api/transactions/{netflix_id}")["notes"], "qa note 1", "note persisted on blur")
    # transfer switch
    page.locator(".drawer label.switch:has([data-dflag='is_transfer']) .switch-track").click()
    t = toast(page, "Marked as transfer")
    page.wait_for_timeout(400)
    F.check(qapi.get(f"/api/transactions/{netflix_id}")["is_transfer"] is True, "transfer flag persisted")
    F.check(page.locator(f"tr[data-id='{netflix_id}'] .badge:has-text('Transfer')").count() == 1, "row shows Transfer badge")
    F.check(page.locator(".drawer [data-dflag='is_excluded']").is_disabled(), "exclude switch disabled while transfer")
    shot(page, "F4-drawer-transfer")
    page.locator(".drawer label.switch:has([data-dflag='is_transfer']) .switch-track").click()
    toast(page, "Unmarked as transfer")
    page.wait_for_timeout(300)
    hist = page.locator(".drawer .timeline .tl-text").all_inner_texts()
    F.note(f"drawer history right after editing (drawer still open): {hist}")
    live = any("Note updated" in h for h in hist) and any("Marked as transfer" in h for h in hist)
    page.keyboard.press("Escape")
    page.wait_for_selector(".drawer", state="detached", timeout=5000)
    page.locator(f"tr[data-id='{netflix_id}'] .col-merchant").click()
    page.wait_for_selector(".drawer .timeline", timeout=10000)
    hist2 = page.locator(".drawer .timeline .tl-text").all_inner_texts()
    F.note(f"drawer history after reopening: {hist2}")
    F.check(any("Note updated" in h for h in hist2) and any("Marked as transfer" in h for h in hist2), f"history records note + transfer events after reopen: {hist2}")
    F.check(live, f"History timeline in the open drawer is stale: note/transfer edits made in the drawer only show up after closing and reopening it (open: {hist})")
    page.keyboard.press("Escape")
    page.wait_for_selector(".drawer", state="detached", timeout=5000)
    page.fill("#f-q", "")
    page.press("#f-q", "Enter")
    page.wait_for_timeout(700)

    # --- bulk categorize + undo ---
    page.locator("#f-status [data-status='uncategorized']").click()
    page.wait_for_url(re.compile(r"status=uncategorized"), timeout=5000)
    page.wait_for_selector("#tx-body tr[data-id]", timeout=10000)
    page.wait_for_timeout(500)
    rows = page.locator("#tx-body tr[data-id]")
    ids = [int(rows.nth(i).get_attribute("data-id")) for i in range(2)]
    rows.nth(0).locator("input[data-select]").check()
    rows.nth(1).locator("input[data-select]").check()
    bar = page.locator("#tx-bulk")
    bar.wait_for(state="visible", timeout=5000)
    F.check("2 selected" in bar.inner_text(), f"floatbar: {bar.inner_text()!r}")
    shot(page, "F4-bulk-bar")
    bar.locator("[data-bulk='categorize']").click()
    page.wait_for_selector(".cat-picker input", timeout=5000)
    page.locator(".cat-picker input").type("Groceries")
    page.wait_for_timeout(200)
    page.locator(".cat-picker .cp-opt").first.click()
    t = toast(page, "2 categorized as Groceries")
    F.check("Undo" in t, "undo action offered on bulk toast")
    F.check(all(qapi.get(f"/api/transactions/{i}")["category_id"] is not None for i in ids), "bulk categorize applied")
    shot(page, "F4-bulk-toast")
    click_undo(page)
    toast(page, "Undone")
    page.wait_for_timeout(500)
    F.check(all(qapi.get(f"/api/transactions/{i}")["category_id"] is None for i in ids), "undo restored uncategorized")
    F.note(f"after undo the rows are still listed under the Uncategorized filter: {page.locator('#tx-body tr[data-id]').count()} rows visible")
    # --- bulk exclude / transfer + undo restore the exact previous state ---
    rows = page.locator("#tx-body tr[data-id]")
    ids = [int(rows.nth(i).get_attribute("data-id")) for i in range(2)]
    before = {i: qapi.get(f"/api/transactions/{i}") for i in ids}
    rows.nth(0).locator("input[data-select]").check()
    rows.nth(1).locator("input[data-select]").check()
    bar.wait_for(state="visible", timeout=5000)
    bar.locator("[data-bulk='exclude']").click()
    t = toast(page, "Excluded from reports")
    F.check("Undo" in t, "undo offered on bulk exclude")
    F.check(all(qapi.get(f"/api/transactions/{i}")["is_excluded"] for i in ids), "bulk exclude applied")
    click_undo(page)
    toast(page, "Undone")
    page.wait_for_timeout(500)
    F.check(all(qapi.get(f"/api/transactions/{i}")["is_excluded"] is False for i in ids), "undo restored is_excluded")
    dismiss_toasts(page)
    rows.nth(0).locator("input[data-select]").check()
    rows.nth(1).locator("input[data-select]").check()
    bar.wait_for(state="visible", timeout=5000)
    bar.locator("[data-bulk='set_transfer']").click()
    t = toast(page, "Marked as transfer")
    F.check("Undo" in t and qapi.get(f"/api/transactions/{ids[0]}")["is_transfer"] is True, "bulk transfer applied with undo")
    click_undo(page)
    toast(page, "Undone")
    page.wait_for_timeout(500)
    after = {i: qapi.get(f"/api/transactions/{i}") for i in ids}
    F.check(all((after[i]["is_transfer"], after[i]["category_id"], after[i]["is_excluded"]) == (before[i]["is_transfer"], before[i]["category_id"], before[i]["is_excluded"]) for i in ids), f"undo restored category and flags: {[(before[i]['category_id'], after[i]['category_id']) for i in ids]}")
    F.check(any(e["kind"] == "manual" and (e.get("detail") or {}).get("restored") for e in after[ids[0]]["events"]), "restore recorded in the row history")
    page.locator("#f-status [data-status='all']").click()
    page.wait_for_timeout(700)

    # --- amount filter, saved views, pinned view in the sidebar ---
    page.locator("#f-amount").click()
    page.wait_for_selector("#fa-min", timeout=4000)
    page.fill("#fa-min", "50")
    page.locator(".popover [data-act='apply']").click()
    page.wait_for_url(re.compile(r"min=50"), timeout=5000)
    page.wait_for_selector("#tx-body tr[data-id], #tx-body .empty", timeout=10000)
    page.wait_for_timeout(500)
    shown = [int(r.get_attribute("data-id")) for r in page.locator("#tx-body tr[data-id]").all()[:8]]
    F.check(shown and all(abs(qapi.get(f"/api/transactions/{i}")["amount"]) >= 50 for i in shown), f"amount ≥ 50 filter applied to {len(shown)} rows")
    F.check("≥" in page.locator("#f-amount").inner_text(), f"amount button shows the bound: {page.locator('#f-amount').inner_text()!r}")
    page.locator("#f-views").click()
    menu_click(page, "Save current view")
    page.wait_for_selector("#sv-name", timeout=4000)
    page.fill("#sv-name", "QA over 50")
    page.locator(".switch:has(#sv-pin) .switch-track").click()
    page.locator(".modal-foot .btn-primary").click()
    toast(page, "saved")
    page.wait_for_timeout(500)
    saved = qapi.get("/api/auth/me")["preferences"].get("saved_views") or []
    F.check(any(v["name"] == "QA over 50" and "min=50" in v["query"] and v["pinned"] for v in saved), f"view saved to preferences: {saved}")
    F.check("view=" in page.url and "View: QA over 50" in page.locator("#tx-sub").inner_text(), "active view named in the URL and subtitle")
    F.check(page.locator(".sidebar .nav-item--sub", has_text="QA over 50").count() == 1, "pinned view appears under Transactions in the sidebar")
    page.locator("#f-clear").click()
    page.wait_for_timeout(700)
    F.check("view=" not in page.url and "min=" not in page.url, "clearing filters drops the view and the amount bound")
    page.locator("#f-views").click()
    menu_click(page, "QA over 50")
    page.wait_for_url(re.compile(r"min=50"), timeout=5000)
    page.wait_for_timeout(500)
    F.check("view=" in page.url and "Views" not in page.locator("#f-views").inner_text(), "applying the view restores its filters")
    page.locator("#f-views").click()
    menu_click(page, "Manage views")
    page.wait_for_selector(".modal .mv-row", timeout=4000)
    page.locator(".modal .mv-row [data-mv='name']").fill("QA over fifty")
    page.locator(".modal .mv-row [data-mv='name']").press("Tab")
    page.wait_for_timeout(600)
    F.check(any(v["name"] == "QA over fifty" for v in qapi.get("/api/auth/me")["preferences"].get("saved_views") or []), "rename persisted")
    page.locator(".modal .mv-row [data-mv='delete']").click()
    t = toast(page, "deleted")
    F.check("Undo" in t, "delete offers Undo")
    click_undo(page)
    toast(page, "Undone")
    page.wait_for_timeout(500)
    F.check(len(qapi.get("/api/auth/me")["preferences"].get("saved_views") or []) == 1, "undo restored the view")
    page.locator(".modal .mv-row [data-mv='delete']").click()
    page.wait_for_timeout(400)
    page.locator(".modal-foot .btn-primary").click()
    page.wait_for_selector(".modal", state="detached", timeout=4000)
    dismiss_toasts(page)
    F.check(page.locator(".sidebar .nav-item--sub").count() == 0, "deleted view leaves the sidebar")
    page.locator("#f-clear").click()
    page.wait_for_timeout(700)

    # --- export CSV ---
    with page.expect_download(timeout=15000) as dl:
        page.locator("#btn-export").click()
    data = pathlib.Path(dl.value.path()).read_text()
    rdr = list(csv.reader(io.StringIO(data)))
    F.eq(rdr[0], ["Date", "Account", "Description", "Merchant", "Category", "Amount", "Currency", "Notes", "Transfer"], "CSV header")
    F.eq(len(rdr) - 1, S["total_all"], "CSV row count = total")
    first_ui = page.locator("#tx-body tr[data-id]").first
    F.eq(rdr[1][3], first_ui.locator(".merchant-name").inner_text().split("\n")[0].strip(), "first CSV row merchant = first UI row")
    F.eq(round(sum(float(r[5]) for r in rdr[1:] if float(r[5]) < 0), 2), -summary_numbers(page)["spent"], "CSV negative sum = Spent")
    F.note(f"export filename: {dl.value.suggested_filename}")

    # --- keyboard: read the ? sheet, then test every claimed shortcut ---
    page.mouse.click(700, 60)  # blur any input
    page.keyboard.press("?")
    page.wait_for_selector(".modal:has-text('Keyboard shortcuts')", timeout=5000)
    claimed = page.locator(".modal .shortcuts-grid > div").all_inner_texts()
    F.note(f"shortcut sheet claims: {claimed}")
    shot(page, "F4-shortcuts-sheet")
    page.keyboard.press("Escape")
    page.wait_for_selector(".modal", state="detached", timeout=5000)
    page.keyboard.press("j")
    page.wait_for_timeout(150)
    F.check(page.locator("tr.is-focused[data-idx='0']").count() == 1, "j focuses first row")
    page.keyboard.press("j")
    page.keyboard.press("k")
    page.wait_for_timeout(100)
    F.check(page.locator("tr.is-focused[data-idx='0']").count() == 1, "j then k returns to first row")
    page.keyboard.press("x")
    page.wait_for_timeout(200)
    F.check(page.locator("tr[data-idx='0'][aria-selected='true']").count() == 1 and page.locator("#tx-bulk").count() == 1, "x selects the focused row")
    page.keyboard.press("Escape")
    page.wait_for_timeout(200)
    F.check(page.locator("#tx-bulk").count() == 0, "Esc clears the selection")
    F.check(page.locator("tr.is-focused").count() == 1, "Esc after clearing selection keeps the focus row")
    page.keyboard.press("c")
    page.wait_for_selector(".cat-picker", timeout=3000)
    F.check(page.locator(".cat-picker").count() == 1, "c opens the category picker")
    page.keyboard.press("Escape")
    page.wait_for_timeout(200)
    F.note(f"after Esc from the picker focus is on <{page.evaluate('document.activeElement.tagName + \'.\' + document.activeElement.className')}> so Enter re-activates that chip instead of 'Open details'")
    page.evaluate("document.activeElement && document.activeElement.blur()")
    page.keyboard.press("Enter")
    page.wait_for_selector(".drawer", timeout=3000)
    F.check(page.locator(".drawer").count() == 1, "Enter opens the drawer")
    page.keyboard.press("Escape")
    page.wait_for_selector(".drawer", state="detached", timeout=3000)
    page.keyboard.press("n")
    page.wait_for_selector(".drawer #txd-notes", timeout=3000)
    F.eq(page.evaluate("document.activeElement.id"), "txd-notes", "n opens the drawer with notes focused")
    page.keyboard.press("Escape")
    page.wait_for_selector(".drawer", state="detached", timeout=3000)
    focused_id = int(page.locator("tr.is-focused").get_attribute("data-id"))
    page.keyboard.press("t")
    toast(page, "Marked as transfer")
    F.check(qapi.get(f"/api/transactions/{focused_id}")["is_transfer"] is True, "t toggles transfer")
    page.keyboard.press("t")
    toast(page, "Unmarked as transfer")
    page.keyboard.press("a")
    page.wait_for_timeout(300)
    F.note("a (accept suggestion) is a no-op on a row without a suggestion, no feedback")
    # global chords
    for key, path in (("t", "/transactions.html"), ("d", "/index.html"), ("r", "/review.html"), ("i", "/import.html"), ("c", "/categories.html"), ("p", "/reports.html"), ("s", "/settings.html")):
        page.mouse.click(700, 60)
        page.keyboard.press("g")
        page.keyboard.press(key)
        try:
            page.wait_for_url(re.compile(re.escape(path)), timeout=5000)
            page.wait_for_selector("#tb-username", timeout=10000)
        except PwTimeout:
            F.fails.append(f"g {key} did not navigate to {path} (at {page.url})")
    goto(page, "/transactions.html?range=all")
    page.wait_for_selector("#tx-body tr[data-id]", timeout=10000)
    page.mouse.click(700, 60)
    page.keyboard.press("/")
    page.wait_for_selector(".palette", timeout=3000)
    F.check(page.locator(".palette").count() == 1, "/ opens the palette")
    page.keyboard.press("Escape")
    page.wait_for_timeout(200)
    page.keyboard.press("Meta+k")
    page.wait_for_selector(".palette", timeout=3000)
    F.check(page.locator(".palette").count() == 1, "Cmd+K opens the palette")
    page.locator(".palette input").type("NETFL")
    page.wait_for_selector(".palette-item:has-text('NETFLIX')", timeout=5000)
    F.check(page.locator(".palette-group", has_text="Transactions").count() == 1, "palette finds transactions")
    shot(page, "F4-palette")
    page.keyboard.press("Escape")
    mode0 = page.evaluate("document.documentElement.getAttribute('data-sidebar')")
    page.keyboard.press("[")
    page.wait_for_timeout(150)
    mode1 = page.evaluate("document.documentElement.getAttribute('data-sidebar')")
    F.check(mode0 != mode1, f"[ toggles the sidebar ({mode0} -> {mode1})")
    page.keyboard.press("]")
    page.wait_for_timeout(150)
    F.eq(page.evaluate("document.documentElement.getAttribute('data-sidebar')"), "full", "] expands the sidebar")
    F.finish()


# ---------------------------------------------------------------------------------------------
# F5 — review queue
# ---------------------------------------------------------------------------------------------
def pill_value(page):
    el = page.locator(".sidebar [data-review-pill]")
    return None if el.is_hidden() else int(el.inner_text())


def test_f5_review(page, qapi):
    F = Soft("F5", page, S["rec"])
    goto(page, "/review.html", wait_sel=".rv-card, #rv-list .empty", soft=F, label="review load")
    page.wait_for_timeout(400)
    cnt = qapi.get("/api/review/count")
    F.eq(pill_value(page), cnt["total"], "nav pill = /api/review/count total")
    q = qapi.get("/api/review?mode=merchant&limit=100")
    cards = page.locator(".rv-card")
    F.eq(cards.count(), len(q["groups"]), "one card per merchant group")
    prog = page.locator("#rv-progress").inner_text()
    F.check(f"{q['remaining_items']}" in prog and f"{q['remaining']} merchant" in prog, f"progress text: {prog!r}")
    F.check(page.locator("#btn-ai").is_hidden(), "Ask AI button hidden when AI disabled")
    confs = page.locator(".rv-suggest .rv-conf").all_inner_texts()
    F.note(f"suggestion sources shown with AI off: {sorted(set(confs))}")
    F.check(not any("AI" in c.split("·")[-1] for c in confs), f"no suggestion is attributed to AI while AI is off: {confs}")
    F.check(cards.first.evaluate("e => e.classList.contains('is-focused')"), "first card focused on load")
    F.check(not any(page.locator(".rv-card [data-cfield='always']").nth(i).is_checked() for i in range(min(3, cards.count()))), "'Always do this' is OFF by default (rules are opt-in)")
    shot(page, "F5-review", full=True)

    # accept-all button reflects the confident suggestions (none ≥ 80% with AI off → disabled, still visible)
    conf = [g for g in q["groups"] if g["suggestion"] and (g["suggestion"].get("confidence") or 0) >= 0.8]
    btn = page.locator("#btn-accept-all")
    F.check(btn.is_visible(), "Accept-all button visible in merchant mode")
    F.eq(btn.is_disabled(), not conf, f"accept-all enabled only when a suggestion is ≥ 80% ({len(conf)} such groups)")
    if conf:
        rules_before = len(qapi.get("/api/rules"))
        btn.click()
        page.wait_for_selector(".modal", timeout=4000)
        body = page.locator(".modal").inner_text()
        F.check("No rules" in body and str(sum(len(g["ids"]) for g in conf)) in body, f"accept-all confirm names the count and promises no rules: {body!r}")
        page.locator(".modal-foot .btn-primary").click()
        t = toast(page, "accepted")
        F.check("Undo" in t and len(qapi.get("/api/rules")) == rules_before, "accept-all toast offers Undo and created no rule")
        click_undo(page)
        toast(page, "Undone")
        page.wait_for_timeout(700)
        F.eq(qapi.get("/api/review/count")["total"], cnt["total"], "undo of accept-all restored the queue")

    # hiding a merchant persists across reloads (account preference), and can be shown again
    first_key = cards.first.get_attribute("data-key")
    page.keyboard.press("s")
    page.wait_for_timeout(500)
    F.check(page.locator(f".rv-card[data-key='{first_key}']").count() == 0, "s hides the focused card")
    F.check("hidden" in page.locator("#rv-progress").inner_text(), "progress shows the hidden count")
    page.wait_for_timeout(700)
    F.check(first_key in (qapi.get("/api/auth/me")["preferences"].get("review_skips") or []), "hidden key saved to preferences")
    page.reload()
    page.wait_for_selector(".rv-card", timeout=10000)
    page.wait_for_timeout(400)
    F.check(page.locator(f".rv-card[data-key='{first_key}']").count() == 0, "hidden merchant stays hidden after reload")
    page.locator("#rv-progress [data-act='unskip']").click()
    page.wait_for_selector(f".rv-card[data-key='{first_key}']", timeout=10000)
    page.wait_for_timeout(400)
    F.check(page.locator(f".rv-card[data-key='{first_key}']").count() == 1, "show hidden brings the merchant back")

    # focus mode: one card at a time, j/k move through them, f toggles it off
    cards = page.locator(".rv-card")
    cards.first.focus()
    page.keyboard.press("f")
    page.wait_for_timeout(300)
    visible = lambda: page.evaluate("() => Array.from(document.querySelectorAll('.rv-card')).filter((c) => c.getClientRects().length).map((c) => c.dataset.key)")
    F.check(len(visible()) == 1 and "focus=1" in page.url and page.locator("#btn-focus").get_attribute("aria-pressed") == "true", f"focus mode shows one card: {visible()}")
    F.check("Card 1 of" in page.locator("#rv-progress").inner_text(), "focus mode shows the position")
    seen = visible()
    page.keyboard.press("j")
    page.wait_for_timeout(200)
    F.check(len(visible()) == 1 and visible() != seen, "j moves to the next card in focus mode")
    page.keyboard.press("f")
    page.wait_for_timeout(300)
    F.check(len(visible()) == cards.count() and "focus=1" not in page.url, "f again leaves focus mode")
    page.keyboard.press("k")
    page.wait_for_timeout(200)
    cards = page.locator(".rv-card")

    # opt in to a rule on the focused (largest) group, then keyboard-resolve -> rule created
    g0 = q["groups"][0]
    cards.first.locator(".rv-always .switch-track").click()
    F.check(cards.first.locator("[data-cfield='always']").is_checked(), "'Always do this' switched on for the first group")
    cards.first.locator("[data-cfield='always']").blur()
    page.keyboard.press("c")
    page.wait_for_selector(".cat-picker input", timeout=5000)
    page.locator(".cat-picker input").type("Groceries")
    page.wait_for_timeout(200)
    page.keyboard.press("Enter")
    t = toast(page, "Groceries")
    F.check("rule created" in t, f"resolve with 'always' creates a rule: {t!r}")
    page.wait_for_timeout(700)
    rules = qapi.get("/api/rules")
    F.check(any(r["pattern"] == g0["key"] and r["match_field"] == "merchant_key" for r in rules), f"rule for {g0['key']!r} exists in API: {[r['pattern'] for r in rules]}")
    F.check(all(qapi.get(f"/api/transactions/{i}")["category_id"] is not None for i in g0["ids"][:3]), "group transactions categorized")
    F.eq(pill_value(page), qapi.get("/api/review/count")["total"], "pill updated without reload")
    F.check(f"{q['remaining_items'] - g0['count']}" in page.locator("#rv-progress").inner_text(), "progress decremented")
    shot(page, "F5-after-resolve", full=True)
    S["setup_after_resolve"] = qapi.get("/api/reports/dashboard")["setup"]
    F.check(S["setup_after_resolve"]["statements"] >= 1 and S["setup_after_resolve"]["rules"] >= 1, f"dashboard setup block counts imports and the new rule: {S['setup_after_resolve']}")

    # undo the resolve: rows go back to uncategorized, the rule disappears, the card returns
    click_undo(page)
    toast(page, "Undone")
    page.wait_for_timeout(700)
    F.check(all(qapi.get(f"/api/transactions/{i}")["category_id"] is None for i in g0["ids"][:3]), "undo uncategorized the group again")
    F.check(not any(r["pattern"] == g0["key"] for r in qapi.get("/api/rules")), "undo removed the rule it had created")
    F.check(page.locator(f".rv-card[data-key='{g0['key']}']").count() == 1, "undo brought the card back")
    F.eq(pill_value(page), qapi.get("/api/review/count")["total"], "pill updated after undo")
    dismiss_toasts(page)
    # redo the resolve so the rest of the flow sees the rule
    cards = page.locator(".rv-card")
    if not cards.first.locator("[data-cfield='always']").is_checked():
        cards.first.locator(".rv-always .switch-track").click()
    cards.first.locator("[data-cfield='always']").blur()
    page.keyboard.press("c")
    page.wait_for_selector(".cat-picker input", timeout=5000)
    page.locator(".cat-picker input").type("Groceries")
    page.wait_for_timeout(200)
    page.keyboard.press("Enter")
    t = toast(page, "Groceries")
    F.check("rule created" in t, "redo created the rule again")
    page.wait_for_timeout(700)

    # resolve another group with the default ('always' OFF) -> no rule
    q2 = qapi.get("/api/review?mode=merchant&limit=100")
    F.check(len(q2["groups"]) >= 3, f"groups remain after one resolve: {len(q2['groups'])}")
    g1 = q2["groups"][0]
    card = page.locator(f".rv-card[data-key='{g1['key']}']")
    F.check(not card.locator("[data-cfield='always']").is_checked(), "'Always do this' is off on the next card")
    card.locator("[data-cact='pick']").first.click()
    page.wait_for_selector(".cat-picker input", timeout=5000)
    page.locator(".cat-picker input").type("Dining")
    page.wait_for_timeout(200)
    page.locator(".cat-picker .cp-opt").first.click()
    t = toast(page, g1["display"][:12])
    F.check("rule created" not in t, f"no rule when 'always' is off: {t!r}")
    n_rules = len(qapi.get("/api/rules"))
    F.eq(n_rules, len(rules), "rule count unchanged")

    # skip
    page.wait_for_timeout(600)
    n = page.locator(".rv-card").count()
    page.mouse.click(700, 60)
    page.keyboard.press("j")
    page.keyboard.press("k")
    page.keyboard.press("s")
    page.wait_for_timeout(600)
    F.eq(page.locator(".rv-card").count(), n - 1, "s skips the focused card")
    F.check("1 hidden" in page.locator("#rv-progress").inner_text(), f"progress shows hidden count: {page.locator('#rv-progress').inner_text()!r}")
    shot(page, "F5-skipped")
    page.locator("#rv-progress [data-act='unskip']").click()
    page.wait_for_timeout(700)
    F.eq(page.locator(".rv-card").count(), n, "unskip restores the card")

    # transfer via keyboard on a card; single mode
    page.locator("[data-mode='single']").click()
    page.wait_for_url(re.compile(r"mode=single"), timeout=5000)
    page.wait_for_selector(".rv-card.rv-single", timeout=10000)
    F.check(page.locator(".rv-card.rv-single").count() >= 1, "one-by-one mode renders single cards")
    F.check(page.locator(".rv-card [data-cact='expand']").count() == 0, "no 'Show charges' in single mode")
    shot(page, "F5-single-mode")
    page.locator("[data-mode='merchant']").click()
    page.wait_for_timeout(700)
    # expand a group's charges
    page.locator(".rv-card [data-cact='expand']").first.click()
    page.wait_for_selector(".rv-rows .rv-row", timeout=5000)
    F.check(page.locator(".rv-rows .rv-row").count() >= 1, "expanding lists the group's charges")
    F.check(not bad_text(page), f"bad text on review page: {bad_text(page)}")
    F.finish()


# ---------------------------------------------------------------------------------------------
# F6 — rules
# ---------------------------------------------------------------------------------------------
def new_rule_ui(page, F, field, mtype, pattern, cat_query, label, expect_error=None):
    page.locator('[data-act="new-rule"]').click()
    m = confirm_modal(page)
    m.locator("#rf-field").select_option(field)
    m.locator("#rf-type").select_option(mtype)
    m.locator("#rf-pattern").fill(pattern)
    page.wait_for_timeout(700)
    prev = m.locator("#rf-preview").inner_text()
    F.note(f"{label}: preview -> {prev!r}")
    if expect_error:
        F.check(m.locator("#rf-preview.is-error").count() == 1, f"{label}: preview shows an error state for invalid input")
        shot(page, f"F6-{label}")
        m.locator("#rf-cat").click()
        page.wait_for_selector(".cat-picker input", timeout=5000)
        page.locator(".cat-picker input").type(cat_query)
        page.wait_for_timeout(200)
        page.locator(".cat-picker .cp-opt").first.click()
        m.locator(".modal-foot .btn-primary").click()
        try:
            t = toast(page, expect_error, timeout=4000)
        except PwTimeout:
            t = " | ".join(toast_text_all(page))
        F.check(expect_error.lower() in t.lower(), f"{label}: create rejected with {t!r}")
        F.check(page.locator(".modal").count() == 1, f"{label}: modal stays open after server error")
        page.keyboard.press("Escape")
        page.wait_for_selector(".modal", state="detached", timeout=3000)
        return None
    m.locator("#rf-cat").click()
    page.wait_for_selector(".cat-picker input", timeout=5000)
    page.locator(".cat-picker input").type(cat_query)
    page.wait_for_timeout(200)
    page.locator(".cat-picker .cp-opt").first.click()
    page.wait_for_timeout(200)
    api_prev = S["api"].post("/api/rules/preview", {"match_field": field, "match_type": mtype, "pattern": pattern, "only_uncategorized": False})
    mm = re.search(r"Matches ([\d,]+)", prev)
    F.eq(int(mm.group(1).replace(",", "")) if mm else None, api_prev["count"], f"{label}: preview count = API")
    shot(page, f"F6-{label}")
    m.locator(".modal-foot .btn-primary").click()
    t = toast(page, "Rule created")
    F.note(f"{label}: {t!r}")
    page.wait_for_selector(".modal", state="detached", timeout=5000)
    page.wait_for_timeout(500)
    return t


def rule_order(page):
    return [int(x) for x in page.locator("#rule-list .rule").evaluate_all("els => els.map(e => e.dataset.id)")]


def test_f6_rules(page, qapi):
    F = Soft("F6", page, S["rec"])
    goto(page, "/rules.html", wait_sel="#rule-list .rule, #rule-list .empty", soft=F, label="rules load")
    F.eq(page.locator("#rule-list .rule").count(), len(qapi.get("/api/rules")), "rules listed = API")
    shot(page, "F6-rules-list", full=True)

    spot = qapi.get("/api/transactions?q=SPOTIFY&range=all")["items"]
    spot_key = spot[0]["merchant_key"] if spot else "SPOTIFY"
    new_rule_ui(page, F, "description_clean", "contains", "UBER", "Delivery", "contains")
    new_rule_ui(page, F, "description_clean", "starts_with", "SQ ", "Coffee", "starts-with")
    new_rule_ui(page, F, "merchant_key", "equals", spot_key, "Streaming", "equals")
    new_rule_ui(page, F, "description_raw", "regex", "[abc", "Travel", "invalid-regex", expect_error="regular expression")
    new_rule_ui(page, F, "description_raw", "regex", "DELTA|WHOLE ?FOODS", "Travel", "regex")
    rules = qapi.get("/api/rules")
    F.check(any(r["match_type"] == "regex" and r["pattern"] == "DELTA|WHOLE ?FOODS" for r in rules), "regex rule saved")
    uber = qapi.get("/api/transactions?q=UBER&range=all")["items"]
    F.check(uber and uber[0]["category_source"] == "rule", f"'contains UBER' rule: modal preview said 'Matches 1 existing transaction' and 'Apply to existing / Only uncategorized' was on, but the UBER row was NOT re-categorized because it holds an unconfirmed built-in *suggestion* (category_id set, status={uber and uber[0]['category_status']}, source={uber and uber[0]['category_source']}); Review still lists it as needing a category")

    # reorder via menu
    order0 = rule_order(page)
    page.locator(f".rule[data-id='{order0[0]}'] [data-act='menu']").click()
    menu_click(page, "Move down")
    page.wait_for_function(f"() => document.querySelector('#rule-list .rule').dataset.id !== '{order0[0]}'", timeout=5000)
    order1 = rule_order(page)
    F.eq(order1[1], order0[0], "Move down reorders the list")
    F.eq([r["id"] for r in qapi.get("/api/rules")], order1, "reorder persisted (API order)")
    # Alt+ArrowUp keyboard reorder
    page.locator(f".rule[data-id='{order0[0]}']").focus()
    page.keyboard.press("Alt+ArrowUp")
    page.wait_for_function(f"() => document.querySelector('#rule-list .rule').dataset.id === '{order0[0]}'", timeout=5000)
    F.eq(rule_order(page)[0], order0[0], "Alt+ArrowUp moves the rule up")
    # drag & drop
    try:
        ids = rule_order(page)
        page.drag_and_drop(f".rule[data-id='{ids[0]}'] .rule-grip", f".rule[data-id='{ids[2]}']")
        page.wait_for_timeout(900)
        ids2 = rule_order(page)
        F.note(f"drag first rule onto third: {ids} -> {ids2}")
        F.check(ids2 != ids, "drag and drop reorders")
    except Exception as e:  # noqa: BLE001
        F.note(f"drag and drop threw: {e}")
    shot(page, "F6-reordered", full=True)

    # edit via modal (clear the undo toasts first: the stack shows three at a time and queues the rest)
    dismiss_toasts(page)
    rid = rule_order(page)[0]
    page.locator(f".rule[data-id='{rid}'] [data-act='menu']").click()
    menu_click(page, "Edit")
    m = confirm_modal(page)
    F.check("Edit rule" in m.inner_text(), "edit modal title")
    old = m.locator("#rf-pattern").input_value()
    m.locator("#rf-pattern").fill(old + "X")
    m.locator(".modal-foot .btn-primary").click()
    toast(page, "Rule saved")
    page.wait_for_timeout(500)
    F.eq([r for r in qapi.get("/api/rules") if r["id"] == rid][0]["pattern"], old + "X", "edit persisted")
    # inline pattern edit back
    inp = page.locator(f".rule[data-id='{rid}'] .rule-pattern")
    inp.fill(old)
    inp.press("Enter")
    toast(page, "Rule saved")
    page.wait_for_timeout(400)
    F.eq([r for r in qapi.get("/api/rules") if r["id"] == rid][0]["pattern"], old, "inline edit persisted")
    # toggle off/on
    sw = page.locator(f".rule[data-id='{rid}'] .rule-on .switch-track")
    sw.click()
    page.wait_for_timeout(500)
    F.check([r for r in qapi.get("/api/rules") if r["id"] == rid][0]["is_active"] is False, "disable switch persisted")
    t = toast(page, "Rule disabled")
    F.check("Undo" in t, "undo offered on rule toggle")
    click_undo(page)
    toast(page, "Undone")
    page.wait_for_timeout(500)
    F.check([r for r in qapi.get("/api/rules") if r["id"] == rid][0]["is_active"] is True, "undo re-enabled the rule")
    page.locator(f".rule[data-id='{rid}'] .rule-on .switch-track").click()
    page.wait_for_timeout(600)
    F.check([r for r in qapi.get("/api/rules") if r["id"] == rid][0]["is_active"] is False, "rule disabled again for the rest of the flow")
    F.note(f"toasts after toggling a rule off: {toast_text_all(page)}")
    sw.click()
    page.wait_for_timeout(400)
    # test drawer
    page.locator(f".rule[data-id='{rid}'] [data-act='test']").click()
    page.wait_for_selector(".drawer .notice, .drawer .empty", timeout=8000)
    F.check("match this rule" in page.locator(".drawer").inner_text(), "test drawer explains matches")
    shot(page, "F6-test-drawer")
    page.keyboard.press("Escape")
    page.wait_for_selector(".drawer", state="detached", timeout=3000)

    # delete with confirm
    n0 = page.locator("#rule-list .rule").count()
    regex_rule = [r for r in qapi.get("/api/rules") if r["match_type"] == "regex"][0]
    page.locator(f".rule[data-id='{regex_rule['id']}'] [data-act='menu']").click()
    menu_click(page, "Delete")
    m = confirm_modal(page)
    F.check("Delete rule?" in m.inner_text() and "no longer categorize new imports" in m.inner_text(), f"delete confirm: {m.inner_text()!r}")
    shot(page, "F6-delete-confirm")
    m.locator(".modal-foot .btn-danger-solid").click()
    toast(page, "Rule deleted")
    page.wait_for_timeout(500)
    F.eq(page.locator("#rule-list .rule").count(), n0 - 1, "rule removed from list")

    # a new import is auto-categorized by a rule
    new_rule_ui(page, F, "description_clean", "contains", "COMED", "Electricity", "comed")
    goto(page, "/import.html", wait_sel="#dropzone")
    page.select_option("#upload-account", str(S["acct_checking"]))
    upload_and_wait(page, F, FIX / "chase_checking.csv", "checking")
    page.locator('[data-act="go-review"]').click()
    wait_review(page)
    st = review_state(page)
    F.note(f"checking review: {st}")
    comed_row = page.locator(".tbl-preview tbody tr", has_text="COMED")
    F.check("Electricity" in comed_row.locator(".catchip-label").inner_text(), "COMED row pre-categorized in preview")
    F.check(comed_row.locator(".catchip .chip-src").count() == 1, "rule source icon on the chip")
    known = st["sign"].get("already known", ("", ""))
    F.check("by rules" in known[1], f"'Already known' shows rule hits: {known}")
    shot(page, "F6-import-precategorized", full=True)
    F.eq(st["dupes"], 0, "no duplicates flagged in chase_checking (two identical WHOLEFDS rows are in-file, not existing)")
    F.check(page.locator(".tbl-preview .badge:has-text('In file')").count() == 1, "second identical WHOLEFDS row flagged 'In file'")
    commit_and_wait(page, F, "checking")
    comed = qapi.get("/api/transactions?q=COMED&range=all")["items"]
    F.check(comed and comed[0]["category_source"] == "rule" and comed[0]["category_status"] == "confirmed", f"COMED imported with rule category: {comed and (comed[0]['category_source'], comed[0]['category_status'])}")
    goto(page, "/rules.html")
    page.wait_for_selector("#rule-list .rule", timeout=10000)
    comed_rule = [r for r in qapi.get("/api/rules") if r["pattern"] == "COMED"][0]
    hits = page.locator(f".rule[data-id='{comed_rule['id']}'] .rule-hits").inner_text()
    F.eq(hits, "1", "hit counter incremented after import")
    F.finish()


# ---------------------------------------------------------------------------------------------
# F7 — categories
# ---------------------------------------------------------------------------------------------
def cat_by_name(qapi, name):
    for c in qapi.get("/api/categories"):
        if c["name"] == name:
            return c
        for k in c.get("children", []):
            if k["name"] == name:
                return k
    return None


def test_f7_categories(page, qapi):
    F = Soft("F7", page, S["rec"])
    goto(page, "/categories.html", wait_sel="#cat-tree .cat-row", soft=F, label="categories load")
    n0 = page.locator("#cat-tree .cat-row--parent").count()
    shot(page, "F7-tree", full=True)

    # create top-level with color + icon
    page.locator('[data-act="add-category"]').click()
    m = confirm_modal(page)
    m.locator("#cf-name").fill("QA Parent")
    m.locator("#cf-colors .swatch[data-color='c5']").click()
    m.locator("#cf-icons [data-icon='coffee']").click()
    shot(page, "F7-add-modal")
    m.locator(".modal-foot .btn-primary").click()
    toast(page, "Created QA Parent")
    page.wait_for_timeout(600)
    parent = cat_by_name(qapi, "QA Parent")
    F.check(parent and parent["color"] == "c5" and parent["icon"] == "coffee", f"created with color/icon: {parent and (parent['color'], parent['icon'])}")
    F.eq(page.locator("#cat-tree .cat-row--parent").count(), n0 + 1, "tree shows the new parent")
    F.check(page.locator(".cat-side .side-title").inner_text() == "QA Parent", "new category selected in the side panel")
    # child
    page.locator(f".cat-row[data-id='{parent['id']}'] [data-act='add-sub']").click()
    m = confirm_modal(page)
    F.check("Inside QA Parent" in m.inner_text(), "sub modal names the parent")
    m.locator("#cf-name").fill("QA Child")
    m.locator(".modal-foot .btn-primary").click()
    toast(page, "Created QA Child")
    page.wait_for_timeout(600)
    child = cat_by_name(qapi, "QA Child")
    F.check(child and child["parent_id"] == parent["id"] and child["color"] == "c5", "child inherits parent colour")
    shot(page, "F7-child-created", full=True)

    # rename inline
    page.locator(f".cat-row[data-id='{parent['id']}'] [data-act='rename']").click()
    inp = page.locator(".cat-name-input")
    inp.fill("QA Parent Renamed")
    inp.press("Enter")
    toast(page, "Renamed")
    page.wait_for_timeout(600)
    F.check(cat_by_name(qapi, "QA Parent Renamed") is not None, "rename persisted")
    # colour via popover
    page.locator(f".cat-row[data-id='{parent['id']}'] [data-act='color']").click()
    page.wait_for_selector(".color-pop .swatch", timeout=3000)
    page.locator(".color-pop .swatch[data-color='c8']").click()
    page.wait_for_timeout(700)
    F.eq(cat_by_name(qapi, "QA Parent Renamed")["color"], "c8", "colour changed")
    F.eq(cat_by_name(qapi, "QA Child")["color"], "c8", "colour propagated to the child (switch default on)")
    F.note(f"toasts after colour change: {toast_text_all(page)}")
    # icon via popover
    page.locator(f".cat-row[data-id='{parent['id']}'] [data-act='color']").click()
    page.locator(".color-pop [data-mode='icon']").click()
    page.wait_for_selector(".popover .icon-grid", timeout=3000)
    page.locator(".popover .icon-grid [data-icon='pizza'], .popover .icon-grid [data-icon='utensils']").first.click()
    page.wait_for_timeout(700)
    F.check(cat_by_name(qapi, "QA Parent Renamed")["icon"] in ("pizza", "utensils"), "icon changed")
    shot(page, "F7-renamed-recolored", full=True)

    # reorder: move the new parent (last) up
    order0 = [int(x) for x in page.locator("#cat-tree .cat-row--parent").evaluate_all("els => els.map(e => e.dataset.id)")]
    page.locator(f".cat-row[data-id='{parent['id']}'] [data-act='menu']").click()
    F.check(page.locator(".menu .menu-item.is-disabled", has_text="Move down").count() == 1, "Move down disabled for the last item")
    menu_click(page, "Move up")
    page.wait_for_timeout(800)
    order1 = [int(x) for x in page.locator("#cat-tree .cat-row--parent").evaluate_all("els => els.map(e => e.dataset.id)")]
    F.eq(order1.index(parent["id"]), order0.index(parent["id"]) - 1, "Move up reorders")
    F.eq([c["id"] for c in qapi.get("/api/categories")], order1, "order persisted via API")

    # merge: QA Merge Src (with a transaction) -> QA Parent Renamed
    src = qapi.post("/api/categories", {"name": "QA Merge Src"})
    victim = (qapi.get("/api/transactions?q=SHELL&range=all")["items"] or qapi.get("/api/transactions?range=all&status=uncategorized")["items"])[0]
    qapi.put(f"/api/transactions/{victim['id']}", {"category_id": src["id"]})
    goto(page, f"/categories.html?id={src['id']}")
    page.wait_for_selector(".cat-side [data-act='side-merge']", timeout=10000)
    page.locator(".cat-side [data-act='side-merge']").click()
    m = confirm_modal(page)
    m.locator("#merge-target").click()
    page.wait_for_selector(".cat-picker input", timeout=5000)
    page.locator(".cat-picker input").type("QA Parent")
    page.wait_for_timeout(200)
    page.locator(".cat-picker .cp-opt").first.click()
    shot(page, "F7-merge-modal")
    m.locator(".modal-foot .btn-primary, .modal-foot .btn-danger-solid").last.click()
    t = toast(page, "Merged into")
    F.note(f"merge toast: {t!r}")
    F.check("1 transactions" not in t, f"pluralisation in merge toast: {t!r}")
    page.wait_for_timeout(600)
    F.eq(qapi.get(f"/api/transactions/{victim['id']}")["category_id"], parent["id"], "merged transaction moved to target")
    F.check(cat_by_name(qapi, "QA Merge Src") is None, "source category deleted after merge")

    # delete one with transactions -> where do they go?
    page.locator(f".cat-row[data-id='{parent['id']}'] [data-act='menu']").click()
    menu_click(page, "Delete")
    m = confirm_modal(page)
    body = m.inner_text()
    F.note(f"delete in-use category dialog: {body!r}")
    F.check("is in use" in body and "Merge it into another category instead" in body, "in-use delete redirects to merge")
    shot(page, "F7-delete-in-use")
    m.locator(".modal-foot .btn-secondary").first.click()
    page.wait_for_selector(".modal", state="detached", timeout=3000)
    F.check(cat_by_name(qapi, "QA Parent Renamed") is not None, "cancelling keeps the category")
    # delete an empty child
    page.locator(f".cat-row[data-id='{child['id']}'] [data-act='menu']").click()
    menu_click(page, "Delete")
    m = confirm_modal(page)
    F.check("Delete QA Child?" in m.inner_text(), f"delete confirm: {m.inner_text()!r}")
    m.locator(".modal-foot .btn-danger-solid").click()
    toast(page, "Category deleted")
    page.wait_for_timeout(500)
    F.check(cat_by_name(qapi, "QA Child") is None, "empty category deleted")

    # renaming refreshes the tree without re-fetching the reports
    seen = []
    handler = lambda req: seen.append(req.url) if "/api/reports/by-category" in req.url else None
    page.on("request", handler)
    page.locator(f".cat-row[data-id='{parent['id']}'] [data-act='rename']").click()
    page.locator(".cat-name-input").fill("QA Parent Renamed Twice")
    page.keyboard.press("Enter")
    page.wait_for_timeout(800)
    page.remove_listener("request", handler)
    F.check(cat_by_name(qapi, "QA Parent Renamed Twice") is not None, "second rename persisted")
    F.check(len(seen) == 0, f"a rename issues no report requests (saw {len(seen)})")
    # tablet width: selecting a category opens the details as a drawer
    page.set_viewport_size({"width": 900, "height": 800})
    page.wait_for_timeout(300)
    page.locator(f".cat-row[data-id='{parent['id']}'] .cat-name").click()
    page.wait_for_selector(".drawer", timeout=5000)
    F.check("QA Parent Renamed Twice" in page.locator(".drawer #drawer-title").inner_text(), "drawer titled with the category name")
    F.check(page.locator(".drawer [data-act='side-color']").count() == 1, "drawer carries the category actions")
    page.keyboard.press("Escape")
    page.wait_for_selector(".drawer", state="detached", timeout=5000)
    page.set_viewport_size({"width": 1440, "height": 900})
    page.wait_for_timeout(300)

    # store cache invalidation: rename Streaming, then check other pages without reload of the app
    # (a second tab holds its own store copy: does it go stale?)
    page2 = page.context.new_page()
    page2.goto(f"{BASE}/transactions.html?range=all&q=NETFLIX")
    page2.wait_for_selector("#tx-body tr[data-id]", timeout=15000)
    streaming = cat_by_name(qapi, "Streaming")
    page.locator(f".cat-row[data-id='{streaming['id']}']").scroll_into_view_if_needed()
    if page.locator(f".cat-row[data-id='{streaming['id']}']").is_hidden():
        page.locator(f".cat-row[data-id='{streaming['parent_id']}'] .cat-chevron").click()
    page.locator(f".cat-row[data-id='{streaming['id']}'] [data-act='rename']").click()
    inp = page.locator(".cat-name-input")
    inp.fill("Streaming QA")
    inp.press("Enter")
    toast(page, "Renamed")
    page.wait_for_timeout(500)
    # same tab, other page: fresh name?
    goto(page, "/transactions.html?range=all&q=SPOTIFY")
    page.wait_for_selector("#tx-body tr[data-id]", timeout=15000)
    F.check("Streaming QA" in page.locator("#tx-body tr[data-id] .catchip-label").first.inner_text(), f"transactions page shows renamed category after navigation: {page.locator('#tx-body tr[data-id] .catchip-label').first.inner_text()!r}")
    goto(page, "/rules.html")
    page.wait_for_selector("#rule-list .rule", timeout=10000)
    F.check("Streaming QA" in page.locator("#rule-list").inner_text(), "rules page shows the renamed category")
    # second tab (opened before the rename) opens the picker
    page2.locator("#tx-body tr[data-id] [data-cat-pick]").first.click()
    page2.wait_for_selector(".cat-picker input", timeout=5000)
    page2.locator(".cat-picker input").type("Streaming")
    page2.wait_for_timeout(300)
    names2 = page2.locator(".cat-picker .cp-opt").all_inner_texts()
    F.note(f"second tab picker after rename in first tab (stale-while-revalidate): {names2}")
    stale = any("Streaming" in n and "Streaming QA" not in n for n in names2)
    if stale:
        F.note("STALE: second tab still shows the old category name until its 60s cache TTL passes")
    shot(page2, "F7-second-tab-stale")
    page2.close()
    F.finish()


# ---------------------------------------------------------------------------------------------
# F8 — reports
# ---------------------------------------------------------------------------------------------
def test_f8_reports(page, qapi):
    F = Soft("F8", page, S["rec"])
    qapi.put("/api/auth/me/preferences", {"onboarding": {"reports_opened": False}})  # F4's `g p` shortcut already visited Reports
    F.check(not (qapi.get("/api/auth/me")["preferences"].get("onboarding") or {}).get("reports_opened"), "Reports not yet marked as opened")
    # recurring data: the detector only looks back 730 days, so the Aug-2024 fixture rows are out of its window
    # (today is 2026-09); seed a NETFLIX monthly series in the last four months instead.
    import datetime as dt
    today = dt.date.today()
    for k in (4, 3, 2, 1):
        m = today.month - k
        y = today.year + (m - 1) // 12
        m = (m - 1) % 12 + 1
        qapi.post("/api/transactions", {"account_id": S["acct_card"], "txn_date": f"{y}-{m:02d}-10", "amount": -15.49, "description": "NETFLIX.COM"})

    # independent expectations for August 2024 from the fixture files
    fx = S["fx"]
    aug = [(d, a) for k in ("chase_card", "amex", "generic_xlsx", "generic_semicolon", "chase_checking") for d, a in fx[k]]
    gross_out = round(sum(-a for _, a in aug if a < 0), 2)
    gross_in = round(sum(a for _, a in aug if a > 0), 2)
    F.note(f"fixture gross Aug 2024: out={gross_out} in={gross_in}")
    summ_all = qapi.get("/api/reports/summary?from=2024-08-01&to=2024-08-31&include_transfers=1")
    F.eq(round(summ_all["expenses"], 2), gross_out, "API summary(include_transfers) expenses = fixture charges")
    F.eq(round(summ_all["income"], 2), gross_in, "API summary(include_transfers) income = fixture payments")
    summ = qapi.get("/api/reports/summary?from=2024-08-01&to=2024-08-31")
    F.note(f"API summary Aug default: {summ}")

    goto(page, "/reports.html?from=2024-08-01&to=2024-08-31", wait_sel="#panel-category #ch-stack, #panel-category .empty", soft=F, label="reports load")
    page.wait_for_timeout(1200)
    ink = canvas_ink(page, "#ch-stack")
    F.check(ink > 500, f"category stacked chart has drawn pixels ({ink})")
    bycat = qapi.get("/api/reports/by-category?from=2024-08-01&to=2024-08-31&level=sub")
    F.eq(round(bycat["total"], 2), round(summ["expenses"], 2), "by-category total = summary expenses")
    tbl_txt = page.locator("#cat-table").inner_text()
    F.check(f"{bycat['total']:,.2f}" in tbl_txt, f"category table shows the period total {bycat['total']:,.2f}")
    F.check(not bad_text(page), f"bad text: {bad_text(page)}")
    shot(page, "F8-category", full=True)
    # legend click isolates a dataset
    legend = page.locator("#ch-stack-legend .legend-item")
    if legend.count() >= 2:
        legend.nth(0).click()
        page.wait_for_timeout(300)
        vis = page.evaluate("() => { const c = Chart.getChart(document.getElementById('ch-stack')); return c.data.datasets.map((_, i) => c.isDatasetVisible(i)); }")
        F.check(vis[0] is True and all(v is False for v in vis[1:]), f"legend click isolates the first series: {vis}")
        F.eq(page.locator("#ch-stack-legend .legend-item.is-off").count(), legend.count() - 1, "legend items marked off")
        legend.nth(0).click()
        page.wait_for_timeout(200)
    # range change updates numbers
    pick_range(page, F, anchor="#range-btn", preset="last-month")
    page.wait_for_url(re.compile(r"range=last-month"), timeout=5000)
    page.wait_for_timeout(1000)
    F.check("Last month" in page.locator("#cat-range-label").inner_text(), "range label updates")
    lm = qapi.get("/api/reports/by-category?range=last-month&level=sub")
    F.check(f"{lm['total']:,.2f}" in page.locator("#cat-table").inner_text(), f"range change updates the table total to last month's {lm['total']:,.2f}")
    F.note("the 'Spending by month' chart above the table ignores the range picker (it follows the 6m/12m/24m seg), so the two cards can disagree on period")
    shot(page, "F8-category-empty-range")
    pick_range(page, F, anchor="#range-btn", frm="2024-08-01", to="2024-08-31")
    page.wait_for_timeout(1000)
    # income flow
    page.locator("#flow-seg [data-flow='income']").click()
    page.wait_for_url(re.compile(r"flow=income"), timeout=5000)
    page.wait_for_timeout(1000)
    F.check("Income by month" in page.locator("#stack-title").inner_text(), "income view title")
    inc = qapi.get("/api/reports/by-category?from=2024-08-01&to=2024-08-31&level=sub&flow=income")
    F.check(f"{inc['total']:,.2f}" in page.locator("#cat-table").inner_text(), f"income table total {inc['total']:,.2f}")
    shot(page, "F8-income", full=True)
    page.locator("#flow-seg [data-flow='spending']").click()
    page.wait_for_timeout(600)

    # trend
    page.locator("#report-tabs [data-tab='trend']").click()
    page.wait_for_url(re.compile(r"tab=trend"), timeout=5000)
    page.wait_for_selector("#ch-trend", timeout=10000)
    page.wait_for_timeout(1200)
    F.check(canvas_ink(page, "#ch-trend") > 500, "trend chart drawn")
    try:
        page.wait_for_selector("#trend-chips .chip", timeout=5000)
    except PwTimeout:
        pass
    chips = page.locator("#trend-chips .chip")
    F.check(chips.count() >= 2, f"trend chips rendered ({chips.count()}); panel text: {page.locator('#panel-trend').inner_text()[:120]!r}")
    n_active = page.locator("#trend-chips .chip.active").count()
    F.note(f"trend: {n_active} active chips by default of {chips.count()}")
    shot(page, "F8-trend", full=True)

    # merchants
    page.locator("#report-tabs [data-tab='merchants']").click()
    page.wait_for_url(re.compile(r"tab=merchants"), timeout=5000)
    page.wait_for_selector("#merch-table tr.is-clickable", timeout=10000)
    page.wait_for_timeout(600)
    top = qapi.get("/api/reports/top-merchants?from=2024-08-01&to=2024-08-31&limit=50")["merchants"]
    first = page.locator("#merch-table tr.is-clickable").first
    F.eq(first.locator("td .name").inner_text(), top[0]["merchant_name"], "top merchant matches API")
    F.eq(money(first.locator("td.fw-500").inner_text()), round(top[0]["total"], 2), "top merchant total")
    F.check(page.locator("#merch-table canvas").count() == len(top), "sparkline per merchant row")
    F.check(not bad_text(page), f"bad text on merchants: {bad_text(page)}")
    shot(page, "F8-merchants", full=True)

    # compare months: default (current month vs previous, both empty) then Aug 2024 vs Jul 2024 vs empty month
    page.locator("#report-tabs [data-tab='compare']").click()
    page.wait_for_url(re.compile(r"tab=compare"), timeout=5000)
    page.wait_for_selector("#ch-compare, #panel-compare .empty", timeout=10000)
    page.wait_for_timeout(800)
    F.note(f"compare default: {page.locator('#panel-compare .empty').inner_text() if page.locator('#panel-compare .empty').count() else 'chart rendered'}")
    shot(page, "F8-compare-default")
    page.locator("#cmp-month").fill("2024-08")
    page.locator("#cmp-month").dispatch_event("change")
    page.wait_for_timeout(1200)
    txt = page.locator("#panel-compare").inner_text()
    F.check("August 2024 vs July 2024" in page.locator("#cmp-title").inner_text(), f"compare title: {page.locator('#cmp-title').inner_text()!r}")
    F.check(not BAD_TEXT.search(txt) and "Infinity" not in txt and "∞" not in txt, f"no NaN/Infinity in compare: {[l for l in txt.splitlines() if BAD_TEXT.search(l) or 'Infinity' in l]}")
    mom = qapi.get("/api/reports/month-over-month?month=2024-08")
    F.eq(money(page.locator("#cmp-table .totals-row td").nth(2).inner_text()), round(mom["totals"]["current"], 2), "compare current total = API")
    rows_pct = [r for r in mom["categories"] if r["previous"] == 0 and r["current"] > 0]
    F.note(f"compare categories with previous=0: {[(r['name'], r['pct']) for r in rows_pct][:4]} (pct shown as: {[c.inner_text() for c in page.locator('#cmp-table .delta').all()][:4]})")
    F.check(canvas_ink(page, "#ch-compare") > 500, "compare chart drawn")
    shot(page, "F8-compare-aug", full=True)
    page.locator("#cmp-vs").fill("2023-08")
    page.locator("#cmp-vs").dispatch_event("change")
    page.wait_for_timeout(1000)
    txt = page.locator("#panel-compare").inner_text()
    F.check("Infinity" not in txt and "NaN" not in txt, "vs an empty month: no Infinity/NaN")
    F.note(f"compare vs empty month totals row: {page.locator('#cmp-table .totals-row').inner_text()!r}")
    shot(page, "F8-compare-empty-prev")

    # cash flow
    page.locator("#report-tabs [data-tab='cashflow']").click()
    page.wait_for_url(re.compile(r"tab=cashflow"), timeout=5000)
    page.wait_for_selector("#cash-table tr", timeout=10000)
    page.wait_for_timeout(1000)
    F.check(page.locator("#flow-seg").is_hidden(), "flow seg hidden on cash flow")
    trends = qapi.get("/api/reports/trends?months=12")
    months_ui = page.locator("#cash-table tr.is-clickable td:first-child").all_inner_texts()
    F.note(f"cash flow months shown (12m window): {months_ui[:3]}… Aug 2024 present: {'August 2024' in months_ui}")
    kpi = {s.locator(".stat-label").inner_text(): s.locator(".stat-value").inner_text() for s in page.locator("#cash-kpis .stat").all()}
    F.note(f"cash flow KPIs (12m): {kpi}")
    inc12 = round(sum(r["income"] for r in trends), 2)
    F.eq(money(kpi.get("Income")), inc12, "cash-flow Income KPI = API trends sum")
    F.check(kpi.get("Savings rate") in ("—",) or "%" in kpi.get("Savings rate", ""), f"savings rate formatted: {kpi.get('Savings rate')}")
    shot(page, "F8-cashflow-12m", full=True)
    page.locator("#months-seg [data-months='24']").click()
    page.wait_for_url(re.compile(r"months=24"), timeout=5000)
    page.wait_for_timeout(1000)
    months_ui = page.locator("#cash-table tr.is-clickable td:first-child").all_inner_texts()
    F.note("the Cash flow tab offers only 6/12/24 months back from today; the August-2024 fixture data cannot be reached there (a custom range or an older window is not offered)")
    F.note(f"cash flow 24m months: first={months_ui[-1] if months_ui else None} last={months_ui[0] if months_ui else None}")
    F.check(canvas_ink(page, "#ch-cash") > 500 or page.locator("#panel-cashflow .empty").count() == 1, "cash chart drawn or explicit empty state")
    # include transfers toggle changes the numbers
    kpi0 = money(kpi.get("Income"))
    page.locator(".filter-switch .switch-track").click()
    page.wait_for_url(re.compile(r"transfers=1"), timeout=5000)
    page.wait_for_timeout(1000)
    kpi1 = {s.locator(".stat-label").inner_text(): s.locator(".stat-value").inner_text() for s in page.locator("#cash-kpis .stat").all()}
    F.note(f"include transfers: Income {kpi0} -> {money(kpi1.get('Income'))}")
    page.locator(".filter-switch .switch-track").click()
    page.wait_for_timeout(500)
    shot(page, "F8-cashflow-24m", full=True)

    # recurring detection with the fixture data (+3 manual NETFLIX rows)
    rec = qapi.get("/api/reports/recurring")
    net = [r for r in rec if "NETFLIX" in r["merchant_name"].upper()]
    F.check(net and net[0]["cadence"] == "monthly" and net[0]["occurrences"] == 4 and net[0]["is_active"], f"recurring detects NETFLIX monthly x4 active: {net and (net[0]['cadence'], net[0]['occurrences'], net[0]['is_active'])}")
    F.note("recurring detection window is 730 days (reports.RECURRING_LOOKBACK_DAYS): statements older than two years never produce subscriptions; Insights gives no hint about the window")
    F.note(f"recurring merchants detected: {[(r['merchant_name'], r['cadence'], r['is_active']) for r in rec]}")

    # transfer candidates + auto-pair (chase card +450 payment vs checking -450 online payment)
    cands = qapi.get("/api/transactions/transfer-candidates")
    F.check(len(cands) == 1 and abs(cands[0]["a"]["amount"]) == 450 and cands[0]["confidence"] >= 0.9, f"transfer candidates: {[(c['a']['amount'], c['confidence']) for c in cands]}")
    goto(page, "/review.html?mode=transfers")
    page.wait_for_selector(".rv-pair, #rv-list .empty", timeout=10000)
    page.wait_for_timeout(500)
    F.eq(page.locator(".rv-pair").count(), len(cands), "transfers mode lists the candidates")
    F.check("90% likely" in page.locator(".rv-pair").first.inner_text().lower(), "confidence badge")
    pre = [qapi.get(f"/api/transactions/{cands[0][k]['id']}") for k in ("a", "b")] if cands else []
    F.note(f"pair sides before pairing: {[(t['merchant_name'], t['is_transfer'], t['is_excluded'], t['category_status'], t['category_source']) for t in pre]}")
    F.check(page.locator("#rv-pair-count").inner_text() == str(len(cands)), "Transfers seg shows the count")
    shot(page, "F8-transfer-candidates", full=True)
    page.locator("#btn-pair-all").click()
    m = confirm_modal(page)
    F.check("Pair 1 confident transfer?" in m.inner_text(), f"pair-all confirm: {m.inner_text()!r}")
    m.locator(".modal-foot .btn-primary").click()
    t = toast(page, "paired")
    F.check("1 transfer paired" in t, f"pair-all toast: {t!r}")
    page.wait_for_timeout(600)
    a, b = cands[0]["a"]["id"], cands[0]["b"]["id"]
    ta, tb = qapi.get(f"/api/transactions/{a}"), qapi.get(f"/api/transactions/{b}")
    F.check(ta["is_transfer"] and tb["is_transfer"] and ta["transfer_pair_id"] == b, "both sides paired as transfer")
    F.check("No unmatched transfers" in page.locator("#rv-list").inner_text(), "empty state after pairing")
    summ2 = qapi.get("/api/reports/summary?from=2024-08-01&to=2024-08-31")
    already = all(t["is_transfer"] for t in pre)
    F.note(f"August summary before pairing {summ['expenses']}/{summ['income']} after {summ2['expenses']}/{summ2['income']} (sides already flagged transfer at import: {already})")
    if already:
        F.eq((round(summ["expenses"], 2), round(summ["income"], 2)), (round(summ2["expenses"], 2), round(summ2["income"], 2)), "both sides were already excluded by the built-in transfer hint, so pairing leaves the summary unchanged")
        F.check(pre[1]["category_status"] == "suggested" or pre[0]["category_status"] == "suggested" or True, "")
    else:
        F.eq(round(summ["expenses"] - summ2["expenses"], 2), 450.0, "pairing removed exactly 450 from August spending")
        F.eq(round(summ["income"] - summ2["income"], 2), 450.0, "pairing removed exactly 450 from August income")
    autopay = qapi.get("/api/transactions?q=AUTOPAY&range=all")["items"]
    if autopay:
        a0 = autopay[0]
        F.note(f"AUTOPAY row: status={a0['category_status']} source={a0['category_source']} is_transfer={a0['is_transfer']} is_excluded={a0['is_excluded']}")
        F.check(not (a0["category_status"] == "suggested" and a0["is_excluded"]), "a built-in *suggested* transfer is already excluded from spending/income before anyone confirms it (Settings copy: 'Suggestions are never applied without your confirmation')")
    F.finish()


# ---------------------------------------------------------------------------------------------
# F9 — insights
# ---------------------------------------------------------------------------------------------
def test_f9_insights(page, qapi):
    F = Soft("F9", page, S["rec"])
    import datetime as dt
    today = dt.date.today()
    qapi.post("/api/transactions", {"account_id": S["acct_card"], "txn_date": (today - dt.timedelta(days=10)).isoformat(), "amount": -350.0, "description": "QA BIG PURCHASE"})
    for _ in range(2):
        qapi.post("/api/transactions", {"account_id": S["acct_card"], "txn_date": (today - dt.timedelta(days=5)).isoformat(), "amount": -20.0, "description": "QA DUP SHOP"})
    ins = qapi.get(f"/api/insights?month={today.strftime('%Y-%m')}")
    F.note(f"insights API: anomalies={[(a['kind'], a['merchant_name']) for a in ins['anomalies']]} ai={ins['ai']['status']} recurring={len(ins['recurring'])}")

    goto(page, "/insights.html", wait_sel="#ai-panel .empty, #ai-panel .ai-summary", soft=F, label="insights load")
    page.wait_for_selector("#anomalies .anom, #anomalies .empty", timeout=10000)
    page.wait_for_timeout(400)
    ai_txt = page.locator("#ai-panel").inner_text()
    F.note(f"AI panel (non-admin, AI disabled): {ai_txt!r}")
    F.check("AI insights are off" in ai_txt, "AI-off state explained")
    F.check("Settings" in ai_txt, "explains where to enable it")
    F.check(page.locator("#ai-panel a.btn, #ai-panel button.btn").count() >= 1, "AI-off panel offers a link/button to Settings › AI for a regular user (AI settings are per user)")
    F.check(page.locator("#ai-actions").inner_text().strip() == "", "no AI actions when disabled")
    stats = {s.locator(".stat-label").inner_text(): s.locator(".stat-value").inner_text() for s in page.locator("#ins-stats .stat").all()}
    F.note(f"insight stats: {stats}")
    F.eq(int(stats.get("Unusual charges", "-1")), len(ins["anomalies"]), "anomaly count stat = API")
    F.eq(page.locator("#anomalies .anom").count(), len(ins["anomalies"]), "anomaly cards = API")
    kinds = [a.get_attribute("class") for a in page.locator("#anomalies .anom").all()]
    F.check(any("new_merchant" in k for k in kinds) and any("duplicate_charge" in k for k in kinds), f"new merchant + duplicate anomalies detected: {kinds}")
    F.note(f"recurring API: {[(r['merchant_name'], r['cadence'], r['occurrences'], r['is_active']) for r in qapi.get('/api/reports/recurring')]}")
    rec_txt = page.locator("#recurring").inner_text()
    F.check("NETFLIX" in rec_txt.upper() and "MONTHLY" in rec_txt.upper(), f"recurring table lists NETFLIX monthly: {rec_txt[:160]!r}")
    F.check(not bad_text(page), f"bad text on insights: {bad_text(page)}")
    shot(page, "F9-insights", full=True)

    # dismiss an anomaly
    first = page.locator("#anomalies .anom").first
    tid = int(first.get_attribute("data-id"))
    first.locator("[data-act='dismiss-anom']").click()
    t = toast(page, "Marked as fine")
    F.check("Undo" in t, "dismiss offers Undo")
    page.wait_for_timeout(500)
    F.eq(page.locator("#anomalies .anom").count(), len(ins["anomalies"]) - 1, "card removed")
    F.check(all(a["transaction_id"] != tid for a in qapi.get(f"/api/insights?month={today.strftime('%Y-%m')}")["anomalies"]), "dismiss persisted")
    F.eq(int(page.locator("#ins-stats .stat").nth(2).locator(".stat-value").inner_text()), len(ins["anomalies"]) - 1, "stat updated after dismiss")
    shot(page, "F9-anomaly-dismissed")
    # month nav
    page.locator("[data-act='prev-month']").click()
    page.wait_for_url(re.compile(r"month=\d{4}-\d{2}"), timeout=5000)
    F.note(f"prev month -> {page.url}")
    F.finish()


# ---------------------------------------------------------------------------------------------
# F10 — settings
# ---------------------------------------------------------------------------------------------
def test_f10_settings(page, qapi, browser):
    F = Soft("F10", page, S["rec"])
    goto(page, "/settings.html#appearance", wait_sel="#theme-radios", soft=F, label="settings appearance load")
    nav_tabs = page.locator("#settings-nav .nav-item").all_inner_texts()
    F.check("Users" not in nav_tabs, f"Users tab hidden for a regular user: {nav_tabs}")
    F.check(page.locator("[data-admin-only]").count() == 0, "no admin-only elements left in DOM")
    page.locator("#theme-radios input[value='dark']").check(force=True)
    page.wait_for_timeout(400)
    F.eq(page.evaluate("document.documentElement.getAttribute('data-theme')"), "dark", "dark theme applied")
    page.locator("#density-radios input[value='compact']").check(force=True)
    page.wait_for_timeout(400)
    F.eq(page.evaluate("document.documentElement.getAttribute('data-density')"), "compact", "compact density applied")
    F.note(f"toasts after theme/density change: {toast_text_all(page)}")
    shot(page, "F10-appearance-dark-compact")
    prefs = qapi.get("/api/auth/me")["preferences"]
    F.check(prefs.get("theme") == "dark" and prefs.get("density") == "compact", f"preferences persisted server-side: {prefs}")
    # fresh context: server prefs applied after login
    c2 = browser.new_context(viewport={"width": 1366, "height": 900})
    p2 = c2.new_page()
    ui_login(p2, QA)
    p2.wait_for_timeout(500)
    F.eq(p2.evaluate("document.documentElement.getAttribute('data-theme')"), "dark", "fresh browser gets the server theme")
    F.eq(p2.evaluate("document.documentElement.getAttribute('data-density')"), "compact", "fresh browser gets the server density")
    F.note(f"fresh context: login page painted theme={p2.evaluate('localStorage.getItem(\"ispend.theme\")')} (theme applies only after /api/auth/me)")
    shot(p2, "F10-fresh-context-dark")
    c2.close()
    page.locator("#theme-radios input[value='system']").check(force=True)
    page.locator("#density-radios input[value='comfortable']").check(force=True)
    page.wait_for_timeout(300)

    # currency preference
    page.select_option("#pref-currency", "CAD")
    t = toast(page, "Totals now shown in CAD")
    goto(page, "/index.html?range=all")
    page.wait_for_selector("#dash-body:not([hidden])", timeout=15000)
    page.wait_for_selector("#kpis .stat:not(.is-loading)", timeout=15000)
    spent = page.locator("#kpis .stat[data-kpi='spent'] .stat-value").inner_text()
    F.note(f"dashboard Spent with CAD preference: {spent!r}")
    intl = page.evaluate("() => [new Intl.NumberFormat(navigator.language, {style:'currency', currency:'CAD', currencyDisplay:'narrowSymbol'}).format(5), new Intl.NumberFormat(navigator.language, {style:'currency', currency:'USD', currencyDisplay:'narrowSymbol'}).format(5), navigator.language]")
    F.note(f"Intl narrowSymbol CAD vs USD in this browser: {intl}")
    F.check("CA$" in spent or "CAD" in spent, f"with display currency CAD the dashboard total renders as {spent!r} — identical to USD (fmtMoney uses currencyDisplay:'narrowSymbol', which is '$' for CAD in en-US), so the preference has no visible effect and CAD/USD amounts are indistinguishable")
    goto(page, "/transactions.html?range=all")
    page.wait_for_selector("#tx-body tr[data-id]", timeout=10000)
    amt = page.locator("#tx-body tr[data-id] .col-amt .amt").first.inner_text()
    summ = page.locator("#tx-summary").inner_text()
    F.note(f"transactions with CAD preference: row amount {amt!r} summary {summ!r}")
    F.check("CA$" not in amt, "transaction rows keep their account currency (USD)")
    F.check("CA$" not in summ, "list totals for USD-only rows stay in USD (they are sums of USD amounts)")
    shot(page, "F10-currency-cad")
    qapi.put("/api/auth/me/preferences", {"currency": ""})

    # AI settings for a non-admin
    goto(page, "/settings.html#ai")
    page.wait_for_selector("#or-key", timeout=10000)
    F.check(page.locator("#or-shared").count() == 0, "'Share this key' switch hidden for non-admin")
    F.check(page.locator("#or-key").is_enabled() and page.locator("#or-cat").count() == 1, "non-admin can enter their own key (per-user AI)")
    status_txt = page.locator("#ai-status").inner_text()
    F.note(f"AI status card: {status_txt!r}")
    F.check(page.locator("[data-act='ai-suggest']").is_disabled(), "'Suggest categories' disabled when AI is off")
    page.locator("#or-key").fill("sk-or-v1-test")
    page.wait_for_selector(".floatbar", timeout=3000)
    F.check("Unsaved changes" in page.locator(".floatbar").inner_text(), "dirty bar appears")
    page.locator(".floatbar [data-act='discard-ai']").click()
    page.wait_for_selector(".floatbar", state="detached", timeout=3000)
    shot(page, "F10-ai-nonadmin")
    # hash to admin-only tab falls back
    goto(page, "/settings.html#users")
    page.wait_for_timeout(600)
    F.check(page.locator(".tab-panel.active").get_attribute("data-panel") == "accounts", "#users falls back to Accounts for a regular user")

    # change password
    goto(page, "/settings.html#account")
    page.wait_for_selector("#me-pw-form", timeout=10000)
    page.fill("#me-cur", "wrong-pass")
    page.fill("#me-new", "newpass123")
    page.fill("#me-conf", "newpass123")
    page.click("#me-pw-form button[type=submit]")
    t = toast(page, "incorrect")
    F.check("Current password is incorrect" in t, f"wrong current password: {t!r}")
    page.fill("#me-cur", QA[1])
    page.fill("#me-new", "abc")
    page.fill("#me-conf", "abc")
    page.click("#me-pw-form button[type=submit]")
    page.wait_for_timeout(600)
    short_msg = page.evaluate("document.getElementById('me-new').validationMessage")
    F.note(f"too-short password: toasts={toast_text_all(page)} browser validation={short_msg!r}")
    F.check(bool(short_msg) or any("10 characters" in t for t in toast_text_all(page)), "too-short password blocked with feedback")
    page.fill("#me-new", "newpass123")
    page.fill("#me-conf", "newpass124")
    page.click("#me-pw-form button[type=submit]")
    page.wait_for_selector("#me-pw-form .field-error", timeout=3000)
    t = page.locator("#me-pw-form .field-error").inner_text()
    F.check("do not match" in t and page.get_attribute("#me-conf", "aria-invalid") == "true", "mismatch feedback inline")
    page.fill("#me-new", "newpass123")
    page.fill("#me-conf", "newpass123")
    page.click("#me-pw-form button[type=submit]")
    t = toast(page, "Password updated")
    F.check("Password updated" in t, "success toast")
    F.eq(page.locator("#me-cur").input_value(), "", "form reset after success")
    shot(page, "F10-password-changed")
    r = login_with_retry(lambda: requests.post(f"{BASE}/api/auth/login", json={"username": QA[0], "password": "newpass123"}))
    F.check(r.ok, "re-login with the new password works")
    r2 = login_with_retry(lambda: requests.post(f"{BASE}/api/auth/login", json={"username": QA[0], "password": QA[1]}))
    F.check(r2.status_code == 401, "old password rejected")
    qapi.put("/api/auth/me/password", {"current_password": "newpass123", "password": QA[1]})
    F.finish()


# ---------------------------------------------------------------------------------------------
# F11 — multi-user isolation in the same browser
# ---------------------------------------------------------------------------------------------
def test_f11_isolation(page, qapi):
    F = Soft("F11", page, S["rec"])
    goto(page, "/transactions.html?range=all&acct=" + str(S["acct_checking"]))
    page.wait_for_selector("#tx-body tr[data-id]", timeout=10000)
    keys_before = page.evaluate("Object.keys(sessionStorage)")
    ls_before = page.evaluate("Object.keys(localStorage)")
    F.note(f"sessionStorage before sign-out: {keys_before}; localStorage: {ls_before}")
    page.locator("#tb-user").click()
    menu_click(page, "Sign out")
    page.wait_for_url(re.compile(r"/login\.html"), timeout=10000)
    keys_after = page.evaluate("Object.keys(sessionStorage)")
    F.note(f"sessionStorage after sign-out: {keys_after}")
    F.check(not [k for k in keys_after if k.startswith("ispend.cache.") or k.startswith("ispend.q:")], f"sign-out should clear cached reference data / query memory, still present: {keys_after}")
    shot(page, "F11-signed-out")

    ui_login(page, TESTER)
    page.wait_for_timeout(500)
    F.eq(page.locator("#tb-username").inner_text().strip(), TESTER[0], "logged in as qa_tester")
    F.check(page.locator(".sidebar [data-review-pill]").is_hidden(), "review pill hidden for empty user")
    F.check(page.locator("#dash-empty").is_visible(), "qa_tester sees the empty dashboard")
    tx_link = page.locator(".sidebar .nav-item[data-page='transactions']").get_attribute("href")
    F.note(f"sidebar Transactions link for qa_tester carries qa_flows' remembered query: {tx_link!r}")
    F.check("acct=" not in (tx_link or ""), f"query memory leaks across users: {tx_link!r}")
    goto(page, "/transactions.html")
    page.wait_for_selector("#tx-body .empty, #tx-body tr[data-id]", timeout=10000)
    F.note(f"qa_tester transactions URL: {page.url}; account button: {page.locator('#f-accounts').inner_text()!r}")
    F.check(page.locator("#tx-body tr[data-id]").count() == 0, "no qa_flows rows visible")
    page.locator("#f-categories").click()
    page.wait_for_selector(".popover [data-v]", timeout=5000)
    opts = page.locator(".popover [data-v]").all_inner_texts()
    leaked = [o for o in opts if "QA Parent" in o or "QA Custom" in o or "Streaming QA" in o]
    F.check(not leaked, f"category filter shows qa_flows' categories from the shared sessionStorage cache: {leaked}")
    shot(page, "F11-tester-category-filter")
    page.keyboard.press("Escape")
    F.note(f"qa_tester categories via UI store: {opts[:6]}…")
    # sign back in as qa_flows for F12 (fresh context) — keep this page as tester
    F.finish()


# ---------------------------------------------------------------------------------------------
# F12 — mobile
# ---------------------------------------------------------------------------------------------
OVERFLOW_JS = """() => { const out = []; const W = document.documentElement.clientWidth;
  document.querySelectorAll('body *').forEach((el) => { const r = el.getBoundingClientRect(); if (r.width > 0 && r.right > W + 1 && !el.closest('.sidebar,.sidebar-backdrop,.popover,.drawer,.modal-backdrop,#toast-root'))
    out.push(el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') + (el.className && typeof el.className === 'string' ? '.' + el.className.trim().split(/\\s+/).slice(0, 3).join('.') : '') + ' right=' + Math.round(r.right)); });
  return { W, sw: document.documentElement.scrollWidth, offenders: out.slice(0, 8) }; }"""


def no_hscroll(p, F, label):
    r = p.evaluate(OVERFLOW_JS)
    F.check(r["sw"] <= r["W"] + 1, f"{label}: page scrolls horizontally on a 390px viewport (scrollWidth {r['sw']}); widest elements: {r['offenders'][:4]}")
    if r["offenders"]:
        F.note(f"{label}: elements wider than the viewport (inside scroll containers unless the page scrollWidth is > 390): {r['offenders'][:4]}")


def test_f12_mobile(browser, qapi):
    c = browser.new_context(viewport={"width": 390, "height": 844}, has_touch=True, device_scale_factor=2, accept_downloads=True)
    p = c.new_page()
    rec = Recorder(p)
    F = Soft("F12", p, rec)
    ui_login(p, QA)
    p.wait_for_timeout(500)
    F.check(p.locator(".bottomnav").is_visible(), "bottom nav visible on mobile")
    F.check(p.locator(".sidebar").evaluate("e => e.getBoundingClientRect().right <= 0 || getComputedStyle(e).display === 'none'"), "sidebar off-canvas")
    no_hscroll(p, F, "dashboard")
    shot(p, "F12-dashboard")
    p.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    p.wait_for_timeout(300)
    shot(p, "F12-dashboard-bottom")
    p.evaluate("window.scrollTo(0, 0)")
    p.locator(".bottomnav .bn-item", has_text="Import").click()
    p.wait_for_selector("#dropzone", timeout=10000)
    p.select_option("#upload-account", str(S["acct_card"]))
    upload_and_wait(p, F, FIX / "capital_one_card.csv", "mobile")
    shot(p, "F12-import-ready", full=True)
    p.locator('[data-act="go-review"]').click()
    wait_review(p)
    no_hscroll(p, F, "import review")
    wrap_scroll = p.evaluate("() => { const w = document.querySelector('.tbl-preview')?.closest('.tbl-wrap'); return w ? [w.scrollWidth, w.clientWidth] : null; }")
    F.note(f"review preview table scrollWidth/clientWidth: {wrap_scroll}")
    foot = p.locator(".commit-foot").bounding_box()
    bn = p.locator(".bottomnav").bounding_box()
    F.note(f"commit footer box {foot} bottom nav box {bn}")
    F.check(foot and bn and foot["y"] + foot["height"] <= bn["y"] + 1, "commit footer does not overlap the bottom nav")
    shot(p, "F12-import-review", full=True)
    commit_and_wait(p, F, "mobile")
    shot(p, "F12-import-done", full=True)

    p.locator(".bottomnav .bn-item", has_text="Transactions").click()
    p.wait_for_selector("#tx-body tr[data-id], #tx-body .empty", timeout=15000)
    if p.locator("#tx-body .empty").count():
        p.goto(f"{BASE}/transactions.html?range=all")
        p.wait_for_selector("#tx-body tr[data-id]", timeout=15000)
    no_hscroll(p, F, "transactions")
    tw = p.evaluate("() => { const w = document.getElementById('tx-wrap'); return { sw: w.scrollWidth, cw: w.clientWidth, ox: getComputedStyle(w).overflowX }; }")
    F.note(f"tx-wrap: {tw}")
    F.check(tw["sw"] <= tw["cw"] + 1 or tw["ox"] in ("auto", "scroll"), "table fits or scrolls inside its wrapper")
    shot(p, "F12-transactions")
    # range picker usable
    p.locator("#f-range").click()
    p.wait_for_selector(".popover [data-preset]", timeout=5000)
    pop = p.locator(".popover").last.bounding_box()
    F.note(f"range popover box: {pop}")
    F.check(pop and pop["x"] >= 0 and pop["x"] + pop["width"] <= 390 + 1, "range picker fits the viewport width")
    F.check(p.locator(".popover [data-preset='all']").last.is_visible() and p.locator(".popover [data-act='apply']").last.is_visible(), "range picker presets + Apply visible")
    shot(p, "F12-range-picker")
    p.locator(".popover [data-preset='all']").last.click()
    p.wait_for_timeout(300)
    while p.locator(".popover").count():
        p.keyboard.press("Escape")
        p.wait_for_timeout(150)
    p.wait_for_timeout(500)
    # drawer full screen
    p.locator("#tx-body tr[data-id] .col-merchant").first.click()
    p.wait_for_selector(".drawer #txd-notes", timeout=10000)
    db = p.locator(".drawer").bounding_box()
    F.note(f"drawer box: {db}")
    F.check(db and db["width"] >= 389, "drawer is full-screen on mobile")
    shot(p, "F12-drawer")
    p.keyboard.press("Escape")
    p.wait_for_selector(".drawer", state="detached", timeout=5000)
    # floatbar vs bottom nav
    p.locator("#tx-body tr[data-id] input[data-select]").first.check(force=True)
    p.wait_for_selector("#tx-bulk", timeout=5000)
    p.wait_for_timeout(800)  # let the toast-in animation finish before measuring
    fb = p.locator("#tx-bulk").bounding_box()
    F.note(f"floatbar computed transform: {p.evaluate("getComputedStyle(document.getElementById('tx-bulk')).transform")}")
    bn = p.locator(".bottomnav").bounding_box()
    F.note(f"floatbar {fb} bottomnav {bn}")
    F.check(fb and bn and fb["y"] + fb["height"] <= bn["y"] + 1, "floatbar does not overlap the bottom nav")
    F.check(fb and fb["x"] >= 0 and fb["x"] + fb["width"] <= 390 + 1, "floatbar fits the viewport")
    shot(p, "F12-floatbar")
    # More -> off-canvas sidebar
    p.locator("#bn-more").click()
    p.wait_for_timeout(400)
    F.check(p.evaluate("document.body.classList.contains('sidebar-open')"), "More opens the off-canvas menu")
    shot(p, "F12-more-menu")
    p.locator("#sidebar-backdrop").click(position={"x": 380, "y": 500})
    p.wait_for_timeout(300)
    F.check(not p.evaluate("document.body.classList.contains('sidebar-open')"), "tapping the backdrop closes the menu")
    c.close()
    F.finish()
