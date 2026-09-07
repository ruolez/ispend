"""Targeted computed-style probes that back specific findings in css-a11y-review.md.
Run: <qa-venv>/bin/python qa/e2e/a11y_probe.py"""
import json, os
from playwright.sync_api import sync_playwright
BASE = os.environ.get('ISPEND_BASE', 'http://localhost:5559')

def login(ctx, u, p):
    r = ctx.request.post(f'{BASE}/api/auth/login', data=json.dumps({'username': u, 'password': p}), headers={'Content-Type': 'application/json'}); assert r.ok

with sync_playwright() as pw:
    b = pw.chromium.launch()
    for theme in ('light', 'dark'):
        ctx = b.new_context(viewport={'width': 1440, 'height': 900})
        ctx.add_init_script(f"localStorage.setItem('ispend.theme','{theme}')")
        login(ctx, 'admin', 'admin')
        pg = ctx.new_page()
        pg.goto(f'{BASE}/import.html'); pg.wait_for_load_state('networkidle'); pg.wait_for_timeout(500)
        sel = pg.evaluate("(() => { const s = document.querySelector('#upload-account'); const c = getComputedStyle(s); return { bgImage: c.backgroundImage.slice(0, 40), bg: c.backgroundColor, appearance: c.appearance }; })()")
        print(theme, 'select#upload-account:', sel)
        pg.goto(f'{BASE}/index.html'); pg.wait_for_load_state('networkidle'); pg.wait_for_timeout(1500)
        spark = pg.evaluate("Array.from(document.querySelectorAll('.stat-spark')).map(c => { const r = c.getBoundingClientRect(); const p = c.parentElement.getBoundingClientRect(); return { w: Math.round(r.width), h: Math.round(r.height), statW: Math.round(p.width), styleW: c.style.width, attrW: c.width }; })")
        print(theme, 'stat-spark canvases:', spark)
        val = pg.evaluate("(() => { const v = document.querySelector('.stat .stat-value').getBoundingClientRect(); const c = document.querySelector('.stat .stat-spark').getBoundingClientRect(); return { valueBox: [Math.round(v.left), Math.round(v.right), Math.round(v.top), Math.round(v.bottom)], sparkBox: [Math.round(c.left), Math.round(c.right), Math.round(c.top), Math.round(c.bottom)], overlap: !(v.right < c.left || c.right < v.left || v.bottom < c.top || c.bottom < v.top) }; })()")
        print(theme, 'value/spark overlap:', val)
        if theme == 'dark':
            fb = pg.evaluate("(() => { const s = document.createElement('div'); s.className = 'floatbar'; s.innerHTML = '<span>x</span><button class=\"btn btn-ghost\">Undo</button>'; document.body.appendChild(s); const c = getComputedStyle(s); const bc = getComputedStyle(s.querySelector('.btn')); const o = { bg: c.backgroundColor, color: c.color, btnColor: bc.color }; s.remove(); return o; })()")
            print('dark floatbar:', fb)
            month = pg.evaluate("(() => { const i = document.createElement('input'); i.type = 'month'; i.className = 'input'; document.body.appendChild(i); const c = getComputedStyle(i); const o = { colorScheme: c.colorScheme, bg: c.backgroundColor, color: c.color }; i.remove(); return o; })()")
            print('dark input[type=month]:', month)
        # rail mode accessible names
        pg.evaluate("localStorage.setItem('ispend.sidebar','rail')"); pg.reload(); pg.wait_for_load_state('networkidle'); pg.wait_for_timeout(500)
        rail = pg.evaluate("Array.from(document.querySelectorAll('.sidebar .nav-item')).slice(0,3).map(a => ({ text: a.textContent.trim(), labelDisplay: getComputedStyle(a.querySelector('.label')).display, ariaLabel: a.getAttribute('aria-label'), title: a.getAttribute('title') }))")
        print(theme, 'rail nav-items:', rail)
        pg.evaluate("localStorage.removeItem('ispend.sidebar')")
        # mobile empty-state squeeze (qa_tester) + hidden labels
        ctx2 = b.new_context(viewport={'width': 390, 'height': 844})
        ctx2.add_init_script(f"localStorage.setItem('ispend.theme','{theme}')")
        login(ctx2, 'qa_tester', 'qa-tester-pass1')
        m = ctx2.new_page(); m.goto(f'{BASE}/transactions.html'); m.wait_for_load_state('networkidle'); m.wait_for_timeout(800)
        sq = m.evaluate("(() => { const e = document.querySelector('#tx-body .empty'); if (!e) return 'no .empty'; const td = e.closest('td'); const r = td.getBoundingClientRect(); return { tdWidth: Math.round(r.width), tdLeft: Math.round(r.left), gridArea: getComputedStyle(td).gridArea, colspan: td.getAttribute('colspan') }; })()")
        print(theme, '390 transactions empty-state td:', sq)
        names = m.evaluate("['#btn-add','#btn-export','#btn-density'].map(s => { const e = document.querySelector(s); return s + ': text=\"' + e.textContent.trim() + '\" aria-label=' + e.getAttribute('aria-label') + ' title=' + e.getAttribute('title'); })")
        print(theme, '390 page-action names:', names)
        ctx2.close(); ctx.close()
    b.close()
