"""Before/after screenshots of every page on phones, for eyeballing a mobile change.

    <venv>/bin/python qa/e2e/phone_gallery.py <out-dir> [--widths 320,375,430] [--themes light,dark] [--user admin]

Writes <out-dir>/<width>-<theme>-<page>.png (first screen) and ...-full.png (whole page) plus the
login/signup pages signed out. Read-only: it only navigates. Keep <out-dir> outside the repo.
"""
import argparse
import json
import pathlib

from helpers import PAGES, wait_loaded
from playwright.sync_api import sync_playwright
from ratelimit import login_with_retry
from smoke_fixtures import BASE_URL, INIT_JS, PERSONAS

UA_IOS = ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 "
          "(KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--widths", default="320,375,430")
    ap.add_argument("--themes", default="light,dark")
    ap.add_argument("--user", default="admin")
    ap.add_argument("--pages", default=",".join(PAGES))
    a = ap.parse_args()
    out = pathlib.Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        b = p.chromium.launch()
        seed = b.new_context(base_url=BASE_URL, service_workers="block")
        user, pw_ = PERSONAS[a.user]
        r = login_with_retry(lambda: seed.request.post("/api/auth/login", data={"username": user, "password": pw_}))
        assert r.ok, r.text()
        state = seed.storage_state()
        seed.close()
        for w in (int(x) for x in a.widths.split(",")):
            h = 568 if w <= 320 else 812
            for theme in a.themes.split(","):
                for signed_in in (False, True):
                    ctx = b.new_context(viewport={"width": w, "height": h}, is_mobile=True, has_touch=True, user_agent=UA_IOS,
                                        device_scale_factor=2, base_url=BASE_URL, service_workers="block",
                                        storage_state=state if signed_in else None)
                    ctx.add_init_script(f"({INIT_JS})({json.dumps(theme)});")
                    page = ctx.new_page()
                    keys = a.pages.split(",") if signed_in else ["login", "signup"]
                    for key in keys:
                        page.goto(PAGES[key] if signed_in else f"/{key}.html")
                        if signed_in:
                            wait_loaded(page)
                        else:
                            page.wait_for_timeout(600)
                        stem = out / f"{w}-{theme}-{key}"
                        page.screenshot(path=f"{stem}.png")
                        if signed_in:
                            page.screenshot(path=f"{stem}-full.png", full_page=True)
                    ctx.close()
        b.close()
    print(f"wrote {len(list(out.glob('*.png')))} screenshots to {out}")


if __name__ == "__main__":
    main()
