"""PWA contract: manifest, icons, service worker and the offline page.

Run:  <venv>/bin/pytest qa/e2e/test_pwa.py -q -p no:cacheprovider   (on its own, like every
Playwright module here: two modules importing the session-scoped `pw` fixture in one process
would open a second sync_playwright() inside the first one's loop.)
The static tests need no stack; the served and browser tests run against ISPEND_BASE_URL
(default http://localhost:5559). This is the only suite that lets the service worker register —
every other Playwright context passes service_workers="block" (see helpers.SW_BLOCKED_WARNING).
"""
# ruff: noqa: F811  (pytest fixtures imported from smoke_fixtures are re-bound as test parameters)
import json
import pathlib
import re
import struct

import pytest
import requests
from playwright.sync_api import Error as PwError

from helpers import Recorder
from smoke_fixtures import BASE_URL, INIT_JS, api_login, browser, pw  # noqa: F401  (pytest fixtures)

FRONTEND = pathlib.Path(__file__).resolve().parents[2] / "frontend"
MANIFEST = FRONTEND / "manifest.json"
SHELL_PAGES = {"index", "transactions", "review", "import", "statements", "categories", "rules",
               "reports", "budgets", "insights", "settings", "billing", "admin"}
NO_SW_PAGES = {"landing", "privacy", "terms", "offline"}
HEAD_LINKS = ('<link rel="manifest" href="/manifest.json">',
              '<link rel="apple-touch-icon" href="/img/icons/apple-touch-icon.png">',
              '<meta name="apple-mobile-web-app-title" content="iSpend">')


def pages():
    return sorted(FRONTEND.glob("*.html"), key=lambda p: p.stem)


def png_size(path):
    """(width, height) from the IHDR chunk — no Pillow in CI."""
    head = path.read_bytes()[:24]
    assert head[:8] == b"\x89PNG\r\n\x1a\n", f"{path.name} is not a PNG"
    return struct.unpack(">II", head[16:24])


# ---------- static: the files in the repo ----------

def test_every_page_declares_the_manifest_and_apple_icon():
    missing = {p.stem: [t for t in HEAD_LINKS if t not in p.read_text()] for p in pages()}
    assert {k: v for k, v in missing.items() if v} == {}


def test_shell_pages_register_and_cover_the_viewport():
    by_page = {}
    for p in pages():
        s = p.read_text()
        by_page[p.stem] = (
            '<script src="/js/pwa.js"></script>' in s,
            "viewport-fit=cover" in s,
            'content="black-translucent"' in s,
        )
    expected = {}
    for name in by_page:
        if name in SHELL_PAGES:
            expected[name] = (True, True, True)
        elif name == "offline":
            expected[name] = (False, True, True)
        elif name in NO_SW_PAGES:
            expected[name] = (False, False, False)
        else:  # auth pages: register, but Safari's own insets suit a page with no topbar
            expected[name] = (True, False, False)
    assert by_page == expected


def test_manifest_shape_and_icons():
    m = json.loads(MANIFEST.read_text())
    assert (m["id"], m["start_url"], m["scope"], m["display"]) == ("/index.html", "/index.html", "/", "standalone")
    assert {i["purpose"] for i in m["icons"]} == {"any", "maskable"}
    declared = {i["src"]: tuple(int(x) for x in i["sizes"].split("x")) for i in m["icons"]}
    declared["/img/icons/apple-touch-icon.png"] = (180, 180)
    actual = {src: png_size(FRONTEND / src.lstrip("/")) for src in declared}
    assert actual == declared
    assert [s["url"] for s in m["shortcuts"]] == ["/import.html", "/transactions.html", "/review.html"]


def test_offline_page_is_self_contained():
    s = (FRONTEND / "offline.html").read_text()
    external = re.findall(r'(?:src|href)="(/[^"]+)"', s)
    assert sorted(set(external)) == ["/favicon.svg", "/img/icons/apple-touch-icon.png", "/index.html", "/manifest.json"]
    assert "<script" not in s
    assert re.search(r"url\((?!#)", s) is None, "no CSS url() fetches — only the inline SVG's #id references"


def test_service_worker_never_touches_the_api():
    s = (FRONTEND / "sw.js").read_text()
    assert "url.pathname.startsWith('/api/')" in s
    assert s.index("startsWith('/api/')") < s.index("req.mode !== 'navigate'"), "the /api/ guard must run before the navigation branch"
    assert re.search(r"caches\.open\(CACHE\)\.then\(\(\w+\) => \w+\.add\(", s), "install precaches through caches.open().add()"
    assert "const OFFLINE_URL = '/offline.html'" in s
    assert "caches.put" not in s and "cache.put" not in s, "nothing but the offline page may ever be stored"


# ---------- served: what nginx hands out ----------

@pytest.fixture(scope="module")
def anon():
    s = requests.Session()
    s.headers["User-Agent"] = "ispend-qa-pwa"
    return s


def test_manifest_and_worker_are_served(anon):
    r = anon.get(f"{BASE_URL}/manifest.json")
    assert (r.status_code, r.headers["Content-Type"].split(";")[0]) == (200, "application/json")
    assert r.json()["start_url"] == "/index.html"
    r = anon.get(f"{BASE_URL}/sw.js")
    assert r.status_code == 200
    assert r.headers["Content-Type"].split(";")[0] in ("application/javascript", "text/javascript")
    assert "no-store" in r.headers["Cache-Control"], "a cached worker script would outlive its deploy"
    r = anon.get(f"{BASE_URL}/offline.html")
    assert (r.status_code, r.headers["Content-Type"].split(";")[0]) == (200, "text/html")


def test_icons_are_served_as_png(anon):
    m = json.loads(MANIFEST.read_text())
    got = {}
    for src in [i["src"] for i in m["icons"]] + ["/img/icons/apple-touch-icon.png"]:
        r = anon.get(f"{BASE_URL}{src}")
        got[src] = (r.status_code, r.headers["Content-Type"], r.content[:8] == b"\x89PNG\r\n\x1a\n")
    assert got == {src: (200, "image/png", True) for src in got}


# ---------- browser: the worker in a real chromium ----------

@pytest.fixture
def sw_context(browser):
    ctx = browser.new_context(viewport={"width": 1366, "height": 900}, base_url=BASE_URL)
    ctx.add_init_script(f"({INIT_JS})('light');")
    sw_console = []
    ctx.on("console", lambda m: sw_console.append(m.text) if m.type in ("error", "warning") else None)
    api_login(ctx, "admin")
    page = ctx.new_page()
    rec = Recorder(page)
    yield ctx, page, rec, sw_console
    ctx.close()


READY_JS = "() => navigator.serviceWorker.ready.then((r) => new URL(r.scope).pathname)"


def test_worker_registers_and_controls_the_app(sw_context):
    ctx, page, rec, sw_console = sw_context
    page.goto("/index.html")
    assert page.evaluate(READY_JS) == "/"
    page.reload()
    page.wait_for_selector("h1")
    assert page.evaluate("() => !!navigator.serviceWorker.controller")
    for path in ("/transactions.html", "/settings.html"):
        page.goto(path)
        page.wait_for_selector(".sidebar, .bottomnav")
    assert (rec.console, rec.pageerrors, rec.failed, rec.http_errors, sw_console) == ([], [], [], [], [])


def test_offline_navigation_lands_on_the_offline_page(sw_context):
    ctx, page, _, _ = sw_context
    page.goto("/index.html")
    page.evaluate(READY_JS)
    ctx.set_offline(True)
    try:
        page.goto("/transactions.html")
        assert page.locator("h1").inner_text() == "You’re offline"
        assert page.locator("a.btn").get_attribute("href") == "/index.html"
        with pytest.raises(PwError) as exc:
            page.goto("/api/auth/me")
        assert "ERR_INTERNET_DISCONNECTED" in str(exc.value), "the worker must never answer for /api/"
    finally:
        ctx.set_offline(False)
