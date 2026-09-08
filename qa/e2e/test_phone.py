"""Phone-parity and shared-primitive regression checks (Playwright, chromium).

    <venv>/bin/pytest qa/e2e/test_phone.py -q -p no:cacheprovider

Uses the smoke fixtures (one context per test, API-cookie login). Admin is used read-only for
populated pages; qa_tester for anything that mutates. Tests that document a known defect are
marked xfail(strict=True) until the phase that fixes them lands, so a fix shows up as XPASS.
"""
import time

import pytest

from helpers import PAGES, wait_loaded
from smoke_fixtures import base_url, browser, make_context, pw  # noqa: F401  (pytest fixtures)
from playwright.sync_api import TimeoutError as PWTimeout

# Elements that legitimately scroll sideways on a phone (chip rows, tab strips, mapping tables, charts).
ALLOW_HSCROLL = [".tbl-toolbar .seg", ".tabs", ".file-tabs", ".settings-nav", ".filter-row", ".mapping-table",
                 "canvas", ".menu-opts", ".tx-wrap", ".rv-toolbar .seg", ".page-actions .seg"]
AMOUNT_SEL = ".amt, td.num, .stat-value, .rt-amt, .anom-amt, .cat-total, .col-total, .rec-amt .num, .inc-row .num, .bd-table .num"

INNER_OVERFLOW_JS = """
(allow) => {
  const main = document.getElementById('main') || document.body;
  const vw = document.documentElement.clientWidth;
  const vis = (el) => { const r = el.getBoundingClientRect(); const cs = getComputedStyle(el); return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none'; };
  const sel = (el) => el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') + (el.className && typeof el.className === 'string' ? '.' + el.className.trim().split(/\\s+/).slice(0, 3).join('.') : '');
  const allowed = (el) => allow.some((s) => el.matches(s) || el.closest(s));
  const out = [];
  main.querySelectorAll('*').forEach((el) => {
    if (!vis(el) || allowed(el) || el.closest('.popover, .modal, .drawer, .toast, [hidden]')) return;
    const cs = getComputedStyle(el);
    const r = el.getBoundingClientRect();
    const scrolls = (cs.overflowX === 'auto' || cs.overflowX === 'scroll') && el.scrollWidth > el.clientWidth + 1;
    const offscreen = cs.position !== 'fixed' && r.right > vw + 1;
    if (scrolls || offscreen) out.push({ el: sel(el), scrollWidth: el.scrollWidth, clientWidth: el.clientWidth, right: Math.round(r.right), vw });
  });
  return out.slice(0, 15);
}
"""

AMOUNT_WRAP_JS = """
(selector) => {
  const out = [];
  document.querySelectorAll(selector).forEach((el) => {
    const r = el.getBoundingClientRect(); if (!r.width || !r.height) return;
    const cs = getComputedStyle(el);
    const lh = parseFloat(cs.lineHeight) || parseFloat(cs.fontSize) * 1.45;
    if (r.height > lh * 1.6) out.push({ el: el.tagName.toLowerCase() + '.' + String(el.className).trim().split(/\\s+/).slice(0, 2).join('.'), text: el.textContent.trim().slice(0, 30), h: Math.round(r.height), lh: Math.round(lh) });
  });
  return out.slice(0, 15);
}
"""

GROUPS_JS = """
() => Array.from(document.querySelectorAll('#main [role=radiogroup], #main [role=tablist]'))
  .filter((g) => g.getClientRects().length && !g.hasAttribute('data-allow-none'))
  .map((g, i) => { g.setAttribute('data-qa-group', String(i)); const kids = Array.from(g.querySelectorAll('[role=radio], [role=tab]')).filter((k) => k.getClientRects().length);
    return { idx: i, id: g.id, n: kids.length, tab0: kids.filter((k) => k.getAttribute('tabindex') === '0').length }; })
"""

# Pages whose phone layout still clips a table or wraps an amount (audit A1-A7); Phase 4 removes these.
OVERFLOW_XFAIL = set()
WRAP_XFAIL = set()
KEYBOARD_XFAIL = set()


def _params(keys, xfail, reason):
    return [pytest.param(k, marks=pytest.mark.xfail(strict=True, reason=reason)) if k in xfail else k for k in keys]


@pytest.mark.parametrize("page_key", _params(sorted(PAGES), OVERFLOW_XFAIL, "phone card-mode layouts land in Phase 4"))
def test_no_inner_overflow_390(make_context, page_key):
    _, page, _ = make_context("admin", "light", "390")
    page.goto(PAGES[page_key])
    wait_loaded(page)
    bad = page.evaluate(INNER_OVERFLOW_JS, ALLOW_HSCROLL)
    assert bad == [], f"{page_key}: elements overflow sideways at 390px: {bad}"


@pytest.mark.parametrize("page_key", _params(sorted(PAGES), WRAP_XFAIL, "amount cells still wrap; fixed in Phases 2 and 4"))
def test_amounts_single_line_390(make_context, page_key):
    _, page, _ = make_context("admin", "light", "390")
    page.goto(PAGES[page_key])
    wait_loaded(page)
    wrapped = page.evaluate(AMOUNT_WRAP_JS, AMOUNT_SEL)
    assert wrapped == [], f"{page_key}: amounts wrap onto two lines: {wrapped}"


@pytest.mark.parametrize("page_key", _params(["index", "transactions", "review", "rules", "reports"], KEYBOARD_XFAIL,
                                             "hand-painted segmented controls/tabs gain arrow keys in Phase 2"))
def test_seg_tabs_keyboard(make_context, page_key):
    _, page, _ = make_context("admin", "light", "1440")
    page.goto(PAGES[page_key])
    wait_loaded(page)
    groups = page.evaluate(GROUPS_JS)
    assert groups, f"{page_key}: no radiogroup/tablist found"
    for g in groups:
        assert g["tab0"] == 1, f"{page_key} group {g['id'] or g['idx']}: expected one tabbable item, got {g['tab0']}"
        root = f"[data-qa-group='{g['idx']}']"
        page.focus(f"{root} [tabindex='0']")
        page.keyboard.press("ArrowRight")
        active = page.evaluate("() => { const a = document.activeElement; return { checked: a.getAttribute('aria-checked') || a.getAttribute('aria-selected'), inGroup: !!a.closest('[data-qa-group]') }; }")
        assert active["inGroup"] and active["checked"] == "true", f"{page_key} group {g['id'] or g['idx']}: ArrowRight did not move selection ({active})"
        page.keyboard.press("Home")
        first = page.evaluate(f"() => document.activeElement === document.querySelector(\"{root} [role=radio], {root} [role=tab]\")")
        assert first, f"{page_key} group {g['id'] or g['idx']}: Home did not focus the first item"
        page.keyboard.press("Escape")


# ---------------------------------------------------------------------------------------------
# shared primitives (Phase 1)

def test_global_error_net(make_context):
    """A rejected promise outside any try/catch surfaces as an error toast (and is still logged)."""
    _, page, rec = make_context("qa_tester", "light", "1440")
    page.goto(PAGES["index"])
    wait_loaded(page)
    page.evaluate("() => { Promise.reject(new Error('qa boom')); }")
    page.wait_for_selector(".toast-error", timeout=3000)
    assert "qa boom" in page.locator(".toast-error").first.inner_text()
    page.evaluate("() => { Promise.reject(new Error('qa boom')); }")
    page.wait_for_timeout(300)
    assert page.locator(".toast-error").count() == 1, "the same message within 5 s must not stack"
    assert any("qa boom" in c["text"] for c in rec.console), "the browser log still carries the rejection"


def test_login_expired_notice(make_context):
    """A browser that signed in before is told its session expired; a fresh one just sees the form."""
    ctx, page, _ = make_context("qa_tester", "light", "1440")
    page.goto(PAGES["index"])
    wait_loaded(page)
    ctx.clear_cookies()
    page.goto(PAGES["transactions"])
    page.wait_for_url(lambda u: "/login.html" in u, timeout=8000)
    assert "reason=expired" in page.url and "next=%2Ftransactions.html" in page.url
    assert page.locator("#login-notice").is_visible()
    assert "expired" in page.locator("#login-notice").inner_text().lower()
    assert not page.locator("#login-error").is_visible()
    _, fresh, _ = make_context("anon", "light", "1440")
    fresh.goto(PAGES["transactions"])
    fresh.wait_for_url(lambda u: "/login.html" in u, timeout=8000)
    assert "reason=" not in fresh.url
    assert not fresh.locator("#login-notice").is_visible()


def test_toast_queue(make_context):
    """More than three toasts queue instead of dropping; identical repeats bump a counter."""
    _, page, _ = make_context("qa_tester", "light", "1440")
    page.goto(PAGES["index"])
    wait_loaded(page)
    page.evaluate("() => { for (let i = 0; i < 6; i++) toast('qa toast ' + i, { duration: 900 }); }")
    assert page.locator("#toast-root .toast").count() == 3
    page.wait_for_timeout(1400)
    texts = page.locator("#toast-root .toast").all_inner_texts()
    assert any("qa toast 3" in t for t in texts), f"queued toasts never appeared: {texts}"
    page.wait_for_timeout(1400)
    assert page.locator("#toast-root .toast").count() == 0, "queued toasts should have expired by now"
    page.evaluate("() => { toast('qa same'); toast('qa same'); toast('qa same'); }")
    page.wait_for_timeout(100)
    same = page.locator("#toast-root .toast", has_text="qa same")
    assert same.count() == 1 and "×3" in same.first.inner_text()


def test_field_error_primitive(make_context):
    """ui.fieldError / ui.validate paint an inline message wired through aria-invalid + aria-describedby."""
    _, page, _ = make_context("qa_tester", "light", "1440")
    page.goto(PAGES["index"])
    wait_loaded(page)
    state = page.evaluate("""() => {
      const f = document.createElement('div'); f.className = 'field';
      f.innerHTML = '<label for="qa-in" data-required>Name</label><input class="input" id="qa-in"><div class="hint">A hint</div>';
      document.getElementById('main').appendChild(f);
      ui.linkHints(f);
      const ok = ui.validate(f, [{ sel: '#qa-in', message: 'Name is required' }]);
      const inp = f.querySelector('#qa-in');
      const box = f.querySelector('.field-error');
      const out = { ok, invalid: inp.getAttribute('aria-invalid'), described: inp.getAttribute('aria-describedby'), msg: box && box.textContent.trim(), focused: document.activeElement === inp, boxId: box && box.id, hintId: f.querySelector('.hint').id };
      inp.value = 'x';
      out.ok2 = ui.validate(f, [{ sel: '#qa-in', message: 'Name is required' }]);
      out.cleared = !f.querySelector('.field-error') && !inp.hasAttribute('aria-invalid');
      out.described2 = inp.getAttribute('aria-describedby');
      f.remove();
      return out;
    }""")
    assert state["ok"] is False and state["msg"] == "Name is required" and state["invalid"] == "true" and state["focused"]
    assert set(state["described"].split()) == {state["boxId"], state["hintId"]}
    assert state["ok2"] is True and state["cleared"] and state["described2"] == state["hintId"]


def test_tooltip_focus(make_context):
    """[data-tip] shows a role=tooltip bubble on focus and hover, linked by aria-describedby; Escape hides it."""
    _, page, _ = make_context("qa_tester", "light", "1440")
    page.goto(PAGES["index"])
    wait_loaded(page)
    page.evaluate("() => { const b = document.createElement('button'); b.id = 'qa-tip'; b.className = 'btn'; b.textContent = 'Tip'; b.setAttribute('data-tip', 'Hello tooltip'); document.getElementById('main').prepend(b); }")
    page.focus("#qa-tip")
    page.wait_for_selector("#ui-tip:visible", timeout=2000)
    assert page.locator("#ui-tip").inner_text() == "Hello tooltip"
    assert page.get_attribute("#qa-tip", "aria-describedby") == "ui-tip"
    page.keyboard.press("Escape")
    page.wait_for_timeout(100)
    assert not page.locator("#ui-tip").is_visible() and not page.get_attribute("#qa-tip", "aria-describedby")
    page.hover("#qa-tip")
    page.wait_for_selector("#ui-tip:visible", timeout=2000)
    page.mouse.move(5, 5)
    page.wait_for_timeout(200)
    assert not page.locator("#ui-tip").is_visible()
    assert page.locator(".popover").count() == 0, "the tooltip must not count as an open layer"


# ---------------------------------------------------------------------------------------------
# primitives adopted on pages (Phase 2)

def test_inline_validation(make_context):
    """Submitting the Add category modal empty paints an inline field error and keeps the modal open."""
    _, page, _ = make_context("qa_tester", "light", "1440")
    page.goto(PAGES["categories"])
    wait_loaded(page)
    page.click("[data-act='add-category']")
    page.wait_for_selector(".modal #cf-name", timeout=3000)
    page.click(".modal-foot .btn-primary")
    page.wait_for_selector(".modal .field-error", timeout=2000)
    assert page.get_attribute("#cf-name", "aria-invalid") == "true"
    assert "required" in page.locator(".modal .field-error").inner_text().lower()
    assert page.locator(".modal").count() == 1, "the modal must stay open"
    assert page.evaluate("() => document.activeElement && document.activeElement.id") == "cf-name"
    page.fill("#cf-name", "x")
    page.wait_for_timeout(50)
    assert page.locator(".modal .field-error").count() == 0, "typing clears the error"
    page.keyboard.press("Escape")


def test_async_button_disabled(make_context):
    """While a request is in flight the submit button is disabled (Enter cannot double-submit)."""
    _, page, _ = make_context("anon", "light", "1440")
    page.route("**/api/auth/login", lambda route: (time.sleep(0.7), route.continue_()))
    page.goto("/login.html")
    page.fill("#username", "qa_tester")
    page.fill("#password", "qa-tester-pass1")
    page.click("#login-btn", no_wait_after=True)
    page.wait_for_timeout(120)
    state = page.evaluate("() => { const b = document.getElementById('login-btn'); return { disabled: b.disabled, busy: b.getAttribute('aria-busy'), loading: b.classList.contains('is-loading') }; }")
    assert state == {"disabled": True, "busy": "true", "loading": True}, state
    # the login rate limit (10/min per IP) can reject this attempt when the suites run back to back
    for _ in range(6):
        try:
            page.wait_for_url(lambda u: "/login.html" not in u, timeout=10000)
            return
        except PWTimeout:
            if "too many" not in page.locator("#login-error").inner_text().lower():
                raise
            page.wait_for_timeout(10000)
            page.click("#login-btn", no_wait_after=True)
    raise AssertionError("login kept hitting the rate limit")


def test_rem_scale(make_context):
    """The type scale is rem-based: pixel-identical at default zoom, and it follows a larger root font size."""
    _, page, _ = make_context("qa_tester", "light", "1440")
    page.goto(PAGES["index"])
    wait_loaded(page)
    sizes = page.evaluate("() => { const px = (s) => parseFloat(getComputedStyle(document.querySelector(s)).fontSize); return { body: px('body'), btn: px('.btn'), th: px('.sb-group-label'), stat: px('.stat-value') }; }")
    assert sizes == {"body": 14, "btn": 13, "th": 11, "stat": 26}, sizes
    scaled = page.evaluate("() => { document.documentElement.style.fontSize = '20px'; const v = parseFloat(getComputedStyle(document.querySelector('.btn')).fontSize); document.documentElement.style.fontSize = ''; return v; }")
    assert abs(scaled - 16.25) < 0.01, scaled


# ---------------------------------------------------------------------------------------------
# phone parity (Phase 4)

def test_kpi_grid_2x2_390(make_context):
    """Stat cards sit two per row on a phone and their values stay on one line."""
    for key in ("index", "insights"):
        _, page, _ = make_context("admin", "light", "390")
        page.goto(PAGES[key])
        wait_loaded(page)
        rects = page.evaluate("() => Array.from(document.querySelectorAll('.stat-grid > .stat')).map((s) => { const r = s.getBoundingClientRect(); const v = s.querySelector('.stat-value').getBoundingClientRect(); return { top: Math.round(r.top), h: Math.round(v.height) }; })")
        assert len(rects) >= 3, f"{key}: {rects}"
        assert rects[0]["top"] == rects[1]["top"] and rects[2]["top"] > rects[0]["top"], f"{key}: cards are not 2 per row: {rects}"
        assert all(r["h"] <= 30 for r in rects), f"{key}: a stat value wraps: {rects}"


def test_card_mode_tables_390(make_context):
    """Breakdown, recurring, statements and import-preview tables become cards on a phone: no sideways scroll."""
    _, page, _ = make_context("admin", "light", "390")
    for key, table in (("index", ".bd-table"), ("insights", ".rec-table"), ("statements", ".tbl-statements")):
        page.goto(PAGES[key])
        wait_loaded(page)
        st = page.evaluate("(sel) => { const t = document.querySelector(sel); if (!t) return null; const th = t.querySelector('thead'); const w = t.closest('.tbl-wrap') || t.parentElement; return { thead: th ? getComputedStyle(th).display : 'none', sw: w.scrollWidth, cw: w.clientWidth }; }", table)
        assert st, f"{key}: {table} not rendered"
        assert st["thead"] == "none" and st["sw"] <= st["cw"] + 1, f"{key} {table}: {st}"
    ctx, page, _ = make_context("qa_tester", "light", "390")
    fixture = open("/Users/ruolez/Desktop/Dev/ispend/backend/tests/fixtures/chase_card.csv", "rb").read()
    r = ctx.request.post("/api/statements", multipart={"file": {"name": "chase_card.csv", "mimeType": "text/csv", "buffer": fixture}})
    assert r.ok, r.text()
    sid = r.json()["id"]
    try:
        for _ in range(60):
            body = ctx.request.get(f"/api/statements/{sid}").json()
            if body["status"] in ("previewed", "error"):
                break
            page.wait_for_timeout(300)
        assert body["status"] == "previewed", body.get("error_message")
        page.goto(f"/import.html?statement={sid}")
        page.wait_for_selector(".tbl-preview tr[data-row]", timeout=15000)
        st = page.evaluate("() => { const t = document.querySelector('.tbl-preview'); const w = t.closest('.tbl-wrap'); return { thead: getComputedStyle(t.querySelector('thead')).display, sw: w.scrollWidth, cw: w.clientWidth, right: Math.round(t.getBoundingClientRect().right), vw: innerWidth }; }")
        assert st["thead"] == "none" and st["sw"] <= st["cw"] + 1 and st["right"] <= st["vw"] + 1, st
    finally:
        ctx.request.delete(f"/api/statements/{sid}")


def test_bottom_sheet_pickers_390(make_context):
    """Popovers open as bottom sheets on a phone: full width, inside the viewport, presets and Apply visible."""
    _, page, _ = make_context("admin", "light", "390")
    page.goto(PAGES["transactions"])
    wait_loaded(page)
    page.click("#f-range")
    page.wait_for_selector(".popover--sheet [data-preset]", timeout=5000)
    box = page.evaluate("() => { const p = document.querySelector('.popover--sheet'); const r = p.getBoundingClientRect(); return { x: r.x, right: r.right, bottom: r.bottom, vw: innerWidth, vh: innerHeight, apply: !!p.querySelector('[data-act=apply]') && p.querySelector('[data-act=apply]').getBoundingClientRect().height > 0 }; }")
    assert box["x"] >= 0 and box["right"] <= box["vw"] + 1 and box["bottom"] <= box["vh"] + 1 and box["apply"], box
    page.keyboard.press("Escape")
    page.wait_for_selector(".popover", state="detached", timeout=3000)
