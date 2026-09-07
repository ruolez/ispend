"""Fixtures for the smoke suite, imported by test_smoke.py (qa/e2e/conftest.py belongs to the API suite).
One chromium per session, one fresh context per test (isolated storage), API-cookie login per persona,
theme seeded before any page script runs, JSONL result sink."""
import json
import os
import pathlib

import pytest
from playwright.sync_api import sync_playwright

from helpers import Recorder

BASE_URL = os.environ.get("ISPEND_BASE_URL", "http://localhost:5559")
QA_DIR = pathlib.Path(__file__).resolve().parents[1]
REPORTS = QA_DIR / "reports"
SHOTS = REPORTS / "screenshots"
RESULTS = REPORTS / "smoke-results.jsonl"

PERSONAS = {"admin": ("admin", "admin"), "qa_tester": ("qa_tester", "qa-tester-pass1")}
VIEWPORTS = {"1440": (1440, 900), "390": (390, 844)}
THEMES = ["light", "dark"]

INIT_JS = """
(theme) => {
  // seed the stored theme once per context; never clobber a choice the page itself made later
  try { if (theme && !localStorage.getItem('ispend.theme')) localStorage.setItem('ispend.theme', theme); } catch (e) {}
  window.addEventListener('unhandledrejection', (e) => { const r = e.reason; console.error('UNHANDLED_REJECTION: ' + (r && (r.stack || r.message) || r)); });
}
"""


def record(kind, **data):
    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    with RESULTS.open("a") as f:
        f.write(json.dumps({"kind": kind, **data}, default=str) + "\n")


@pytest.fixture(scope="session", autouse=True)
def _results_sink():
    REPORTS.mkdir(parents=True, exist_ok=True)
    SHOTS.mkdir(parents=True, exist_ok=True)
    if RESULTS.exists() and os.environ.get("SMOKE_APPEND") != "1":
        RESULTS.unlink()
    yield


@pytest.fixture(scope="session")
def pw():
    with sync_playwright() as p:
        yield p


@pytest.fixture(scope="session")
def browser(pw):
    b = pw.chromium.launch(headless=True)
    yield b
    b.close()


def api_login(context, persona):
    if persona in (None, "anon"):
        return None
    user, pw_ = PERSONAS[persona]
    r = context.request.post(f"{BASE_URL}/api/auth/login", data={"username": user, "password": pw_})
    assert r.ok, f"login as {persona} failed: {r.status} {r.text()[:200]}"
    return r.json()


@pytest.fixture
def make_context(browser):
    """make_context(persona, theme, viewport_key, color_scheme=None, stored_theme=True) -> (context, page, recorder)."""
    created = []

    def _make(persona="admin", theme="light", viewport="1440", color_scheme=None, stored_theme=True):
        w, h = VIEWPORTS[viewport]
        kw = {"viewport": {"width": w, "height": h}, "accept_downloads": True, "base_url": BASE_URL}
        if color_scheme:
            kw["color_scheme"] = color_scheme
        ctx = browser.new_context(**kw)
        ctx.add_init_script(f"({INIT_JS})({json.dumps(theme if stored_theme else None)});")
        api_login(ctx, persona)
        page = ctx.new_page()
        rec = Recorder(page)
        created.append(ctx)
        return ctx, page, rec

    yield _make
    for c in created:
        try:
            c.close()
        except Exception:  # noqa: BLE001
            pass


@pytest.fixture
def base_url():
    return BASE_URL
