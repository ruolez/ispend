"""Static CSS audit for iSpend: dead selectors, contrast ratios, duplicate rules.
Run: python qa/e2e/css_audit.py  (no deps)."""
import re, os, json, sys, itertools
from collections import defaultdict

ROOT = os.path.join(os.path.dirname(__file__), '..', '..', 'frontend')
CSS_FILES = ['css/app.css', 'css/breakdown.css'] + sorted('css/pages/' + f for f in os.listdir(os.path.join(ROOT, 'css/pages')))
SRC_FILES = [f for f in os.listdir(ROOT) if f.endswith('.html')] + ['js/' + f for f in os.listdir(os.path.join(ROOT, 'js')) if f.endswith('.js')] + ['js/pages/' + f for f in os.listdir(os.path.join(ROOT, 'js/pages'))]

def read(p):
    with open(os.path.join(ROOT, p), encoding='utf-8') as fh: return fh.read()

# ---------- dead selectors ----------
src = '\n'.join(read(f) for f in SRC_FILES)
# tokens that could be class names in html/js: any word chars incl - inside quotes/templates
src_tokens = set(re.findall(r'[A-Za-z_][\w-]*', src))
# JS builds some classes dynamically: `toast-${type}`, `badge-${x}`, `anom--${kind}`, `ai-item--${x}`, `cat-row--child`...
dyn_prefixes = set(re.findall(r'([A-Za-z][\w-]*[-_])\$\{', src))
ids_in_src = set(re.findall(r'id="([^"]+)"', src)) | set(re.findall(r"id=\"?'?([\w-]+)", src)) | set(re.findall(r"\.id = '([\w-]+)'", src)) | set(re.findall(r"getElementById\('([\w-]+)'\)", src))

def strip_comments(css): return re.sub(r'/\*.*?\*/', '', css, flags=re.S)

def rules(css):
    """yield (selector, body, line) for non-@ rules; recurse into @media blocks."""
    css = strip_comments(css)
    out = []
    i = 0; n = len(css); line = 1
    def scan(start, end):
        pos = start
        while pos < end:
            b = css.find('{', pos)
            if b < 0 or b >= end: break
            sel = css[pos:b].strip()
            # find matching close
            depth = 1; k = b + 1
            while k < end and depth:
                if css[k] == '{': depth += 1
                elif css[k] == '}': depth -= 1
                k += 1
            body = css[b+1:k-1]
            ln = css.count('\n', 0, b) + 1
            if sel.startswith('@media') or sel.startswith('@supports'):
                scan(b+1, k-1)
            elif sel.startswith('@'):
                pass
            else:
                out.append((sel, body.strip(), ln))
            pos = k
    scan(0, n)
    return out

dead = []
all_rules = {}
for f in CSS_FILES:
    css = read(f)
    all_rules[f] = rules(css)
    for sel, body, ln in all_rules[f]:
        for part in sel.split(','):
            part = part.strip()
            classes = re.findall(r'\.(-?[_a-zA-Z][\w-]*)', re.sub(r'\([^)]*\)', '', part))
            idsel = re.findall(r'#(-?[_a-zA-Z][\w-]*)', part)
            missing = [c for c in classes if c not in src_tokens and not any(c.startswith(p) for p in dyn_prefixes)]
            missing_ids = [i for i in idsel if i not in src_tokens]
            if missing or missing_ids:
                dead.append((f, ln, part, missing + ['#' + i for i in missing_ids]))

# ---------- duplicates ----------
dups = []
seen = defaultdict(list)
for f, rs in all_rules.items():
    for sel, body, ln in rs:
        seen[re.sub(r'\s+', ' ', sel)].append((f, ln))
dup_sel = {s: locs for s, locs in seen.items() if len(locs) > 1}

# ---------- contrast ----------
def hex2rgb(h):
    h = h.lstrip('#')
    if len(h) == 3: h = ''.join(c*2 for c in h)
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))
def lum(rgb):
    def ch(c):
        c /= 255
        return c/12.92 if c <= 0.03928 else ((c+0.055)/1.055)**2.4
    r, g, b = rgb
    return 0.2126*ch(r) + 0.7152*ch(g) + 0.0722*ch(b)
def ratio(fg, bg):
    l1, l2 = lum(fg), lum(bg)
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)
def blend(fg_rgba, bg):
    (r, g, b, a) = fg_rgba
    return tuple(round(a*c + (1-a)*d) for c, d in zip((r, g, b), bg))
def mix(c1, c2, p):  # color-mix(in srgb, c1 p%, c2)
    return tuple(round(p*a + (1-p)*b) for a, b in zip(c1, c2))

tokens_css = read('css/tokens.css')
def token_block(text):
    return {k: v.strip() for k, v in re.findall(r'--([\w-]+):\s*([^;]+);', text)}
light_txt = tokens_css.split('@media (prefers-color-scheme: dark)')[0]
dark_txt = tokens_css.split(':root[data-theme="dark"]')[1].split('}')[0]
LIGHT = token_block(light_txt); DARK = token_block(dark_txt)
for k, v in LIGHT.items(): DARK.setdefault(k, v)

def rgb(theme, name):
    v = theme[name]
    m = re.match(r'rgba\(([\d.]+),\s*([\d.]+),\s*([\d.]+),\s*([\d.]+)\)', v)
    if m:
        return (float(m[1]), float(m[2]), float(m[3]), float(m[4]))
    return hex2rgb(v)

PAIRS = [  # (label, fg token, bg token, size class: 'text' 4.5 | 'large' 3 | 'ui' 3)
    ('body text (.text-1 on --bg)', 'text-1', 'bg', 'text'),
    ('body text on card (.text-1 on --surface)', 'text-1', 'surface', 'text'),
    ('.text-2 / p / labels on --surface', 'text-2', 'surface', 'text'),
    ('.text-3 / .hint / .page-sub / th on --surface', 'text-3', 'surface', 'text'),
    ('.text-3 on --surface-2 (table th bg, seg bg)', 'text-3', 'surface-2', 'text'),
    ('.text-4 / placeholder / .merchant-raw / .tl-time on --surface', 'text-4', 'surface', 'text'),
    ('.text-4 on --bg', 'text-4', 'bg', 'text'),
    ('.text-4 on --surface-2', 'text-4', 'surface-2', 'text'),
    ('link / .text-accent / .tab.active on --surface', 'accent', 'surface', 'text'),
    ('link on --bg', 'accent', 'bg', 'text'),
    ('.btn-primary text (--accent-fg on --accent)', 'accent-fg', 'accent', 'text'),
    ('.btn-primary:hover text', 'accent-fg', 'accent-hover', 'text'),
    ('.badge-accent / .pill-soft / nav-item current (--accent on --accent-soft)', 'accent', 'accent-soft', 'text'),
    ('.badge-info / .catchip--suggested (--accent on --info-soft)', 'accent', 'info-soft', 'text'),
    ('.amt--income / .badge-success (--success on --surface)', 'success', 'surface', 'text'),
    ('.badge-success / .chip-ok (--success on --success-soft)', 'success', 'success-soft', 'text'),
    ('.badge-danger / .error-box (--danger-text on --danger-soft)', 'danger-text', 'danger-soft', 'text'),
    ('.text-danger / .btn-danger (--danger on --surface)', 'danger', 'surface', 'text'),
    ('.badge-warning / .pill-warning (--warning on --warning-soft)', 'warning', 'warning-soft', 'text'),
    ('.text-warning / .chip-q (--warning on --surface)', 'warning', 'surface', 'text'),
    ('.badge default / .badge-neutral (--text-2 on --surface-3)', 'text-2', 'surface-3', 'text'),
    ('kbd (--text-3 on --surface-3)', 'text-3', 'surface-3', 'text'),
    ('.step-num (--text-3 on --surface-3)', 'text-3', 'surface-3', 'text'),
    ('.pill (--accent-fg on --accent)', 'accent-fg', 'accent', 'text'),
    ('.floatbar text (--bg on --text-1)', 'bg', 'text-1', 'text'),
    ('rail tooltip (--bg on --text-1)', 'bg', 'text-1', 'text'),
    ('focus ring --accent vs --surface', 'accent', 'surface', 'ui'),
    ('focus ring --accent vs --bg', 'accent', 'bg', 'ui'),
    ('input border --border-strong vs --surface', 'border-strong', 'surface', 'ui'),
    ('card border --border vs --surface', 'border', 'surface', 'ui'),
    ('card border --border vs --bg', 'border', 'bg', 'ui'),
    ('switch off track --border-strong vs --surface', 'border-strong', 'surface', 'ui'),
    ('.tbl th sort caret (text-3 @35% opacity) on surface-2', None, None, 'ui'),
    ('.sb-group-label / .menu-label / .palette-group (--text-4 on --surface)', 'text-4', 'surface', 'text'),
    ('.bn-item label 10px (--text-3 on --surface)', 'text-3', 'surface', 'text'),
    ('.stat-delta-vs (--text-4 on --surface)', 'text-4', 'surface', 'text'),
    ('.legend-pct / .rt-date / .cat-sub-count (--text-4 on --surface)', 'text-4', 'surface', 'text'),
    ('.avatar (--accent on --accent-soft)', 'accent', 'accent-soft', 'text'),
    ('.acct-mark white text on --chart-muted', None, None, 'text'),
]

def contrast_table():
    rows = []
    for label, fg, bg, kind in PAIRS:
        for tname, T in (('light', LIGHT), ('dark', DARK)):
            bgc = rgb(T, bg) if bg else None
            if fg is None:
                if 'caret' in label:
                    fgc = blend(rgb(T, 'text-3') + (0.35,), rgb(T, 'surface-2')); bgc = rgb(T, 'surface-2')
                elif 'acct-mark' in label:
                    fgc = (255, 255, 255); bgc = rgb(T, 'chart-muted')
            else:
                fgc = rgb(T, fg)
                if len(fgc) == 4: fgc = blend(fgc, bgc)
            r = ratio(fgc[:3], bgc[:3])
            need = 4.5 if kind == 'text' else 3.0
            rows.append((label, tname, '#%02x%02x%02x' % tuple(fgc[:3]), '#%02x%02x%02x' % tuple(bgc[:3]), round(r, 2), need, r >= need))
    # disabled buttons: opacity .5 -> blend text over surface
    for tname, T in (('light', LIGHT), ('dark', DARK)):
        s = rgb(T, 'surface')
        for lbl, fg, bgt in (('.btn:disabled .btn-secondary text (text-1 @50%)', 'text-1', 'surface'), ('.btn-primary:disabled text (accent-fg @50% on accent @50%)', 'accent-fg', 'accent'), ('.input:disabled text (text-1 @60%)', 'text-1', 'surface')):
            a = 0.6 if 'input' in lbl else 0.5
            bgc = blend(rgb(T, bgt) + (a,), s)
            fgc = blend(rgb(T, fg) + (a,), s)
            r = ratio(fgc, bgc)
            rows.append((lbl, tname, '#%02x%02x%02x' % fgc, '#%02x%02x%02x' % bgc, round(r, 2), 4.5, r >= 4.5))
        # category palette as text (cat-icon color on 16% tint) and as chart fill vs surface (3:1 graphical)
        for i in range(1, 13):
            c = rgb(T, f'c{i}')
            tint = mix(c, s, 0.16)
            r1 = ratio(c, tint)
            rows.append((f'.cat-icon --c{i} glyph on 16% tint', tname, '#%02x%02x%02x' % c, '#%02x%02x%02x' % tint, round(r1, 2), 3.0, r1 >= 3.0))
            r2 = ratio(c, s)
            rows.append((f'--c{i} chart fill / .dot vs --surface', tname, '#%02x%02x%02x' % c, '#%02x%02x%02x' % s, round(r2, 2), 3.0, r2 >= 3.0))
        # selection color: text-1 on accent-soft
        r = ratio(rgb(T, 'text-1'), rgb(T, 'accent-soft'))
        rows.append(('::selection text (--text-1 on --accent-soft)', tname, T['text-1'], T['accent-soft'], round(r, 2), 4.5, r >= 4.5))
        # bd-child bg mix
    return rows

if __name__ == '__main__':
    out = {'dead': dead, 'dup': dup_sel, 'contrast': contrast_table(), 'dyn_prefixes': sorted(dyn_prefixes)}
    print(json.dumps(out, indent=1, default=str))
