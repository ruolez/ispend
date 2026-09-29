"""PWA contract: manifest, icons, service worker and the offline page.

Run:  <venv>/bin/pytest qa/e2e/test_pwa.py -q -p no:cacheprovider   (on its own, like every
Playwright module here: two modules importing the session-scoped `pw` fixture in one process
would open a second sync_playwright() inside the first one's loop.)
The static tests need no stack; the served and browser tests run against ISPEND_BASE_URL
(default http://localhost:5559). This is the only suite that lets the service worker register —
every other Playwright context passes service_workers="block" (see helpers.SW_BLOCKED_WARNING).

The worker is stamped by nginx/40-ispend-sw.sh at container start ("dev" under the dev overlay, a
content hash in production). The `stamp` fixture re-runs the stamper inside the nginx container to
test both modes, and puts the container's own mode back afterwards.
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
from perf_probe import stamp_worker
from smoke_fixtures import BASE_URL, INIT_JS, api_login, browser, pw  # noqa: F401  (pytest fixtures)

ROOT = pathlib.Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend"
MANIFEST = FRONTEND / "manifest.json"
ADMIN_MANIFEST = FRONTEND / "manifest-admin.json"
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

# The console installs as its own app, opening on /admin.
ADMIN_HEAD_LINKS = ('<link rel="manifest" href="/manifest-admin.json">', HEAD_LINKS[1],
                    '<meta name="apple-mobile-web-app-title" content="iSpend Admin">')


def test_every_page_declares_the_manifest_and_apple_icon():
    missing = {p.stem: [t for t in (ADMIN_HEAD_LINKS if p.stem == "admin" else HEAD_LINKS) if t not in p.read_text()]
               for p in pages()}
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


def test_stylesheets_come_before_head_scripts():
    """A page revealed while a <head> script is still loading, before its stylesheets are parsed, has no
    @view-transition opt-in yet: Chromium aborts the screen change's transition and the rejection lands in
    a page that never saw it (it reached the error toast). Stylesheets first makes them render-blocking."""
    late = {}
    for p in pages():
        head = p.read_text().split("</head>")[0]
        first_script, last_css = head.find("<script"), head.rfind('rel="stylesheet"')
        if first_script >= 0 and last_css > first_script:
            late[p.stem] = "stylesheet after a script"
    assert late == {}


def test_manifest_shape_and_icons():
    m = json.loads(MANIFEST.read_text())
    assert (m["id"], m["start_url"], m["scope"], m["display"]) == ("/index.html", "/index.html", "/", "standalone")
    assert {i["purpose"] for i in m["icons"]} == {"any", "maskable"}
    declared = {i["src"]: tuple(int(x) for x in i["sizes"].split("x")) for i in m["icons"]}
    declared["/img/icons/apple-touch-icon.png"] = (180, 180)
    actual = {src: png_size(FRONTEND / src.lstrip("/")) for src in declared}
    assert actual == declared
    assert [s["url"] for s in m["shortcuts"]] == ["/import.html", "/transactions.html", "/review.html"]


def test_admin_manifest_opens_the_console():
    m, app = json.loads(ADMIN_MANIFEST.read_text()), json.loads(MANIFEST.read_text())
    assert (m["id"], m["start_url"], m["scope"], m["name"]) == ("/admin", "/admin", "/", "iSpend Admin")
    assert m["icons"] == app["icons"]
    assert [s["url"] for s in m["shortcuts"]] == ["/admin#customers", "/admin#revenue"]


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
    assert re.search(r"caches\.open\(CACHE\)\.then\(\(\w+\) => \w+\.addAll\(", s), "install precaches through caches.open().addAll()"
    assert "const OFFLINE_URL = '/offline.html'" in s
    assert "caches.put" not in s and "cache.put" not in s, "only the stamped precache list is ever stored — never a runtime response"


def test_unstamped_worker_is_valid_and_caches_nothing():
    """The raw file (served if the stamp is missing) must parse, and its placeholders must switch caching off."""
    s = (FRONTEND / "sw.js").read_text()
    assert "const BUILD = '__ISPEND_BUILD__';" in s
    assert "const PRECACHE = [/*__ISPEND_PRECACHE__*/];" in s
    assert "const CACHING = !BUILD.startsWith('__') && BUILD !== 'dev' && PRECACHE.length > 0;" in s


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
    r = anon.get(f"{BASE_URL}/manifest-admin.json")
    assert (r.status_code, r.json()["start_url"]) == (200, "/admin")
    r = anon.get(f"{BASE_URL}/sw.js")
    assert r.status_code == 200
    assert r.headers["Content-Type"].split(";")[0] in ("application/javascript", "text/javascript")
    assert r.headers["Cache-Control"] == "no-cache", "the browser must byte-check the worker on every visit, or a deploy goes unnoticed"
    r = anon.get(f"{BASE_URL}/offline.html")
    assert (r.status_code, r.headers["Content-Type"].split(";")[0]) == (200, "text/html")


def test_shell_revalidates_and_the_api_is_never_stored(anon):
    got = {path: anon.get(f"{BASE_URL}{path}").headers.get("Cache-Control")
           for path in ("/index.html", "/css/app.css", "/js/nav.js", "/api/health")}
    assert got == {"/index.html": "no-cache", "/css/app.css": "no-cache", "/js/nav.js": "no-cache",
                   "/api/health": "no-store, no-cache, must-revalidate"}


# ---------- the stamper (nginx/40-ispend-sw.sh) ----------

def _worker(anon):
    s = anon.get(f"{BASE_URL}/sw.js").text
    build = re.search(r"const BUILD = '([^']*)';", s).group(1)
    precache = json.loads(re.search(r"const PRECACHE = (\[.*?\]);", s).group(1))
    return build, precache


@pytest.fixture
def stamp(anon):
    """stamp('on'|'off') re-stamps the served worker; the container's own mode is restored afterwards."""
    if not stamp_worker(check=True):
        pytest.skip("needs the nginx container (docker compose exec)")

    def _stamp(mode):
        stamp_worker(mode)
        return _worker(anon)

    yield _stamp
    stamp_worker(None)


APP_HTML = sorted(p for p in FRONTEND.glob("*.html") if p.stem not in {"landing", "privacy", "terms"})


def test_stamp_precaches_every_file_the_app_pages_reference(stamp, anon):
    build, precache = stamp("on")
    assert re.fullmatch(r"[0-9a-f]{12}", build)
    referenced = set()
    for p in APP_HTML:
        referenced.add("/admin" if p.stem == "admin" else f"/{p.name}")   # the console lives at /admin
        referenced.update(re.findall(r'(?:src|href)="(/[^"?#]+\.(?:js|css|svg|png|json))"', p.read_text()))
    referenced.update({"/fonts/InterVariable-latin-v2.woff2", "/vendor/chart.umd.js"})
    assert sorted(referenced - set(precache)) == []
    # Without following redirects: a cached redirect cannot answer a navigation, so every entry must
    # be the page itself (this is how /admin.html once slipped in and broke the console).
    status = {u: anon.get(f"{BASE_URL}{u}", allow_redirects=False).status_code for u in precache}
    assert {u: code for u, code in status.items() if code != 200} == {}
    assert [u for u in precache if u.startswith("/api/") or u in ("/sw.js", "/landing.html")] == []


def test_stamp_changes_with_the_files_and_off_means_dev(stamp):
    build, precache = stamp("on")
    assert stamp("on") == (build, precache), "same files, same build"
    assert stamp("off") == ("dev", [])


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
    api_login(ctx, "qa_tester")
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


def test_a_skipped_screen_transition_is_not_an_error(sw_context):
    """Chrome rejects a cross-document view transition it had to skip; that is not the person's
    problem, so no page error and no toast — while a real failure still gets one."""
    ctx, page, rec, _ = sw_context
    page.goto("/index.html")
    page.wait_for_function("() => !!window.currentUser")
    page.evaluate("""() => { Promise.reject(new DOMException(
        'Transition was aborted because of invalid state. Page already revealed', 'InvalidStateError')); }""")
    page.wait_for_timeout(400)
    assert (rec.pageerrors, page.locator("#toast-root .toast").count()) == ([], 0)
    page.evaluate("() => { Promise.reject(new Error('a real failure')); }")
    page.locator("#toast-root .toast", has_text="a real failure").wait_for()


def test_the_console_loads_under_the_production_worker(browser, stamp):
    """The production stamp precaches the console; a reload served from that cache must work."""
    stamp("on")
    ctx = browser.new_context(viewport={"width": 1366, "height": 900}, base_url=BASE_URL)
    try:
        api_login(ctx, "admin")
        page = ctx.new_page()
        rec = Recorder(page)
        page.goto("/admin")
        page.evaluate(READY_JS)
        page.reload()
        page.wait_for_selector("#admin-title")
        assert page.evaluate("() => !!navigator.serviceWorker.controller")
        page.goto("/admin?range=90d#customers")
        page.reload()
        page.wait_for_selector(".sb-nav a[data-page='customers'][aria-current='page']")
        assert (rec.pageerrors, rec.failed) == ([], [])
    finally:
        ctx.close()


def test_offline_navigation_lands_on_the_offline_page(sw_context, stamp):
    """Dev stamp: nothing is cached but the offline page, so an offline navigation lands there."""
    stamp("off")
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


def test_stamped_worker_serves_the_shell_from_its_cache(sw_context, stamp):
    """Production stamp: after the first visit every shell file comes from the worker, and an offline
    navigation still opens the app page, which then explains it needs a connection."""
    stamp("on")
    ctx, page, rec, sw_console = sw_context
    page.goto("/index.html")
    page.evaluate(READY_JS)
    page.wait_for_function("() => caches.keys().then((k) => k.some((n) => /^ispend-shell-[0-9a-f]{12}$/.test(n)))")
    page.reload()
    page.wait_for_function("() => !!window.currentUser")
    shell = []
    page.on("response", lambda r: shell.append((r.url.replace(BASE_URL, ""), r.from_service_worker))
            if r.url.startswith(BASE_URL) and "/api/" not in r.url else None)
    page.goto("/transactions.html")
    page.wait_for_function("() => !!window.currentUser")
    assert shell and [u for u, from_sw in shell if not from_sw] == []
    ctx.set_offline(True)
    try:
        page.goto("/review.html")
        page.wait_for_selector("#main .error-box")
        # This tab already knows the user, so the page gets past the auth gate; its own request then says why it failed.
        assert (page.locator("#tb-title").inner_text(), page.locator("#main .error-box").first.inner_text().strip()) == (
            "Review", "You’re offline — iSpend needs a connection for this. Try again once you’re back online.\nRetry")
    finally:
        ctx.set_offline(False)
    offline_fetch = "Failed to load resource: net::ERR_INTERNET_DISCONNECTED"  # the page's own /api/auth/me
    assert (rec.pageerrors, [m for m in sw_console if m != offline_fetch]) == ([], [])
