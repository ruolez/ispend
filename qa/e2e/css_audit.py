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
light_txt = tokens_css.split(':root[data-theme="dark"]')[0]
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
    ('.btn-primary text (--accent-solid-fg on --accent-solid)', 'accent-solid-fg', 'accent-solid', 'text'),
    ('.btn-primary:hover text', 'accent-solid-fg', 'accent-solid-hover', 'text'),
    ('.badge-accent / .pill-soft / nav-item current (--accent on --accent-soft)', 'accent', 'accent-soft', 'text'),
    ('.badge-info / .catchip--suggested (--accent on --info-soft)', 'accent', 'info-soft', 'text'),
    ('.amt--income / .badge-success (--success on --surface)', 'success', 'surface', 'text'),
    ('.badge-success / .chip-ok (--success on --success-soft)', 'success', 'success-soft', 'text'),
    ('.badge-danger / .error-box (--danger-text on --danger-soft)', 'danger-text', 'danger-soft', 'text'),
    ('.text-danger / .btn-danger (--danger-text on --surface)', 'danger-text', 'surface', 'text'),
    ('.badge-warning / .pill-warning (--warning on --warning-soft)', 'warning', 'warning-soft', 'text'),
    ('.text-warning / .chip-q (--warning on --surface)', 'warning', 'surface', 'text'),
    ('.badge default / .badge-neutral (--text-2 on --surface-3)', 'text-2', 'surface-3', 'text'),
    ('kbd (--text-3 on --surface-3)', 'text-3', 'surface-3', 'text'),
    ('.step-num (--text-3 on --surface-3)', 'text-3', 'surface-3', 'text'),
    ('.pill (--accent-solid-fg on --accent-solid)', 'accent-solid-fg', 'accent-solid', 'text'),
    ('.floatbar text (--bg on --text-1)', 'bg', 'text-1', 'text'),
    ('rail tooltip (--bg on --text-1)', 'bg', 'text-1', 'text'),
    ('focus ring --accent vs --surface', 'accent', 'surface', 'ui'),
    ('focus ring --accent vs --bg', 'accent', 'bg', 'ui'),
    ('input border --border-control vs --surface', 'border-control', 'surface', 'ui'),
    ('card border --border vs --surface', 'border', 'surface', 'ui'),
    ('card border --border vs --bg', 'border', 'bg', 'ui'),
    ('switch off track --switch-off vs --surface', 'switch-off', 'surface', 'ui'),
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
                    fgc = (255, 255, 255); bgc = mix(rgb(T, 'chart-muted'), (0, 0, 0), 0.75)
            else:
                fgc = rgb(T, fg)
                if len(fgc) == 4: fgc = blend(fgc, bgc)
            r = ratio(fgc[:3], bgc[:3])
            need = 4.5 if kind == 'text' else 3.0
            rows.append((label, tname, '#%02x%02x%02x' % tuple(fgc[:3]), '#%02x%02x%02x' % tuple(bgc[:3]), round(r, 2), need, r >= need))
    # disabled buttons: opacity .5 -> blend text over surface
    for tname, T in (('light', LIGHT), ('dark', DARK)):
        s = rgb(T, 'surface')
        for lbl, fg, bgt in (('.btn:disabled .btn-secondary text (text-1 @50%)', 'text-1', 'surface'), ('.btn-primary:disabled text (accent-solid-fg @50% on accent-solid @50%)', 'accent-solid-fg', 'accent-solid'), ('.input:disabled text (text-1 @60%)', 'text-1', 'surface')):
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

def apca_lc(fg, bg):
    """APCA 0.0.98G lightness contrast |Lc| of text colour fg on bg (sRGB 0-255 tuples)."""
    def y(c):
        r, g, b = ((v / 255) ** 2.4 for v in c)
        v = 0.2126729 * r + 0.7151522 * g + 0.0721750 * b
        return v if v > 0.022 else v + (0.022 - v) ** 1.414
    t, b = y(fg), y(bg)
    if b > t:
        s = (b ** 0.56 - t ** 0.57) * 1.14
        return 0.0 if s < 0.1 else (s - 0.027) * 100
    s = (b ** 0.65 - t ** 0.62) * 1.14
    return 0.0 if s > -0.1 else -(s + 0.027) * 100


# Enforced by test_css_audit.test_contrast_tokens. Text needs WCAG 2.2 AA 4.5:1 on every surface it sits
# on; control boundaries need 3:1 (1.4.11). APCA floors follow the Bronze Simple Mode levels: Lc 75 for
# body text, 60 for other content text, 45 for large/heavy or tertiary labels.
TEXT_SURFACES = ['bg', 'surface', 'surface-2', 'surface-3', 'surface-overlay', 'accent-soft']
CONTRAST_RULES = (
    [(f'--{fg} on --{bg}', fg, bg, 4.5) for fg in ('text-1', 'text-2', 'text-3', 'text-4') for bg in TEXT_SURFACES]
    + [(f'--accent text on --{bg}', 'accent', bg, 4.5) for bg in ('bg', 'surface', 'surface-2', 'surface-overlay', 'accent-soft', 'info-soft')]
    + [(f'--success on --{bg}', 'success', bg, 4.5) for bg in ('surface', 'surface-2', 'success-soft')]
    + [(f'--warning on --{bg}', 'warning', bg, 4.5) for bg in ('surface', 'surface-2', 'warning-soft')]
    + [(f'--danger-text on --{bg}', 'danger-text', bg, 4.5) for bg in ('surface', 'surface-2', 'surface-overlay', 'danger-soft')]
    + [('filled button text', 'accent-solid-fg', 'accent-solid', 4.5), ('filled button text, hover', 'accent-solid-fg', 'accent-solid-hover', 4.5)]
    + [(f'control border vs --{bg}', 'border-control', bg, 3.0) for bg in ('bg', 'surface', 'surface-overlay')]
    + [(f'switch off track vs --{bg}', 'switch-off', bg, 3.0) for bg in ('surface', 'surface-overlay')]
    + [(f'focus ring vs --{bg}', 'accent', bg, 3.0) for bg in ('bg', 'surface', 'surface-overlay')]
)
APCA_RULES = (
    [(f'--text-1 on --{bg}', 'text-1', bg, 90) for bg in ('bg', 'surface')]
    + [(f'--text-2 on --{bg}', 'text-2', bg, 75) for bg in ('bg', 'surface')]
    + [(f'--text-3 on --{bg}', 'text-3', bg, 60) for bg in ('bg', 'surface', 'surface-2')]
    + [(f'--text-4 on --{bg}', 'text-4', bg, 45) for bg in ('bg', 'surface', 'surface-2')]
    + [(f'--{fg} on --surface', fg, 'surface', 50) for fg in ('accent', 'danger-text', 'success', 'warning')]
)


def contrast_checks():
    """Return failing colour pairs for both themes; empty when every rule holds."""
    fails = []
    for tname, T in (('light', LIGHT), ('dark', DARK)):
        def c(name):
            v = rgb(T, name)
            return v[:3]
        for label, fg, bg, need in CONTRAST_RULES:
            try:
                r = ratio(c(fg), c(bg))
            except KeyError as e:
                fails.append(f'{tname}: {label}: token {e} missing'); continue
            if r < need:
                fails.append(f'{tname}: {label} {T[fg]} on {T[bg]} = {r:.2f}:1 < {need}')
        for label, fg, bg, need in APCA_RULES:
            try:
                lc = apca_lc(c(fg), c(bg))
            except KeyError as e:
                fails.append(f'{tname}: {label}: token {e} missing'); continue
            if lc < need:
                fails.append(f'{tname}: {label} {T[fg]} on {T[bg]} = APCA Lc {lc:.0f} < {need}')
        for fill in ('accent-solid', 'accent-solid-hover', 'danger-solid'):
            try:
                r = ratio((255, 255, 255), c(fill))
            except KeyError as e:
                fails.append(f'{tname}: white on --{fill}: token {e} missing'); continue
            if r < 4.5:
                fails.append(f'{tname}: white on --{fill} {T[fill]} = {r:.2f}:1 < 4.5')
        # account initials: white on the category colour darkened as .acct-mark paints it
        for i in range(1, 13):
            mark = mix(c(f'c{i}'), (0, 0, 0), 0.75)
            r = ratio((255, 255, 255), mark)
            if r < 4.5:
                fails.append(f'{tname}: .acct-mark white on --c{i} (75% mix with black) = {r:.2f}:1 < 4.5')
    return fails


# ---------- static design-system rules (python css_audit.py --check) ----------
BREAKPOINTS = {'1280', '960', '768', '640', '480'}
HEX_ALLOW = {'#fff', '#ffffff'}   # solid white on accent/danger fills

def static_checks():
    """Return a list of human-readable violations; empty when the design-system rules hold."""
    fails = []
    for f in CSS_FILES:
        css = strip_comments(read(f))
        for m in re.finditer(r'font-size:\s*(\d+(?:\.\d+)?)px', css):
            fails.append(f"{f}:{css.count(chr(10), 0, m.start()) + 1} font-size in px ({m.group(1)}px); use the --fs-* rem scale")
        for m in re.finditer(r'@media[^{]*\((?:max|min)-width:\s*(\d+)px\)', css):
            if m.group(1) not in BREAKPOINTS:
                fails.append(f"{f}:{css.count(chr(10), 0, m.start()) + 1} off-ladder breakpoint {m.group(1)}px (ladder: 1280 JS / 960 / 768 / 640 / 480)")
        for m in re.finditer(r'border-radius:\s*(\d+(?:\.\d+)?)px', css):
            fails.append(f"{f}:{css.count(chr(10), 0, m.start()) + 1} border-radius in px ({m.group(1)}px); use --r-xs/--r-sm/--r-md/--r-lg/--r-xl")
        for m in re.finditer(r'#[0-9a-fA-F]{3,8}\b', css):
            if m.group(0).lower() in HEX_ALLOW:
                continue
            line = css.count(chr(10), 0, m.start()) + 1
            if 'url(' in css[max(0, m.start() - 200):m.start()] and ')' not in css[max(0, m.start() - 200):m.start()].split('url(')[-1]:
                fails.append(f"{f}:{line} hex colour inside a data URI ({m.group(0)}); swap per theme with a token")
            else:
                fails.append(f"{f}:{line} hex colour {m.group(0)} outside tokens.css")
    fails += touch_checks()
    n_dark = tokens_css.count('--bg: #0c0e13')
    if n_dark != 1:
        fails.append(f"css/tokens.css: dark token block defined {n_dark} times (expected once)")
    return fails

def _blocks(css):
    """yield (media_prelude_or_None, selector, body, line) for every rule, one @media level deep."""
    out = []
    def scan(start, end, media):
        pos = start
        while pos < end:
            b = css.find('{', pos)
            if b < 0 or b >= end:
                break
            head = css[pos:b].strip()
            depth, k = 1, b + 1
            while k < end and depth:
                depth += {'{': 1, '}': -1}.get(css[k], 0)
                k += 1
            if head.startswith('@media') or head.startswith('@supports'):
                scan(b + 1, k - 1, head)
            elif not head.startswith('@'):
                out.append((media, head, css[b + 1:k - 1], css.count(chr(10), 0, b) + 1))
            pos = k
    scan(0, len(css), None)
    return out


def touch_checks():
    """Mobile-makeover rules: no bare 100vh (pair it with dvh) and hover styling only for real hover devices."""
    fails = []
    for f in CSS_FILES:
        if f.endswith('landing.css'):
            continue  # marketing page, out of the app makeover's scope
        css = re.sub(r'/\*.*?\*/', lambda m: chr(10) * m.group(0).count(chr(10)), read(f), flags=re.S)
        for media, sel, body, line in _blocks(css):
            if '100vh' in body and 'dvh' not in body:
                fails.append(f"{f}:{line} {sel[:60]} uses 100vh without a dvh companion")
            if ':hover' in sel and not (media and 'hover: hover' in media.replace('hover:hover', 'hover: hover')):
                fails.append(f"{f}:{line} {sel[:60]} :hover outside @media (hover: hover)")
    return fails


if __name__ == '__main__':
    if '--check' in sys.argv:
        problems = static_checks() + contrast_checks()
        for p in problems:
            print(p)
        print(f"{len(problems)} static rule violation(s)")
        sys.exit(1 if problems else 0)
    out = {'dead': dead, 'dup': dup_sel, 'contrast': contrast_table(), 'dyn_prefixes': sorted(dyn_prefixes)}
    print(json.dumps(out, indent=1, default=str))
