/* App shell: sidebar (full/rail/hidden), topbar, command palette, user menu,
   theme controls, mobile bottom nav. initNav(page) resolves after /api/auth/me. */
const NAV_GROUPS = [
  { label: 'Overview', items: [
    { page: 'dashboard', href: '/index.html', label: 'Dashboard', icon: 'layout-dashboard', key: 'd' },
    { page: 'transactions', href: '/transactions.html', label: 'Transactions', icon: 'list', key: 't' },
    { page: 'review', href: '/review.html', label: 'Review', icon: 'inbox', key: 'r', pill: true },
  ] },
  { label: 'Data', items: [
    { page: 'import', href: '/import.html', label: 'Import', icon: 'upload', key: 'i' },
    { page: 'statements', href: '/statements.html', label: 'Statements', icon: 'file-text' },
  ] },
  { label: 'Organize', items: [
    { page: 'categories', href: '/categories.html', label: 'Categories', icon: 'tags', key: 'c' },
    { page: 'rules', href: '/rules.html', label: 'Rules', icon: 'sliders' },
  ] },
  { label: 'Analyze', items: [
    { page: 'reports', href: '/reports.html', label: 'Reports', icon: 'bar-chart', key: 'p' },
    { page: 'insights', href: '/insights.html', label: 'Insights', icon: 'lightbulb' },
  ] },
];
const NAV_SETTINGS = { page: 'settings', href: '/settings.html', label: 'Settings', icon: 'settings', key: 's' };
const NAV_ITEMS = [...NAV_GROUPS.flatMap((g) => g.items), NAV_SETTINGS];
const BOTTOM_NAV = ['dashboard', 'transactions', 'review', 'import'];
const SIDEBAR_KEY = 'ispend.sidebar';
const UID_KEY = 'ispend.uid';
const ROOT_LABELS = { 'popover-root': 'Menus', 'modal-root': 'Dialogs', 'drawer-root': 'Panels', 'toast-root': 'Notifications' };

function sidebarMode() {
  try { const v = localStorage.getItem(SIDEBAR_KEY); if (v) return v; } catch { /* ignore */ }
  const w = window.innerWidth;
  return w >= 1280 ? 'full' : w >= 960 ? 'rail' : 'hidden';
}
function applySidebarMode() {
  const w = window.innerWidth;
  let mode = sidebarMode();
  if (w < 960) mode = 'hidden';
  document.documentElement.setAttribute('data-sidebar', mode);
}
function setSidebarMode(mode) {
  try { localStorage.setItem(SIDEBAR_KEY, mode); } catch { /* ignore */ }
  applySidebarMode();
}
applySidebarMode();

function navItemHtml(i, activePage) {
  return `<a href="${i.href}${esc(savedQuery(i.href))}" class="nav-item" data-page="${i.page}" data-label="${esc(i.label)}" ${i.page === activePage ? 'aria-current="page"' : ''} ${i.adminOnly ? 'data-admin-only' : ''}>
    ${icon(i.icon)}<span class="label">${esc(i.label)}</span>${i.pill ? '<span class="pill" data-review-pill hidden>0</span>' : ''}</a>`;
}

async function initNav(activePage) {
  restoreQuery();
  const active = NAV_ITEMS.find((i) => i.page === activePage);
  document.body.dataset.page = activePage;
  document.title = `${active ? active.label : 'iSpend'} · iSpend`;

  // Skip link + sidebar
  const skipNav = document.createElement('nav'); skipNav.className = 'skip-nav'; skipNav.setAttribute('aria-label', 'Skip');
  const skip = document.createElement('a'); skip.className = 'skip'; skip.href = '#main'; skip.textContent = 'Skip to content';
  skipNav.appendChild(skip);
  document.body.prepend(skipNav);
  const sb = document.createElement('div');
  sb.innerHTML = `
    <div class="sidebar-backdrop" id="sidebar-backdrop"></div>
    <aside class="sidebar" id="sidebar" aria-label="Main navigation">
      <div class="sb-brand"><span class="sb-mark">${icon('activity')}</span><span class="sb-name">iSpend</span>
        <button type="button" class="sb-collapse" id="sb-collapse" title="Collapse sidebar ([)" aria-label="Collapse sidebar">${icon('chevrons-left')}</button></div>
      <nav class="sb-nav">
        ${NAV_GROUPS.map((g) => `<div class="sb-group"><div class="sb-group-label">${esc(g.label)}</div>${g.items.map((i) => navItemHtml(i, activePage)).join('')}</div>`).join('')}
      </nav>
      <div class="sb-foot">
        <button type="button" class="sb-expand" id="sb-expand" title="Expand sidebar (])" aria-label="Expand sidebar">${icon('chevrons-right')}</button>
        ${navItemHtml(NAV_SETTINGS, activePage)}
      </div>
    </aside>`;
  Array.from(sb.children).forEach((c) => document.body.prepend(c));

  // Topbar + shell wrapper around existing <main>
  let main = document.getElementById('main');
  if (!main) { main = document.createElement('main'); main.id = 'main'; main.className = 'main'; document.body.appendChild(main); }
  const shell = document.createElement('div'); shell.className = 'shell';
  shell.innerHTML = `
    <header class="topbar" id="topbar">
      <button type="button" class="btn btn-icon btn-ghost tb-menu" id="tb-menu" aria-label="Open menu">${icon('menu')}</button>
      <div class="tb-title" id="tb-title">${esc(active ? active.label : 'iSpend')}</div>
      <button type="button" class="tb-search" id="tb-search" aria-label="Search (⌘K)">${icon('search')}<span>Search transactions…</span><kbd>⌘K</kbd></button>
      <div class="tb-actions">
        <button type="button" class="btn btn-icon btn-ghost" id="tb-theme" aria-label="Toggle theme" title="Toggle theme">${icon('sun')}</button>
        <button type="button" class="tb-avatar-btn" id="tb-user" aria-haspopup="menu" aria-label="Account menu"><span class="avatar" id="tb-avatar"></span><span class="tb-username text-2 fs-base" id="tb-username"></span>${icon('chevron-down', 'ico-sm text-3')}</button>
      </div>
    </header>`;
  main.parentNode.insertBefore(shell, main);
  shell.appendChild(main);

  // Mobile bottom nav
  const bn = document.createElement('nav'); bn.className = 'bottomnav'; bn.setAttribute('aria-label', 'Primary');
  bn.innerHTML = BOTTOM_NAV.map((p) => NAV_ITEMS.find((i) => i.page === p)).map((i) => `<a href="${i.href}${esc(savedQuery(i.href))}" class="bn-item" ${i.page === activePage ? 'aria-current="page"' : ''}>${icon(i.icon)}<span>${esc(i.label)}</span>${i.pill ? '<span class="pill" data-review-pill hidden>0</span>' : ''}</a>`).join('')
    + `<button type="button" class="bn-item" id="bn-more">${icon('menu')}<span>More</span></button>`;
  document.body.appendChild(bn);
  ['drawer-root', 'modal-root', 'toast-root'].forEach((id) => { if (!document.getElementById(id)) { const d = document.createElement('div'); d.id = id; d.setAttribute('role', 'region'); d.setAttribute('aria-label', ROOT_LABELS[id]); document.body.appendChild(d); } });

  // Wiring
  // The off-canvas sidebar is a layer like a modal: Escape closes it, focus stays inside, and returns to the opener.
  let mobileLayer = null;
  let untrapMobile = null;
  function openMobile(e) {
    if (mobileLayer) return;
    document.body.classList.add('sidebar-open');
    const sb = $('#sidebar');
    untrapMobile = ui.trapFocus(sb);
    mobileLayer = { close: closeMobile, onEsc: true, opener: e && e.currentTarget };
    ui.pushLayer(mobileLayer);
    const first = sb.querySelector('a[href], button'); if (first) first.focus();
  }
  function closeMobile() {
    if (!document.body.classList.contains('sidebar-open')) return;
    document.body.classList.remove('sidebar-open');
    if (untrapMobile) { untrapMobile(); untrapMobile = null; }
    const opener = mobileLayer && mobileLayer.opener;
    if (mobileLayer) { ui.popLayer(mobileLayer); mobileLayer = null; }
    if (opener && opener.offsetParent) opener.focus();
  }
  $('#tb-menu').addEventListener('click', openMobile);
  $('#bn-more').addEventListener('click', openMobile);
  $('#sidebar-backdrop').addEventListener('click', closeMobile);
  $('#sidebar').addEventListener('click', (e) => { if (e.target.closest('a')) closeMobile(); });
  $('#sb-collapse').addEventListener('click', () => { if (document.documentElement.getAttribute('data-sidebar') === 'hidden') closeMobile(); else setSidebarMode('rail'); });
  $('#sb-expand').addEventListener('click', () => setSidebarMode('full'));
  window.addEventListener('resize', applySidebarMode);
  window.addEventListener('scroll', () => $('#topbar').classList.toggle('scrolled', window.scrollY > 4), { passive: true });
  const themeBtn = $('#tb-theme');
  const paintTheme = () => { themeBtn.innerHTML = icon(Theme.effective() === 'dark' ? 'sun' : 'moon'); };
  paintTheme();
  themeBtn.addEventListener('click', toggleThemePersisted);
  window.addEventListener('ispend:theme', paintTheme);
  $('#tb-search').addEventListener('click', openPalette);
  $('#tb-user').addEventListener('click', (e) => openUserMenu(e.currentTarget));

  ui.shortcuts.register('[', () => setSidebarMode(sidebarMode() === 'rail' ? 'full' : 'rail'), { description: 'Toggle sidebar' });
  ui.shortcuts.register(']', () => setSidebarMode('full'));
  ui.shortcuts.register('?', () => ui.shortcutsSheet(window.PAGE_SHORTCUTS || []), { description: 'Shortcuts' });
  ui.shortcuts.register('/', () => openPalette(), { description: 'Search' });
  NAV_ITEMS.filter((i) => i.key).forEach((i) => ui.shortcuts.register(`g ${i.key}`, () => { location.href = i.href; }, { description: `Go to ${i.label}` }));
  document.addEventListener('keydown', (e) => { if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); openPalette(); } });

  // Auth gate
  let me;
  try {
    me = await api('/api/auth/me');
  } catch (err) {
    if (err.message !== 'Not authenticated') {
      main.innerHTML = ui.errorBox(`Could not reach the server: ${err.message}`, { retry: 'reload' });
      main.addEventListener('click', (e) => { if (e.target.closest('[data-act="reload"]')) location.reload(); });
    }
    return new Promise(() => {}); // never resolve: page must not proceed
  }
  window.currentUser = me;
  try {
    if (localStorage.getItem(UID_KEY) !== String(me.id)) { clearUserState(); localStorage.setItem(UID_KEY, String(me.id)); }
  } catch { /* storage unavailable */ }
  $('#tb-avatar').textContent = initials(me.username);
  $('#tb-username').textContent = me.username;
  const prefs = me.preferences || {};
  if (prefs.theme && !Theme.hasStored()) Theme.set(prefs.theme);
  if (prefs.density && !Theme.hasStoredDensity()) Theme.setDensity(prefs.density);
  if (me.role !== 'admin') {
    $$('[data-admin-only]').forEach((el) => el.remove());
    if (active && active.adminOnly) { location.replace('/index.html'); return new Promise(() => {}); }
  }
  refreshReviewPill();
  window.addEventListener('ispend:transactions-changed', refreshReviewPill);
  return me;
}

async function refreshReviewPill() {
  try {
    const c = await api('/api/review/count');
    const n = (c && (c.total ?? ((c.uncategorized || 0) + (c.suggested || 0)))) || 0;
    $$('[data-review-pill]').forEach((el) => { el.textContent = n > 999 ? '999+' : String(n); el.hidden = !n; });
  } catch { /* endpoint may not exist yet */ }
}

/* ---------- User menu ---------- */
function openUserMenu(anchor) {
  const me = window.currentUser || {};
  const mode = Theme.get();
  ui.menu(anchor, [
    { label: `${me.username || ''} · ${me.role || ''}`, header: true },
    { label: 'Theme: System', icon: 'monitor', checked: mode === 'system', onClick: () => setThemePref('system') },
    { label: 'Theme: Light', icon: 'sun', checked: mode === 'light', onClick: () => setThemePref('light') },
    { label: 'Theme: Dark', icon: 'moon', checked: mode === 'dark', onClick: () => setThemePref('dark') },
    { divider: true },
    { label: 'Keyboard shortcuts', icon: 'keyboard', shortcut: '?', onClick: () => ui.shortcutsSheet(window.PAGE_SHORTCUTS || []) },
    { label: 'Change password', icon: 'lock', onClick: openChangePassword },
    { label: 'Settings', icon: 'settings', href: '/settings.html' },
    { divider: true },
    { label: 'Sign out', icon: 'log-out', onClick: async () => {
      try { await api('/api/auth/logout', { method: 'POST' }); } catch { /* the cookie is cleared server-side on the next request anyway */ }
      clearUserState();
      location.href = '/login.html';
    } },
  ]);
}
/* Quick toggle that persists: when the target equals what the device would show anyway, store "system"
   so the preference stays device-driven (and a double toggle is a no-op). */
function toggleThemePersisted() {
  const next = Theme.effective() === 'dark' ? 'light' : 'dark';
  setThemePref(next === Theme.system() ? 'system' : next);
}
function setThemePref(mode) {
  Theme.set(mode);
  api('/api/auth/me/preferences', { method: 'PUT', body: { theme: mode } }).catch(() => {});
}
function openChangePassword() {
  const m = ui.modal({
    title: 'Change password',
    html: `<form id="pw-form" class="col" style="gap:0">
      <div class="field"><label for="pw-cur">Current password</label><input id="pw-cur" class="input" type="password" autocomplete="current-password" required></div>
      <div class="field"><label for="pw-new">New password</label><input id="pw-new" class="input" type="password" autocomplete="new-password" minlength="10" required><div class="hint">At least 10 characters.</div></div>
      <div class="field"><label for="pw-conf">Confirm new password</label><input id="pw-conf" class="input" type="password" autocomplete="new-password" required></div>
      <button type="submit" hidden></button></form>`,
    actions: [
      { label: 'Cancel' },
      { label: 'Update password', primary: true, onClick: async () => {
        const cur = $('#pw-cur').value, nw = $('#pw-new').value, conf = $('#pw-conf').value;
        if (nw !== conf) throw new Error('New passwords do not match');
        await api('/api/auth/me/password', { method: 'PUT', body: { current_password: cur, password: nw } });
        toast('Password updated', { type: 'success' });
      } },
    ],
  });
  m.el.querySelector('#pw-form').addEventListener('submit', (e) => { e.preventDefault(); m.el.querySelectorAll('.modal-foot .btn')[1].click(); });
}

/* ---------- Command palette ---------- */
let paletteOpen = false;
function openPalette() {
  if (paletteOpen) return;
  paletteOpen = true;
  const host = document.getElementById('modal-root') || document.body;
  const wrap = document.createElement('div');
  wrap.className = 'palette-backdrop';
  wrap.innerHTML = `<div class="palette" role="dialog" aria-label="Search">
      <div class="palette-input">${icon('search')}<input type="text" placeholder="Search transactions, categories, pages…" aria-label="Search" role="combobox" aria-expanded="true" aria-autocomplete="list" aria-controls="palette-list" aria-haspopup="listbox" autocomplete="off" spellcheck="false"><kbd>esc</kbd></div>
      <div class="palette-list" id="palette-list" role="listbox" aria-label="Results"></div>
      <div class="sr-only" id="palette-live" aria-live="polite"></div></div>`;
  host.appendChild(wrap);
  const input = wrap.querySelector('input');
  const list = wrap.querySelector('.palette-list');
  const prevFocus = document.activeElement;
  let items = [], active = 0, seq = 0;
  const handle = { close() { paletteOpen = false; ui.layers.splice(ui.layers.indexOf(handle), 1); wrap.remove(); untrap(); prevFocus && prevFocus.focus && prevFocus.focus(); } };
  ui.layers.unshift(handle);
  const untrap = ui.trapFocus(wrap);
  wrap.addEventListener('mousedown', (e) => { if (e.target === wrap) handle.close(); });

  const actions = [
    { group: 'Actions', label: 'Import a statement', icon: 'upload', run: () => { location.href = '/import.html'; } },
    { group: 'Actions', label: 'Toggle theme', icon: 'moon', run: toggleThemePersisted },
    { group: 'Actions', label: 'Review uncategorized charges', icon: 'inbox', run: () => { location.href = '/review.html?mode=merchant'; } },
  ];
  const pages = NAV_ITEMS.map((i) => ({ group: 'Pages', label: `Go to ${i.label}`, icon: i.icon, run: () => { location.href = i.href; } }));

  async function search(q) {
    const mySeq = ++seq;
    q = q.trim();
    const ql = q.toLowerCase();
    const match = (s) => !ql || s.toLowerCase().includes(ql);
    let out = [];
    if (ql.length >= 2) {
      try {
        const cats = await store.categoriesFlat();
        out.push(...cats.filter((c) => match(c.path)).slice(0, 5).map((c) => ({ group: 'Categories', label: c.path, color: c.color || c.parent_color, run: () => { location.href = `/transactions.html?cat=${c.id}`; } })));
      } catch { /* ignore */ }
    }
    out.push(...pages.filter((p) => match(p.label)).slice(0, ql ? 4 : 6), ...actions.filter((a) => match(a.label)));
    if (mySeq === seq) render(out, q);
    if (ql.length >= 2) {
      try {
        const res = await api(`/api/transactions?q=${encodeURIComponent(q)}&limit=8`);
        if (mySeq !== seq) return;
        const tx = (res.items || []).map((t) => ({ group: 'Transactions', label: t.merchant_name || t.description_clean || '', sub: `${fmtDate(t.txn_date)} · ${fmtMoney(t.amount, t.currency)}`, icon: 'list', run: () => { location.href = `/transactions.html?q=${encodeURIComponent(q)}&open=${t.id}`; } }));
        render([...tx, ...out], q);
      } catch { /* backend not ready */ }
    }
  }
  const live = wrap.querySelector('#palette-live');
  function render(all, q) {
    items = all; active = 0;
    if (!all.length) { list.innerHTML = `<div class="palette-empty">No results for “${esc(q)}”</div>`; input.removeAttribute('aria-activedescendant'); live.textContent = q ? `No results for ${q}` : 'No results'; return; }
    let html = '', last = null;
    all.forEach((it, i) => {
      if (it.group !== last) { html += `<div class="palette-group" role="presentation">${esc(it.group)}</div>`; last = it.group; }
      html += `<button type="button" class="palette-item${i === 0 ? ' is-active' : ''}" role="option" id="pal-opt-${i}" aria-selected="${i === 0}" data-i="${i}">${it.color ? `<i class="dot" style="--c:var(--${esc(it.color)})"></i>` : icon(it.icon || 'arrow-right')}<span class="truncate">${esc(it.label)}</span>${it.sub ? `<span class="sub">${esc(it.sub)}</span>` : ''}</button>`;
    });
    list.innerHTML = html;
    input.setAttribute('aria-activedescendant', 'pal-opt-0');
    live.textContent = `${all.length} result${all.length === 1 ? '' : 's'}`;
  }
  function highlight() {
    $$('.palette-item', list).forEach((el, i) => { const on = i === active; el.classList.toggle('is-active', on); el.setAttribute('aria-selected', on); });
    const a = list.querySelector('.is-active'); if (a) { a.scrollIntoView({ block: 'nearest' }); input.setAttribute('aria-activedescendant', a.id); }
  }
  list.addEventListener('mousemove', (e) => { const b = e.target.closest('[data-i]'); if (b && Number(b.dataset.i) !== active) { active = Number(b.dataset.i); highlight(); } });
  input.addEventListener('input', debounce(() => search(input.value), 150));
  input.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); active = Math.min(active + 1, items.length - 1); highlight(); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); active = Math.max(active - 1, 0); highlight(); }
    else if (e.key === 'Home') { e.preventDefault(); active = 0; highlight(); }
    else if (e.key === 'End') { e.preventDefault(); active = Math.max(items.length - 1, 0); highlight(); }
    else if (e.key === 'Enter') { e.preventDefault(); const it = items[active]; if (it) { handle.close(); it.run(); } }
  });
  list.addEventListener('click', (e) => { const b = e.target.closest('[data-i]'); if (!b) return; const it = items[Number(b.dataset.i)]; handle.close(); it.run(); });
  search('');
  requestAnimationFrame(() => input.focus());
}
