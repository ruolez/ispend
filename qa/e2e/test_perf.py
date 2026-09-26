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
    if not perf_probe.stamp_worker(check=True):
        pytest.skip("needs the nginx container to stamp the production service worker")
    perf_probe.stamp_worker("on")  # the dev overlay stamps "dev"; measure the production worker
    try:
        with sync_playwright() as p:
            b = p.chromium.launch(**perf_probe.LAUNCH_ARGS)
            res = perf_probe.measure(b, perf_probe.phone(p))
            b.close()
    finally:
        perf_probe.stamp_worker(None)
    print("\n" + perf_probe.table(res))
    return res


def test_every_screen_settles(walk):
    assert [n["path"] for n in walk["navs"]] == perf_probe.SEQUENCE
    assert [n for n in walk["navs"] if n["ready"] <= 0] == []


def test_chain_depth_is_measured_from_request_timing():
    a, b, c = ({"start": 0, "end": 80}, {"start": 10, "end": 90}, {"start": 95, "end": 170})
    assert (perf_probe.chain_depth([]), perf_probe.chain_depth([a, b]), perf_probe.chain_depth([c, a, b])) == (0, 1, 2)


def test_warm_switches_fetch_no_shell_file_from_the_network(walk):
    assert {n["path"]: n["network_shell"] for n in walk["navs"] if n["network_shell"]} == {}


def test_back_restores_from_the_back_forward_cache(walk):
    assert walk["back"] == {"restored": True, "reasons": walk["back"]["reasons"]}


def test_no_screen_chains_api_requests(walk):
    assert {n["path"]: n["api"] for n in walk["navs"] if n["depth"] > 1} == {}


def test_warm_bottom_tabs_show_content_fast(walk):
    second_round = walk["navs"][len(perf_probe.TABS):2 * len(perf_probe.TABS)]
    assert {n["path"]: n["content"] for n in second_round if n["content"] > WARM_TABS_MS} == {}


@pytest.mark.xfail(strict=True, reason="phase 4: the topbar and bottom nav exist at first paint")
def test_first_paint_already_has_the_app_chrome(walk):
    assert {n["path"]: (n["fcp"], n["chrome"]) for n in walk["navs"] if n["fcp"] is None or n["chrome"] > n["fcp"]} == {}
