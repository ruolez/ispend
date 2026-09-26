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
OVERFLOW_XFAIL = set()
INPUT_XFAIL = set()
TARGET_XFAIL = set()
TEXT_XFAIL = set()
TITLE_XFAIL = set()
# Visual probes (phone polish pass): text on text, hard-clipped text, "·" stranded at a line edge.
OVERLAP_XFAIL = {("budgets", "se-320"), ("index", "390"), ("index", "se-320")}
CLIP_XFAIL = {("budgets", "se-320")}
ORPHAN_XFAIL = set()
VISUAL_DEVICES = ["se-320", "390"]
CLIP_ALLOW = [s for s in ALLOW_HSCROLL if s not in ("#tx-summary", ".tx-wrap")]


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

VISUAL_JS = r"""
(allowScroll) => {
  const main = document.getElementById('main'); if (!main) return { overlap: [], clip: [], orphan: [] };
  const hidden = (el) => { if (el.closest('[hidden],[inert],[aria-hidden="true"],.sr-only,.visually-hidden')) return true;
    for (let e = el; e && e !== document.body; e = e.parentElement) { const cs = getComputedStyle(e);
      if (cs.visibility === 'hidden' || cs.display === 'none' || parseFloat(cs.opacity) === 0) return true; } return false; };
  const label = (el) => el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') + (typeof el.className === 'string' && el.className.trim() ? '.' + el.className.trim().split(/\s+/).slice(0, 2).join('.') : '');
  const scrolls = (el) => allowScroll.some((s) => el.closest(s));
  const clips = (cs) => /hidden|clip|auto|scroll/.test(cs.overflowX + cs.overflowY);
  // a text rect clipped by every overflow ancestor; null when nothing of it is visible
  const clipRect = (el, r) => { let [l, t, rt, b] = [r.left, r.top, r.right, r.bottom];
    for (let e = el; e && e !== document.body; e = e.parentElement) { const cs = getComputedStyle(e);
      if (clips(cs)) { const c = e.getBoundingClientRect(); // overflow clips at the padding box
        l = Math.max(l, c.left + parseFloat(cs.borderLeftWidth)); t = Math.max(t, c.top + parseFloat(cs.borderTopWidth));
        rt = Math.min(rt, c.right - parseFloat(cs.borderRightWidth)); b = Math.min(b, c.bottom - parseFloat(cs.borderBottomWidth)); }
      const ins = /^inset\(([^)]*)\)/.exec(cs.clipPath);
      if (ins) { const v = ins[1].trim().split(/\s+/).map(parseFloat); const [it, ir, ib, il] = [v[0], v[1] ?? v[0], v[2] ?? v[0], v[3] ?? v[1] ?? v[0]];
        const c = e.getBoundingClientRect(); l = Math.max(l, c.left + il); t = Math.max(t, c.top + it); rt = Math.min(rt, c.right - ir); b = Math.min(b, c.bottom - ib); }
      if (cs.position === 'fixed') break; }
    return rt - l > 1 && b - t > 1 ? { l, t, r: rt, b } : null; };
  const texts = []; const clip = []; const orphan = new Set();
  const walker = document.createTreeWalker(main, NodeFilter.SHOW_TEXT); let n;
  while ((n = walker.nextNode())) {
    const s = n.textContent; if (!s.trim()) continue;
    const el = n.parentElement; if (!el || el.closest('script,style,template,svg,canvas') || hidden(el)) continue;
    const rg = document.createRange(); rg.selectNodeContents(n);
    let abs = false; for (let e = el; e && e !== main; e = e.parentElement) if (getComputedStyle(e).position === 'absolute') { abs = true; break; }
    for (const r of rg.getClientRects()) {
      if (r.width < 1 || r.height < 1) continue;
      const v = clipRect(el, r); if (v) texts.push({ el, v, abs });
      // hard clip: the text runs past a clipping box that shows no ellipsis for it
      if (!scrolls(el)) {
        for (let e = el; e && e !== main; e = e.parentElement) { const cs = getComputedStyle(e); if (!clips(cs)) continue;
          const c = e.getBoundingClientRect(); if (r.right <= c.right + 1 && r.left >= c.left - 1) continue;
          // text-overflow only draws on a block container's own inline content, never through a flex/grid box
          let inlinePath = /^(block|inline-block|table-cell|list-item|flow-root)$/.test(cs.display);
          for (let p = el; p && p !== e; p = p.parentElement) if (getComputedStyle(p).display !== 'inline') inlinePath = false;
          const ellipsis = cs.textOverflow === 'ellipsis' && inlinePath && cs.whiteSpace.startsWith('nowrap');
          if (!ellipsis) clip.push(label(e) + ' clips "' + s.trim().slice(0, 30) + '"'); break; }
      }
    }
  }
  // text on text, and text under a canvas (a sparkline over a label); absolute overlays on charts are by design
  const overlap = new Set();
  const inter = (a, b) => Math.min(a.r, b.r) - Math.max(a.l, b.l) > 2 && Math.min(a.b, b.b) - Math.max(a.t, b.t) > 2;
  texts.sort((a, b) => a.v.t - b.v.t);
  for (let i = 0; i < texts.length; i++) for (let j = i + 1; j < texts.length && texts[j].v.t < texts[i].v.b; j++) {
    const a = texts[i], b = texts[j]; if (a.el === b.el || a.el.contains(b.el) || b.el.contains(a.el)) continue;
    if (inter(a.v, b.v)) overlap.add(label(a.el) + ' "' + a.el.textContent.trim().slice(0, 24) + '" x ' + label(b.el) + ' "' + b.el.textContent.trim().slice(0, 24) + '"');
  }
  main.querySelectorAll('canvas').forEach((c) => { const cs = getComputedStyle(c); if (!c.getClientRects().length || cs.visibility === 'hidden' || c.closest('[hidden]')) return; const cr = c.getBoundingClientRect(); const v = { l: cr.left, t: cr.top, r: cr.right, b: cr.bottom };
    for (const t of texts) if (!t.abs && inter(t.v, v)) overlap.add(label(c) + ' x ' + label(t.el) + ' "' + t.el.textContent.trim().slice(0, 24) + '"'); });
  // a separator dot drawn by a ::before must be invisible when its item starts a line, and must never be
  // left alone at the end of a line (an inline item that breaks right after its dot)
  const vband = (a, b) => Math.min(a.b, b.b) - Math.max(a.t, b.t) > 4;
  main.querySelectorAll('*').forEach((el) => {
    const c = getComputedStyle(el, '::before').content; if (!c || !c.includes('·') || hidden(el)) return;
    const rs = [...el.getClientRects()].filter((r) => r.width > 0); if (!rs.length) return;
    const fs = parseFloat(getComputedStyle(el).fontSize);
    if (rs.length > 1 && rs[0].width < fs * 1.5) { orphan.add(label(el) + '::before at a line end'); return; }
    const r = rs[0]; const dot = { left: r.left, right: r.left + fs * 0.9, top: r.top, bottom: r.bottom };
    if (!clipRect(el, dot)) return; // clipped away
    const cont = el.closest('.dots, tr, li') || (el.parentElement && el.parentElement.parentElement) || main;
    const box = { l: r.left, r: r.right, t: r.top, b: r.bottom };
    const marks = [...cont.querySelectorAll('button, .btn, svg, img, .catchip, .dot')].filter((m) => !el.contains(m) && !m.contains(el) && !hidden(m))
      .map((m) => { const q = m.getBoundingClientRect(); return { el: m, v: { l: q.left, r: q.right, t: q.top, b: q.bottom } }; });
    const before = texts.concat(marks).some((t) => cont.contains(t.el) && !el.contains(t.el) && vband(t.v, box) && t.v.r <= dot.left + 1);
    if (!before) orphan.add(label(el) + '::before starts a line in "' + cont.textContent.trim().replace(/\s+/g, ' ').slice(0, 40) + '"');
  });
  const lines = new Map();
  for (const t of texts) { const blk = t.el.closest('div,li,td,p,section,header,a,button') || main; if (!lines.has(blk)) lines.set(blk, []); lines.get(blk).push(t); }
  for (const [blk, ts] of lines) for (const t of ts) {
    if (t.el.textContent.trim() !== '·') continue;
    const row = ts.filter((o) => o !== t && Math.abs((o.v.t + o.v.b) / 2 - (t.v.t + t.v.b) / 2) < 6);
    if (!row.some((o) => o.v.r <= t.v.l + 1) || !row.some((o) => o.v.l >= t.v.r - 1)) orphan.add(label(blk) + ' stranded "·"');
  }
  return { overlap: [...overlap].slice(0, 20), clip: [...new Set(clip)].slice(0, 20), orphan: [...orphan].slice(0, 20) };
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


_vcache = {}


def visual(device, page_key, dev):
    if (page_key, dev) not in _vcache:
        page = device(dev)
        page.goto(PAGES[page_key])
        wait_loaded(page)
        _vcache[(page_key, dev)] = page.evaluate(VISUAL_JS, CLIP_ALLOW)
        page.context.close()
    return _vcache[(page_key, dev)]


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


VISUAL_KEYS = [(p, d) for p in sorted(PAGES) for d in VISUAL_DEVICES]


@pytest.mark.parametrize("page_key,dev", _mark(VISUAL_KEYS, OVERLAP_XFAIL, "fixed in the phone polish pass"))
def test_no_text_overlap(device, page_key, dev):
    assert visual(device, page_key, dev)["overlap"] == []


@pytest.mark.parametrize("page_key,dev", _mark(VISUAL_KEYS, CLIP_XFAIL, "fixed in the phone polish pass"))
def test_no_hard_clipped_text(device, page_key, dev):
    assert visual(device, page_key, dev)["clip"] == []


@pytest.mark.parametrize("page_key,dev", _mark(VISUAL_KEYS, ORPHAN_XFAIL, "fixed in the phone polish pass"))
def test_no_stranded_separators(device, page_key, dev):
    assert visual(device, page_key, dev)["orphan"] == []


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


@pytest.mark.parametrize("page_key", ["index", "transactions", "review", "budgets", "categories", "rules", "reports", "statements"])
def test_desktop_hides_phone_chrome(browser, session_state, page_key):
    """The phone-only chrome (topbar +, header ⋯, More sheet trigger, .phone-only controls, day headers) never shows at 1440."""
    ctx = browser.new_context(viewport={"width": 1440, "height": 900}, base_url=BASE_URL, service_workers="block", storage_state=session_state)
    try:
        page = ctx.new_page()
        page.goto(PAGES[page_key])
        wait_loaded(page)
        shown = page.evaluate("""() => Array.from(document.querySelectorAll('#tb-primary, .page-more, .phone-only, .bottomnav, tr.tx-day, .tbl-tx .col-ico'))
          .filter((el) => el.getClientRects().length && getComputedStyle(el).visibility !== 'hidden').map((el) => el.id || el.className)""")
        assert shown == [], f"{page_key}: phone-only chrome visible on desktop: {shown}"
    finally:
        ctx.close()
