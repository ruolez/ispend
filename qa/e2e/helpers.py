"""Shared helpers for the iSpend Playwright smoke suite: console/network recorder,
data-loaded waits, DOM scans and read-only interaction probes."""
import re
import time

from playwright.sync_api import Error as PwError
from playwright.sync_api import Page, TimeoutError as PwTimeout

PAGES = {
    "index": "/index.html",
    "transactions": "/transactions.html",
    "review": "/review.html",
    "import": "/import.html",
    "statements": "/statements.html",
    "categories": "/categories.html",
    "rules": "/rules.html",
    "reports": "/reports.html",
    "budgets": "/budgets.html",
    "insights": "/insights.html",
    "settings": "/settings.html",
}
CHART_PAGES = {"index", "categories", "reports"}
SLOW_MS = 1500
BAD_TEXT = re.compile(r"(\bundefined\b|\bNaN\b|\bnull\b|\[object Object\]|\$NaN|−NaN|\bInvalid Date\b)")

# Words that identify a mutating / destructive control. Matched against text, aria-label, title and data-act.
DENY_WORDS = [
    "delete", "remove", "commit", "save", "apply", "run", "merge", "reset", "logout", "sign out", "generate",
    "upload", "learn", "pair", "dismiss", "flip", "reparse", "renormalize", "re-detect", "discard", "undo",
    "forget", "roll back", "archive", "restore", "deactivate", "activate", "duplicate", "import", "accept", "reject",
    "regenerate", "rename", "create", "copy", "set budget", "remove budget", "change limit", "tag", "new tag",
]


class Recorder:
    """Attach to a page; collects console errors/warnings, page errors, failed and slow requests,
    each tagged with the current probe label so per-step deltas can be reported."""

    def __init__(self, page: Page):
        self.page = page
        self.label = "load"
        self.console = []
        self.pageerrors = []
        self.failed = []
        self.http_errors = []
        self.slow = []
        self.requests = []
        self._start = {}
        page.on("console", self._on_console)
        page.on("pageerror", self._on_pageerror)
        page.on("request", self._on_request)
        page.on("requestfailed", self._on_failed)
        page.on("response", self._on_response)
        page.on("filechooser", lambda fc: None)

    def mark(self, label):
        self.label = label

    def _on_console(self, msg):
        if msg.type not in ("error", "warning"):
            return
        loc = msg.location or {}
        self.console.append({
            "label": self.label, "type": msg.type, "text": msg.text[:400],
            "source": f"{loc.get('url', '')}:{loc.get('lineNumber', '')}", "url": self.page.url,
        })

    def _on_pageerror(self, err):
        self.pageerrors.append({"label": self.label, "text": str(err)[:600], "url": self.page.url})

    def _on_request(self, req):
        self._start[req] = time.monotonic()

    def _on_failed(self, req):
        f = req.failure or ""
        self.failed.append({"label": self.label, "url": req.url, "method": req.method, "failure": f, "type": req.resource_type})

    def _on_response(self, res):
        req = res.request
        t0 = self._start.pop(req, None)
        ms = round((time.monotonic() - t0) * 1000) if t0 else None
        entry = {"label": self.label, "url": res.url, "method": req.method, "status": res.status, "ms": ms, "type": req.resource_type}
        self.requests.append(entry)
        if res.status >= 400:
            self.http_errors.append(entry)
        if ms is not None and ms > SLOW_MS:
            self.slow.append(entry)

    def snapshot(self):
        return {k: len(getattr(self, k)) for k in ("console", "pageerrors", "failed", "http_errors", "slow")}

    def delta(self, snap):
        out = {}
        for k, n in snap.items():
            items = getattr(self, k)[n:]
            if items:
                out[k] = items
        return out

    def summary(self):
        return {
            "console": self.console, "pageerrors": self.pageerrors, "failed": self.failed,
            "http_errors": self.http_errors, "slow": self.slow,
            "api_calls": [r for r in self.requests if "/api/" in r["url"]],
        }


SKEL_STATE_JS = """
() => {
  const vis = (el) => el.getClientRects().length > 0;
  const main = document.getElementById('main') || document.body;
  const skel = Array.from(main.querySelectorAll('.skel, .skel-row')).filter(vis);
  const loading = Array.from(main.querySelectorAll('.is-loading')).filter((el) => vis(el) && !el.matches('.btn'));
  const spin = Array.from(main.querySelectorAll('.spinner')).filter(vis);
  const sel = (el) => el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') + (el.className ? '.' + String(el.className).trim().split(/\\s+/).slice(0, 3).join('.') : '');
  const host = (el) => { const c = el.closest('.card, section, [id]'); return c ? sel(c) : sel(el); };
  return { skel: skel.length, loading: loading.length, spinners: spin.length,
    where: Array.from(new Set([...skel, ...loading].map(host))).slice(0, 8) };
}
"""

TEXT_SCAN_JS = r"""
(pattern) => {
  const bad = new RegExp(pattern);
  const out = [];
  const sel = (el) => el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') + (el.className ? '.' + String(el.className).trim().split(/\s+/).slice(0, 3).join('.') : '');
  const vis = (el) => el && el.getClientRects().length > 0;
  const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let n;
  while ((n = w.nextNode())) {
    const t = n.nodeValue; if (!t || !bad.test(t)) continue;
    const el = n.parentElement; if (!el || !vis(el) || el.closest('script,style,noscript')) continue;
    out.push({ kind: 'text', text: t.trim().slice(0, 140), el: sel(el) });
  }
  document.querySelectorAll('[title],[data-tip],[aria-label],[placeholder],[alt]').forEach((el) => {
    ['title', 'data-tip', 'aria-label', 'placeholder', 'alt'].forEach((a) => { const v = el.getAttribute(a); if (v && bad.test(v)) out.push({ kind: a, text: v.slice(0, 140), el: sel(el) }); });
  });
  return out.slice(0, 40);
}
"""

SHELL_JS = """
() => {
  const vis = (s) => { const el = document.querySelector(s); return !!(el && el.getClientRects().length && getComputedStyle(el).visibility !== 'hidden'); };
  const h1 = document.querySelector('#main h1, main h1, h1');
  const sbMode = document.documentElement.getAttribute('data-sidebar');
  const sb = document.querySelector('.sidebar');
  const sbOnScreen = !!(sb && sb.getBoundingClientRect().right > 0 && getComputedStyle(sb).display !== 'none');
  return { sidebar: sbOnScreen, sidebarMode: sbMode, bottomnav: vis('.bottomnav'), topbar: vis('.topbar'),
    h1: h1 ? h1.textContent.trim() : null, title: document.title, user: window.currentUser ? window.currentUser.username : null,
    errorBoxes: Array.from(document.querySelectorAll('.error-box')).filter((e) => e.getClientRects().length && getComputedStyle(e).display !== 'none').map((e) => e.textContent.trim().slice(0, 200)),
    theme: document.documentElement.getAttribute('data-theme'), themeMode: document.documentElement.getAttribute('data-theme-mode'),
    bg: getComputedStyle(document.body).backgroundColor, hScroll: document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
    scrollWidth: document.documentElement.scrollWidth, clientWidth: document.documentElement.clientWidth };
}
"""

PERF_JS = """
() => {
  const nav = performance.getEntriesByType('navigation')[0] || {};
  const res = performance.getEntriesByType('resource').map((r) => ({ name: r.name.replace(location.origin, ''), type: r.initiatorType, ms: Math.round(r.duration), size: r.transferSize || 0, enc: r.encodedBodySize || 0 }));
  const api = res.filter((r) => r.name.startsWith('/api/'));
  const counts = {}; api.forEach((r) => { counts[r.name] = (counts[r.name] || 0) + 1; });
  const dups = Object.entries(counts).filter(([, n]) => n > 1).map(([u, n]) => `${u} x${n}`);
  const byType = (t) => res.filter((r) => r.type === t || (t === 'script' && r.name.endsWith('.js')) || (t === 'link' && r.name.endsWith('.css')));
  const largest = (arr) => arr.slice().sort((a, b) => b.enc - a.enc)[0] || null;
  return { dcl: Math.round(nav.domContentLoadedEventEnd || 0), load: Math.round(nav.loadEventEnd || 0), ttfb: Math.round(nav.responseStart || 0),
    requests: res.length, bytes: res.reduce((s, r) => s + r.size, 0), apiCalls: api.length, apiDups: dups, apiSlow: api.filter((r) => r.ms > 1500).map((r) => `${r.name} ${r.ms}ms`),
    largestJs: largest(byType('script')), largestCss: largest(byType('link')),
    chartLoaded: typeof Chart !== 'undefined', chartBytes: (res.find((r) => r.name.includes('chart.umd')) || {}).enc || 0, canvases: document.querySelectorAll('canvas').length,
    slowest: res.slice().sort((a, b) => b.ms - a.ms).slice(0, 3).map((r) => `${r.name} ${r.ms}ms`) };
}
"""

LAYERS_JS = """
() => ({ layers: (typeof ui !== 'undefined' && ui.layers) ? ui.layers.length : 0,
  dom: document.querySelectorAll('.modal-backdrop, .drawer, .popover, .palette-backdrop').length,
  sidebarOpen: document.body.classList.contains('sidebar-open'),
  top: (() => { const m = document.querySelector('.modal-head h2, #drawer-title, .palette'); return m ? (m.textContent || 'palette').trim().slice(0, 60) : null; })() })
"""

BUTTONS_JS = """
(deny) => {
  const vis = (el) => el.getClientRects().length > 0 && getComputedStyle(el).visibility !== 'hidden';
  const inside = 'tbody, .rv-list, #cat-tree, #rule-list, .list, .bd-table, .chart-legend, .legend-list, .modal, .drawer, .popover, .menu, .palette-backdrop, .anom-list, .rec-table, .floatbar, #toast-root';
  const skip = '.seg-btn, .tab, [type=submit], .modal-close, .drawer-close, .toast-close, .file-tab, .cat-chevron, .legend-item, .chip, .swatch';
  const out = [];
  document.querySelectorAll('[data-qa-idx]').forEach((b) => b.removeAttribute('data-qa-idx'));  // tags from an earlier collection
  document.querySelectorAll('#main button, #topbar button, .bottomnav button').forEach((b) => {
    if (!vis(b) || b.disabled || b.closest(inside) || b.matches(skip)) return;
    const label = (b.textContent.trim() || b.getAttribute('aria-label') || b.getAttribute('title') || b.id || '').slice(0, 50);
    const hay = `${b.textContent} ${b.getAttribute('aria-label') || ''} ${b.getAttribute('title') || ''} ${b.getAttribute('data-tip') || ''} ${b.dataset.act || ''}`.toLowerCase();
    const denied = deny.find((w) => hay.includes(w));
    const idx = out.length;
    b.setAttribute('data-qa-idx', String(idx));
    out.push({ idx, label, id: b.id, act: b.dataset.act || '', denied: denied || null });
  });
  return out;
}
"""


def unexpected_console(rec, expected_urls=()):
    """Console entries minus the browser's own 'Failed to load resource' line for requests whose
    non-2xx status is the designed outcome (e.g. the 401 that drives the login redirect)."""
    out = []
    for c in rec.console:
        if c["text"].startswith("Failed to load resource") and any(u in c["source"] for u in expected_urls):
            continue
        out.append(c)
    return out


def settle(page: Page, ms=400, idle_timeout=6000):
    """Short pause then wait for network idle; a timeout here is not itself a failure (polling pages)."""
    page.wait_for_timeout(ms)
    try:
        page.wait_for_load_state("networkidle", timeout=idle_timeout)
    except PwTimeout:
        pass


def wait_loaded(page: Page, authenticated=True, timeout=12000):
    """Wait for the auth gate (window.currentUser), network idle, then for every skeleton /
    loading shimmer in #main to clear. Returns timing + anything still stuck."""
    t0 = time.monotonic()
    if authenticated:
        page.wait_for_function("() => !!window.currentUser", timeout=timeout)
    settle(page, 150)
    stuck = None
    try:
        page.wait_for_function("() => { const s = (" + SKEL_STATE_JS + ")(); return s.skel === 0 && s.loading === 0; }", timeout=8000)
    except PwTimeout:
        stuck = page.evaluate(SKEL_STATE_JS)
    # let any last render settle (charts animate 400ms)
    page.wait_for_timeout(450)
    return {"loaded_ms": round((time.monotonic() - t0) * 1000), "stuck": stuck}


def scan_text(page: Page):
    return page.evaluate(TEXT_SCAN_JS, BAD_TEXT.pattern)


def shell_state(page: Page):
    return page.evaluate(SHELL_JS)


def perf_state(page: Page):
    return page.evaluate(PERF_JS)


def layers(page: Page):
    return page.evaluate(LAYERS_JS)


def close_layers(page: Page, max_esc=4):
    """Press Escape until no layer is open. Returns (closed_by_escape: bool, escapes_used, final_state)."""
    for i in range(max_esc):
        st = layers(page)
        if not st["layers"] and not st["dom"] and not st["sidebarOpen"]:
            return True, i, st
        page.keyboard.press("Escape")
        page.wait_for_timeout(250)
    st = layers(page)
    if not st["layers"] and not st["dom"] and not st["sidebarOpen"]:
        return True, max_esc, st
    # force close so the run can continue
    page.evaluate("() => { try { while (ui.layers.length) ui.closeTop(); } catch (e) {} document.querySelectorAll('.modal-backdrop, .palette-backdrop').forEach((e) => e.remove()); document.body.classList.remove('sidebar-open'); }")
    page.wait_for_timeout(150)
    return False, max_esc, st


def rgb(s):
    m = re.findall(r"\d+", s or "")
    return tuple(int(x) for x in m[:3]) if len(m) >= 3 else None


def safe(fn, default=None):
    try:
        return fn()
    except (PwError, PwTimeout):
        return default
