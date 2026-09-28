/* Admin console shell: its own navigation, one router, the global date range and the KPI tile.

   Sections register themselves (one file each) and this file boots once every script has run:
     AdminPanels.register('customers', { label, icon, sub, group, ranged, compare, params, actions,
                                         markup, load(host, ctx) })
   group — 'home' | 'customers' | 'business' | 'operations' | 'footer'; ranged — shows the range
   picker, and ctx.range is the query string for the API (range=30d&compare=1); compare — the
   section's numbers answer "compare with the period before"; params — the query keys the section
   owns (dropped when another section opens); markup — the panel's static HTML; actions — HTML for
   the page-head buttons shown while the section is open.

   URL: /admin?<range and the open section's filters>#<section>[/<id>[/<tab>]], so every view
   bookmarks, survives a reload and opens the same in a new tab. adminHref() builds these; links
   written the old way (#users?state=trialing) are rewritten on arrival. ctx.route is {section, id, tab}. */

const ADMIN = { sections: {}, current: null, route: null, me: null, loadSeq: 0, forms: {} };
const ADMIN_GROUPS = [['home', ''], ['customers', 'Customers'], ['business', 'Business'], ['operations', 'Operations']];
const ADMIN_ORDER = ['overview', 'customers', 'messages', 'revenue', 'engagement', 'imports', 'system',
  'activity', 'backup', 'settings'];
const ADMIN_BOTTOM = ['overview', 'customers', 'revenue', 'activity'];
const ADMIN_KEYS = { overview: 'h', customers: 'c', revenue: 'r', engagement: 'e', activity: 'l', system: 'y', settings: 's' };
/* Old section names, and settings that used to be sections of their own. */
const ADMIN_ALIASES = { users: 'customers', billing: 'settings/billing', signups: 'settings/signups',
  retention: 'settings/retention', landing: 'settings/landing' };
const RANGE_CHOICES = [['7d', 'Last 7 days'], ['30d', 'Last 30 days'], ['90d', 'Last 90 days'],
  ['12m', 'Last 12 months'], ['mtd', 'Month to date'], ['ytd', 'Year to date']];
const RANGE_QS = ['range', 'from', 'to', 'cmp'];

window.AdminPanels = {
  register(key, meta) {
    ADMIN.sections[key] = { group: 'operations', params: [], ...meta, key };
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
  route: () => ADMIN.route || adminRoute(),
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

function adminParseHash(hash) {
  const raw = decodeURIComponent((hash || '').replace(/^#/, ''));
  const cut = raw.indexOf('?');
  const path = cut < 0 ? raw : raw.slice(0, cut);
  let parts = path.split('/').filter(Boolean);
  if (parts.length && ADMIN_ALIASES[parts[0]]) parts = [...ADMIN_ALIASES[parts[0]].split('/'), ...parts.slice(1)];
  return { parts, query: cut < 0 ? '' : raw.slice(cut + 1) };
}

function adminRoute() {
  const { parts } = adminParseHash(location.hash);
  const section = ADMIN.sections[parts[0]] ? parts[0] : 'overview';
  return { section, id: section === parts[0] ? parts[1] || null : null, tab: section === parts[0] ? parts[2] || null : null };
}

/* The page a form belongs to: a section, or a settings page. Unsaved-changes checks compare these. */
function adminPageKey(route) {
  if (route.section !== 'settings') return route.section;
  const keys = window.AdminSettings ? AdminSettings.keys() : [];
  return `settings/${keys.includes(route.id) ? route.id : keys[0] || ''}`;
}

/* Filters written into the hash (older links, the server's alert links) move into the query, and old
   section names become new ones. Returns true when the address changed. */
function adminNormalizeUrl() {
  const { parts, query } = adminParseHash(location.hash);
  const canonical = `#${parts.join('/')}`;
  if (!query && (canonical === location.hash || !location.hash)) return false;
  const url = new URL(location.href);
  new URLSearchParams(query).forEach((v, k) => url.searchParams.set(k, v));
  url.hash = parts.length ? canonical : '';
  history.replaceState(history.state, '', url);
  return true;
}

/* A link into the console: the current period, the given filters, and the path. */
function adminHref(section, { id, tab, params } = {}) {
  const q = new URLSearchParams();
  const cur = qs();
  RANGE_QS.forEach((k) => { if (cur[k]) q.set(k, cur[k]); });
  Object.entries(params || {}).forEach(([k, v]) => { if (v != null && v !== '') q.set(k, v); });
  const query = q.toString();
  return `/admin${query ? `?${query}` : ''}#${[section, id, tab].filter((x) => x != null && x !== '').join('/')}`;
}

/* Leaving a section drops the filters it owned, so they do not ride along into the next one. */
function adminDropForeignParams(key) {
  const keep = new Set([...RANGE_QS, ...(ADMIN.sections[key].params || [])]);
  const drop = Object.keys(qs()).filter((k) => !keep.has(k));
  if (drop.length) setQs(Object.fromEntries(drop.map((k) => [k, undefined])), { merge: true, replace: true });
}

async function adminLoadCurrent({ force = false } = {}) {
  const key = ADMIN.current;
  const meta = ADMIN.sections[key];
  if (!meta || !meta.load) return undefined;
  const seq = ++ADMIN.loadSeq;
  const ctx = { force, range: adminRangeQuery(), route: ADMIN.route,
    isCurrent: () => seq === ADMIN.loadSeq && ADMIN.current === key };
  return meta.load(AdminPanels.panel(key), ctx);
}

/* The page head belongs to the shell; a section showing one record (a customer) replaces it with
   its own header and calls this with null. Every navigation puts the shell's back. */
function adminSetHead(head) {
  const el = $('#main .page-head');
  el.hidden = !head;
  if (!head) return;
  $('#admin-title').textContent = head.title;
  $('#tb-title').textContent = head.title;
  $('#admin-sub').textContent = head.sub || '';
}

function adminShow() {
  // Section switches are same-document hash changes; a menu or drawer opened on the previous
  // section would otherwise stay on screen. Bounded: a drawer may decline to close.
  for (let i = 0; ui.layers.length && i < 8; i++) ui.closeTop();
  adminNormalizeUrl();
  const route = adminRoute();
  const key = route.section;
  const meta = ADMIN.sections[key];
  const changed = ADMIN.current !== key;
  if (changed) adminDropForeignParams(key);
  ADMIN.current = key;
  ADMIN.route = route;
  ADMIN.lastUrl = location.href;
  $$('.sb-nav .nav-item[data-page], .sb-foot .nav-item[data-page], .bottomnav .bn-item[data-page]').forEach((a) => {
    if (a.dataset.page === key) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
  });
  const more = $('#bn-more');
  if (more) { if (ADMIN_BOTTOM.includes(key)) more.removeAttribute('aria-current'); else more.setAttribute('aria-current', 'page'); }
  $$('#admin-panels .tab-panel').forEach((p) => p.classList.toggle('active', p.dataset.panel === key));
  const groups = $$('#admin-actions .adm-actions');
  groups.forEach((g) => { g.hidden = g.dataset.actions !== key; });
  $('#adm-range-group').hidden = !meta.ranged;
  $('#adm-compare').hidden = !meta.compare;
  $('#admin-actions').hidden = !meta.ranged && !groups.some((g) => g.dataset.actions === key);
  adminSetHead({ title: meta.label, sub: meta.sub });
  setPageTitle(meta.label);
  if (changed) window.scrollTo(0, 0);
  window.dispatchEvent(new CustomEvent('ispend:admin-tab', { detail: key }));
  return adminLoadCurrent();
}

/* ---------- unsaved changes ---------- */

/* Settings pages register their form here (adminDirtyBar does it); leaving a page with unsaved
   edits asks first instead of dropping them. */
function adminDirtyForm() {
  const f = ADMIN.forms[adminPageKey(ADMIN.route || adminRoute())];
  return f && f.dirty ? f : null;
}

function adminAskToLeave(form) {
  return new Promise((resolve) => {
    let choice = 'stay';
    ui.modal({
      title: 'Save your changes?',
      width: 420,
      html: '<p>This page has changes that have not been saved yet.</p>',
      actions: [
        { label: 'Stay here' },
        { label: 'Discard', onClick: () => { choice = 'discard'; } },
        { label: 'Save', primary: true, onClick: async () => { await form.save(); choice = 'save'; } },
      ],
      onClose: () => resolve(choice),
    });
  });
}

async function adminOnHashChange() {
  const form = adminDirtyForm();
  const next = adminRoute();
  if (form && ADMIN.lastUrl && adminPageKey(next) !== adminPageKey(ADMIN.route)) {
    const target = location.href;
    history.replaceState(history.state, '', ADMIN.lastUrl);
    const choice = await adminAskToLeave(form);
    if (choice === 'stay') return undefined;
    if (choice === 'discard') form.discard();
    history.replaceState(history.state, '', target);
  }
  return adminShow();
}

window.addEventListener('beforeunload', (e) => {
  if (adminDirtyForm()) { e.preventDefault(); e.returnValue = ''; }
});

/* ---------- palette ---------- */

function adminPalette() {
  const sections = adminSectionKeys().map((k) => ADMIN.sections[k]);
  const go = (section, opts) => () => { location.href = adminHref(section, opts); };
  window.PALETTE = {
    placeholder: 'Search customers by email, name or #id, or jump to a page…',
    pages: [
      ...sections.map((s) => ({ group: 'Pages', label: `Go to ${s.label}`, icon: s.icon, run: go(s.key) })),
      ...(window.AdminSettings ? AdminSettings.keys().map((k) => ({ group: 'Settings', label: `Settings › ${AdminSettings.meta(k).label}`,
        icon: AdminSettings.meta(k).icon, run: go('settings', { id: k }) })) : []),
    ],
    actions: [
      { group: 'Actions', label: 'Refresh this page', icon: 'refresh', run: () => AdminPanels.refresh() },
      { group: 'Actions', label: 'Add a customer', icon: 'plus', run: () => { location.href = adminHref('customers', { params: { add: 1 } }); } },
      { group: 'Actions', label: 'Email customers', icon: 'send', run: go('messages') },
      { group: 'Actions', label: 'Compare revenue records with Stripe', icon: 'refresh', run: go('revenue', { params: { reconcile: 1 } }) },
      { group: 'Actions', label: 'Make a backup', icon: 'database', run: go('backup') },
      { group: 'Actions', label: 'Toggle theme', icon: 'moon', run: toggleThemePersisted },
    ],
    async search(q) {
      const res = await api(`/api/admin/search?q=${encodeURIComponent(q)}`);
      return (res.users || []).map((u) => ({
        group: 'Customers', label: u.email || u.username, icon: 'user',
        sub: [u.email && u.username !== u.email ? u.username : null, u.status !== 'active' ? u.status : null,
          u.ent_state ? u.ent_state.replace('_', ' ') : null, `#${u.id}`].filter(Boolean).join(' · '),
        run: () => adminOpenUser(u.id),
      }));
    },
  };
}

function adminOpenUser(id, tab) {
  location.href = adminHref('customers', { id, tab });
}

/* ---------- boot ---------- */

/* Always in view: whether Stripe charges real cards. The operator should never have to wonder. */
const ENV_BADGES = {
  test: ['badge-warning', 'Test mode', 'Stripe is using test keys: no real card is charged.'],
  live: ['badge-success', 'Live', 'Stripe is using live keys: payments are real.'],
  off: ['badge-neutral', 'Billing off', 'Stripe is not set up, so every customer has full access for free.'],
};
function paintEnvBadge(mode) {
  const [cls, label, tip] = ENV_BADGES[mode] || ENV_BADGES.off;
  let el = $('#adm-env');
  if (!el) {
    el = document.createElement('a');
    el.id = 'adm-env';
    el.href = '#settings/billing';
    $('.tb-actions').prepend(el);
  }
  el.className = `badge ${cls} adm-env`;
  el.dataset.tip = tip;
  el.setAttribute('aria-label', `${label}. ${tip}`);
  el.textContent = label;
}

function adminNavItem(key) {
  const s = ADMIN.sections[key];
  return { page: key, href: `#${key}`, label: s.label, short: s.short, icon: s.icon, key: ADMIN_KEYS[key] };
}

/* The account menu: the admin's own account lives in Settings, like everything else here. */
const ADMIN_MENU = [
  { label: 'My account', icon: 'user', href: '#settings/account' },
  { label: 'Settings', icon: 'settings', href: '#settings' },
];

document.addEventListener('DOMContentLoaded', () => {
  const keys = adminSectionKeys();
  adminNormalizeUrl();
  const groups = ADMIN_GROUPS.map(([g, label]) => ({ label, items: keys.filter((k) => ADMIN.sections[k].group === g).map(adminNavItem) }))
    .filter((g) => g.items.length);
  const footer = keys.filter((k) => ADMIN.sections[k].group === 'footer').map(adminNavItem);
  adminPalette();
  $('#adm-range').addEventListener('click', (e) => openRangePicker(e.currentTarget));
  $('#adm-compare').addEventListener('click', () => setAdminRange({ compare: !adminRange().compare }));
  $('#adm-compare').innerHTML = `${icon('arrow-left-right')}<span class="label">Compare</span>`;
  paintRangeControl();
  // initNav sends anyone who is not an admin back to the app before this resolves.
  initNav(adminRoute().section, {
    shell: 'admin', groups, footer, bottom: ADMIN_BOTTOM.filter((k) => ADMIN.sections[k]),
    more: keys.filter((k) => !ADMIN_BOTTOM.includes(k)).map(adminNavItem), menu: ADMIN_MENU,
    brandBadge: 'Admin', searchLabel: 'Search customers, pages and actions…', title: 'Admin',
  }).then((me) => {
    ADMIN.me = me;
    $('#admin-layout').hidden = false;
    api('/api/admin/shell').then((sh) => paintEnvBadge(sh.stripe_mode)).catch(() => {});
    window.addEventListener('hashchange', adminOnHashChange);
    ui.shortcuts.register('r', () => AdminPanels.refresh(), { description: 'Admin: refresh this page' });
    window.PAGE_SHORTCUTS = [{ title: 'Admin', items: [['r', 'Refresh this page'],
      ...keys.filter((k) => ADMIN_KEYS[k]).map((k) => [`g ${ADMIN_KEYS[k]}`, ADMIN.sections[k].label])] }];
    adminShow();
  });
});
