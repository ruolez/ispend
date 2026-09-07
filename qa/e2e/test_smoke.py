"""iSpend smoke-and-diagnostics suite (Playwright, chromium headless).

Run:  <venv>/bin/pytest qa/e2e/test_smoke.py -q
Results are appended to qa/reports/smoke-results.jsonl; qa/e2e/make_report.py renders the tables.
Personas: qa_tester (empty data, mutable) and admin (populated, READ-ONLY: every modal is cancelled)."""
# ruff: noqa: F811  (pytest fixtures imported from smoke_fixtures are re-bound as test parameters)
import json
import time

import pytest
from playwright.sync_api import TimeoutError as PwTimeout

from smoke_fixtures import PERSONAS, SHOTS, THEMES, VIEWPORTS, record
from smoke_fixtures import _results_sink, base_url, browser, make_context, pw  # noqa: F401  (pytest fixtures)
from helpers import (
    BUTTONS_JS, CHART_PAGES, DENY_WORDS, PAGES, close_layers, layers, perf_state, rgb, scan_text, settle,
    shell_state, unexpected_console, wait_loaded,
)

MATRIX = [(pg, persona, theme, vp) for pg in PAGES for persona in PERSONAS for theme in THEMES for vp in VIEWPORTS]
LIGHT_BG = (245, 246, 248)
DARK_BG = (12, 14, 19)

# Requests whose 4xx is the designed outcome of the probe that triggers them.
EXPECTED_4XX = ("/api/settings/openrouter/test",)


def shot(page, name):
    path = SHOTS / f"{name}.png"
    page.screenshot(path=str(path), full_page=True)
    return path.name


def collect_issues(rec, shell, text_hits, loaded, expected_4xx=()):
    issues = []
    for c in rec.console:
        issues.append(f"console.{c['type']}: {c['text'][:200]} @ {c['source']}")
    for e in rec.pageerrors:
        issues.append(f"pageerror: {e['text'][:200]}")
    for f in rec.failed:
        issues.append(f"requestfailed: {f['method']} {f['url']} ({f['failure']})")
    for h in rec.http_errors:
        if any(x in h["url"] for x in expected_4xx):
            continue
        issues.append(f"http {h['status']}: {h['method']} {h['url']}")
    if not shell["h1"]:
        issues.append("shell: no page h1")
    if not (shell["sidebar"] or shell["bottomnav"]):
        issues.append("shell: neither sidebar nor bottom nav visible")
    if shell["errorBoxes"]:
        issues.append(f"error-box visible: {shell['errorBoxes']}")
    if shell["hScroll"]:
        issues.append(f"horizontal page scroll: scrollWidth {shell['scrollWidth']} > {shell['clientWidth']}")
    for t in text_hits:
        issues.append(f"bad text [{t['kind']}] '{t['text']}' in {t['el']}")
    if loaded["stuck"]:
        issues.append(f"skeleton stuck after 8s: {loaded['stuck']}")
    elif loaded["loaded_ms"] > 5000:
        issues.append(f"skeletons took {loaded['loaded_ms']}ms to clear (>5s)")
    return issues


# ---------------------------------------------------------------- 1-3, 6: page matrix
@pytest.mark.parametrize("pg,persona,theme,vp", MATRIX, ids=[f"{a}-{b}-{c}-{d}" for a, b, c, d in MATRIX])
def test_page_matrix(make_context, pg, persona, theme, vp):
    ctx, page, rec = make_context(persona, theme, vp)
    page.goto(PAGES[pg], wait_until="domcontentloaded")
    loaded = wait_loaded(page)
    shell = shell_state(page)
    text_hits = scan_text(page)
    perf = perf_state(page)
    name = shot(page, f"smoke-{pg}-{persona}-{theme}-{vp}")
    issues = collect_issues(rec, shell, text_hits, loaded)
    exp_bg = DARK_BG if theme == "dark" else LIGHT_BG
    if rgb(shell["bg"]) != exp_bg:
        issues.append(f"theme: body bg {shell['bg']} != expected {exp_bg} for {theme}")
    if shell["theme"] != theme:
        issues.append(f"theme: html[data-theme]={shell['theme']} expected {theme}")
    if perf["chartLoaded"] and pg not in CHART_PAGES:
        issues.append("perf: Chart.js loaded on a page without charts")
    record("matrix", page=pg, persona=persona, theme=theme, vp=vp, url=page.url, status="fail" if issues else "pass",
           issues=issues, shell=shell, text_hits=text_hits, loaded=loaded, perf=perf, screenshot=name, **rec.summary())
    assert not issues, f"{pg}/{persona}/{theme}/{vp}:\n  " + "\n  ".join(issues)


@pytest.mark.parametrize("theme,vp", [(t, v) for t in THEMES for v in VIEWPORTS])
def test_login_page_anon(make_context, theme, vp):
    ctx, page, rec = make_context("anon", theme, vp)
    page.goto("/login.html", wait_until="load")
    settle(page)
    shell = page.evaluate("""() => ({ h1: (document.querySelector('h1')||{}).textContent, form: !!document.querySelector('#login-form'),
        err: getComputedStyle(document.querySelector('#login-error')).display, bg: getComputedStyle(document.body).backgroundColor,
        theme: document.documentElement.getAttribute('data-theme'), hScroll: document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
        themeBtn: (document.querySelector('#login-theme')||{}).textContent })""")
    text_hits = scan_text(page)
    perf = perf_state(page)
    name = shot(page, f"smoke-login-anon-{theme}-{vp}")
    issues = [f"console.{c['type']}: {c['text'][:200]} @ {c['source']}" for c in rec.console]
    issues += [f"pageerror: {e['text'][:200]}" for e in rec.pageerrors]
    issues += [f"requestfailed: {f['url']} ({f['failure']})" for f in rec.failed]
    issues += [f"http {h['status']}: {h['url']}" for h in rec.http_errors]
    if not shell["form"] or not shell["h1"]:
        issues.append("login form/h1 missing")
    if shell["err"] != "none":
        issues.append("login error box visible before any attempt")
    if rgb(shell["bg"]) != (DARK_BG if theme == "dark" else LIGHT_BG):
        issues.append(f"theme bg {shell['bg']} for {theme}")
    if shell["hScroll"]:
        issues.append("horizontal scroll on login")
    issues += [f"bad text '{t['text']}' in {t['el']}" for t in text_hits]
    record("matrix", page="login", persona="anon", theme=theme, vp=vp, url=page.url, status="fail" if issues else "pass",
           issues=issues, shell=shell, text_hits=text_hits, loaded={"loaded_ms": 0, "stuck": None}, perf=perf, screenshot=name, **rec.summary())
    assert not issues, "\n".join(issues)


# ---------------------------------------------------------------- 4: read-only interaction probes (admin)
PROBE_COMBOS = [(pg, "light", "1440") for pg in PAGES] + [(pg, "dark", "390") for pg in PAGES]


def step(steps, rec, label, fn):
    """Run one probe step, capture new console/network problems it caused."""
    page = rec.page
    rec.mark(label)
    snap = rec.snapshot()
    detail, ok = None, True
    t0 = time.monotonic()
    try:
        detail = fn()
    except (PwTimeout, AssertionError) as e:
        ok, detail = False, f"{type(e).__name__}: {str(e)[:300]}"
        close_layers(page)  # never let a failed step leave a layer open for the next one
    ms = round((time.monotonic() - t0) * 1000)
    d = rec.delta(snap)
    bad = {k: v for k, v in d.items() if k != "slow"}
    # expected 4xx from designed probes (the response itself and the browser's own console line about it)
    if "http_errors" in bad:
        bad["http_errors"] = [h for h in bad["http_errors"] if not any(x in h["url"] for x in EXPECTED_4XX)]
        if not bad["http_errors"]:
            del bad["http_errors"]
    if "console" in bad:
        bad["console"] = [c for c in bad["console"] if not (c["text"].startswith("Failed to load resource") and any(x in c["source"] for x in EXPECTED_4XX))]
        if not bad["console"]:
            del bad["console"]
    steps.append({"label": label, "ok": ok and not bad, "detail": detail, "problems": bad, "slow": d.get("slow", []), "ms": ms})
    return detail


def probe_buttons(page, rec, steps):
    btns = page.evaluate(BUTTONS_JS, DENY_WORDS)
    clicked, skipped = [], []
    for b in btns:
        if b["denied"]:
            skipped.append(f"{b['label']} (deny:{b['denied']})")
            continue
        sel = f"[data-qa-idx='{b['idx']}']"

        def do(sel=sel, b=b):
            loc = page.locator(sel)
            if not loc.count() or not loc.first.is_visible():
                # a re-render dropped our tag: re-tag and find the same control by label/id/act
                fresh = page.evaluate(BUTTONS_JS, DENY_WORDS)
                match = next((x for x in fresh if (x["id"] and x["id"] == b["id"]) or (x["act"] and x["act"] == b["act"]) or (x["label"] and x["label"] == b["label"])), None)
                if not match:
                    return "gone before click"
                loc = page.locator(f"[data-qa-idx='{match['idx']}']")
            url0 = page.url.split("?")[0]
            loc.first.click(timeout=3000)
            settle(page, 350)
            st = layers(page)
            note = f"opened: {st['top']}" if (st["layers"] or st["dom"] or st["sidebarOpen"]) else "no layer"
            if b["id"] in ("tb-theme", "btn-density"):
                page.locator(sel).first.click(timeout=3000)  # restore
                settle(page, 200)
                note += " · toggled back"
            closed, n, final = close_layers(page)
            if not closed:
                raise AssertionError(f"layer not closed by Escape x{n}: {final}")
            if final["sidebarOpen"]:
                raise AssertionError("mobile sidebar still open after Escape")
            if page.url.split("?")[0] != url0:  # query changes are URL state (history.replaceState), not navigation
                note += f" · NAVIGATED to {page.url}"
                page.goto(url0, wait_until="domcontentloaded")
                wait_loaded(page)
            elif "?" in page.url:
                note += f" · url state {page.url.split('?', 1)[1][:60]}"
            return f"{note} (esc x{n})"

        step(steps, rec, f"button:{b['label'] or b['id'] or b['act']}", do)
        clicked.append(b["label"])
    steps.append({"label": "buttons:summary", "ok": True, "detail": f"clicked {len(clicked)}: {clicked}; skipped {len(skipped)}: {skipped}", "problems": {}, "slow": []})


def probe_segs_tabs(page, rec, steps):
    groups = page.evaluate("""() => Array.from(document.querySelectorAll('#main .seg, #main .tabs, #main [role=tablist]')).filter((g) => g.getClientRects().length && !g.closest('.modal,.drawer,.popover')).map((g, i) => { g.setAttribute('data-qa-seg', String(i)); return { i, id: g.id || g.className, n: g.querySelectorAll('.seg-btn, .tab, [role=tab]').length, tabs: g.matches('.tabs, [role=tablist]') && !g.matches('.seg') }; })""")
    groups.sort(key=lambda g: g["tabs"])  # segmented controls first, tab strips last (tabs hide the other controls)
    for g in groups:
        def do(g=g):
            out = []
            n = page.locator(f"[data-qa-seg='{g['i']}'] .seg-btn, [data-qa-seg='{g['i']}'] .tab, [data-qa-seg='{g['i']}'] [role=tab]").count()
            for k in range(n):
                btn = page.locator(f"[data-qa-seg='{g['i']}'] .seg-btn, [data-qa-seg='{g['i']}'] .tab, [data-qa-seg='{g['i']}'] [role=tab]").nth(k)
                if not btn.is_visible():
                    continue
                label = btn.inner_text().strip()[:30]
                btn.click(timeout=3000)
                settle(page, 300)
                ld = wait_loaded(page)
                st = shell_state(page)
                hits = scan_text(page)
                flag = ""
                if ld["stuck"]:
                    flag += f" STUCK:{ld['stuck']['where']}"
                if st["errorBoxes"]:
                    flag += f" ERRORBOX:{st['errorBoxes']}"
                if hits:
                    flag += f" BADTEXT:{[h['text'] for h in hits]}"
                out.append(f"{label}{flag}")
                if flag:
                    raise AssertionError(f"{g['id']} -> {label}:{flag}")
                close_layers(page)
            return out
        step(steps, rec, f"seg/tabs:{g['id']}", do)


def probe_range(page, rec, steps):
    trig = None
    for s in ("#f-range", "#range-btn"):
        if page.locator(s).count() and page.locator(s).first.is_visible():
            trig = s
            break
    if not trig:
        return
    presets = ["this-month", "last-month", "last-30", "last-90", "this-year", "last-year", "all"]
    for p in presets:
        def do(p=p):
            page.locator(trig).click(timeout=3000)
            opt = page.locator(f".popover [data-preset='{p}']").last  # topmost copy when the button opens two
            opt.wait_for(state="visible", timeout=3000)
            n_pop = page.locator(".popover").count()
            opt.click(timeout=5000)
            settle(page, 300)
            ld = wait_loaded(page)
            st = shell_state(page)
            label = page.locator(trig).inner_text().strip()
            left = layers(page)
            close_layers(page)
            if ld["stuck"] or st["errorBoxes"]:
                raise AssertionError(f"{p}: stuck={ld['stuck']} err={st['errorBoxes']}")
            if n_pop > 1:
                raise AssertionError(f"{p}: one click on {trig} opened {n_pop} popovers; after picking a preset {left['dom']} layer(s) were still open")
            return f"label='{label}' loaded {ld['loaded_ms']}ms"
        step(steps, rec, f"range:{p}", do)


def probe_sort(page, rec, steps):
    close_layers(page)
    ths = page.locator("th.sortable:visible")
    n = ths.count()
    for i in range(n):
        def do(i=i):
            th = page.locator("th.sortable:visible").nth(i)
            key = th.get_attribute("data-sort")
            states = [th.get_attribute("aria-sort")]
            for _ in range(2):
                th.click(timeout=3000)
                settle(page, 300)
                ld = wait_loaded(page)
                if ld["stuck"]:
                    raise AssertionError(f"sort {key}: stuck {ld['stuck']}")
                states.append(page.locator("th.sortable:visible").nth(i).get_attribute("aria-sort"))
            if states[1] == states[2]:
                raise AssertionError(f"sort {key}: second click did not change direction ({states})")
            return f"{key}: {states}"
        step(steps, rec, f"sort:{i}", do)


def probe_search(page, rec, steps):
    if not page.locator("#f-q").count():
        return

    def do():
        with page.expect_response(lambda r: "/api/transactions?" in r.url and "q=amazon" in r.url, timeout=6000):
            page.fill("#f-q", "amazon")
        settle(page, 300)
        ld = wait_loaded(page)
        rows = page.locator("#tx-body tr[data-id]").count()
        summary = page.locator("#tx-summary").inner_text().replace("\n", " ")[:120]
        if ld["stuck"]:
            raise AssertionError(f"stuck {ld['stuck']}")
        page.fill("#f-q", "")
        page.keyboard.press("Enter")
        settle(page, 300)
        wait_loaded(page)
        return f"rows={rows} summary='{summary}' url has q={'q=amazon' in page.url}"
    step(steps, rec, "search:amazon", do)


def probe_palette(page, rec, steps):
    def do():
        page.keyboard.press("Control+k")
        inp = page.locator(".palette input")
        inp.wait_for(state="visible", timeout=3000)
        inp.press_sequentially("rep")  # focuses first: the palette focuses its input in a rAF, a bare keyboard.type can race it
        # the search is debounced (150ms); wait until the list reflects the query rather than the initial empty-query list
        page.wait_for_function("() => document.querySelector('.palette input').value === 'rep' && (document.querySelector('.palette-empty') || Array.from(document.querySelectorAll('.palette-item')).some((b) => /Reports/.test(b.textContent)))", timeout=4000)
        settle(page, 300)
        items = page.locator(".palette-item").all_inner_texts()
        page.keyboard.press("Escape")
        page.wait_for_timeout(200)
        if page.locator(".palette").count():
            raise AssertionError("palette still open after Escape")
        if not any("Reports" in t for t in items):
            raise AssertionError(f"'Go to Reports' missing from results: {items}")
        return f"{len(items)} results: {[t.replace(chr(10), ' ')[:40] for t in items[:6]]}"
    step(steps, rec, "palette:rep", do)


def probe_shortcuts_sheet(page, rec, steps):
    def do():
        page.keyboard.press("?")
        page.locator(".modal").wait_for(state="visible", timeout=3000)
        title = page.locator(".modal-head h2").inner_text()
        rows = page.locator(".modal .shortcuts-grid > div").count()
        page.keyboard.press("Escape")
        page.wait_for_timeout(200)
        if page.locator(".modal").count():
            raise AssertionError("shortcuts sheet still open after Escape")
        return f"'{title}' {rows} rows"
    step(steps, rec, "shortcuts:?", do)


def probe_sidebar(page, rec, steps, vp):
    def do():
        modes = [page.evaluate("() => document.documentElement.getAttribute('data-sidebar')")]
        page.keyboard.press("[")
        page.wait_for_timeout(150)
        modes.append(page.evaluate("() => document.documentElement.getAttribute('data-sidebar')"))
        page.keyboard.press("]")
        page.wait_for_timeout(150)
        modes.append(page.evaluate("() => document.documentElement.getAttribute('data-sidebar')"))
        if vp == "1440":
            if modes != ["full", "rail", "full"]:
                raise AssertionError(f"expected full->rail->full, got {modes}")
        elif any(m != "hidden" for m in modes):
            raise AssertionError(f"mobile: expected hidden throughout, got {modes}")
        return modes
    step(steps, rec, "sidebar:[ ]", do)


def probe_drawer(page, rec, steps):
    if not page.locator("#tx-body tr[data-id]").count():
        steps.append({"label": "drawer:first-row", "ok": True, "detail": "no rows (empty data)", "problems": {}, "slow": []})
        return

    def do():
        page.locator("#tx-body tr[data-id] td.col-merchant").first.click(timeout=3000)
        page.locator(".drawer").wait_for(state="visible", timeout=3000)
        page.locator(".drawer #txd-notes").wait_for(state="visible", timeout=5000)
        settle(page, 300)
        title = page.locator("#drawer-title").inner_text()
        hits = [h for h in scan_text(page) if ".drawer" in h["el"] or "txd" in h["el"]]
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
        if page.locator(".drawer").count():
            raise AssertionError("drawer still open after Escape")
        if hits:
            raise AssertionError(f"bad text in drawer: {hits}")
        return f"opened '{title}', closed with Escape"
    step(steps, rec, "drawer:first-row", do)


def probe_settings_tabs(page, rec, steps):
    for tab in ("accounts", "ai", "appearance", "users", "account"):
        def do(tab=tab):
            a = page.locator(f"#settings-nav [data-tab='{tab}']")
            if not a.count():
                return "tab absent"
            a.click(timeout=3000)
            settle(page, 300)
            ld = wait_loaded(page)
            st = shell_state(page)
            hits = scan_text(page)
            if ld["stuck"] or st["errorBoxes"] or hits:
                raise AssertionError(f"{tab}: stuck={ld['stuck']} err={st['errorBoxes']} text={[h['text'] for h in hits]}")
            return f"#{tab} ok ({ld['loaded_ms']}ms)"
        if step(steps, rec, f"settings-tab:{tab}", do) != "tab absent":
            probe_buttons(page, rec, steps)  # each settings tab has its own controls


def probe_goto(page, rec, steps, pg):
    key, target = ("d", "/index.html") if pg == "transactions" else ("t", "/transactions.html")

    def do():
        page.evaluate("() => { if (document.activeElement) document.activeElement.blur(); }")
        page.keyboard.press("g")
        # The second key of the chord navigates synchronously inside the keydown handler, and a real
        # keyboard.press has no timeout: its keyup ack is lost with the unloading renderer and hangs forever.
        # Dispatch the second keydown as a DOM event instead; the shortcut map and navigation still run.
        with page.expect_navigation(timeout=5000):
            page.evaluate("(k) => document.dispatchEvent(new KeyboardEvent('keydown', { key: k, bubbles: true, cancelable: true }))", key)
        wait_loaded(page)
        if target not in page.url:
            raise AssertionError(f"g {key} landed on {page.url}")
        return page.url
    step(steps, rec, f"goto:g {key}", do)


@pytest.mark.parametrize("pg,theme,vp", PROBE_COMBOS, ids=[f"{a}-{b}-{c}" for a, b, c in PROBE_COMBOS])
def test_probes_admin(make_context, pg, theme, vp):
    ctx, page, rec = make_context("admin", theme, vp)
    page.goto(PAGES[pg], wait_until="domcontentloaded")
    wait_loaded(page)
    steps = []
    if pg == "reports":
        probe_range(page, rec, steps)
    probe_segs_tabs(page, rec, steps)
    if pg == "settings":
        probe_settings_tabs(page, rec, steps)
    else:
        probe_buttons(page, rec, steps)
    if pg == "transactions":
        probe_range(page, rec, steps)
    if pg == "reports":  # the merchants leaderboard is the only report with sortable headers
        page.click("#report-tabs [data-tab='merchants']")
        wait_loaded(page)
    probe_sort(page, rec, steps)
    if pg == "transactions":
        probe_search(page, rec, steps)
        probe_drawer(page, rec, steps)
    probe_palette(page, rec, steps)
    probe_shortcuts_sheet(page, rec, steps)
    probe_sidebar(page, rec, steps, vp)
    shot(page, f"probe-{pg}-admin-{theme}-{vp}")
    probe_goto(page, rec, steps, pg)
    bad = [s for s in steps if not s["ok"]]
    record("probe", page=pg, persona="admin", theme=theme, vp=vp, steps=steps, status="fail" if bad else "pass",
           slow=rec.slow, console=rec.console, pageerrors=rec.pageerrors, http_errors=rec.http_errors, failed=rec.failed)
    assert not bad, f"{pg}/{theme}/{vp} probe failures:\n  " + "\n  ".join(f"{s['label']}: {s['detail']} {json.dumps(s['problems'], default=str)[:400]}" for s in bad)


# ---------------------------------------------------------------- 5: auth behaviours
@pytest.mark.parametrize("pg", list(PAGES))
def test_auth_redirect_logged_out(make_context, pg):
    ctx, page, rec = make_context("anon", "light", "1440")
    page.goto(PAGES[pg] + "?x=1", wait_until="domcontentloaded")
    page.wait_for_url(lambda u: "/login.html" in u, timeout=6000)
    settle(page)
    from urllib.parse import parse_qs, urlparse
    q = parse_qs(urlparse(page.url).query)
    nxt = (q.get("next") or [""])[0]
    issues = []
    if nxt != PAGES[pg] + "?x=1":
        issues.append(f"next={nxt!r} expected {PAGES[pg] + '?x=1'!r}")
    unexpected = [h for h in rec.http_errors if not (h["status"] == 401 and h["url"].endswith("/api/auth/me"))]
    if unexpected:
        issues.append(f"unexpected http errors: {unexpected}")
    issues += [f"console.{c['type']}: {c['text'][:160]}" for c in unexpected_console(rec, ["/api/auth/me"])]
    issues += [f"pageerror: {e['text'][:160]}" for e in rec.pageerrors]
    record("auth", name=f"redirect:{pg}", ok=not issues, detail={"url": page.url, "issues": issues, "requests": [r["url"] for r in rec.requests if "/api/" in r["url"]]})
    assert not issues, "\n".join(issues)


def test_auth_login_next_and_wrong_password(make_context):
    ctx, page, rec = make_context("anon", "light", "1440")
    page.goto("/login.html?next=%2Freports.html%3Ftab%3Dtrend", wait_until="load")
    page.fill("#username", "qa_tester")
    page.fill("#password", "wrong-password")
    page.click("#login-btn")
    page.locator("#login-error.show").wait_for(state="visible", timeout=5000)
    err_text = page.locator("#login-error").inner_text().strip()
    role = page.locator("#login-error").get_attribute("role")
    focused = page.evaluate("() => document.activeElement && document.activeElement.id")
    page.wait_for_timeout(3000)
    still_visible = page.locator("#login-error.show").is_visible()
    toasts = page.locator(".toast").count()
    page.fill("#password", PERSONAS["qa_tester"][1])
    with page.expect_navigation(timeout=8000):
        page.click("#login-btn")
    wait_loaded(page)
    detail = {"error_text": err_text, "role": role, "focus_after_error": focused, "error_persists_3s": still_visible, "toasts": toasts,
              "landed": page.url, "console": unexpected_console(rec, ["/api/auth/login"]), "pageerrors": rec.pageerrors,
              "http": [h for h in rec.http_errors if not h["url"].endswith("/api/auth/login")]}
    issues = []
    if "Invalid username or password" not in err_text:
        issues.append(f"wrong-password message: {err_text!r}")
    if not still_visible:
        issues.append("inline error disappeared within 3s")
    if role != "alert":
        issues.append(f"error box role={role}")
    if not page.url.endswith("/reports.html?tab=trend"):
        issues.append(f"next not honoured: {page.url}")
    if detail["http"] or detail["console"] or rec.pageerrors:
        issues.append("console/network problems during login flow")
    record("auth", name="login-next-wrong-password", ok=not issues, detail={**detail, "issues": issues})
    assert not issues, "\n".join(issues) + "\n" + json.dumps(detail, default=str)


def test_auth_logout_back_and_storage_residue(make_context, base_url):
    """Admin logs in via UI, loads transactions (populates caches), signs out, presses Back, then
    qa_tester logs in from the same tab: does qa_tester see admin's cached accounts/categories?"""
    ctx, page, rec = make_context("anon", "light", "1440")
    # admin's real account names, via API in a separate request context (read-only)
    api = ctx.request
    r = api.post(f"{base_url}/api/auth/login", data={"username": "admin", "password": "admin"})
    assert r.ok
    admin_accounts = [a["name"] for a in api.get(f"{base_url}/api/accounts?all=1").json()]
    api.post(f"{base_url}/api/auth/logout")

    page.goto("/login.html", wait_until="load")
    page.fill("#username", "admin")
    page.fill("#password", "admin")
    with page.expect_navigation(timeout=8000):
        page.click("#login-btn")
    page.goto("/transactions.html?range=all", wait_until="domcontentloaded")
    wait_loaded(page)
    rows_admin = page.locator("#tx-body tr[data-id]").count()
    keys_before = page.evaluate("() => ({ ss: Object.keys(sessionStorage).sort(), ls: Object.keys(localStorage).sort() })")

    # Sign out through the UI user menu
    page.click("#tb-user")
    page.locator(".menu .menu-item", has_text="Sign out").wait_for(state="visible", timeout=3000)
    with page.expect_navigation(timeout=8000):
        page.locator(".menu .menu-item", has_text="Sign out").click()
    settle(page)
    keys_after_logout = page.evaluate("() => ({ ss: Object.keys(sessionStorage).sort(), ls: Object.keys(localStorage).sort() })")
    me_after = api.get(f"{base_url}/api/auth/me").status

    # Back button: must not show admin's data
    page.go_back(wait_until="domcontentloaded")
    settle(page, 800)
    back_url = page.url
    back_rows = page.locator("#tx-body tr[data-id]").count()
    back_body_has_amounts = page.locator("#tx-body .amt").count()

    # Log in as qa_tester in the same tab, land on transactions
    if "/login.html" not in page.url:
        page.goto("/login.html?next=%2Ftransactions.html", wait_until="load")
    page.fill("#username", "qa_tester")
    page.fill("#password", PERSONAS["qa_tester"][1])
    with page.expect_navigation(timeout=8000):
        page.click("#login-btn")
    if "/transactions.html" not in page.url:
        page.goto("/transactions.html", wait_until="domcontentloaded")
    wait_loaded(page)
    cached = page.evaluate("""() => { const g = (k) => { try { const e = JSON.parse(sessionStorage.getItem('ispend.cache.' + k) || 'null'); return e && e.data; } catch (e) { return null; } };
        const acc = g('accounts') || []; const cats = g('categories') || [];
        return { accountNames: acc.map((a) => a.name), catNames: cats.map((c) => c.name), ss: Object.keys(sessionStorage).sort(), user: window.currentUser && window.currentUser.username }; }""")
    # UI-level evidence: open the accounts filter and read the options
    page.click("#f-accounts")
    page.locator(".menu").wait_for(state="visible", timeout=3000)
    filter_accounts = page.locator(".menu [data-v] .grow").all_inner_texts()
    page.keyboard.press("Escape")
    leaked_accounts = [a for a in filter_accounts if a in admin_accounts]
    leaked_cached = [a for a in cached["accountNames"] if a in admin_accounts]
    period_leak = page.evaluate("() => sessionStorage.getItem('ispend.period')")

    detail = {"admin_rows": rows_admin, "keys_before_logout": keys_before, "keys_after_logout": keys_after_logout, "api_me_after_logout": me_after,
              "back_url": back_url, "back_rows": back_rows, "back_amount_cells": back_body_has_amounts,
              "qa_user": cached["user"], "qa_cached_account_names": cached["accountNames"], "qa_filter_accounts": filter_accounts,
              "leaked_accounts_in_filter": leaked_accounts, "leaked_accounts_in_cache": leaked_cached, "admin_accounts": admin_accounts,
              "qa_session_keys": cached["ss"], "period_residue": period_leak, "console": rec.console, "pageerrors": rec.pageerrors, "http": rec.http_errors}
    issues = []
    if me_after != 401:
        issues.append(f"/api/auth/me after logout returned {me_after}")
    if back_rows or back_body_has_amounts or "/login.html" not in back_url:
        issues.append(f"Back after logout showed data: url={back_url} rows={back_rows}")
    residue = [k for k in keys_after_logout["ss"] if k.startswith("ispend.cache.") or k.startswith("ispend.q:") or k == "ispend.period"]
    if residue:
        issues.append(f"sessionStorage residue after logout: {residue}")
    if leaked_accounts or leaked_cached:
        issues.append(f"qa_tester sees admin's accounts: filter={leaked_accounts} cache={leaked_cached}")
    record("auth", name="logout-back-storage", ok=not issues, detail={**detail, "issues": issues})
    assert not issues, "\n".join(issues) + "\n" + json.dumps(detail, default=str)[:3000]


# ---------------------------------------------------------------- 7: theme behaviours
def test_theme_no_flash_with_stored_dark(make_context):
    ctx, page, rec = make_context("admin", "dark", "1440")
    page.goto("/index.html", wait_until="domcontentloaded")
    at_dcl = page.evaluate("() => ({ bg: getComputedStyle(document.body).backgroundColor, attr: document.documentElement.getAttribute('data-theme') })")
    wait_loaded(page)
    at_load = page.evaluate("() => ({ bg: getComputedStyle(document.body).backgroundColor, attr: document.documentElement.getAttribute('data-theme') })")
    ok = rgb(at_dcl["bg"]) == DARK_BG == rgb(at_load["bg"]) and at_dcl["attr"] == "dark" == at_load["attr"]
    record("theme", name="no-flash-stored-dark", ok=ok, detail={"dcl": at_dcl, "load": at_load})
    assert ok, f"{at_dcl} -> {at_load}"


def test_theme_persists_across_reload_and_pages(make_context):
    ctx, page, rec = make_context("qa_tester", "light", "1440")
    page.goto("/index.html", wait_until="domcontentloaded")
    wait_loaded(page)
    page.click("#tb-theme")
    page.wait_for_timeout(200)
    after_toggle = page.evaluate("() => ({ attr: document.documentElement.getAttribute('data-theme'), ls: localStorage.getItem('ispend.theme'), bg: getComputedStyle(document.body).backgroundColor })")
    page.reload(wait_until="domcontentloaded")
    wait_loaded(page)
    after_reload = page.evaluate("() => ({ attr: document.documentElement.getAttribute('data-theme'), bg: getComputedStyle(document.body).backgroundColor })")
    page.goto("/reports.html", wait_until="domcontentloaded")
    wait_loaded(page)
    other_page = page.evaluate("() => ({ attr: document.documentElement.getAttribute('data-theme'), bg: getComputedStyle(document.body).backgroundColor })")
    page.click("#tb-theme")  # restore local choice
    ok = after_toggle["attr"] == "dark" and after_toggle["ls"] == "dark" and after_reload["attr"] == "dark" and other_page["attr"] == "dark" and rgb(other_page["bg"]) == DARK_BG
    record("theme", name="persists-reload-pages", ok=ok, detail={"toggle": after_toggle, "reload": after_reload, "other": other_page, "console": rec.console})
    assert ok and not rec.console and not rec.pageerrors, json.dumps({"toggle": after_toggle, "reload": after_reload, "other": other_page, "console": rec.console})


@pytest.mark.parametrize("scheme", ["dark", "light"])
def test_theme_follows_system_when_unset(make_context, scheme):
    ctx, page, rec = make_context("qa_tester", None, "1440", color_scheme=scheme, stored_theme=False)
    page.goto("/index.html", wait_until="domcontentloaded")
    at_dcl = page.evaluate("() => ({ bg: getComputedStyle(document.body).backgroundColor, attr: document.documentElement.getAttribute('data-theme'), mode: document.documentElement.getAttribute('data-theme-mode') })")
    wait_loaded(page)
    st = page.evaluate("() => ({ bg: getComputedStyle(document.body).backgroundColor, attr: document.documentElement.getAttribute('data-theme'), mode: document.documentElement.getAttribute('data-theme-mode'), eff: Theme.effective(), ls: localStorage.getItem('ispend.theme') })")
    exp = DARK_BG if scheme == "dark" else LIGHT_BG
    ok = rgb(st["bg"]) == exp and st["attr"] is None and st["mode"] == "system" and st["eff"] == scheme and rgb(at_dcl["bg"]) == exp
    record("theme", name=f"system-{scheme}", ok=ok, detail={"dcl": at_dcl, "load": st})
    assert ok, json.dumps({"dcl": at_dcl, "load": st})


def test_theme_server_preference_first_paint(make_context, base_url):
    """A user whose saved preference is dark but who has no localStorage choice (new browser):
    is the first paint already dark, or does the page flash light and switch after /api/auth/me?
    Uses qa_tester (mutable) and restores the preference afterwards."""
    ctx, page, rec = make_context("qa_tester", None, "1440", color_scheme="light", stored_theme=False)
    api = ctx.request
    before = api.get(f"{base_url}/api/auth/me").json()["preferences"]
    api.put(f"{base_url}/api/auth/me/preferences", data={"theme": "dark"})
    try:
        page.goto("/transactions.html", wait_until="domcontentloaded")
        at_dcl = page.evaluate("() => ({ bg: getComputedStyle(document.body).backgroundColor, attr: document.documentElement.getAttribute('data-theme') })")
        wait_loaded(page)
        at_load = page.evaluate("() => ({ bg: getComputedStyle(document.body).backgroundColor, attr: document.documentElement.getAttribute('data-theme'), ls: localStorage.getItem('ispend.theme') })")
    finally:
        api.put(f"{base_url}/api/auth/me/preferences", data={"theme": before.get("theme") or "system"})
    flash = rgb(at_dcl["bg"]) != rgb(at_load["bg"])
    record("theme", name="server-pref-first-paint", ok=not flash, detail={"dcl": at_dcl, "load": at_load, "flash": flash})
    assert not flash, f"first paint {at_dcl} then switched to {at_load}: theme flash for users with a saved preference"
