/* Admin console shell: its own navigation, section routing, the global date range and the KPI tile.

   Sections register themselves (one file each) and this file boots once every script has run:
     AdminPanels.register('users', { label, icon, sub, group, ranged, actions, markup, load(host, ctx) })
   group — 'insights' | 'operations' | 'settings'; ranged — shows the range picker, and ctx.range is
   the query string for the API (range=30d&compare=1); markup — the panel's static HTML; actions —
   HTML for the page-head buttons shown while the section is open.

   URL: the hash is the section (#users), the query holds filters and the range (?range=90d&cmp=1),
   so a view can be bookmarked and survives a reload. */

const ADMIN = { sections: {}, current: null, me: null, loadSeq: 0 };
const ADMIN_GROUPS = [['insights', 'Insights'], ['operations', 'Operations'], ['settings', 'Settings']];
const ADMIN_ORDER = ['overview', 'revenue', 'users', 'engagement', 'imports', 'system', 'activity',
  'billing', 'signups', 'messages', 'landing', 'retention', 'backup'];
const ADMIN_DEFAULT_GROUP = { billing: 'settings', signups: 'settings', backup: 'settings' };
const ADMIN_BOTTOM = ['overview', 'revenue', 'users', 'activity'];
const ADMIN_KEYS = { overview: 'o', revenue: 'v', users: 'u', engagement: 'e', activity: 'l', system: 'y' };
const RANGE_CHOICES = [['7d', 'Last 7 days'], ['30d', 'Last 30 days'], ['90d', 'Last 90 days'],
  ['12m', 'Last 12 months'], ['mtd', 'Month to date'], ['ytd', 'Year to date']];
const RANGE_QS = ['range', 'from', 'to', 'cmp'];

window.AdminPanels = {
  register(key, meta) {
    ADMIN.sections[key] = { group: ADMIN_DEFAULT_GROUP[key] || 'operations', ...meta, key };
    const panels = document.getElementById('admin-panels');
    if (panels && !panels.querySelector(`[data-panel="${key}"]`)) {
      const panel = document.createElement('section');
      panel.className = 'tab-panel';
      panel.dataset.panel = key;
      panel.setAttribute('aria-labelledby', 'admin-title');
      if (meta.markup) panel.innerHTML = meta.markup;
      panels.appendChild(panel);
    }
    if (meta.actions) {
      const group = document.createElement('div');
      group.className = 'adm-actions';
      group.dataset.actions = key;
      group.hidden = true;
      group.innerHTML = meta.actions;
      document.getElementById('admin-actions').appendChild(group);
    }
  },
  refresh: () => adminLoadCurrent({ force: true }),
  current: () => ADMIN.current,
  panel: (key) => document.querySelector(`[data-panel="${key || ADMIN.current}"]`),
};

/* ---------- range ---------- */

function adminRange() {
  const q = qs();
  const range = RANGE_CHOICES.some(([k]) => k === q.range) || q.range === 'custom' ? q.range : '30d';
  return { range, from: q.from || '', to: q.to || '', compare: q.cmp === '1' };
}

function adminRangeQuery(extra = {}) {
  const r = adminRange();
  return toQuery({ range: r.range, from: r.range === 'custom' ? r.from : undefined,
    to: r.range === 'custom' ? r.to : undefined, compare: r.compare ? 1 : undefined, ...extra });
}

function adminRangeLabel(r = adminRange()) {
  if (r.range === 'custom' && r.from && r.to) return `${fmtDate(r.from, { year: true })} – ${fmtDate(r.to, { year: true })}`;
  return (RANGE_CHOICES.find(([k]) => k === r.range) || RANGE_CHOICES[1])[1];
}

function paintRangeControl() {
  const r = adminRange();
  $('#adm-range').innerHTML = `${icon('calendar')}<span class="label">${esc(adminRangeLabel(r))}</span>${icon('chevron-down', 'ico-sm')}`;
  const cmp = $('#adm-compare');
  cmp.setAttribute('aria-pressed', String(r.compare));
  cmp.classList.toggle('is-on', r.compare);
}

function setAdminRange(next) {
  const cur = adminRange();
  const r = { ...cur, ...next };
  setQs({ range: r.range === '30d' ? undefined : r.range, from: r.range === 'custom' ? r.from : undefined,
    to: r.range === 'custom' ? r.to : undefined, cmp: r.compare ? '1' : undefined }, { merge: true, replace: true });
  paintRangeControl();
  adminLoadCurrent();
}

function openRangePicker(anchor) {
  const r = adminRange();
  const el = document.createElement('div');
  el.className = 'menu adm-range-menu';
  el.innerHTML = `
    <div role="radiogroup" aria-label="Period">${RANGE_CHOICES.map(([k, l]) => `
      <button type="button" class="menu-item" role="radio" aria-checked="${r.range === k}" data-range="${k}">
        <span class="grow">${esc(l)}</span>${r.range === k ? `<span class="menu-check">${icon('check')}</span>` : ''}</button>`).join('')}</div>
    <div class="menu-divider" role="separator"></div>
    <div class="adm-range-custom">
      <div class="menu-label">Custom</div>
      <div class="row gap-2">
        <label class="sr-only" for="adm-from">From</label><input id="adm-from" class="input input-sm" type="date" value="${esc(r.from)}">
        <label class="sr-only" for="adm-to">To</label><input id="adm-to" class="input input-sm" type="date" value="${esc(r.to)}">
      </div>
      <button type="button" class="btn btn-primary btn-sm btn-block mt-2" data-range-apply>Apply</button>
    </div>`;
  const pop = ui.popover(anchor, el, { placement: 'bottom-end' });
  el.addEventListener('click', (e) => {
    const pick = e.target.closest('[data-range]');
    if (pick) { pop.close(); setAdminRange({ range: pick.dataset.range }); return; }
    if (e.target.closest('[data-range-apply]')) {
      const from = $('#adm-from', el).value, to = $('#adm-to', el).value;
      if (!from || !to) { ui.fieldError($(from ? '#adm-to' : '#adm-from', el), 'Pick a date'); return; }
      if (to < from) { ui.fieldError($('#adm-to', el), 'Must be on or after the start'); return; }
      pop.close();
      setAdminRange({ range: 'custom', from, to });
    }
  });
}

/* "1 person", "3 people" — plural() only knows how to add an s. */
function people(n) { return `${fmtNumber(n)} ${n === 1 ? 'person' : 'people'}`; }

/* ---------- KPI tiles ---------- */

function fmtAdminValue(v, unit, currency) {
  if (v == null) return '—';
  if (unit === 'money') return fmtMoney(v / 100, (currency || 'usd').toUpperCase());
  if (unit === 'pct') return fmtPct(v);
  return fmtNumber(v);
}

/* A change is good or bad by what it means, not by its sign: more cancellations is red. */
function deltaHtml(t) {
  if (t.prev == null || t.value == null) return '';
  const dir = !t.delta ? 'flat' : t.delta > 0 ? 'up' : 'down';
  const tone = dir === 'flat' || t.good === 'neutral' ? '' : ((dir === 'up') === (t.good === 'up') ? 'stat-delta--good' : 'stat-delta--bad');
  const size = t.unit === 'pct' ? `${Math.round(Math.abs(t.delta) * 1000) / 10} pts`
    : t.delta_pct != null ? fmtPct(Math.abs(t.delta_pct)) : fmtAdminValue(Math.abs(t.delta), t.unit, t.currency);
  const word = dir === 'flat' ? 'No change' : size;
  return `<span class="stat-delta ${tone}">${dir === 'flat' ? '' : icon(dir === 'up' ? 'arrow-up-right' : 'arrow-down-right')}${esc(word)}
    <span class="stat-delta-vs">vs previous</span></span>`;
}

/* At most `max` points, combined the way the metric allows (see admin_metrics.tile): a 90-day run of
   daily sign-ups drawn point for point is noise, summed into weeks it is a trend. */
function thinSpark(values, agg = 'last', max = 16) {
  const v = (values || []).filter((x) => x != null);
  if (v.length <= max) return v;
  const size = Math.ceil(v.length / max);
  const out = [];
  for (let i = 0; i < v.length; i += size) {
    const chunk = v.slice(i, i + size);
    if (agg === 'sum') out.push(chunk.reduce((a, b) => a + b, 0));
    else if (agg === 'mean') out.push(chunk.reduce((a, b) => a + b, 0) / chunk.length);
    else out.push(chunk[chunk.length - 1]);
  }
  return out;
}

/* Rows that divide evenly: 10 tiles are 5 × 2, 6 are 3 × 2, never 4 + 2. */
function tileColumns(n) {
  if (n <= 5) return n;
  return [5, 4, 3].find((c) => n % c === 0) || 5;
}

function adminTiles(tiles) {
  return `<div class="stat-grid adm-tiles" style="--cols:${tileColumns(tiles.length)}">${tiles.map((t) => {
    const spark = thinSpark(t.spark, t.agg);
    const tag = t.drill ? 'a' : 'div';
    return `<${tag} class="stat adm-kpi ${spark.length > 1 ? 'has-spark' : ''}" data-kpi="${esc(t.key)}" ${t.drill ? `href="${esc(t.drill)}"` : ''}>
      <div class="stat-label">${esc(t.label)}${t.help ? ` <span class="adm-help" tabindex="0" role="note" aria-label="${esc(t.help)}" data-tip="${esc(t.help)}">${icon('help', 'ico-sm')}</span>` : ''}</div>
      <div class="stat-value">${esc(fmtAdminValue(t.value, t.unit, t.currency))}${t.value != null && t.suffix ? `<span class="u">${esc(t.suffix)}</span>` : ''}</div>
      ${deltaHtml(t)}
      ${spark.length > 1 ? charts.sparkSvg(spark, 'var(--accent)', { width: 96, height: 32, cls: 'stat-spark' }) : ''}
    </${tag}>`;
  }).join('')}</div>`;
}

function adminSkeletonTiles(n = 8) {
  return `<div class="stat-grid adm-tiles">${Array.from({ length: n }, () =>
    `<div class="stat is-loading"><div class="stat-label">${ui.skeleton(90, 12)}</div>${ui.skeleton(120, 26, 'stat-skel')}</div>`).join('')}</div>`;
}

function adminAsOf(iso) {
  return iso ? `<span class="adm-asof" data-tip="${esc(fmtDateTime(iso))}">Updated ${esc(fmtRelative(iso))}</span>` : '';
}

/* Small labelled figures and proportion bars, shared by every section's cards. */
function kpis(items, extraClass = '') {
  return `<div class="adm-kpis ${extraClass}">${items.map(([l, v, cls]) =>
    `<div><div class="l">${esc(l)}</div><div class="v ${cls || ''}">${v}</div></div>`).join('')}</div>`;
}

function bar(label, value, max, hint) {
  const pct = max > 0 ? Math.round((value / max) * 100) : 0;
  return `<div class="adm-bar-row">
    <div class="adm-bar-head"><span class="text-1">${esc(label)}</span><span class="text-3 num">${esc(hint)}</span></div>
    <div class="progress"><span style="width:${pct}%"></span></div></div>`;
}

/* ---------- routing ---------- */

function adminSectionKeys() {
  const keys = Object.keys(ADMIN.sections);
  return ADMIN_ORDER.filter((k) => keys.includes(k)).concat(keys.filter((k) => !ADMIN_ORDER.includes(k)));
}

function adminCurrentKey() {
  const key = (location.hash || '').slice(1).split('?')[0];
  return ADMIN.sections[key] ? key : 'overview';
}

async function adminLoadCurrent({ force = false } = {}) {
  const key = ADMIN.current;
  const meta = ADMIN.sections[key];
  if (!meta || !meta.load) return undefined;
  const seq = ++ADMIN.loadSeq;
  const ctx = { force, range: adminRangeQuery(), isCurrent: () => seq === ADMIN.loadSeq && ADMIN.current === key };
  return meta.load(AdminPanels.panel(key), ctx);
}

function adminShow() {
  // Section switches are same-document hash changes; a menu or drawer opened on the previous
  // section would otherwise stay on screen. Bounded: a drawer may decline to close.
  for (let i = 0; ui.layers.length && i < 8; i++) ui.closeTop();
  const key = adminCurrentKey();
  const meta = ADMIN.sections[key];
  const changed = ADMIN.current !== key;
  ADMIN.current = key;
  $$('.sb-nav .nav-item[data-page], .bottomnav .bn-item[data-page]').forEach((a) => {
    if (a.dataset.page === key) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
  });
  const more = $('#bn-more');
  if (more) { if (ADMIN_BOTTOM.includes(key)) more.removeAttribute('aria-current'); else more.setAttribute('aria-current', 'page'); }
  $$('#admin-panels .tab-panel').forEach((p) => p.classList.toggle('active', p.dataset.panel === key));
  const groups = $$('#admin-actions .adm-actions');
  groups.forEach((g) => { g.hidden = g.dataset.actions !== key; });
  $('#adm-range-group').hidden = !meta.ranged;
  $('#admin-actions').hidden = !meta.ranged && !groups.some((g) => g.dataset.actions === key);
  $('#admin-title').textContent = meta.label;
  $('#tb-title').textContent = meta.label;
  $('#admin-sub').textContent = meta.sub || '';
  setPageTitle(meta.label);
  if (changed) window.scrollTo(0, 0);
  window.dispatchEvent(new CustomEvent('ispend:admin-tab', { detail: key }));
  return adminLoadCurrent();
}

/* ---------- palette ---------- */

function adminPalette() {
  const sections = adminSectionKeys().map((k) => ADMIN.sections[k]);
  window.PALETTE = {
    placeholder: 'Search people by email, name or #id, or jump to a page…',
    pages: sections.map((s) => ({ group: 'Pages', label: `Go to ${s.label}`, icon: s.icon, run: () => { location.hash = `#${s.key}`; } })),
    actions: [
      { group: 'Actions', label: 'Refresh this page', icon: 'refresh', run: () => AdminPanels.refresh() },
      ...(ADMIN.sections.users ? [{ group: 'Actions', label: 'Add a user', icon: 'plus', run: () => { location.hash = '#users'; setTimeout(() => window.openAddUser && openAddUser(), 50); } }] : []),
      ...(ADMIN.sections.billing ? [{ group: 'Actions', label: 'Compare revenue records with Stripe', icon: 'refresh', run: () => { location.hash = '#billing'; } }] : []),
      { group: 'Actions', label: 'Toggle theme', icon: 'moon', run: toggleThemePersisted },
      { group: 'Actions', label: 'Back to the app', icon: 'arrow-left', run: () => { location.href = '/index.html'; } },
    ],
    async search(q) {
      const res = await api(`/api/admin/search?q=${encodeURIComponent(q)}`);
      return (res.users || []).map((u) => ({
        group: 'People', label: u.email || u.username, icon: 'user',
        sub: [u.email && u.username !== u.email ? u.username : null, u.status !== 'active' ? u.status : null,
          u.ent_state ? u.ent_state.replace('_', ' ') : null, `#${u.id}`].filter(Boolean).join(' · '),
        run: () => adminOpenUser(u.id),
      }));
    },
  };
}

/* Users owns the person view; until it has loaded, go there and let it open. */
function adminOpenUser(id) {
  setQs({ user: id }, { merge: true, replace: true });
  if (location.hash !== '#users') location.hash = '#users';
  else if (window.openUserDrawer) openUserDrawer(id);
}

/* ---------- boot ---------- */

function adminNavItem(key) {
  const s = ADMIN.sections[key];
  return { page: key, href: `#${key}`, label: s.label, short: s.short, icon: s.icon, key: ADMIN_KEYS[key] };
}

document.addEventListener('DOMContentLoaded', () => {
  const keys = adminSectionKeys();
  const groups = ADMIN_GROUPS.map(([g, label]) => ({ label, items: keys.filter((k) => ADMIN.sections[k].group === g).map(adminNavItem) }))
    .filter((g) => g.items.length);
  const back = { page: 'app', href: '/index.html', label: 'Back to app', icon: 'arrow-left' };
  adminPalette();
  $('#adm-range').addEventListener('click', (e) => openRangePicker(e.currentTarget));
  $('#adm-compare').addEventListener('click', () => setAdminRange({ compare: !adminRange().compare }));
  $('#adm-compare').innerHTML = `${icon('arrow-left-right')}<span class="label">Compare</span>`;
  paintRangeControl();
  initNav(adminCurrentKey(), {
    groups, footer: [back], bottom: ADMIN_BOTTOM.filter((k) => ADMIN.sections[k]),
    more: [...keys.filter((k) => !ADMIN_BOTTOM.includes(k)).map(adminNavItem), back],
    brandBadge: 'Admin', searchLabel: 'Search people and pages…', title: 'Admin',
  }).then((me) => {
    ADMIN.me = me;
    // /admin.html is a static file anyone can fetch; every endpoint is admin-only, but the page still
    // has to explain itself rather than render a wall of failed requests.
    if (me.role !== 'admin') {
      $('#admin-gate').innerHTML = ui.emptyState({ icon: 'lock', title: 'Admins only',
        body: 'This page manages everyone’s accounts. Ask an administrator if you need access.',
        action: { label: 'Back to dashboard', href: '/index.html' } });
      return;
    }
    $('#admin-layout').hidden = false;
    window.addEventListener('hashchange', adminShow);
    ui.shortcuts.register('r', () => AdminPanels.refresh(), { description: 'Admin: refresh this page' });
    window.PAGE_SHORTCUTS = [{ title: 'Admin', items: [['r', 'Refresh this page'],
      ...keys.filter((k) => ADMIN_KEYS[k]).map((k) => [`g ${ADMIN_KEYS[k]}`, ADMIN.sections[k].label])] }];
    adminShow();
  });
});
