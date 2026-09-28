"""Screen-switch timing probe: an iPhone-sized chromium with a throttled CPU and network walks the
bottom tabs and a few More pages, and records for each navigation how long the shell and the data
took, which shell files came over the network, and how deep the API request chain was.

Run on its own for a table:  <venv>/bin/python qa/e2e/perf_probe.py [--cpu 4] [--rtt 80]
test_perf.py drives the same measure() and asserts on the result.

Service workers are allowed here (as in test_pwa.py): the worker's cache is part of what is measured.
Playwright passes --disable-back-forward-cache by default; it is dropped (and the new headless mode used) so
Back can be measured.
"""
import argparse
import json
import os
import pathlib
import subprocess
import time

from playwright.sync_api import sync_playwright

from ratelimit import login_with_retry

BASE_URL = os.environ.get("ISPEND_BASE_URL", "http://localhost:5559")
USER = (os.environ.get("ISPEND_PERF_USER", "qa_data"), os.environ.get("ISPEND_PERF_PASSWORD", "qa-data-pass1"))
TABS = ["/index.html", "/transactions.html", "/review.html", "/budgets.html"]
# Two rounds of the bottom tabs (the second is the "warm" switch a returning thumb makes), then More pages.
SEQUENCE = TABS + TABS + ["/reports.html", "/insights.html", "/categories.html", "/rules.html", "/settings.html"]
# channel="chromium" is the new headless mode: the old headless shell never uses the back/forward cache.
LAUNCH_ARGS = {"channel": "chromium", "headless": True, "ignore_default_args": ["--disable-back-forward-cache"]}

# Runs at document creation on every navigation (not on a bfcache restore — the old window comes back).
PROBE_JS = """
(() => {
  const vis = (el) => el.getClientRects().length > 0;
  const P = window.__perf = { me: null, chrome: null, fcp: null, restored: false, pending: 0, frames: [] };
  addEventListener('pageshow', (e) => { if (e.persisted) P.restored = true; });
  try {
    new PerformanceObserver((l) => l.getEntries().forEach((e) => {
      if (e.name === 'first-contentful-paint') P.fcp = e.startTime;
    })).observe({ type: 'paint', buffered: true });
  } catch (e) { /* unsupported */ }
  const origFetch = window.fetch;
  window.fetch = function (...args) {
    P.pending++;
    return origFetch.apply(this, args).finally(() => { P.pending--; });
  };
  const hasChrome = () => document.querySelector('.bottomnav .bn-item') && document.querySelector('.topbar .tb-title');
  function tick() {
    const t = performance.now();
    if (P.chrome === null && hasChrome()) P.chrome = t;
    if (P.me === null && window.currentUser) P.me = t;
    const main = document.getElementById('main');
    if (main) {
      const busy = Array.from(main.querySelectorAll('.skel, .skel-row, .spinner')).filter(vis).length
        + Array.from(main.querySelectorAll('.is-loading')).filter((el) => vis(el) && !el.matches('.btn')).length;
      P.frames.push([t, busy, main.innerText.length, P.pending, P.me !== null ? 1 : 0]);
    }
    if (P.frames.length < 4000) requestAnimationFrame(tick);
  }
  requestAnimationFrame(tick);
  new MutationObserver(() => { if (P.chrome === null && hasChrome()) P.chrome = performance.now(); })
    .observe(document, { childList: true, subtree: true });
})();
"""

# Settled = signed in, no skeletons, no fetch in flight, for STABLE_MS. Content = the first frame with no
# skeletons whose text is within 10% of the settled text (so a cached render counts before its refresh ends).
STABLE_MS = 350
SETTLED_JS = """
(stable) => {
  const P = window.__perf; if (!P || P.chrome === null) return false;
  const f = P.frames; if (!f.length) return false;
  const now = performance.now();
  let since = null;
  for (let i = f.length - 1; i >= 0; i--) {
    const [t, busy, , pending, me] = f[i];
    if (busy || pending || !me) break;
    since = t;
  }
  return since !== null && now - since >= stable && f[f.length - 1][0] - since >= stable * 0.5;
}
"""

COLLECT_JS = """
() => {
  const P = window.__perf || {};
  const f = P.frames || [];
  let settled = null;
  for (let i = f.length - 1; i >= 0; i--) { const [t, busy, , pending, me] = f[i]; if (busy || pending || !me) break; settled = t; }
  const finalLen = f.length ? f[f.length - 1][2] : 0;
  const content = (f.find(([t, busy, len, , me]) => me && !busy && len >= finalLen * 0.9) || [null])[0];
  const n = performance.getEntriesByType('navigation')[0] || {};
  const api = performance.getEntriesByType('resource').filter((r) => new URL(r.name).pathname.startsWith('/api/'))
    .map((r) => ({ path: new URL(r.name).pathname, start: r.startTime, end: r.responseEnd }));
  return { dcl: n.domContentLoadedEventEnd || 0, fcp: P.fcp, chrome: P.chrome, me: P.me, content, ready: settled, api };
}
"""


ROOT = pathlib.Path(__file__).resolve().parents[2]


def stamp_worker(mode="on", check=False):
    """Re-run nginx/40-ispend-sw.sh in the running nginx container. mode "on"/"off" forces
    ISPEND_SW_CACHE; None uses the container's own setting (restores the dev stamp). check=True only
    reports whether the container is reachable."""
    def compose(*args):
        return subprocess.run(["docker", "compose", *args], cwd=ROOT, capture_output=True, text=True, timeout=60)
    if check:
        return compose("exec", "-T", "nginx", "true").returncode == 0
    env = ["-e", f"ISPEND_SW_CACHE={mode}"] if mode else []
    r = compose("exec", "-T", *env, "nginx", "/docker-entrypoint.d/40-ispend-sw.sh")
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def chain_depth(api):
    """Longest chain of API requests where each one started only after the previous one ended."""
    reqs = sorted(api, key=lambda r: r["start"])
    depth = {}
    for i, r in enumerate(reqs):
        before = [depth[j] for j in range(i) if reqs[j]["end"] <= r["start"] - 2]
        depth[i] = 1 + max(before, default=0)
    return max(depth.values(), default=0)


def _throttle(page, cpu, rtt):
    cdp = page.context.new_cdp_session(page)
    cdp.send("Emulation.setCPUThrottlingRate", {"rate": cpu})
    cdp.send("Network.enable")
    if rtt:
        # ~12 Mbps: a decent 4G/LTE link; the round trip is what hurts, not the bandwidth.
        cdp.send("Network.emulateNetworkConditions", {"offline": False, "latency": rtt,
                                                       "downloadThroughput": 1_500_000, "uploadThroughput": 750_000})
    return cdp


def _wait_ready(page, timeout=30000):
    page.wait_for_function(SETTLED_JS, arg=STABLE_MS, timeout=timeout, polling=50)


def phone(pw):
    """Playwright's iPhone 13 descriptor, minus the WebKit default: the probe needs chromium for CDP throttling."""
    return {k: v for k, v in pw.devices["iPhone 13"].items() if k != "default_browser_type"}


def measure(browser, device, cpu=4, rtt=80, sequence=SEQUENCE):
    """Warm the app once, then walk `sequence`. Returns {'navs': [...], 'back': {...}}."""
    ctx = browser.new_context(**device)
    ctx.add_init_script(PROBE_JS)
    r = login_with_retry(lambda: ctx.request.post(f"{BASE_URL}/api/auth/login", data={"username": USER[0], "password": USER[1]}))
    assert r.ok, f"perf login failed: {r.status} {r.text()[:200]}"
    page = ctx.new_page()
    network_shell = []

    def on_response(resp):
        url = resp.url
        if not url.startswith(BASE_URL) or "/api/" in url:
            return
        if not resp.from_service_worker:
            network_shell.append(url.replace(BASE_URL, ""))

    page.on("response", on_response)
    # Cold visit: installs the worker (if any) and fills caches; then one reload so it controls the page.
    page.goto(f"{BASE_URL}/index.html")
    _wait_ready(page)
    try:
        page.evaluate("() => navigator.serviceWorker && navigator.serviceWorker.ready.then(() => true)")
    except Exception:  # noqa: BLE001
        pass
    page.reload()
    _wait_ready(page)
    _throttle(page, cpu, rtt)

    navs = []
    for path in sequence:
        network_shell.clear()
        t0 = time.monotonic()
        page.goto(f"{BASE_URL}{path}", wait_until="commit")
        _wait_ready(page)
        wall = (time.monotonic() - t0) * 1000
        d = page.evaluate(COLLECT_JS)
        navs.append({
            "path": path,
            "wall": round(wall),
            "dcl": round(d["dcl"]),
            "fcp": round(d["fcp"]) if d["fcp"] is not None else None,
            "chrome": round(d["chrome"]),
            "me": round(d["me"]),
            "content": round(d["content"]),
            "ready": round(d["ready"]),
            "depth": chain_depth(d["api"]),
            "api": [f"{a['path']}@{round(a['start'])}-{round(a['end'])}" for a in sorted(d["api"], key=lambda a: a["start"])],
            "network_shell": sorted(set(network_shell)),
        })

    # Back from the last page: restored from the back/forward cache, or a full reload?
    page.goto(f"{BASE_URL}/budgets.html")
    _wait_ready(page)
    page.go_back(wait_until="commit")  # a bfcache restore fires no load event
    page.wait_for_timeout(500)
    back = page.evaluate("""() => {
      const n = performance.getEntriesByType('navigation')[0] || {};
      return { restored: !!(window.__perf && window.__perf.restored),
               reasons: n.notRestoredReasons ? JSON.stringify(n.notRestoredReasons) : null };
    }""")
    ctx.close()
    return {"navs": navs, "back": back, "cpu": cpu, "rtt": rtt}


def table(result):
    lines = [f"cpu x{result['cpu']}, rtt {result['rtt']} ms — times are ms from navigation start",
             f"{'page':20} {'fcp':>5} {'chrome':>6} {'me':>5} {'content':>7} {'ready':>5} {'depth':>5}  network shell files"]
    for n in result["navs"]:
        lines.append(f"{n['path']:20} {str(n['fcp']):>5} {n['chrome']:6} {n['me']:5} {n['content']:7} {n['ready']:5} {n['depth']:5}  {len(n['network_shell'])}")
    lines.append(f"back: restored={result['back']['restored']} reasons={result['back']['reasons']}")
    return "\n".join(lines)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--cpu", type=float, default=4)
    ap.add_argument("--rtt", type=int, default=80)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    stamp_worker("on")  # the dev overlay stamps "dev"; measure the production worker
    try:
        with sync_playwright() as p:
            b = p.chromium.launch(**LAUNCH_ARGS)
            res = measure(b, phone(p), cpu=a.cpu, rtt=a.rtt)
            b.close()
    finally:
        stamp_worker(None)
    print(json.dumps(res, indent=1) if a.json else table(res))
