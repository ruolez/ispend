"""Render the PWA / home-screen icons: the enamel wave disc as a polished app tile.

Run:  .venv/bin/python tools/make_pwa_icons.py
Writes frontend/img/icons/{icon-192,icon-512,icon-maskable-512,apple-touch-icon}.png with
Playwright's chromium (the host has no SVG rasteriser).

The tile is built here rather than taken from frontend/img/mark-enamel.svg: that file is the
30 px web mark, and an icon needs more depth at 180-512 px — a full-bleed periwinkle field (the
disc is dark, so the tile must be light), a crisp
offset step under the disc, a glass cap and a bevelled rim. Depth comes from hard edges and
gradients, not blur (the logo rule). Maskable and Apple icons carry the field (iOS paints
transparent pixels black; Android masks the outer 10%); the "any" icons are the bare disc.
"""
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "frontend" / "img" / "icons"

# name, size, disc diameter as a share of the box, with the periwinkle field
ICONS = [
    ("icon-192.png", 192, 0.96, False),
    ("icon-512.png", 512, 0.96, False),
    ("icon-maskable-512.png", 512, 0.72, True),
    ("apple-touch-icon.png", 180, 0.78, True),
]

FIELD = """
<defs>
  <radialGradient id="fld" cx="0.5" cy="0.42" r="0.75">
    <stop offset="0" stop-color="#eef1ff"/><stop offset="1" stop-color="#cdd6fb"/>
  </radialGradient>
</defs>
<rect width="100" height="100" fill="url(#fld)"/>
"""

# The disc is drawn on a 64-unit grid centred at (32,32), r=30, then scaled into the tile.
DISC = """
<defs>
  <linearGradient id="blu" x1="0.15" y1="0" x2="0.6" y2="1">
    <stop offset="0" stop-color="#7f98ff"/><stop offset="0.55" stop-color="#3f5fe0"/><stop offset="1" stop-color="#2743b8"/>
  </linearGradient>
  <linearGradient id="nvy" x1="0.15" y1="0" x2="0.6" y2="1">
    <stop offset="0" stop-color="#22305f"/><stop offset="0.6" stop-color="#0f1a3f"/><stop offset="1" stop-color="#070c22"/>
  </linearGradient>
  <linearGradient id="cap" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0" stop-color="#ffffff" stop-opacity="0.42"/><stop offset="1" stop-color="#ffffff" stop-opacity="0.04"/>
  </linearGradient>
  <linearGradient id="rim" x1="0" y1="0" x2="0.4" y2="1">
    <stop offset="0" stop-color="#ffffff" stop-opacity="0.85"/><stop offset="0.5" stop-color="#ffffff" stop-opacity="0.18"/><stop offset="1" stop-color="#040818" stop-opacity="0.7"/>
  </linearGradient>
  <linearGradient id="edge" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0" stop-color="#2a3f95"/><stop offset="1" stop-color="#050a20"/>
  </linearGradient>
  <clipPath id="disc"><circle cx="32" cy="32" r="30"/></clipPath>
</defs>
<!-- coin edge: a crisp step below the face, no blur -->
<circle cx="32.9" cy="33.6" r="30" fill="#040818" fill-opacity="{shadow}"/>
<circle cx="32" cy="32.9" r="30" fill="url(#edge)"/>
<!-- face -->
<circle cx="32" cy="32" r="30" fill="url(#blu)"/>
<path d="M32 2A11 15 0 0 1 32 32A11 15 0 0 0 32 62A30 30 0 0 1 32 2Z" fill="#1e35a6" clip-path="url(#disc)" transform="translate(1.6 1.2)"/>
<path d="M32 2A11 15 0 0 1 32 32A11 15 0 0 0 32 62A30 30 0 0 1 32 2Z" fill="url(#nvy)"/>
<!-- wave hairline -->
<path d="M32 2A11 15 0 0 1 32 32A11 15 0 0 0 32 62" fill="none" stroke="#ffffff" stroke-opacity="0.8" stroke-width="1" clip-path="url(#disc)" transform="translate(0.8 0)"/>
<!-- glass cap: a hard-edged sheen over the upper third -->
<path d="M4.5 24 A30 30 0 0 1 59.5 24 A34 16 0 0 1 4.5 24Z" fill="url(#cap)" clip-path="url(#disc)"/>
<!-- bevel arcs and rim -->
<path d="M9.6 12.4A29 29 0 0 1 54.4 12.4" fill="none" stroke="#ffffff" stroke-opacity="0.55" stroke-width="1.2" stroke-linecap="round"/>
<path d="M54.4 51.6A29 29 0 0 1 9.6 51.6" fill="none" stroke="#040818" stroke-opacity="0.55" stroke-width="1.2" stroke-linecap="round"/>
<circle cx="32" cy="32" r="29.4" fill="none" stroke="url(#rim)" stroke-width="1.2"/>
"""


def icon_svg(share: float, field: bool) -> str:
    d = 100 * share
    s = d / 64
    off = (100 - d) / 2
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" width="100%" height="100%">'
            f'{FIELD if field else ""}'
            f'<g transform="translate({off:.3f} {off:.3f}) scale({s:.5f})">{DISC.format(shadow=0.35 if field else 0)}</g>'
            f'</svg>')


def page_html(svg: str, size: int, field: bool) -> str:
    return (f'<!doctype html><html><head><style>html,body{{margin:0;width:{size}px;height:{size}px;'
            f'background:{"#cdd6fb" if field else "transparent"}}}svg{{display:block}}</style></head>'
            f'<body>{svg}</body></html>')


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for name, size, share, field in ICONS:
            page = browser.new_page(viewport={"width": size, "height": size}, device_scale_factor=1)
            page.set_content(page_html(icon_svg(share, field), size, field))
            page.screenshot(path=str(OUT / name), omit_background=not field)
            page.close()
            print(f"wrote {OUT / name} ({size}x{size})")
        browser.close()


if __name__ == "__main__":
    main()
