"""Mobile makeover gates: a real-device matrix with touch emulation (Playwright, chromium).

    QA_SCRATCH=<dir> <venv>/bin/pytest qa/e2e/test_mobile.py -q -p no:cacheprovider

Every page is measured once per device (one logged-in context per device, the session cookie is
shared so the login rate limit is hit once) and the tests assert on the cached measurements.
Known defects are xfail(strict=True) until the phase that fixes them lands, so a fix shows up as an
XPASS and the mark has to go. Admin is used read-only.
"""
import json

import pytest
from helpers import PAGES, wait_loaded
from smoke_fixtures import (  # noqa: F401  (pytest fixtures)
    BASE_URL,
    INIT_JS,
    api_login,
    browser,
    pw,
)
from test_phone import ALLOW_HSCROLL, INNER_OVERFLOW_JS

UA_IOS = ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 "
          "(KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1")
DEVICES = {
    "se-320": (320, 568),
    "375": (375, 667),
    "390": (390, 844),
    "430": (430, 932),
    "landscape": (844, 390),
    "ipad-mini": (744, 1133),
}
PHONE = "375"
AUTH_PAGES = {"login": "/login.html", "signup": "/signup.html", "forgot": "/forgot.html"}

# Hit areas below this (CSS px, both axes) fail. The tokens aim for 44; 40 leaves room for dense rows
# where neighbours share the spacing (WCAG 2.5.8 spacing exception).
MIN_HIT = 40
MIN_TEXT_PX = 12

# ---------------------------------------------------------------------------------------------
# Known defects, removed phase by phase (see the mobile makeover plan).
OVERFLOW_XFAIL = {("review", "se-320"), ("import", "se-320"), ("statements", "landscape")}
INPUT_XFAIL = set()
TARGET_XFAIL = {"budgets", "categories", "index", "insights", "reports", "settings", "statements",
                "forgot", "login", "signup"}
TEXT_XFAIL = set()
TITLE_XFAIL = set()


MEASURE_JS = r"""
(minHit) => {
  const vw = document.documentElement.clientWidth, vh = window.innerHeight;
  const hidden = (el) => { if (el.closest('[hidden],[inert],[aria-hidden="true"],.sr-only')) return true;
    const cs = getComputedStyle(el); return cs.visibility === 'hidden' || cs.display === 'none' || parseFloat(cs.opacity) === 0; };
  const name = (el) => el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') + (typeof el.className === 'string' && el.className.trim() ? '.' + el.className.trim().split(/\s+/).slice(0, 2).join('.') : '')
    + ' "' + (el.getAttribute('aria-label') || el.textContent || el.placeholder || '').trim().replace(/\s+/g, ' ').slice(0, 30) + '"';

  // inputs that make iOS zoom on focus
  const smallInputs = [];
  document.querySelectorAll('input:not([type=checkbox]):not([type=radio]):not([type=hidden]):not([type=file]):not([type=range]), select, textarea').forEach((el) => {
    const r = el.getBoundingClientRect(); if (!r.width || hidden(el)) return;
    const fs = parseFloat(getComputedStyle(el).fontSize);
    if (fs < 16) smallInputs.push(name(el) + ' ' + fs + 'px');
  });

  // text below the phone minimum
  const smallText = new Map();
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let n;
  while ((n = walker.nextNode())) {
    if (!n.textContent.trim()) continue;
    const el = n.parentElement; if (!el || hidden(el) || el.closest('#sidebar, script, style, noscript')) continue;
    const r = el.getBoundingClientRect(); if (!r.width || !r.height) continue;
    const fs = parseFloat(getComputedStyle(el).fontSize);
    if (fs < 12) { const k = name(el) + ' ' + fs + 'px'; smallText.set(k, 1); }
  }

  // title="" tooltips are invisible to touch users
  const titled = Array.from(document.querySelectorAll('#main [title], .topbar [title], .bottomnav [title]'))
    .filter((el) => !(el instanceof SVGElement) && !hidden(el)).map(name);

  // hit areas, probed with elementFromPoint so ::before/::after expansions count
  const SEL = 'a[href], button, input:not([type=hidden]), select, textarea, summary, label.switch, [role=button], [role=tab], [role=radio], [role=menuitem], [role=checkbox], [role=switch], [role=option]';
  const INLINE = 'p a, .hint a, .page-sub a, .empty-body a, .notice a, .field-error a, li a:not(.btn)';
  const small = [];
  const own = (el, x, y) => { const h = document.elementFromPoint(x, y); return h && (h === el || el.contains(h) || (el.control && h === el.control) || (h.control === el)); };
  const extent = (el, cx, cy, dx, dy) => { let d = 0; while (d < 60 && own(el, cx + dx * (d + 1), cy + dy * (d + 1))) d += 1; return d; };
  const cands = Array.from(document.querySelectorAll(SEL)).filter((el) => !hidden(el) && !el.closest('#sidebar, .skip, .sidebar-backdrop') && !el.matches(INLINE));
  const y0 = window.scrollY;
  for (const el of cands) {
    if (el.matches('input[type=checkbox], input[type=radio]') && el.closest('label')) continue; // the label is the target
    let r = el.getBoundingClientRect(); if (!r.width || !r.height) continue;
    if (r.top < 60 || r.bottom > vh - 80) { el.scrollIntoView({ block: 'center', inline: 'nearest' }); r = el.getBoundingClientRect(); }
    const cx = Math.round(r.left + r.width / 2), cy = Math.round(r.top + r.height / 2);
    if (cx < 0 || cx > vw || cy < 0 || cy > vh) continue;
    if (!own(el, cx, cy)) continue; // covered by the topbar / bottom nav / a sibling: not measurable here
    const w = extent(el, cx, cy, -1, 0) + extent(el, cx, cy, 1, 0) + 1;
    const h = extent(el, cx, cy, 0, -1) + extent(el, cx, cy, 0, 1) + 1;
    if (w < minHit || h < minHit) small.push(name(el) + ' ' + w + 'x' + h);
  }
  window.scrollTo(0, y0);
  const doc = { sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth };
  return { smallInputs, smallText: Array.from(smallText.keys()), titled, small, doc };
}
"""

SHELL_JS = r"""
() => {
  const vis = (el) => { if (!el) return false; const r = el.getBoundingClientRect(); const cs = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none' && parseFloat(cs.opacity) > 0.1; };
  const items = Array.from(document.querySelectorAll('.bottomnav .bn-item')).filter(vis).map((i) => i.textContent.replace(/\d+/g, '').trim());
  return { items, topTitle: vis(document.getElementById('tb-title')), h1: vis(document.querySelector('#main .page-head h1')) };
}
"""


@pytest.fixture(scope="module")
def session_state(browser):
    ctx = browser.new_context(base_url=BASE_URL, service_workers="block")
    api_login(ctx, "admin")
    state = ctx.storage_state()
    ctx.close()
    return state


@pytest.fixture(scope="module")
def device(browser, session_state):
    """device(key, persona='admin'|'anon') -> a fresh page in a touch/mobile context of that size."""
    made = []

    def _open(key, persona="admin"):
        w, h = DEVICES[key]
        ctx = browser.new_context(viewport={"width": w, "height": h}, is_mobile=True, has_touch=True, user_agent=UA_IOS,
                                  device_scale_factor=2, base_url=BASE_URL, service_workers="block",
                                  storage_state=session_state if persona == "admin" else None)
        ctx.add_init_script(f"({INIT_JS})({json.dumps('light')});")
        made.append(ctx)
        return ctx.new_page()

    yield _open
    for c in made:
        try:
            c.close()
        except Exception:  # noqa: BLE001  (measure() closes its contexts early)
            pass


_cache = {}


def measure(device, page_key, dev=PHONE):
    """Measurements for one page on one device, computed once per session."""
    if (page_key, dev) not in _cache:
        auth = page_key in PAGES
        page = device(dev, "admin" if auth else "anon")
        page.goto(PAGES[page_key] if auth else AUTH_PAGES[page_key])
        if auth:
            wait_loaded(page)
        else:
            page.wait_for_load_state("load")
            page.wait_for_timeout(400)
        overflow = page.evaluate(INNER_OVERFLOW_JS, ALLOW_HSCROLL)
        m = page.evaluate(MEASURE_JS, MIN_HIT) if dev == PHONE else {"doc": page.evaluate(
            "() => ({ sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth })")}
        m["overflow"] = overflow
        _cache[(page_key, dev)] = m
        page.context.close()
    return _cache[(page_key, dev)]


def _mark(keys, xfail, reason):
    return [pytest.param(*k if isinstance(k, tuple) else (k,), marks=pytest.mark.xfail(strict=True, reason=reason))
            if k in xfail else (pytest.param(*k) if isinstance(k, tuple) else k) for k in keys]


ALL_PAGES = sorted(PAGES) + sorted(AUTH_PAGES)


@pytest.mark.parametrize("page_key,dev", _mark([(p, d) for p in sorted(PAGES) for d in DEVICES], OVERFLOW_XFAIL,
                                              "overflow fixed in the page phases"))
def test_no_horizontal_overflow(device, page_key, dev):
    m = measure(device, page_key, dev)
    assert m["doc"]["sw"] <= m["doc"]["cw"] + 1 and m["overflow"] == [], f"{page_key}@{dev}: {m['doc']} {m['overflow']}"


@pytest.mark.parametrize("page_key", _mark(ALL_PAGES, INPUT_XFAIL, "16px inputs land in Phase 1"))
def test_inputs_at_least_16px(device, page_key):
    assert measure(device, page_key)["smallInputs"] == []


@pytest.mark.parametrize("page_key", _mark(ALL_PAGES, TARGET_XFAIL, "touch targets land in Phases 1-7"))
def test_touch_targets(device, page_key):
    small = measure(device, page_key)["small"]
    assert small == [], f"{page_key}: {len(small)} targets under {MIN_HIT}px: {small[:25]}"


@pytest.mark.parametrize("page_key", _mark(ALL_PAGES, TEXT_XFAIL, "phone type ramp lands in Phase 1"))
def test_no_text_below_12px(device, page_key):
    assert measure(device, page_key)["smallText"] == []


@pytest.mark.parametrize("page_key", _mark(sorted(PAGES), TITLE_XFAIL, "title tooltips replaced in Phase 2"))
def test_no_title_tooltips(device, page_key):
    assert measure(device, page_key)["titled"] == []


def test_transactions_first_row_in_first_screen(device):
    page = device("375")
    page.goto(PAGES["transactions"])
    wait_loaded(page)
    top = page.evaluate("() => { const r = document.querySelector('.tbl-tx tbody tr[data-id]'); return r && r.getBoundingClientRect().top; }")
    nav = page.evaluate("() => document.querySelector('.bottomnav').getBoundingClientRect().top")
    assert top is not None and top + 56 <= nav, f"first row starts at {top}px, bottom nav at {nav}px"


def test_bottom_nav_and_more_sheet(device):
    page = device("375")
    page.goto(PAGES["index"])
    wait_loaded(page)
    shell = page.evaluate(SHELL_JS)
    assert shell["items"] == ["Home", "Transactions", "Review", "Budgets", "More"], shell
    page.locator("#bn-more").tap()
    page.wait_for_selector(".sheet", timeout=3000)
    assert not page.evaluate("() => document.body.classList.contains('sidebar-open')")


def test_single_page_title(device):
    page = device("375")
    page.goto(PAGES["transactions"])
    wait_loaded(page)
    shell = page.evaluate(SHELL_JS)
    assert shell["h1"] and not shell["topTitle"], shell


def test_date_sheet_fits_visual_viewport(device):
    page = device("se-320")
    page.goto(PAGES["transactions"])
    wait_loaded(page)
    page.locator("#f-range").tap()
    page.wait_for_selector(".sheet [data-preset]", timeout=3000)
    page.wait_for_timeout(400)  # let the slide-in finish before measuring
    box = page.evaluate("""() => { const s = document.querySelector('.sheet'); const r = s.getBoundingClientRect(); const a = s.querySelector('[data-act=apply]').getBoundingClientRect();
      const vv = window.visualViewport; return { top: r.top, bottom: r.bottom, vh: vv.height, applyBottom: a.bottom, applyH: a.height }; }""")
    assert box["top"] >= 0 and box["bottom"] <= box["vh"] + 1 and box["applyBottom"] <= box["vh"] and box["applyH"] >= 44, box
