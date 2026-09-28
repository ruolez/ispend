/* Admin › Customers: everyone with an account — searchable, filterable, sortable, paged server-side —
   with view tabs, bulk actions, saved views and CSV export. A row opens the customer's own page
   (#customers/<id>, admin-user360.js).

   Filters live in the URL query (see FILTER_KEYS), so a view bookmarks, survives a reload and can be
   linked to from a KPI tile or a cohort cell. */

const AU = {
  items: [], total: 0, retention: 30, purgeDue: 0,
  selected: new Set(), allMatching: false, facets: null, wired: false, seq: 0,
};
const FILTER_KEYS = ['q', 'status', 'state', 'plan', 'trial_ending', 'confirmed', 'activated',
  'seen_within', 'inactive_for', 'signup_from', 'signup_to', 'source', 'tag', 'sort', 'dir', 'page'];
const PER_PAGE = 50;

const STATUS_LABEL = { active: 'Active', locked: 'Locked', deleted: 'Trash' };
const STATUS_COLOR = { active: 'success', locked: 'warning', deleted: 'text-4' };
const ACCESS_LABEL = { trialing: 'Trial', active: 'Paying', grace: 'Payment due', read_only: 'Read-only',
  comped: 'Free access' };
const ACCESS_BADGE = { trialing: 'badge-info', active: 'badge-success', grace: 'badge-warning',
  read_only: 'badge-neutral', comped: 'badge-accent' };
/* With billing off every account evaluates to "active": call that what it is. */
function accessBadge(key) {
  const free = key === 'active' && ADMIN.stripeMode === 'off';
  const label = free ? 'Full access' : ACCESS_LABEL[key] || key;
  return `<span class="badge ${free ? 'badge-neutral' : ACCESS_BADGE[key] || 'badge-neutral'}">${esc(label)}</span>`;
}

/* The tabs above the table: the questions asked every day, then the admin's own saved views. */
const VIEW_TABS = [['All', ''], ['On a trial', '?state=trialing'], ['Paying', '?state=active'],
  ['Payment due', '?state=grace'], ['Never imported', '?activated=0&sort=created_at&dir=desc']];
const STATUS_FILTERS = [['', 'Active & locked'], ['active', 'Active'], ['locked', 'Locked'], ['deleted', 'Trash'], ['all', 'Everyone']];
const PLAN_FILTERS = [['', 'Any plan'], ['monthly', 'Monthly'], ['yearly', 'Yearly']];
const SEEN_FILTERS = [['', 'Any time', {}], ['s7', 'Seen in the last 7 days', { seen_within: 7 }],
  ['s30', 'Seen in the last 30 days', { seen_within: 30 }], ['i30', 'Not seen for 30+ days', { inactive_for: 30 }],
  ['i90', 'Not seen for 90+ days', { inactive_for: 90 }]];
const SETUP_FILTERS = [['confirmed', '1', 'Confirmed email'], ['confirmed', '0', 'Email not confirmed'],
  ['activated', '1', 'Imported in the first week'], ['activated', '0', 'Did not import in the first week']];
const USER_COLS = [
  ['user', 'Person', 'email'], ['state', 'Access', 'state'], ['mrr', 'MRR', 'mrr', 'right'],
  ['last_seen', 'Last seen', 'last_seen_at'], ['activated', 'Activated', null], ['source', 'Source', null],
  ['txn', 'Transactions', 'txn_count', 'right'], ['created', 'Signed up', 'created_at'],
];

AdminPanels.register('customers', {
  label: 'Customers', icon: 'users', group: 'customers', params: [...FILTER_KEYS, 'add'],
  sub: 'Everyone with an account, their access, and how they are getting on',
  actions: `<button type="button" class="btn btn-secondary" data-act="users-views" aria-haspopup="menu"></button>
    <button type="button" class="btn btn-secondary" data-act="users-export"></button>
    <button type="button" class="btn btn-primary" data-act="add-user"></button>`,
  markup: `
    <div id="cust-list">
      <div id="users-notice"></div>
      <div class="tabs adm-view-tabs" id="u-views" role="tablist" aria-label="Views"></div>
      <div class="tbl-toolbar adm-users-toolbar" id="users-toolbar">
        <div class="input-group adm-search"><span class="ico-wrap"></span>
          <input id="u-search" class="input input-sm" type="search" placeholder="Search by email, name or #id…" aria-label="Search customers"></div>
        <div class="adm-chips" id="u-chips"></div>
      </div>
      <div class="tbl-summary" id="users-summary"></div>
      <div id="users-table"></div>
      <div class="adm-pager" id="users-pager"></div>
    </div>
    <div id="cust-person" hidden></div>`,
  load: loadUsersSection,
});

function uq() {
  const q = qs();
  return Object.fromEntries(FILTER_KEYS.filter((k) => q[k]).map((k) => [k, q[k]]));
}
function setUq(patch, { keepPage = false } = {}) {
  setQs({ ...patch, ...(keepPage ? {} : { page: undefined }) }, { merge: true });
  AU.selected.clear();
  AU.allMatching = false;
  loadUsers();
}
function filtered() {
  const f = uq();
  return Object.keys(f).some((k) => !['sort', 'dir', 'page'].includes(k));
}

async function loadUsersSection(host, ctx) {
  wireUsers();
  const id = Number(ctx.route.id);
  $('#cust-list').hidden = !!id;
  $('#cust-person').hidden = !id;
  paintBulkBar();
  if (id) return showCustomer(id, ctx.route.tab);
  closeCustomer();
  if (qs().add) { setQs({ add: undefined }, { merge: true, replace: true }); openAddUser(); }
  return loadUsers();
}

function customerHref(id) { return `#customers/${id}`; }

function wireUsers() {
  if (AU.wired) return;
  AU.wired = true;
  $('[data-act="add-user"]').innerHTML = `${icon('plus')}<span class="label">Add customer</span>`;
  $('[data-act="users-export"]').innerHTML = `${icon('download')}<span class="label">Export</span>`;
  $('[data-act="users-views"]').innerHTML = `${icon('star')}<span class="label">Views</span>${icon('chevron-down', 'ico-sm')}`;
  $('#users-toolbar .ico-wrap').innerHTML = icon('search');
  const search = $('#u-search');
  search.value = uq().q || '';
  search.addEventListener('input', debounce(() => setUq({ q: search.value.trim() }), 250));
  $('#users-table').addEventListener('click', (e) => {
    if (e.target.closest('[data-act], input, label')) return;
    const tr = e.target.closest('tr[data-id]');
    if (tr && !e.target.closest('a')) location.hash = customerHref(tr.dataset.id);
  });
  $('#users-table').addEventListener('change', onSelectChange);
  document.body.addEventListener('click', onUsersAction);
  api('/api/admin/users/facets').then((f) => { AU.facets = f; paintChips(); }).catch(() => {});
}

async function loadUsers() {
  const seq = ++AU.seq;
  const host = $('#users-table');
  if (!AU.items.length) host.innerHTML = `<div class="tbl-wrap"><table class="tbl tbl--cards tbl--list"><tbody>${ui.skeletonRows(6, 8)}</tbody></table></div>`;
  else host.classList.add('is-refreshing');
  paintChips();
  let data;
  try {
    data = await api(`/api/admin/users${toQuery({ ...uq(), per_page: PER_PAGE })}`);
  } catch (err) {
    host.classList.remove('is-refreshing');
    host.innerHTML = ui.errorBox(err.message, { retry: 'reload-users' });
    $('#users-summary').innerHTML = '';
    $('#users-pager').innerHTML = '';
    return;
  }
  if (seq !== AU.seq) return;
  host.classList.remove('is-refreshing');
  paintViewTabs();
  Object.assign(AU, { items: data.items, total: data.total, page: data.page, retention: data.retention_days,
    purgeDue: data.purge_due_count });
  renderUsersNotice();
  renderUsersSummary();
  renderUsersTable();
  renderPager();
  paintBulkBar();
}

/* ---------- filter chips ---------- */

function chip(act, label, on, icn = 'filter') {
  return `<button type="button" class="btn btn-secondary btn-sm ${on ? 'is-on' : ''}" data-act="${act}" aria-haspopup="menu">
    ${icon(icn)}<span class="label">${esc(label)}</span>${icon('chevron-down', 'ico-sm')}</button>`;
}

function paintChips() {
  const f = uq();
  const states = (f.state || '').split(',').filter(Boolean);
  const seen = SEEN_FILTERS.find(([, , p]) => (p.seen_within && String(p.seen_within) === f.seen_within)
    || (p.inactive_for && String(p.inactive_for) === f.inactive_for)) || SEEN_FILTERS[0];
  const setup = SETUP_FILTERS.filter(([k, v]) => f[k] === v).map(([, , l]) => l);
  const tags = (f.tag || '').split(',').filter(Boolean);
  const tagNames = tags.map((id) => ((AU.facets && AU.facets.tags) || []).find((t) => String(t.id) === id)).filter(Boolean).map((t) => t.name);
  const signup = f.signup_from || f.signup_to;
  // Four filters people reach for every day stay in view; the rest sit behind "More filters", which
  // says how many of them are on.
  const moreOn = [!!f.plan, !!setup.length, !!f.source, !!signup].filter(Boolean).length;
  $('#u-chips').innerHTML = [
    chip('f-state', states.length ? states.map((s) => ACCESS_LABEL[s] || s).join(', ') + (f.trial_ending ? ' · ending soon' : '')
      : (f.trial_ending ? 'Trial ending soon' : 'Any access'), !!(states.length || f.trial_ending), 'credit-card'),
    chip('f-seen', seen[0] ? seen[1] : 'Any activity', !!seen[0], 'clock'),
    chip('f-tag', tagNames.length ? tagNames.join(', ') : 'Any tag', !!tags.length, 'tag'),
    chip('f-status', (STATUS_FILTERS.find(([v]) => v === (f.status || '')) || STATUS_FILTERS[0])[1], !!f.status, 'users'),
    chip('f-more', moreOn ? `More filters · ${moreOn}` : 'More filters', !!moreOn, 'filter'),
    filtered() ? '<button type="button" class="btn btn-ghost btn-sm" data-act="clear-filters">Clear</button>' : '',
  ].join('');
  AU.moreLabels = {
    plan: (PLAN_FILTERS.find(([v]) => v === (f.plan || '')) || PLAN_FILTERS[0])[1],
    setup: setup.length ? setup.join(', ') : 'Getting started: anyone',
    source: f.source ? `From ${f.source}` : 'Any source',
    signup: signup ? `Signed up ${f.signup_from ? fmtDate(f.signup_from, { year: true }) : '…'} – ${f.signup_to ? fmtDate(f.signup_to, { year: true }) : '…'}` : 'Any sign-up date',
  };
}

/* The view tabs: a built-in or saved view is "on" when the list's filters are exactly its query. */
function currentViewQuery() {
  return toQuery(Object.fromEntries(Object.entries(uq()).filter(([k]) => k !== 'page')));
}
function paintViewTabs() {
  const cur = currentViewQuery();
  const views = [...VIEW_TABS, ...adminViews().map((v) => [v.name, v.query])];
  $('#u-views').innerHTML = views.map(([label, query]) => {
    const on = (query || '') === cur;
    return `<button type="button" role="tab" class="tab ${on ? 'active' : ''}" aria-selected="${on}" data-act="view-tab" data-query="${esc(query)}">${esc(label)}</button>`;
  }).join('');
}

function menuOf(anchor, rows, current, apply) {
  ui.menu(anchor, rows.map(([v, l]) => ({ label: l, checked: (current || '') === v, onClick: () => apply(v) })));
}

function openStateFilter(anchor) {
  const f = uq();
  const states = new Set((f.state || '').split(',').filter(Boolean));
  const toggle = (v) => { if (states.has(v)) states.delete(v); else states.add(v); setUq({ state: Array.from(states).join(',') }); };
  ui.menu(anchor, [
    { label: 'Any access', checked: !states.size && !f.trial_ending, onClick: () => setUq({ state: undefined, trial_ending: undefined }) },
    { divider: true },
    ...Object.entries(ACCESS_LABEL).map(([v, l]) => ({ label: l, checked: states.has(v), onClick: () => toggle(v) })),
    { divider: true },
    { label: 'Trial ends within 7 days', checked: !!f.trial_ending, onClick: () => setUq({ trial_ending: f.trial_ending ? undefined : 7 }) },
  ]);
}

function openSetupFilter(anchor) {
  const f = uq();
  ui.menu(anchor, [{ label: 'Anyone', checked: !f.confirmed && !f.activated, onClick: () => setUq({ confirmed: undefined, activated: undefined }) },
    { divider: true },
    ...SETUP_FILTERS.map(([k, v, l]) => ({ label: l, checked: f[k] === v, onClick: () => setUq({ [k]: f[k] === v ? undefined : v }) }))]);
}

function openSourceFilter(anchor) {
  const f = uq();
  const sources = (AU.facets && AU.facets.sources) || [];
  ui.menu(anchor, [{ label: 'Any source', checked: !f.source, onClick: () => setUq({ source: undefined }) }, { divider: true },
    ...sources.map((x) => ({ label: `${x.channel} (${fmtNumber(x.n)})`, checked: f.source === x.channel, onClick: () => setUq({ source: x.channel }) }))]);
}

function openSignupFilter(anchor) {
  const f = uq();
  const el = document.createElement('div');
  el.className = 'menu adm-range-menu';
  el.innerHTML = `<div class="adm-range-custom"><div class="menu-label">Signed up between</div>
    <div class="row gap-2"><label class="sr-only" for="su-from">From</label><input id="su-from" class="input input-sm" type="date" value="${esc(f.signup_from || '')}">
      <label class="sr-only" for="su-to">To</label><input id="su-to" class="input input-sm" type="date" value="${esc(f.signup_to || '')}"></div>
    <div class="row gap-2 mt-2"><button type="button" class="btn btn-ghost btn-sm grow" data-su="clear">Any date</button>
      <button type="button" class="btn btn-primary btn-sm grow" data-su="apply">Apply</button></div></div>`;
  const pop = ui.popover(anchor, el, { placement: 'bottom-start' });
  el.addEventListener('click', (e) => {
    const b = e.target.closest('[data-su]');
    if (!b) return;
    pop.close();
    if (b.dataset.su === 'clear') setUq({ signup_from: undefined, signup_to: undefined });
    else setUq({ signup_from: $('#su-from', el).value || undefined, signup_to: $('#su-to', el).value || undefined });
  });
}

/* ---------- table ---------- */

function renderUsersNotice() {
  const n = AU.purgeDue;
  $('#users-notice').innerHTML = n
    ? `<div class="notice notice-warning mb-4">${icon('trash')}<div class="grow">
         ${esc(people(n))} ${n === 1 ? 'has' : 'have'} been in the trash longer than ${fmtNumber(AU.retention)} days.</div>
       <button type="button" class="btn btn-secondary btn-sm" data-act="show-trash">Review trash</button></div>`
    : '';
}

function renderUsersSummary() {
  const from = (AU.page - 1) * PER_PAGE + 1;
  const to = from + AU.items.length - 1;
  $('#users-summary').innerHTML = AU.total
    ? `<span><b>${fmtNumber(AU.total)}</b> ${AU.total === 1 ? 'person' : 'people'}${filtered() ? ' match' : ''}</span>
       ${AU.total > AU.items.length ? `<span class="text-3">showing ${fmtNumber(from)}–${fmtNumber(to)}</span>` : ''}`
    : '';
}

function stateBadge(u) {
  const key = u.comped_until ? 'comped' : u.state;
  if (!key) return '<span class="text-4">—</span>';
  const sub = key === 'trialing' && u.trial_end ? ` <span class="sub">ends ${esc(fmtRelative(u.trial_end))}</span>` : '';
  return `${accessBadge(key)}${u.plan && key === 'active' ? ` <span class="sub">${esc(u.plan)}</span>` : ''}${sub}`;
}

function renderUsersTable() {
  const host = $('#users-table');
  if (!AU.items.length) {
    host.innerHTML = filtered()
      ? ui.emptyState({ icon: 'search', title: 'Nobody matches', body: 'Try a different search or clear the filters.', action: { label: 'Clear filters', act: 'clear-filters' } })
      : ui.emptyState({ icon: 'users', title: 'No customers yet', body: 'Open sign-ups in Settings › Sign-ups, or add an account for someone.', action: { label: 'Add a customer', act: 'add-user' } });
    return;
  }
  const f = uq();
  const sort = f.sort || 'id';
  const dir = f.dir || 'asc';
  const allOn = AU.items.every((u) => AU.selected.has(u.id));
  const head = USER_COLS.map(([key, label, sortKey, align]) => (sortKey
    ? `<th class="sortable ${align || ''}" data-act="sort" data-sort="${sortKey}" aria-sort="${sort === sortKey ? (dir === 'asc' ? 'ascending' : 'descending') : 'none'}">${esc(label)}</th>`
    : `<th class="${align || ''}">${esc(label)}</th>`)).join('');
  host.innerHTML = `<div class="tbl-wrap"><table class="tbl tbl--cards tbl--list adm-users"><thead><tr>
    ${head.replace('>Person<', `><input type="checkbox" class="check adm-check" data-select="page" aria-label="Select everyone on this page" ${allOn ? 'checked' : ''}>Person<`)}
    <th class="col-actions"><span class="sr-only">Actions</span></th></tr></thead><tbody>
    ${AU.items.map((u) => `<tr data-id="${u.id}" class="is-clickable ${u.status === 'deleted' ? 'is-trashed' : ''} ${AU.selected.has(u.id) ? 'is-selected' : ''}">
      <td><span class="row gap-2 min-w-0"><input type="checkbox" class="check adm-check hide-mobile" data-select="${u.id}" aria-label="Select ${esc(u.email || u.username)}" ${AU.selected.has(u.id) ? 'checked' : ''}><span class="avatar" aria-hidden="true">${esc(initials(u.email || u.username))}</span>
        <span class="min-w-0"><a class="row-link fw-500 truncate" href="${customerHref(u.id)}">${esc(u.email || u.username)}</a>
          <span class="sub adm-user-sub">${u.status !== 'active' ? `<span class="user-status"><i class="dot" style="--c:var(--${STATUS_COLOR[u.status]})"></i>${esc(STATUS_LABEL[u.status])}</span>` : ''}
            ${u.email && !u.email_confirmed ? '<span class="text-4">unconfirmed</span>' : ''}
            ${u.email && u.username !== u.email ? `<span class="text-4">${esc(u.username)}</span>` : ''}
            ${(u.tags || []).map((t) => `<span class="adm-tag" style="--c:var(--${esc(t.color)})">${esc(t.name)}</span>`).join('')}</span></span></span></td>
      <td class="${u.state || u.comped_until ? '' : 'hide-mobile'}" data-label="Access">${stateBadge(u)}</td>
      <td class="right num ${u.mrr_cents ? '' : 'hide-mobile'}" data-label="MRR">${u.mrr_cents ? esc(fmtMoney(u.mrr_cents / 100, (u.currency || 'usd').toUpperCase())) : '<span class="text-4">—</span>'}</td>
      <td class="text-3" data-label="Last seen">${u.last_seen_at ? `<span data-tip="${esc(fmtDateTime(u.last_seen_at))}">${esc(fmtRelative(u.last_seen_at))}</span>` : 'Never'}</td>
      <td class="hide-mobile" data-label="Activated">${u.activated ? `<span class="text-success" aria-label="Yes">${icon('check', 'ico-sm')}</span>` : '<span class="text-4" aria-label="No">—</span>'}</td>
      <td class="text-3 hide-mobile" data-label="Source">${esc(u.source || '—')}</td>
      <td class="right num hide-mobile" data-label="Transactions">${fmtNumber(u.txn_count)}</td>
      <td class="text-3" data-label="Signed up">${fmtDate(u.created_at, { year: true })}</td>
      <td class="col-actions"><div class="row-actions">
        <button type="button" class="btn btn-icon btn-ghost btn-xs" data-act="user-menu" data-id="${u.id}" aria-label="Actions for ${esc(u.email || u.username)}">${icon('more-horizontal')}</button>
      </div></td></tr>`).join('')}</tbody></table></div>`;
}

function renderPager() {
  const pages = Math.max(1, Math.ceil(AU.total / PER_PAGE));
  if (pages <= 1) { $('#users-pager').innerHTML = ''; return; }
  $('#users-pager').innerHTML = `
    <button type="button" class="btn btn-secondary btn-sm" data-act="page" data-page="${AU.page - 1}" ${AU.page <= 1 ? 'disabled' : ''}>${icon('chevron-left')}<span class="label">Previous</span></button>
    <span class="text-3">Page ${fmtNumber(AU.page)} of ${fmtNumber(pages)}</span>
    <button type="button" class="btn btn-secondary btn-sm" data-act="page" data-page="${AU.page + 1}" ${AU.page >= pages ? 'disabled' : ''}><span class="label">Next</span>${icon('chevron-right')}</button>`;
}

/* ---------- selection and bulk ---------- */

function onSelectChange(e) {
  const box = e.target.closest('[data-select]');
  if (!box) return;
  if (box.dataset.select === 'page') {
    AU.items.forEach((u) => { if (box.checked) AU.selected.add(u.id); else AU.selected.delete(u.id); });
  } else {
    const id = Number(box.dataset.select);
    if (box.checked) AU.selected.add(id); else AU.selected.delete(id);
  }
  AU.allMatching = false;
  $$('#users-table tr[data-id]').forEach((tr) => {
    const on = AU.selected.has(Number(tr.dataset.id));
    tr.classList.toggle('is-selected', on);
    const b = tr.querySelector('[data-select]'); if (b) b.checked = on;
  });
  const all = $('#users-table [data-select="page"]');
  if (all) all.checked = AU.items.length > 0 && AU.items.every((u) => AU.selected.has(u.id));
  paintBulkBar();
}

function selectionCount() { return AU.allMatching ? AU.total : AU.selected.size; }

function paintBulkBar() {
  let bar = document.getElementById('users-bulk');
  const n = selectionCount();
  if (!n || AdminPanels.current() !== 'customers' || P360.open) { if (bar) bar.remove(); return; }
  if (!bar) {
    bar = document.createElement('div');
    bar.id = 'users-bulk';
    bar.className = 'floatbar';
    bar.setAttribute('role', 'status');
    document.body.appendChild(bar);
  }
  const pageFull = AU.items.length && AU.items.every((u) => AU.selected.has(u.id));
  bar.innerHTML = `<span><b>${fmtNumber(n)}</b> selected</span>
    ${pageFull && !AU.allMatching && AU.total > AU.items.length ? `<button type="button" class="btn btn-ghost btn-sm" data-act="bulk-all">Select all ${fmtNumber(AU.total)}</button>` : ''}
    <button type="button" class="btn btn-ghost btn-sm" data-act="bulk-email">${icon('mail', 'ico-sm')}Email</button>
    <button type="button" class="btn btn-ghost btn-sm" data-act="bulk-tag">${icon('tag', 'ico-sm')}Tag</button>
    <button type="button" class="btn btn-ghost btn-sm" data-act="bulk-trial">${icon('clock', 'ico-sm')}Extend trial</button>
    <button type="button" class="btn btn-ghost btn-sm" data-act="bulk-lock">${icon('lock', 'ico-sm')}Lock</button>
    <button type="button" class="btn btn-ghost btn-sm" data-act="bulk-unlock">${icon('unlock', 'ico-sm')}Unlock</button>
    <button type="button" class="btn btn-icon btn-ghost btn-sm" data-act="bulk-clear" aria-label="Clear selection">${icon('x')}</button>`;
}
window.addEventListener('ispend:admin-tab', paintBulkBar);

async function runBulk(action, params = {}) {
  const f = uq();
  delete f.page;
  const body = AU.allMatching ? { action, query: toQuery(f), params } : { action, ids: Array.from(AU.selected), params };
  const r = await api('/api/admin/users/bulk', { method: 'POST', body });
  toast(`${people(r.affected)} updated${r.skipped.length ? ` · ${r.skipped.length} skipped` : ''}`,
    { type: r.affected ? 'success' : 'info' });
  AU.selected.clear();
  AU.allMatching = false;
  loadUsers();
}

function bulkLabel() { return people(selectionCount()); }

async function bulkLock() {
  const m = ui.modal({
    title: `Lock ${bulkLabel()}?`,
    html: `<p class="mb-4">They are signed out and cannot sign in until unlocked. Nothing is deleted.</p>
      <div class="field"><label for="bl-reason">Reason (optional)</label><input id="bl-reason" class="input" maxlength="200"></div>`,
    actions: [{ label: 'Cancel' }, { label: 'Lock', danger: true, onClick: () => runBulk('lock', { reason: $('#bl-reason', m.el).value.trim() }) }],
  });
}

async function bulkTrial() {
  const m = ui.modal({
    title: `Extend the trial for ${bulkLabel()}`,
    html: `<div class="field"><label for="bt-days">Extra days</label><input id="bt-days" class="input num-input" type="number" min="1" max="365" value="7"></div>
      <div class="hint">Counted from today, or from the current trial end if that is later.</div>`,
    actions: [{ label: 'Cancel' }, { label: 'Extend', primary: true, onClick: () => runBulk('extend_trial', { days: Number($('#bt-days', m.el).value) }) }],
  });
}

async function bulkTag(anchor) {
  const tags = await adminTags();
  ui.menu(anchor, [
    ...tags.map((t) => ({ label: `Add “${t.name}”`, onClick: () => runBulk('tag', { tag_ids: [t.id] }) })),
    ...(tags.length ? [{ divider: true }] : []),
    ...tags.map((t) => ({ label: `Remove “${t.name}”`, onClick: () => runBulk('untag', { tag_ids: [t.id] }) })),
    ...(tags.length ? [{ divider: true }] : []),
    { label: 'New tag…', icon: 'plus', onClick: () => newTag().then((t) => t && runBulk('tag', { tag_ids: [t.id] })) },
  ]);
}

/* ---------- tags, shared with the person view ---------- */

async function adminTags(force = false) {
  if (!AU.tags || force) AU.tags = await api('/api/admin/tags');
  return AU.tags;
}

function newTag() {
  return new Promise((resolve) => {
    let made = null;
    const m = ui.modal({
      title: 'New tag',
      html: `<div class="field"><label for="nt-name">Name</label><input id="nt-name" class="input" maxlength="40" autofocus>
        <div class="hint">Only administrators see tags.</div></div>`,
      actions: [{ label: 'Cancel' }, { label: 'Create', primary: true, onClick: async () => {
        made = await api('/api/admin/tags', { method: 'POST', body: { name: $('#nt-name', m.el).value.trim() } });
        await adminTags(true);
        AU.facets = null;
        api('/api/admin/users/facets').then((f) => { AU.facets = f; paintChips(); }).catch(() => {});
      } }],
      onClose: () => resolve(made),
    });
  });
}

/* Rename or delete the labels admins put on customers. */
async function manageTags() {
  const tags = await adminTags(true);
  const row = (t) => `<li class="row gap-2" data-tag="${t.id}"><span class="adm-tag" style="--c:var(--${esc(t.color)})">${esc(t.name)}</span>
    <span class="grow"></span>
    <button type="button" class="btn btn-ghost btn-xs" data-tag-act="rename">Rename</button>
    <button type="button" class="btn btn-ghost btn-xs text-danger" data-tag-act="delete">Delete</button></li>`;
  const m = ui.modal({
    title: 'Tags',
    html: tags.length ? `<ul class="adm-tag-list">${tags.map(row).join('')}</ul>` : '<p class="hint">No tags yet.</p>',
    actions: [{ label: 'Done', primary: true }],
  });
  const refresh = async () => {
    await adminTags(true);
    AU.facets = await api('/api/admin/users/facets').catch(() => AU.facets);
    loadUsers();
  };
  m.el.addEventListener('click', async (e) => {
    const b = e.target.closest('[data-tag-act]');
    if (!b) return;
    const li = b.closest('[data-tag]');
    const tag = tags.find((t) => String(t.id) === li.dataset.tag);
    if (b.dataset.tagAct === 'delete') {
      if (!(await ui.confirm({ title: `Delete the tag “${tag.name}”?`, body: 'It comes off every customer who has it. The customers are not affected.', confirmText: 'Delete tag', danger: true }))) return;
      await api(`/api/admin/tags/${tag.id}`, { method: 'DELETE' });
      li.remove();
      refresh();
      return;
    }
    const r = ui.modal({
      title: 'Rename tag',
      width: 420,
      html: `<div class="field"><label for="rt-name">Name</label><input id="rt-name" class="input" maxlength="40" value="${esc(tag.name)}" autofocus></div>`,
      actions: [{ label: 'Cancel' }, { label: 'Rename', primary: true, onClick: async () => {
        const name = $('#rt-name', r.el).value.trim();
        if (!name) { ui.fieldError($('#rt-name', r.el), 'Give it a name'); return false; }
        if (name === tag.name) return undefined;
        await api(`/api/admin/tags/${tag.id}`, { method: 'PUT', body: { name } });
        tag.name = name;
        li.outerHTML = row(tag);
        refresh();
        return undefined;
      } }],
    });
  });
}

/* ---------- saved views ---------- */

function adminViews() { return (((window.currentUser || {}).preferences || {}).admin_views) || []; }

async function saveAdminViews(views) {
  const prefs = await api('/api/auth/me/preferences', { method: 'PUT', body: { admin_views: views } });
  window.currentUser.preferences = prefs;
}

function openViews(anchor) {
  const views = adminViews();
  const cur = toQuery(Object.fromEntries(Object.entries(uq()).filter(([k]) => k !== 'page')));
  ui.menu(anchor, [
    { label: 'Saved views', header: true },
    ...(views.length ? views.map((v) => ({ label: v.name, icon: 'star', checked: v.query === cur, onClick: () => applyView(v.query) }))
      : [{ label: 'None yet — filter the list, then save it here', disabled: true }]),
    { divider: true },
    { label: 'Save current view…', icon: 'plus', disabled: !filtered(), onClick: saveViewDialog },
    ...(views.length ? [{ label: 'Delete a view…', icon: 'trash', onClick: deleteViewMenu }] : []),
    { divider: true },
    { label: 'Suggested', header: true },
    { label: 'Trials ending this week', onClick: () => applyView('?state=trialing&trial_ending=7&sort=created_at') },
    { label: 'Payment due', onClick: () => applyView('?state=grace') },
    { label: 'Signed up, never imported', onClick: () => applyView('?activated=0&sort=created_at&dir=desc') },
    { label: 'Paying but quiet for 30 days', onClick: () => applyView('?state=active&inactive_for=30') },
    { label: 'Email not confirmed', onClick: () => applyView('?confirmed=0&sort=created_at&dir=desc') },
  ]);
}

function applyView(query) {
  const next = Object.fromEntries(new URLSearchParams(query.replace(/^\?/, '')));
  setQs(Object.fromEntries(FILTER_KEYS.map((k) => [k, next[k]])), { merge: true });
  $('#u-search').value = next.q || '';
  AU.selected.clear();
  loadUsers();
}

function saveViewDialog() {
  const m = ui.modal({
    title: 'Save this view',
    html: '<div class="field"><label for="sv-name">Name</label><input id="sv-name" class="input" maxlength="60" autofocus></div>',
    actions: [{ label: 'Cancel' }, { label: 'Save', primary: true, onClick: async () => {
      const name = $('#sv-name', m.el).value.trim();
      if (!name) { ui.fieldError($('#sv-name', m.el), 'Give it a name'); return false; }
      const query = toQuery(Object.fromEntries(Object.entries(uq()).filter(([k]) => k !== 'page')));
      await saveAdminViews([...adminViews(), { id: uid().slice(0, 12).toLowerCase().replace(/[^a-z0-9_-]/g, ''), name, query }]);
      toast('View saved', { type: 'success' });
      return undefined;
    } }],
  });
}

function deleteViewMenu() {
  ui.menu($('[data-act="users-views"]'), adminViews().map((v) => ({
    label: `Delete “${v.name}”`, icon: 'trash', danger: true,
    onClick: async () => { await saveAdminViews(adminViews().filter((x) => x.id !== v.id)); toast('View deleted'); },
  })));
}

/* ---------- row menu and dialogs ---------- */

function userRowMenu(anchor, u) { ui.menu(anchor, userMenuItems(u)); }

function userMenuItems(u) {
  const items = [
    { label: 'Open', icon: 'eye', onClick: () => { location.hash = customerHref(u.id); } },
    { label: 'Email a password reset link', icon: 'mail', disabled: !u.email || u.status !== 'active', onClick: () => sendResetLink(u) },
    { label: 'Sign out everywhere', icon: 'log-out', onClick: () => signOutEverywhere(u) },
    { label: 'Set a password…', icon: 'key', onClick: () => openSetPassword(u) },
    { divider: true },
  ];
  if (u.status !== 'deleted') {
    items.push(u.status === 'locked'
      ? { label: 'Unlock', icon: 'unlock', onClick: () => unlockUser(u) }
      : { label: 'Lock', icon: 'lock', onClick: () => openLock(u) });
    items.push({ label: 'Move to trash', icon: 'trash', danger: true, onClick: () => trashUser(u) });
  } else {
    items.push({ label: 'Restore', icon: 'rotate-ccw', onClick: () => restoreUser(u) });
    items.push({ label: 'Delete permanently', icon: 'trash', danger: true, onClick: () => openPurge(u) });
  }
  return items;
}

function whoOf(u) { return u.email || u.username; }

/* The list and an open person view both listen, so either one can make the change. */
function afterChange() {
  window.dispatchEvent(new CustomEvent('ispend:admin-user-changed'));
}

async function sendResetLink(u) {
  await api(`/api/admin/users/${u.id}/send-password-reset`, { method: 'POST' });
  toast(`Reset link emailed to ${whoOf(u)}`, { type: 'success' });
}

async function signOutEverywhere(u) {
  const ok = await ui.confirm({ title: `Sign ${whoOf(u)} out everywhere?`,
    body: 'Every browser and phone they are signed in on has to sign in again. Their password does not change.', confirmText: 'Sign out everywhere' });
  if (!ok) return;
  await api(`/api/admin/users/${u.id}/sign-out-all`, { method: 'POST' });
  toast('Signed out everywhere', { type: 'success' });
}

function openSetPassword(u) {
  const m = ui.modal({
    title: `Set a password for ${whoOf(u)}`,
    html: `<div class="field"><label for="rp">New password</label>
      <input id="rp" class="input" type="password" minlength="10" autocomplete="new-password" autofocus>
      <div class="hint">At least 10 characters. They are signed out everywhere and you have to tell them the password yourself — ${u.email ? 'emailing a reset link is usually better' : 'this account has no email address for a reset link'}.</div></div>`,
    actions: [{ label: 'Cancel' }, { label: 'Set password', primary: true, onClick: async () => {
      await api(`/api/admin/users/${u.id}/password`, { method: 'PUT', body: { password: $('#rp', m.el).value } });
      toast('Password set', { type: 'success' });
    } }],
  });
}

function openLock(u) {
  const m = ui.modal({
    title: `Lock ${whoOf(u)}?`,
    html: `<p class="mb-4">They are signed out immediately and cannot sign in until you unlock them. All of their data is kept.</p>
      <div class="field"><label for="lk">Reason (optional)</label><input id="lk" class="input" maxlength="200" autofocus>
      <div class="hint">Shown to administrators, never to them.</div></div>`,
    actions: [{ label: 'Cancel' }, { label: 'Lock account', danger: true, onClick: async () => {
      await api(`/api/admin/users/${u.id}/lock`, { method: 'POST', body: { reason: $('#lk', m.el).value.trim() } });
      afterChange();
      ui.undoable(`${whoOf(u)} locked`, async () => {
        await api(`/api/admin/users/${u.id}/unlock`, { method: 'POST' });
        afterChange();
      });
    } }],
  });
}

async function unlockUser(u) {
  await api(`/api/admin/users/${u.id}/unlock`, { method: 'POST' });
  afterChange();
  ui.undoable(`${whoOf(u)} unlocked`, async () => {
    await api(`/api/admin/users/${u.id}/lock`, { method: 'POST', body: {} });
    afterChange();
  });
}

async function trashUser(u) {
  const ok = await ui.confirm({
    title: `Move ${whoOf(u)} to the trash?`,
    body: `They are signed out immediately and cannot sign in. All ${plural(u.txn_count || 0, 'transaction')} and their uploaded files are kept, and you can restore them.`,
    confirmText: 'Move to trash', danger: true,
  });
  if (!ok) return;
  await api(`/api/admin/users/${u.id}`, { method: 'DELETE' });
  afterChange();
  ui.undoable(`${whoOf(u)} moved to the trash`, async () => {
    await api(`/api/admin/users/${u.id}/restore`, { method: 'POST' });
    afterChange();
  });
}

async function restoreUser(u) {
  const r = await api(`/api/admin/users/${u.id}/restore`, { method: 'POST' });
  toast(r.status === 'locked' ? `${whoOf(u)} restored — the account is still locked` : `${whoOf(u)} restored`, { type: 'success' });
  afterChange();
}

function openPurge(u) {
  const name = u.username;
  const m = ui.modal({
    title: `Delete ${whoOf(u)} permanently?`,
    html: `<p>This deletes ${esc(plural(u.txn_count || 0, 'transaction'))}, ${esc(plural(u.statement_count || 0, 'statement'))}
        and ${esc(fmtBytes(u.storage_bytes || 0))} of uploaded files. <b>It cannot be undone.</b></p>
      <div class="field mt-4"><label for="pg">Type <b>${esc(name)}</b> to confirm</label>
        <input id="pg" class="input" autocomplete="off" spellcheck="false" autofocus></div>`,
    actions: [{ label: 'Cancel' }, { label: 'Delete permanently', danger: true, onClick: async () => {
      const typed = $('#pg', m.el).value.trim();
      if (typed !== name) { ui.fieldError($('#pg', m.el), 'That does not match'); return false; }
      // The server re-checks the phrase; this one is only here to slow the hand down.
      await api(`/api/admin/users/${u.id}?permanent=true&confirm=${encodeURIComponent(name)}`, { method: 'DELETE' });
      toast(`${whoOf(u)} deleted permanently`);
      ui.closeTop();
      afterChange();
      return undefined;
    } }],
  });
  const input = $('#pg', m.el);
  const btn = m.el.querySelector('.modal-foot .btn-danger-solid');
  btn.disabled = true;
  input.addEventListener('input', () => { btn.disabled = input.value.trim() !== name; });
}

function openAddUser() {
  const m = ui.modal({
    title: 'Add a customer',
    html: `<form id="user-form" novalidate>
      <div class="field"><label for="u-email">Email</label><input id="u-email" class="input" type="email" autocomplete="off" spellcheck="false" autofocus></div>
      <label class="check-row mb-4"><input type="checkbox" id="u-invite" class="check" checked>
        <span>Email them an invitation to choose their own password</span></label>
      <div class="field" id="u-pass-field" hidden><label for="u-pass">Password</label><input id="u-pass" class="input" type="password" minlength="10" autocomplete="new-password">
        <div class="hint">At least 10 characters. You will have to tell them.</div></div>
      <div class="field"><label for="u-name">Username <span class="text-4">(optional)</span></label><input id="u-name" class="input" autocomplete="off" spellcheck="false">
        <div class="hint">Defaults to the email address.</div></div>
      <div class="field"><label for="u-access">Access</label><select id="u-access" class="select">
        <option value="trial">Free trial, then subscribe</option><option value="comped">Complimentary — never billed</option></select></div>
      <button type="submit" hidden></button></form>`,
    actions: [{ label: 'Cancel' }, { label: 'Create', primary: true, onClick: async () => {
      const email = $('#u-email', m.el).value.trim();
      const invite = $('#u-invite', m.el).checked;
      const username = $('#u-name', m.el).value.trim() || email;
      if (invite && !email) { ui.fieldError($('#u-email', m.el), 'An invitation needs an email address'); return false; }
      if (!username) { ui.fieldError($('#u-email', m.el), 'Enter an email address or a username'); return false; }
      try {
        await api('/api/admin/users', { method: 'POST', body: { email: email || undefined, username, send_invite: invite,
          password: invite ? undefined : $('#u-pass', m.el).value, access: $('#u-access', m.el).value } });
      } catch (err) {
        // A deleted user keeps their username reserved, so offer the action that actually helps.
        if (err.data && err.data.code === 'username_deleted') { m.close(); return offerRestore(err.data, username); }
        throw err;
      }
      toast(invite ? `Invitation sent to ${email}` : 'Customer added', { type: 'success' });
      afterChange();
      return undefined;
    } }],
  });
  const sync = () => { $('#u-pass-field', m.el).hidden = $('#u-invite', m.el).checked; };
  $('#u-invite', m.el).addEventListener('change', sync);
  $('#user-form', m.el).addEventListener('submit', (e) => { e.preventDefault(); m.el.querySelector('.modal-foot .btn-primary').click(); });
}

function offerRestore(data, username) {
  ui.modal({
    title: `${username} is in the trash`,
    html: `<p>${esc(data.error)} Restoring keeps everything they had — accounts, transactions, categories and rules.</p>`,
    actions: [{ label: 'Cancel' }, { label: `Restore ${username}`, primary: true, onClick: async () => {
      const r = await api(`/api/admin/users/${data.user_id}/restore`, { method: 'POST' });
      toast(r.status === 'locked' ? `${username} restored — the account is still locked` : `${username} restored`, { type: 'success' });
      setUq({ status: undefined });
    } }],
  });
  return undefined;
}

/* ---------- actions ---------- */

async function onUsersAction(e) {
  if (e.target.closest('[data-select]')) return undefined;   // the select-all box sits in a sortable header
  const el = e.target.closest('[data-act]');
  if (!el || AdminPanels.current() !== 'customers' || P360.open) return undefined;
  const f = uq();
  const u = AU.items.find((x) => x.id === Number(el.dataset.id));
  switch (el.dataset.act) {
    case 'reload-users': return loadUsers();
    case 'add-user': return openAddUser();
    case 'users-export': return adminDownload(`/api/admin/users${toQuery({ ...f, page: undefined, format: 'csv' })}`).catch((err) => { if (!err.cancelled) toast(err.message, { type: 'error' }); });
    case 'users-views': return openViews(el);
    case 'view-tab': return applyView(el.dataset.query || '');
    case 'f-more': return ui.menu(el, [
      { label: AU.moreLabels.plan, icon: 'repeat', onClick: () => menuOf(el, PLAN_FILTERS, f.plan, (v) => setUq({ plan: v || undefined })) },
      { label: AU.moreLabels.setup, icon: 'check-circle', onClick: () => openSetupFilter(el) },
      { label: AU.moreLabels.source, icon: 'globe', onClick: () => openSourceFilter(el) },
      { label: AU.moreLabels.signup, icon: 'calendar', onClick: () => openSignupFilter(el) },
    ]);
    case 'user-menu': return u ? userRowMenu(el, u) : undefined;
    case 'sort': {
      const key = el.dataset.sort;
      return setUq({ sort: key, dir: f.sort === key && f.dir !== 'desc' ? 'desc' : 'asc' }, { keepPage: false });
    }
    case 'page': return setUq({ page: Number(el.dataset.page) > 1 ? el.dataset.page : undefined }, { keepPage: true });
    case 'clear-filters':
      $('#u-search').value = '';
      return setUq(Object.fromEntries(FILTER_KEYS.map((k) => [k, undefined])));
    case 'show-trash': return setUq({ status: 'deleted' });
    case 'f-status': return menuOf(el, STATUS_FILTERS, f.status, (v) => setUq({ status: v || undefined }));
    case 'f-state': return openStateFilter(el);
    case 'f-plan': return menuOf(el, PLAN_FILTERS, f.plan, (v) => setUq({ plan: v || undefined }));
    case 'f-seen': return ui.menu(el, SEEN_FILTERS.map(([k, l, p]) => ({ label: l,
      checked: (!k && !f.seen_within && !f.inactive_for) || (p.seen_within && String(p.seen_within) === f.seen_within) || (p.inactive_for && String(p.inactive_for) === f.inactive_for),
      onClick: () => setUq({ seen_within: p.seen_within, inactive_for: p.inactive_for }) })));
    case 'f-tag': {
      const tags = (AU.facets && AU.facets.tags) || [];
      if (!tags.length) return ui.menu(el, [{ label: 'No tags yet — add one from a customer or the bulk bar', disabled: true }]);
      return ui.menu(el, [
        { label: 'Filter by tag…', icon: 'filter', onClick: () => ui.multiFilter(el, { title: 'Tags', options: tags.map((t) => ({ value: String(t.id), label: `${t.name} (${fmtNumber(t.n)})` })),
          selected: new Set((f.tag || '').split(',').filter(Boolean)), onChange: (sel) => setUq({ tag: Array.from(sel).join(',') }) }) },
        ...(f.tag ? [{ label: 'Any tag', onClick: () => setUq({ tag: undefined }) }] : []),
        { divider: true },
        { label: 'Manage tags…', icon: 'pencil', onClick: manageTags },
      ]);
    }
    case 'f-signup': return openSignupFilter(el);
    case 'bulk-all': AU.allMatching = true; return paintBulkBar();
    case 'bulk-clear':
      AU.selected.clear(); AU.allMatching = false;
      renderUsersTable(); return paintBulkBar();
    case 'bulk-lock': return bulkLock();
    case 'bulk-unlock': return ui.busy(el, () => runBulk('unlock'));
    case 'bulk-trial': return bulkTrial();
    case 'bulk-tag': return bulkTag(el);
    case 'bulk-email': {
      const f = { ...uq() };
      delete f.page;
      return openComposer(AU.allMatching
        ? { audience: { query: toQuery(f) }, label: `Everyone matching these filters (${people(AU.total)})` }
        : { audience: { ids: Array.from(AU.selected) }, label: `${people(AU.selected.size)} you selected` });
    }
    default: return undefined;
  }
}

window.addEventListener('ispend:admin-user-changed', () => { if (AdminPanels.current() === 'customers' && !P360.open) loadUsers(); });
