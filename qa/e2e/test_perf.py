"""Screen-switch performance budget on an emulated phone (iPhone 13 viewport, 4x CPU throttle,
80 ms round trip). One walk of the app per module (perf_probe.measure); each test reads the result.

Run:  <venv>/bin/pytest qa/e2e/test_perf.py -q -p no:cacheprovider   (on its own, like every
Playwright module here). Service workers are allowed; see perf_probe.py.
"""
import pytest
from playwright.sync_api import sync_playwright

import perf_probe

WARM_TABS_MS = 200  # a bottom tab visited before shows its content within this, cached data included


@pytest.fixture(scope="module")
def walk():
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True, **perf_probe.LAUNCH_ARGS)
        res = perf_probe.measure(b, perf_probe.phone(p))
        b.close()
    print("\n" + perf_probe.table(res))
    return res


def test_every_screen_settles(walk):
    assert [n["path"] for n in walk["navs"]] == perf_probe.SEQUENCE
    assert [n for n in walk["navs"] if n["ready"] <= 0] == []


def test_chain_depth_is_measured_from_request_timing():
    a, b, c = ({"start": 0, "end": 80}, {"start": 10, "end": 90}, {"start": 95, "end": 170})
    assert (perf_probe.chain_depth([]), perf_probe.chain_depth([a, b]), perf_probe.chain_depth([c, a, b])) == (0, 1, 2)


@pytest.mark.xfail(strict=True, reason="phase 1: the service worker serves the shell")
def test_warm_switches_fetch_no_shell_file_from_the_network(walk):
    assert {n["path"]: n["network_shell"] for n in walk["navs"] if n["network_shell"]} == {}


@pytest.mark.xfail(strict=True, reason="phase 1: pages are no longer no-store, so Back is a bfcache restore")
def test_back_restores_from_the_back_forward_cache(walk):
    assert walk["back"] == {"restored": True, "reasons": walk["back"]["reasons"]}


@pytest.mark.xfail(strict=True, reason="phase 2: page data no longer waits for /api/auth/me or for each other")
def test_no_screen_chains_api_requests(walk):
    assert {n["path"]: n["api"] for n in walk["navs"] if n["depth"] > 1} == {}


@pytest.mark.xfail(strict=True, reason="phase 3: bottom tabs render the last-seen data at once")
def test_warm_bottom_tabs_show_content_fast(walk):
    second_round = walk["navs"][len(perf_probe.TABS):2 * len(perf_probe.TABS)]
    assert {n["path"]: n["content"] for n in second_round if n["content"] > WARM_TABS_MS} == {}


@pytest.mark.xfail(strict=True, reason="phase 4: the topbar and bottom nav exist at first paint")
def test_first_paint_already_has_the_app_chrome(walk):
    assert {n["path"]: (n["fcp"], n["chrome"]) for n in walk["navs"] if n["fcp"] is None or n["chrome"] > n["fcp"]} == {}
