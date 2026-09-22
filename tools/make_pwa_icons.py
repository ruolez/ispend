"""Rasterise the enamel brand mark into the PNG icons the web manifest and iOS need.

Run:  .venv/bin/python tools/make_pwa_icons.py
Writes frontend/img/icons/{icon-192,icon-512,icon-maskable-512,apple-touch-icon}.png from
frontend/img/mark-enamel.svg using Playwright's chromium (the host has no SVG rasteriser).
The maskable icon keeps the mark inside the 80% safe zone on a solid paper field (the light --bg,
also the manifest background_color); the Apple icon is opaque because iOS paints transparent
pixels black.
"""
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "frontend" / "img" / "mark-enamel.svg"
OUT = ROOT / "frontend" / "img" / "icons"
FIELD = "#f5f6f8"  # --bg, light theme

# name, size, mark share of the box, background (None = transparent)
ICONS = [
    ("icon-192.png", 192, 1.0, None),
    ("icon-512.png", 512, 1.0, None),
    ("icon-maskable-512.png", 512, 0.66, FIELD),
    ("apple-touch-icon.png", 180, 0.8, FIELD),
]


def page_html(svg: str, size: int, share: float, bg: str | None) -> str:
    mark = round(size * share)
    return f"""<!doctype html><html><head><style>
html,body{{margin:0;width:{size}px;height:{size}px;background:{bg or 'transparent'}}}
body{{display:grid;place-items:center}}svg{{width:{mark}px;height:{mark}px;display:block}}
</style></head><body>{svg}</body></html>"""


def main() -> None:
    svg = SRC.read_text()
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for name, size, share, bg in ICONS:
            page = browser.new_page(viewport={"width": size, "height": size}, device_scale_factor=1)
            page.set_content(page_html(svg, size, share, bg))
            page.screenshot(path=str(OUT / name), omit_background=bg is None)
            page.close()
            print(f"wrote {OUT / name} ({size}x{size})")
        browser.close()


if __name__ == "__main__":
    main()
