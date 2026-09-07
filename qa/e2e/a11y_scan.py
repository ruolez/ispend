"""iSpend accessibility / responsive live scan.

Logs in as qa_tester (empty data) and admin (READ-ONLY, populated data), loads
every page in both themes at three viewports, and records: axe-core violations,
horizontal overflow, text below 12px, sub-44px touch targets (mobile), the
visible focus ring after 15 Tab presses, and a sampled text/background contrast
walk. Screenshots go to qa/reports/screenshots/a11y-<page>-<theme>-<w>.png.

Run:
  <qa-venv>/bin/python qa/e2e/a11y_scan.py [--quick]
Writes qa/reports/a11y-scan.json (raw) and prints a summary.
"""
import json, os, sys, time
from playwright.sync_api import sync_playwright

BASE = os.environ.get('ISPEND_BASE', 'http://localhost:5559')
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))
SHOTS = os.path.join(ROOT, 'qa', 'reports', 'screenshots')
OUT = os.path.join(ROOT, 'qa', 'reports', 'a11y-scan.json')
AXE_LOCAL = os.path.join(HERE, 'vendor', 'axe.min.js')
AXE_CDN = 'https://cdnjs.cloudflare.com/ajax/libs/axe-core/4.10.2/axe.min.js'

PAGES = ['index', 'transactions', 'review', 'import', 'statements', 'categories', 'rules', 'reports', 'insights', 'settings', 'login']
THEMES = ['light', 'dark']
VIEWPORTS = [(1440, 900), (1024, 768), (390, 844)]
QUICK = '--quick' in sys.argv

USERS = {
    'qa_tester': {'username': 'qa_tester', 'password': 'qa-tester-pass1'},
    'admin': {'username': 'admin', 'password': 'admin'},
}

WALKER_JS = r"""
(() => {
  const out = { small: [], contrast: [], overflowEls: [], targets: [] };
  const vw = document.documentElement.clientWidth;
  out.scrollWidth = document.documentElement.scrollWidth; out.clientWidth = vw;
  out.overflow = out.scrollWidth > vw;
  const visible = (el) => { const r = el.getBoundingClientRect(); const cs = getComputedStyle(el); return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none' && cs.opacity !== '0'; };
  const sel = (el) => { let s = el.tagName.toLowerCase(); if (el.id) s += '#' + el.id; if (el.className && typeof el.className === 'string') s += '.' + el.className.trim().split(/\s+/).slice(0,3).join('.'); return s; };
  const parse = (c) => { const m = c.match(/rgba?\(([\d.]+),\s*([\d.]+),\s*([\d.]+)(?:,\s*([\d.]+))?\)/); return m ? [+m[1], +m[2], +m[3], m[4] == null ? 1 : +m[4]] : null; };
  const lum = ([r,g,b]) => { const f = (c) => { c/=255; return c <= 0.03928 ? c/12.92 : Math.pow((c+0.055)/1.055, 2.4); }; return 0.2126*f(r)+0.7152*f(g)+0.0722*f(b); };
  const ratio = (a, b) => { const l1 = lum(a), l2 = lum(b); return (Math.max(l1,l2)+0.05)/(Math.min(l1,l2)+0.05); };
  const blend = (fg, bg) => { const a = fg[3]; return [Math.round(fg[0]*a+bg[0]*(1-a)), Math.round(fg[1]*a+bg[1]*(1-a)), Math.round(fg[2]*a+bg[2]*(1-a)), 1]; };
  const effBg = (el) => {
    let bg = [255,255,255,1]; const chain = [];
    for (let e = el; e; e = e.parentElement) { const c = parse(getComputedStyle(e).backgroundColor); if (c && c[3] > 0) chain.push(c); if (c && c[3] >= 1) break; if (getComputedStyle(e).backgroundImage !== 'none') { chain.push(null); break; } }
    const root = parse(getComputedStyle(document.documentElement).backgroundColor) || parse(getComputedStyle(document.body).backgroundColor) || [255,255,255,1];
    let acc = root; for (let i = chain.length - 1; i >= 0; i--) { if (chain[i] == null) return null; acc = blend(chain[i], acc); }
    return acc;
  };
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  const seen = new Set();
  let n;
  while ((n = walker.nextNode())) {
    const t = n.textContent.trim(); if (!t) continue;
    const el = n.parentElement; if (!el || seen.has(el)) continue; seen.add(el);
    if (el.closest('script,style,[aria-hidden="true"],.sr-only,.skip')) continue;
    if (!visible(el)) continue;
    const r = el.getBoundingClientRect(); if (r.bottom < 0 || r.top > innerHeight * 3) continue;
    const cs = getComputedStyle(el);
    const fs = parseFloat(cs.fontSize);
    if (fs < 12) out.small.push({ sel: sel(el), fs, text: t.slice(0, 40) });
    let fg = parse(cs.color); const bg = effBg(el);
    if (fg && bg) {
      // element opacity chain
      let op = 1; for (let e = el; e && e !== document.body; e = e.parentElement) op *= parseFloat(getComputedStyle(e).opacity);
      if (fg[3] < 1 || op < 1) fg = blend([fg[0], fg[1], fg[2], fg[3] * op], bg);
      const rt = ratio(fg, bg);
      const bold = parseInt(cs.fontWeight, 10) >= 700;
      const large = fs >= 24 || (fs >= 18.66 && bold);
      const need = large ? 3 : 4.5;
      if (rt < need) out.contrast.push({ sel: sel(el), text: t.slice(0, 40), fg: cs.color, bg: `rgb(${bg.slice(0,3).join(',')})`, ratio: Math.round(rt*100)/100, fs, need });
    }
  }
  // overflowing elements
  if (out.overflow) {
    document.querySelectorAll('body *').forEach((el) => { const r = el.getBoundingClientRect(); if (r.right > vw + 1 && r.width > 0 && visible(el) && getComputedStyle(el).position !== 'fixed') { if (out.overflowEls.length < 12) out.overflowEls.push({ sel: sel(el), right: Math.round(r.right), width: Math.round(r.width) }); } });
  }
  // touch targets
  document.querySelectorAll('a[href],button,input,select,textarea,[role="button"],[role="tab"],[role="radio"],[role="menuitem"],[role="option"],[tabindex="0"]').forEach((el) => {
    if (!visible(el) || el.closest('.sr-only,.skip,[aria-hidden="true"]')) return;
    const r = el.getBoundingClientRect(); if (r.bottom < 0 || r.top > innerHeight) return;
    if (el.matches('input[type="checkbox"],input[type="radio"]') && el.closest('label')) return; // label extends target
    if (r.width < 44 || r.height < 44) out.targets.push({ sel: sel(el), w: Math.round(r.width), h: Math.round(r.height), name: (el.getAttribute('aria-label') || el.getAttribute('title') || el.textContent || '').trim().slice(0, 30), tiny: r.width < 24 || r.height < 24 });
  });
  return out;
})()
"""

FOCUS_JS = r"""
(() => { const el = document.activeElement; if (!el || el === document.body) return { sel: 'body' };
  const cs = getComputedStyle(el);
  const s = el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') + (el.className && typeof el.className === 'string' ? '.' + el.className.trim().split(/\s+/).slice(0,2).join('.') : '');
  const ring = (cs.outlineStyle !== 'none' && parseFloat(cs.outlineWidth) > 0) || (cs.boxShadow && cs.boxShadow !== 'none');
  // switch: hidden input, ring painted on sibling track
  let ringSib = false; if (el.matches('.switch input')) { const t = el.nextElementSibling; if (t) { const c = getComputedStyle(t); ringSib = c.boxShadow && c.boxShadow !== 'none'; } }
  const r = el.getBoundingClientRect();
  return { sel: s, ring: !!(ring || ringSib), outline: cs.outlineStyle + ' ' + cs.outlineWidth + ' ' + cs.outlineColor, shadow: cs.boxShadow, offscreen: r.width === 0 || r.height === 0, name: (el.getAttribute('aria-label') || el.textContent || '').trim().slice(0, 30) };
})()
"""


def load_axe(page):
    try:
        with open(AXE_LOCAL, encoding='utf-8') as fh:
            page.evaluate(fh.read())
    except Exception:
        page.add_script_tag(url=AXE_CDN)
    page.wait_for_function('typeof window.axe !== "undefined"', timeout=10000)


def run_axe(page):
    load_axe(page)
    res = page.evaluate("""async () => { const r = await axe.run(document, { runOnly: { type: 'tag', values: ['wcag2a','wcag2aa','wcag21a','wcag21aa','wcag22aa','best-practice'] } }); return r.violations.map(v => ({ id: v.id, impact: v.impact, help: v.help, nodes: v.nodes.slice(0, 6).map(n => ({ target: n.target.join(' '), html: n.html.slice(0, 160), summary: n.failureSummary && n.failureSummary.slice(0, 200) })), count: v.nodes.length })); }""")
    return res


def login(context, user):
    r = context.request.post(f'{BASE}/api/auth/login', data=json.dumps(USERS[user]), headers={'Content-Type': 'application/json'})
    assert r.ok, f'login {user} failed: {r.status} {r.text()}'


def settle(page):
    try:
        page.wait_for_load_state('networkidle', timeout=8000)
    except Exception:
        pass
    page.wait_for_timeout(600)


def scan_state(page, label, shot):
    """axe + walker for the current DOM state."""
    rec = {'label': label}
    try:
        rec['axe'] = run_axe(page)
    except Exception as e:
        rec['axe_error'] = str(e)[:200]
    try:
        rec['walk'] = page.evaluate(WALKER_JS)
    except Exception as e:
        rec['walk_error'] = str(e)[:200]
    if shot:
        try:
            page.screenshot(path=shot, full_page=False)
            rec['shot'] = os.path.relpath(shot, ROOT)
        except Exception as e:
            rec['shot_error'] = str(e)[:200]
    return rec


def tab_focus(page, n=15):
    trail = []
    for _ in range(n):
        page.keyboard.press('Tab')
        page.wait_for_timeout(60)
        trail.append(page.evaluate(FOCUS_JS))
    return trail


def main():
    os.makedirs(SHOTS, exist_ok=True)
    results = {'base': BASE, 'started': time.strftime('%Y-%m-%d %H:%M:%S'), 'runs': [], 'admin_runs': [], 'console': []}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for theme in THEMES:
            for (w, h) in VIEWPORTS:
                if QUICK and (w, h) == (1024, 768):
                    continue
                context = browser.new_context(viewport={'width': w, 'height': h}, device_scale_factor=1, has_touch=(w < 500), is_mobile=(w < 500))
                context.add_init_script(f"try{{localStorage.setItem('ispend.theme','{theme}');localStorage.removeItem('ispend.sidebar');}}catch(e){{}}")
                login(context, 'qa_tester')
                page = context.new_page()
                errs = []
                page.on('console', lambda m: errs.append(f'{m.type}: {m.text[:160]}') if m.type in ('error', 'warning') else None)
                page.on('pageerror', lambda e: errs.append(f'pageerror: {str(e)[:160]}'))
                for pg in PAGES:
                    url = f'{BASE}/{pg}.html' if pg != 'login' else f'{BASE}/login.html'
                    if pg == 'login':
                        # logged-out view: open in a fresh context without cookies
                        ctx2 = browser.new_context(viewport={'width': w, 'height': h})
                        ctx2.add_init_script(f"try{{localStorage.setItem('ispend.theme','{theme}')}}catch(e){{}}")
                        lp = ctx2.new_page(); lp.goto(url); settle(lp)
                        rec = scan_state(lp, f'{pg}/{theme}/{w}', os.path.join(SHOTS, f'a11y-{pg}-{theme}-{w}.png'))
                        rec.update(page=pg, theme=theme, width=w, user='anon')
                        rec['focus'] = tab_focus(lp, 6)
                        lp.screenshot(path=os.path.join(SHOTS, f'a11y-{pg}-{theme}-{w}-focus.png'))
                        results['runs'].append(rec); ctx2.close(); continue
                    errs.clear()
                    page.goto(url); settle(page)
                    rec = scan_state(page, f'{pg}/{theme}/{w}', os.path.join(SHOTS, f'a11y-{pg}-{theme}-{w}.png'))
                    rec.update(page=pg, theme=theme, width=w, user='qa_tester')
                    rec['title'] = page.title()
                    rec['h1'] = page.evaluate("Array.from(document.querySelectorAll('h1')).map(h => h.textContent.trim())")
                    rec['headings'] = page.evaluate("Array.from(document.querySelectorAll('h1,h2,h3,h4')).filter(h => h.offsetParent !== null).map(h => h.tagName + ':' + h.textContent.trim().slice(0,30))")
                    rec['landmarks'] = page.evaluate("Array.from(document.querySelectorAll('main,nav,header,aside,footer,[role=main],[role=navigation],[role=banner]')).map(e => e.tagName.toLowerCase() + (e.getAttribute('aria-label') ? '[' + e.getAttribute('aria-label') + ']' : ''))")
                    rec['dupIds'] = page.evaluate("(() => { const m = {}; document.querySelectorAll('[id]').forEach(e => { m[e.id] = (m[e.id]||0)+1; }); return Object.entries(m).filter(([k,v]) => v > 1).map(([k,v]) => k + 'x' + v); })()")
                    rec['themeColor'] = page.evaluate("document.querySelector('meta[name=theme-color]') && document.querySelector('meta[name=theme-color]').content")
                    rec['colorScheme'] = page.evaluate("getComputedStyle(document.documentElement).colorScheme")
                    # focus ring trail
                    page.evaluate('document.activeElement && document.activeElement.blur()')
                    rec['focus'] = tab_focus(page, 15)
                    page.screenshot(path=os.path.join(SHOTS, f'a11y-{pg}-{theme}-{w}-focus.png'))
                    rec['console'] = list(errs)
                    results['runs'].append(rec)
                    print(f"[{theme} {w}] {pg}: axe={len(rec.get('axe', []))} overflow={rec.get('walk', {}).get('overflow')} small={len(rec.get('walk', {}).get('small', []))} lowc={len(rec.get('walk', {}).get('contrast', []))} targets<44={len(rec.get('walk', {}).get('targets', []))}", flush=True)
                context.close()

        # ---- admin: READ-ONLY populated pages (1440 + 390, both themes) ----
        for theme in THEMES:
            for (w, h) in ([(1440, 900), (390, 844)] if not QUICK else [(1440, 900)]):
                context = browser.new_context(viewport={'width': w, 'height': h}, device_scale_factor=1)
                context.add_init_script(f"try{{localStorage.setItem('ispend.theme','{theme}');localStorage.removeItem('ispend.sidebar');}}catch(e){{}}")
                login(context, 'admin')
                page = context.new_page()
                for pg in [p for p in PAGES if p != 'login']:
                    page.goto(f'{BASE}/{pg}.html'); settle(page)
                    if pg == 'transactions':
                        page.wait_for_timeout(1200)
                    rec = scan_state(page, f'admin/{pg}/{theme}/{w}', os.path.join(SHOTS, f'a11y-admin-{pg}-{theme}-{w}.png'))
                    rec.update(page=pg, theme=theme, width=w, user='admin')
                    rec['focus'] = tab_focus(page, 15)
                    results['admin_runs'].append(rec)
                    print(f"[admin {theme} {w}] {pg}: axe={len(rec.get('axe', []))} overflow={rec.get('walk', {}).get('overflow')} lowc={len(rec.get('walk', {}).get('contrast', []))} small={len(rec.get('walk', {}).get('small', []))}", flush=True)
                    # read-only layer states on the wide viewport
                    if w == 1440:
                        states = []
                        if pg == 'index':
                            states.append(('user-menu', lambda: page.click('#tb-user')))
                            states.append(('palette', lambda: page.keyboard.press('Meta+k')))
                            states.append(('shortcuts', lambda: page.keyboard.press('?')))
                        if pg == 'transactions':
                            states.append(('tx-drawer', lambda: page.click('#tx-body tr:first-child .merchant-name')))
                            states.append(('cat-filter', lambda: page.click('#f-categories')))
                            states.append(('range-picker', lambda: page.click('#f-range')))
                        if pg == 'categories':
                            states.append(('cat-side', lambda: page.click('.cat-row:first-child .cat-name')))
                        if pg == 'rules':
                            states.append(('rule-menu', lambda: page.hover('.rule:first-child') or page.click('.rule:first-child [data-act="menu"]')))
                        if pg == 'reports':
                            states.append(('tab-trend', lambda: page.click('[data-tab="trend"]')))
                            states.append(('tab-compare', lambda: page.click('[data-tab="compare"]')))
                            states.append(('tab-cashflow', lambda: page.click('[data-tab="cashflow"]')))
                        if pg == 'settings':
                            states.append(('tab-appearance', lambda: page.click('[data-tab="appearance"]')))
                            states.append(('tab-users', lambda: page.click('[data-tab="users"]')))
                        for name, act in states:
                            try:
                                act(); page.wait_for_timeout(700)
                                srec = scan_state(page, f'admin/{pg}/{name}/{theme}', os.path.join(SHOTS, f'a11y-admin-{pg}-{name}-{theme}-{w}.png'))
                                srec.update(page=pg, theme=theme, width=w, user='admin', state=name)
                                results['admin_runs'].append(srec)
                                print(f"   state {name}: axe={len(srec.get('axe', []))} lowc={len(srec.get('walk', {}).get('contrast', []))}", flush=True)
                            except Exception as e:
                                results['admin_runs'].append({'page': pg, 'state': name, 'theme': theme, 'width': w, 'error': str(e)[:200]})
                            # close layers
                            for _ in range(3):
                                page.keyboard.press('Escape'); page.wait_for_timeout(120)
                context.close()
        browser.close()
    with open(OUT, 'w', encoding='utf-8') as fh:
        json.dump(results, fh, indent=1)
    print('wrote', OUT)


if __name__ == '__main__':
    main()
