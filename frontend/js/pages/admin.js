/* Admin console: instance statistics, the user lifecycle and the activity log.
   Billing and backup mount their own tabs through window.AdminPanels.register(). */

const AD = {
  me: null, days: 90, overview: null,
  users: [], retention: 30, purgeDue: 0,
  filters: { status: '', role: '', q: '', sort: 'id', dir: 'asc' },
  audit: { items: [], cursor: null, action: '', q: '', actions: null },
  charts: {},
};

const TAB_META = {
  overview: { label: 'Overview', icon: 'activity', load: loadOverview },
  users: { label: 'Users', icon: 'users', load: loadUsers },
  activity: { label: 'Activity', icon: 'clock', load: loadActivity },
};

/* Composition hook: a sibling feature adds a tab without editing this file.
   AdminPanels.register('backup', { label, icon, load(hostEl) }) plus one <script> in admin.html. */
window.AdminPanels = {
  register(tab, { label, icon: ico, load }) {
    TAB_META[tab] = { label, icon: ico, load, external: true };
    const nav = document.getElementById('admin-nav');
    if (!nav || nav.querySelector(`[data-tab="${tab}"]`)) return;
    const a = document.createElement('a');
    a.className = 'nav-item';
    a.href = `#${tab}`;
    a.dataset.tab = tab;
    a.innerHTML = `${icon(ico)}<span class="label">${esc(label)}</span>`;
    nav.appendChild(a);
    const panel = document.createElement('section');
    panel.className = 'tab-panel';
    panel.dataset.panel = tab;
    document.getElementById('admin-panels').appendChild(panel);
  },
};

const STATUS_LABEL = { active: 'Active', locked: 'Locked', deleted: 'Trash' };
const STATUS_COLOR = { active: 'success', locked: 'warning', deleted: 'text-4' };
const STATUS_FILTERS = [['', 'Active & locked'], ['active', 'Active'], ['locked', 'Locked'], ['deleted', 'Trash'], ['all', 'All']];
const ROLE_FILTERS = [['', 'Any role'], ['admin', 'Admins'], ['user', 'Users']];

initNav('admin').then(async (me) => {
  AD.me = me;
  // /admin.html is a static file nginx serves to anyone; every endpoint is admin-only, but the
  // page still has to explain itself rather than render a wall of failed requests.
  if (me.role !== 'admin') return renderGate();

  $('#admin-layout').hidden = false;
  $('#admin-actions').hidden = false;
  $$('#admin-nav .nav-item').forEach((a) => {
    const t = TAB_META[a.dataset.tab];
    a.innerHTML = `${icon(t.icon)}<span class="label">${t.label}</span>`;
  });
  $('[data-act="refresh"]').innerHTML = `${icon('refresh')}<span class="label">Refresh</span>`;
  $('[data-act="add-user"]').innerHTML = `${icon('plus')}<span class="label">Add user</span>`;
  $('#a-export').innerHTML = `${icon('download')}<span class="label">Export CSV</span>`;

  ui.segmented($('#range-seg'), { onChange: (btn) => { AD.days = Number(btn.dataset.days); loadOverview({ force: true }); } });
  $('#u-search').addEventListener('input', debounce(() => { AD.filters.q = $('#u-search').value.trim(); loadUsers(); }, 250));
  $('#a-search').addEventListener('input', debounce(() => { AD.audit.q = $('#a-search').value.trim(); loadActivity(); }, 250));
  paintFilterButtons();

  window.addEventListener('hashchange', showTab);
  document.body.addEventListener('click', onAction);
  ui.shortcuts.register('g u', () => { location.hash = '#users'; }, { description: 'Admin: users' });
  window.PAGE_SHORTCUTS = [{ title: 'Admin', items: [['r', 'Refresh'], ['g u', 'Users']] }];
  showTab();
  return me;
});

function renderGate() {
  $('#admin-gate').innerHTML = ui.emptyState({
    icon: 'lock', title: 'Admins only',
    body: 'This page manages every account on this server. Ask an administrator if you need access.',
    action: { label: 'Back to dashboard', href: '/index.html' },
  });
}

function showTab() {
  // Tab switches are same-document hash changes, so a row menu opened on the previous tab would
  // otherwise stay on screen anchored to a row that is no longer visible. Bounded, because a
  // drawer's close() is async and can decline to pop its layer (an unsaved-changes prompt).
  for (let i = 0; ui.layers.length && i < 8; i++) ui.closeTop();
  let tab = (location.hash || '#overview').slice(1);
  if (!TAB_META[tab]) tab = 'overview';
  $$('#admin-nav .nav-item').forEach((a) => a.toggleAttribute('aria-current', a.dataset.tab === tab));
  $$('#admin-panels .tab-panel').forEach((p) => p.classList.toggle('active', p.dataset.panel === tab));
  $('#admin-actions').hidden = tab !== 'overview';
  setPageTitle(TAB_META[tab].label);
  const meta = TAB_META[tab];
  if (meta.external) return meta.load($(`[data-panel="${tab}"]`));
  return meta.load();
}

/* ---------- Overview ---------- */

const STAT_SKELETON = Array.from({ length: 4 }, () =>
  `<div class="stat is-loading"><div class="stat-label">&nbsp;</div><div class="stat-value">0</div>${ui.skeleton('120px', '26px')}</div>`).join('');

async function loadOverview({ force = false } = {}) {
  if (AD.overview && !force) return renderOverview(AD.overview);
  $('#ov-stats').innerHTML = STAT_SKELETON;
  $('#ov-error').innerHTML = '';
  try {
    AD.overview = await api(`/api/admin/stats/overview?days=${AD.days}${force ? '&refresh=1' : ''}`);
  } catch (err) {
    $('#ov-stats').innerHTML = '';
    $('#ov-error').innerHTML = ui.errorBox(err.message, { retry: 'reload-overview' });
    return undefined;
  }
  return renderOverview(AD.overview);
}

function renderOverview(d) {
  const u = d.users || {};
  const t = d.totals || {};
  const stats = [
    ['Users', fmtNumber(u.total || 0), `${fmtNumber(u.active || 0)} active · ${fmtNumber(u.locked || 0)} locked${u.deleted ? ` · ${fmtNumber(u.deleted)} in trash` : ''}`],
    ['Signed in · 30 days', fmtNumber(u.active_30d || 0), `${fmtNumber(u.active_by_activity_30d || 0)} also imported or edited something`],
    ['Transactions', fmtNumber(t.transactions || 0), t.first_txn_date ? `${fmtDate(t.first_txn_date, { year: true })} – ${fmtDate(t.last_txn_date, { year: true })}` : 'No data yet'],
    ['Statements', fmtNumber(t.statements || 0), `${fmtNumber(u.new_30d || 0)} new users in 30 days`],
  ];
  $('#ov-stats').innerHTML = stats.map(([label, value, sub]) =>
    `<div class="stat"><div class="stat-label">${esc(label)}</div><div class="stat-value">${esc(value)}</div>
     <div class="stat-delta"><span class="stat-delta-vs">${esc(sub)}</span></div></div>`).join('');
  renderUsersChart(d);
  renderImportChart(d);
  renderAI(d.ai || {});
  renderStorage(d.storage || {});
  renderProfiles(d.imports || {});
  renderHousekeeping(d.housekeeping || {}, d.generated_at);
}

/* Counts, not money: barOptions/lineOptions format ticks and tooltips as currency, so the
   number callbacks are replaced here. */
function countScales(t, { stacked = false } = {}) {
  return {
    interaction: { mode: 'index', intersect: false },
    plugins: { tooltip: { callbacks: { label: (c) => ` ${c.dataset.label}: ${fmtNumber(c.parsed.y)}` } } },
    scales: {
      x: { stacked, grid: { display: false }, ticks: { maxRotation: 0, autoSkip: true } },
      y: { stacked, beginAtZero: true, border: { display: false }, ticks: { precision: 0, callback: (v) => fmtNumber(v) } },
    },
  };
}

function renderUsersChart(d) {
  const signups = d.series.signups || [];
  const logins = d.series.logins || [];
  const labels = signups.map((r) => fmtDate(r.t));
  const loginBy = new Map(logins.map((r) => [r.t, r.users]));
  AD.charts.users = makeChart($('#ch-users'), () => {
    const a = catColor('c1'); const b = catColor('c5');
    return {
      type: 'line',
      data: { labels, datasets: [
        { label: 'Signups', data: signups.map((r) => r.n), borderColor: a, backgroundColor: (c) => charts.gradientFill(c.chart.ctx, a, { from: 0.25 }), fill: true, tension: 0.3, pointRadius: 0, borderWidth: 2 },
        { label: 'Users signing in', data: signups.map((r) => loginBy.get(r.t) || 0), borderColor: b, backgroundColor: charts.withAlpha(b, 0.1), fill: false, tension: 0.3, pointRadius: 0, borderWidth: 2 },
      ] },
      options: countScales(),
    };
  });
  charts.htmlLegend($('#lg-users'), AD.charts.users);
}

function renderImportChart(d) {
  const txns = d.series.transactions || [];
  const stmts = new Map((d.series.statements || []).map((r) => [r.t, r.n]));
  AD.charts.imp = makeChart($('#ch-import'), () => {
    const a = catColor('c3'); const b = catColor('c8');
    return {
      type: 'bar',
      data: { labels: txns.map((r) => fmtDate(r.t)), datasets: [
        { label: 'Transactions', data: txns.map((r) => r.n), backgroundColor: a, borderRadius: 3 },
        { label: 'Statements', data: txns.map((r) => stmts.get(r.t) || 0), backgroundColor: b, borderRadius: 3 },
      ] },
      options: countScales({ stacked: false }),
    };
  });
  charts.htmlLegend($('#lg-import'), AD.charts.imp);
}

function bar(label, value, max, hint) {
  const pct = max > 0 ? Math.round((value / max) * 100) : 0;
  return `<div class="adm-bar-row">
    <div class="adm-bar-head"><span class="text-1">${esc(label)}</span><span class="text-3 num">${esc(hint)}</span></div>
    <div class="progress"><span style="width:${pct}%"></span></div></div>`;
}

function renderAI(ai) {
  if (!ai.calls) {
    $('#ov-ai').innerHTML = `<div class="hint">No AI calls in this range.</div>`;
    return;
  }
  const models = ai.by_model || [];
  const max = Math.max(...models.map((m) => m.calls), 1);
  $('#ov-ai').innerHTML = `
    <div class="adm-kpis">
      <div><div class="l">Calls</div><div class="v">${fmtNumber(ai.calls)}</div></div>
      <div><div class="l">Tokens</div><div class="v">${fmtNumber((ai.prompt_tokens || 0) + (ai.completion_tokens || 0))}</div></div>
      <div><div class="l">Errors</div><div class="v ${ai.errors ? 'text-danger' : ''}">${fmtPct(ai.error_rate || 0)}</div></div>
      <div><div class="l">Avg time</div><div class="v">${ai.avg_ms ? `${fmtNumber(Math.round(ai.avg_ms))} ms` : '—'}</div></div>
    </div>
    <div class="adm-bars mt-4">${models.map((m) => bar(m.model, m.calls, max, `${fmtNumber(m.calls)} · ${fmtNumber(m.tokens)} tok`)).join('')}</div>
    <div class="hint mt-3">Token counts only. Prices live in the OpenRouter catalogue and change, so no cost is estimated here.</div>`;
}

function renderStorage(s) {
  if (!s.disk_scan_ok) {
    $('#ov-storage').innerHTML = `${ui.errorBox('The statements volume could not be read, so only the database figure is available.')}
      <div class="adm-kpis mt-4"><div><div class="l">Uploaded originals</div><div class="v">${fmtBytes(s.source_bytes || 0)}</div></div>
      <div><div class="l">Files</div><div class="v">${fmtNumber(s.unique_files || 0)}</div></div></div>`;
    return;
  }
  const total = s.disk_bytes || 0;
  const srcPct = total ? Math.round(((s.source_bytes || 0) / total) * 100) : 0;
  $('#ov-storage').innerHTML = `
    <div class="adm-kpis">
      <div><div class="l">On disk</div><div class="v">${fmtBytes(total)}</div></div>
      <div><div class="l">Uploaded originals</div><div class="v">${fmtBytes(s.source_bytes || 0)}</div></div>
      <div><div class="l">OCR &amp; orphans</div><div class="v">${fmtBytes(s.derived_bytes || 0)}</div></div>
      <div><div class="l">Files</div><div class="v">${fmtNumber(s.unique_files || 0)}</div></div>
    </div>
    <div class="adm-storage-bar mt-4" role="img" aria-label="Uploaded originals ${srcPct}% of disk use">
      <i style="width:${srcPct}%;background:var(--c1)"></i><i style="width:${100 - srcPct}%;background:var(--c8)"></i>
    </div>
    <div class="hint mt-3">“On disk” is measured by scanning the volume, so it counts OCR output and any orphaned files.
      “Uploaded originals” comes from the database and is de-duplicated by content hash.</div>`;
}

function renderProfiles(imports) {
  const rows = imports.by_profile || [];
  if (!rows.length) {
    $('#ov-profiles').innerHTML = `<div class="hint">No statements imported yet.</div>`;
    return;
  }
  const max = Math.max(...rows.map((r) => r.n), 1);
  $('#ov-profiles').innerHTML = `<div class="adm-bars">${rows.map((r) => bar(
    r.profile, r.n, max,
    `${fmtNumber(r.n)}${r.ocr ? ` · ${fmtNumber(r.ocr)} OCR` : ''}${r.errors ? ` · ${fmtNumber(r.errors)} failed` : ''}`)).join('')}</div>
    <div class="adm-kinds mt-4">${(imports.by_kind || []).map((k) => `<span class="badge badge-neutral">${esc(k.kind)} · ${fmtNumber(k.n)}</span>`).join(' ')}</div>`;
}

function renderHousekeeping(h, generatedAt) {
  $('#ov-housekeeping').innerHTML = `
    <div class="setting-row">
      <div><div class="title">Activity log retention</div>
        <div class="desc">${fmtNumber(h.audit_rows || 0)} entries${h.oldest_audit_at ? `, oldest ${fmtRelative(h.oldest_audit_at)}` : ''}. Older entries are pruned as new ones are written.</div></div>
      <div class="row gap-2"><input id="hk-audit" class="input input-sm num-input" type="number" min="7" max="3650" aria-label="Activity log retention in days" value="${h.audit_retention_days}"><span class="text-3">days</span></div>
    </div>
    <div class="setting-row">
      <div><div class="title">Trash retention</div>
        <div class="desc">Deleted users are listed for purging after this long. Nothing is ever purged automatically — you always confirm.</div></div>
      <div class="row gap-2"><input id="hk-trash" class="input input-sm num-input" type="number" min="0" max="3650" aria-label="Trash retention in days" value="${h.deleted_user_retention_days}"><span class="text-3">days</span></div>
    </div>
    <div class="row-between mt-4"><span class="hint">Measured ${fmtRelative(generatedAt)}.</span>
      <button type="button" class="btn btn-secondary btn-sm" data-act="save-housekeeping">Save</button></div>`;
}

/* ---------- Users ---------- */

function paintFilterButtons() {
  const status = STATUS_FILTERS.find(([v]) => v === AD.filters.status);
  const role = ROLE_FILTERS.find(([v]) => v === AD.filters.role);
  $('#u-status-btn').innerHTML = `${icon('filter')}<span class="label">${esc(status[1])}</span>`;
  $('#u-role-btn').innerHTML = `${icon('user')}<span class="label">${esc(role[1])}</span>`;
  $('#a-action-btn').innerHTML = `${icon('filter')}<span class="label">${esc(AD.audit.action || 'All actions')}</span>`;
  // Reached from a user's "See all activity"; without a visible chip the filter is invisible and stuck.
  const chip = $('#a-user-chip');
  const name = AD.audit.userId && (AD.users.find((u) => u.id === AD.audit.userId) || {}).username;
  chip.hidden = !AD.audit.userId;
  if (AD.audit.userId) chip.innerHTML = `${icon('user')}<span class="label">${esc(name || `#${AD.audit.userId}`)}</span>${icon('x', 'ico-sm')}`;
}

const USER_COLS = [['username', 'User'], ['role', 'Role'], ['status', 'Status'], ['last_login_at', 'Last sign-in'],
  ['txn_count', 'Transactions'], ['storage_bytes', 'Storage'], ['created_at', 'Created']];

async function loadUsers() {
  const host = $('#users-table');
  host.innerHTML = `<div class="tbl-wrap"><table class="tbl tbl--cards"><tbody>${ui.skeletonRows(4, 8)}</tbody></table></div>`;
  const f = AD.filters;
  const qs = toQuery({ status: f.status || undefined, role: f.role || undefined, q: f.q || undefined, sort: f.sort, dir: f.dir });
  let data;
  try {
    data = await api(`/api/admin/users${qs}`);
  } catch (err) {
    host.innerHTML = ui.errorBox(err.message, { retry: 'reload-users' });
    return;
  }
  AD.users = data.items;
  AD.retention = data.retention_days;
  AD.purgeDue = data.purge_due_count;
  renderUsersNotice();
  renderUsersTable();
}

function renderUsersNotice() {
  const n = AD.purgeDue;
  $('#users-notice').innerHTML = n
    ? `<div class="notice notice-warning mb-4">${icon('trash')}<div class="grow">
         ${esc(plural(n, 'user'))} ${n === 1 ? 'has' : 'have'} been in the trash longer than ${fmtNumber(AD.retention)} days.</div>
       <button type="button" class="btn btn-secondary btn-sm" data-act="show-trash">Review trash</button></div>`
    : '';
}

function activeAdminCount() {
  return AD.users.filter((u) => u.role === 'admin' && u.status === 'active').length;
}

function renderUsersTable() {
  const host = $('#users-table');
  if (!AD.users.length) {
    const filtered = AD.filters.q || AD.filters.status || AD.filters.role;
    host.innerHTML = filtered
      ? ui.emptyState({ icon: 'search', title: 'No users match', body: 'Try a different search or clear the filters.', action: { label: 'Clear filters', act: 'clear-filters' } })
      : ui.emptyState({ icon: 'users', title: 'You are the only user', body: 'Add an account for someone else to give them their own transactions, categories and rules.', action: { label: 'Add user', act: 'add-user' } });
    return;
  }
  const head = USER_COLS.map(([key, label]) => {
    const on = AD.filters.sort === key;
    const num = ['txn_count', 'storage_bytes'].includes(key);
    return `<th class="sortable ${num ? 'right' : ''}" data-act="sort" data-sort="${key}" aria-sort="${on ? (AD.filters.dir === 'asc' ? 'ascending' : 'descending') : 'none'}">${esc(label)}</th>`;
  }).join('');
  host.innerHTML = `<div class="tbl-wrap"><table class="tbl tbl--cards"><thead><tr>${head}
    <th class="col-actions"><span class="sr-only">Actions</span></th></tr></thead><tbody>
    ${AD.users.map((u) => `<tr data-id="${u.id}" class="${u.status === 'deleted' ? 'text-3' : ''}">
      <td><span class="row gap-2"><span class="avatar">${esc(initials(u.username))}</span>
        <span class="fw-500">${esc(u.username)}</span>${u.is_self ? '<span class="badge badge-accent">You</span>' : ''}</span></td>
      <td data-label="Role"><span class="badge ${u.role === 'admin' ? 'badge-info' : 'badge-neutral'}">${esc(u.role)}</span></td>
      <td data-label="Status"><span class="user-status"><i class="dot" style="--c:var(--${STATUS_COLOR[u.status]})"></i>${esc(STATUS_LABEL[u.status])}</span>
        ${u.lock_reason ? `<span class="adm-reason" data-tip="${esc(u.lock_reason)}">${icon('info', 'ico-sm')}</span>` : ''}
        ${u.status === 'deleted' && u.deleted_at ? `<span class="sub">${esc(fmtRelative(u.deleted_at))}</span>` : ''}</td>
      <td data-label="Last sign-in" class="text-3">${u.last_login_at ? esc(fmtRelative(u.last_login_at)) : 'Never'}</td>
      <td class="right num" data-label="Transactions">${fmtNumber(u.txn_count)}</td>
      <td class="right num" data-label="Storage">${fmtBytes(u.storage_bytes)}</td>
      <td class="text-3" data-label="Created">${fmtDate(u.created_at, { year: true })}</td>
      <td class="col-actions"><div class="row-actions">
        <button type="button" class="btn btn-icon btn-ghost btn-xs" data-act="user-menu" data-id="${u.id}" aria-label="Actions for ${esc(u.username)}">${icon('more-horizontal')}</button>
      </div></td>
    </tr>`).join('')}</tbody></table></div>`;
}

function userRowMenu(anchor, u) {
  const isMe = u.is_self;
  const lastAdmin = u.role === 'admin' && u.status === 'active' && activeAdminCount() === 1;
  const guard = isMe ? 'Use the account menu for your own account' : (lastAdmin ? 'This is the only active administrator' : null);
  const items = [
    { label: 'View details', icon: 'eye', onClick: () => openUserDrawer(u.id) },
    { label: 'Reset password', icon: 'lock', disabled: isMe, onClick: () => openResetPassword(u) },
    { label: u.role === 'admin' ? 'Make regular user' : 'Make admin', icon: 'shield',
      disabled: !!guard, ...(guard ? { } : {}), onClick: () => changeRole(u) },
    { divider: true },
  ];
  if (u.status !== 'deleted') {
    items.push(u.status === 'locked'
      ? { label: 'Unlock', icon: 'unlock', onClick: () => unlockUser(u) }
      : { label: 'Lock', icon: 'lock', disabled: !!guard, onClick: () => openLock(u) });
    items.push({ label: 'Move to trash', icon: 'trash', danger: true, disabled: !!guard, onClick: () => trashUser(u) });
  } else {
    items.push({ label: 'Restore', icon: 'rotate-ccw', onClick: () => restoreUser(u) });
    items.push({ label: 'Delete permanently', icon: 'trash', danger: true, onClick: () => openPurge(u) });
  }
  ui.menu(anchor, items);
}

async function changeRole(u) {
  const promote = u.role !== 'admin';
  const ok = await ui.confirm({
    title: promote ? `Make ${u.username} an admin?` : `Remove admin from ${u.username}?`,
    body: promote
      ? 'Admins manage every account on this server, see all usage statistics and can publish the shared AI key. The change applies on their next request.'
      : 'They keep their data but lose user management and the shared AI key on their next request.',
    confirmText: promote ? 'Make admin' : 'Remove admin',
  });
  if (!ok) return;
  await api(`/api/admin/users/${u.id}`, { method: 'PUT', body: { role: promote ? 'admin' : 'user' } });
  toast('Role updated', { type: 'success' });
  loadUsers();
}

function openResetPassword(u) {
  const m = ui.modal({
    title: `Reset password for ${u.username}`,
    html: `<div class="field"><label for="rp">New password</label>
      <input id="rp" class="input" type="password" minlength="10" autocomplete="new-password" autofocus>
      <div class="hint">At least 10 characters. Tell them out of band; iSpend does not email it.</div></div>`,
    actions: [{ label: 'Cancel' }, { label: 'Reset', primary: true, onClick: async () => {
      await api(`/api/admin/users/${u.id}/password`, { method: 'PUT', body: { password: m.el.querySelector('#rp').value } });
      toast('Password reset', { type: 'success' });
    } }],
  });
}

function openLock(u) {
  const m = ui.modal({
    title: `Lock ${u.username}?`,
    html: `<p class="mb-4">They are signed out immediately and cannot sign in until you unlock them. All of their data is kept.</p>
      <div class="field"><label for="lk">Reason (optional)</label><input id="lk" class="input" maxlength="200" autofocus>
      <div class="hint">Shown to you on the users list, never to them.</div></div>`,
    actions: [{ label: 'Cancel' }, { label: 'Lock account', primary: true, danger: true, onClick: async () => {
      await api(`/api/admin/users/${u.id}/lock`, { method: 'POST', body: { reason: m.el.querySelector('#lk').value.trim() } });
      loadUsers();
      ui.undoable(`${u.username} locked`, async () => {
        await api(`/api/admin/users/${u.id}/unlock`, { method: 'POST' });
        loadUsers();
      });
    } }],
  });
}

async function unlockUser(u) {
  await api(`/api/admin/users/${u.id}/unlock`, { method: 'POST' });
  loadUsers();
  ui.undoable(`${u.username} unlocked`, async () => {
    await api(`/api/admin/users/${u.id}/lock`, { method: 'POST', body: {} });
    loadUsers();
  });
}

async function trashUser(u) {
  const ok = await ui.confirm({
    title: `Move ${u.username} to the trash?`,
    body: `They are signed out immediately and cannot sign in. All ${plural(u.txn_count, 'transaction')} and their uploaded files are kept, and you can restore them.`,
    confirmText: 'Move to trash', danger: true,
  });
  if (!ok) return;
  await api(`/api/admin/users/${u.id}`, { method: 'DELETE' });
  loadUsers();
  ui.undoable(`${u.username} moved to the trash`, async () => {
    await api(`/api/admin/users/${u.id}/restore`, { method: 'POST' });
    loadUsers();
  });
}

async function restoreUser(u) {
  const r = await api(`/api/admin/users/${u.id}/restore`, { method: 'POST' });
  toast(r.status === 'locked' ? `${u.username} restored — the account is still locked` : `${u.username} restored`, { type: 'success' });
  loadUsers();
}

function openPurge(u) {
  const m = ui.modal({
    title: `Delete ${u.username} permanently?`,
    html: `<p>This deletes ${esc(plural(u.txn_count, 'transaction'))}, ${esc(plural(u.account_count, 'account'))},
        ${esc(plural(u.statement_count, 'statement'))} and ${esc(fmtBytes(u.storage_bytes))} of uploaded files.
        <b>It cannot be undone.</b></p>
      <div class="field mt-4"><label for="pg">Type <b>${esc(u.username)}</b> to confirm</label>
        <input id="pg" class="input" autocomplete="off" spellcheck="false" autofocus></div>`,
    actions: [{ label: 'Cancel' }, { label: 'Delete permanently', primary: true, danger: true, onClick: async () => {
      const typed = m.el.querySelector('#pg').value.trim();
      if (typed !== u.username) { ui.fieldError(m.el.querySelector('#pg'), 'That does not match the username'); return false; }
      // The server re-checks the phrase; this one is only here to slow the hand down.
      await api(`/api/admin/users/${u.id}?permanent=true&confirm=${encodeURIComponent(u.username)}`, { method: 'DELETE' });
      toast(`${u.username} deleted permanently`);
      loadUsers();
      return undefined;
    } }],
  });
  const input = m.el.querySelector('#pg');
  const btn = m.el.querySelector('.modal-foot .btn-primary');
  btn.disabled = true;
  input.addEventListener('input', () => { btn.disabled = input.value.trim() !== u.username; });
}

function openAddUser() {
  const m = ui.modal({
    title: 'Add user',
    html: `<form id="user-form">
      <div class="field"><label for="u-name">Username</label><input id="u-name" class="input" autocomplete="off" required autofocus spellcheck="false"></div>
      <div class="field"><label for="u-pass">Password</label><input id="u-pass" class="input" type="password" minlength="10" autocomplete="new-password" required>
        <div class="hint">At least 10 characters. The user can change it later.</div></div>
      <div class="field"><label for="u-role">Role</label><select id="u-role" class="select"><option value="user">User</option><option value="admin">Admin</option></select></div>
      <button type="submit" hidden></button></form>`,
    actions: [{ label: 'Cancel' }, { label: 'Create user', primary: true, onClick: async () => {
      const username = m.el.querySelector('#u-name').value.trim();
      try {
        await api('/api/admin/users', { method: 'POST', body: { username, password: m.el.querySelector('#u-pass').value, role: m.el.querySelector('#u-role').value } });
      } catch (err) {
        // A deleted user keeps their username reserved, so offer the action that actually helps.
        if (err.data && err.data.code === 'username_deleted') { m.close(); return offerRestore(err.data, username); }
        throw err;
      }
      toast('User created', { type: 'success' });
      loadUsers();
      return undefined;
    } }],
  });
  m.el.querySelector('#user-form').addEventListener('submit', (e) => { e.preventDefault(); m.el.querySelector('.modal-foot .btn-primary').click(); });
}

function offerRestore(data, username) {
  ui.modal({
    title: `${username} is in the trash`,
    html: `<p>${esc(data.error)} Restoring keeps everything they had — accounts, transactions, categories and rules.</p>`,
    actions: [{ label: 'Cancel' }, { label: `Restore ${username}`, primary: true, onClick: async () => {
      const r = await api(`/api/admin/users/${data.user_id}/restore`, { method: 'POST' });
      toast(r.status === 'locked' ? `${username} restored — the account is still locked` : `${username} restored`, { type: 'success' });
      AD.filters.status = '';
      paintFilterButtons();
      loadUsers();
    } }],
  });
  return undefined;
}

async function openUserDrawer(id) {
  const d = ui.drawer({ title: 'User', html: ui.skeletonList(6), width: 520 });
  let data;
  try {
    data = await api(`/api/admin/users/${id}`);
  } catch (err) {
    d.setBody(ui.errorBox(err.message));
    return;
  }
  const u = data.user;
  const c = data.counts || {};
  const s = data.storage || {};
  const r = data.data_range || {};
  const mix = data.categorization || [];
  const total = mix.reduce((a, m) => a + m.n, 0);
  d.setTitle(u.username);
  const tiles = [['Accounts', c.accounts], ['Transactions', c.transactions], ['Statements', c.statements],
    ['Categories', c.categories], ['Rules', c.rules], ['Budgets', c.budgets], ['Tags', c.tags],
    ['Merchants', c.merchants], ['Insights', c.insights], ['Settings rows', c.settings_rows]];
  d.setBody(`
    <div class="row gap-2 mb-4">
      <span class="badge ${u.role === 'admin' ? 'badge-info' : 'badge-neutral'}">${esc(u.role)}</span>
      <span class="user-status"><i class="dot" style="--c:var(--${STATUS_COLOR[u.status]})"></i>${esc(STATUS_LABEL[u.status])}</span>
      ${u.lock_reason ? `<span class="text-3">· ${esc(u.lock_reason)}</span>` : ''}
    </div>
    <div class="adm-detail-grid">${tiles.map(([l, v]) =>
      `<div class="adm-tile"><div class="l">${esc(l)}</div><div class="v">${fmtNumber(v || 0)}</div></div>`).join('')}</div>
    <div class="setting-row"><div><div class="title">Signed in</div><div class="desc">Created ${esc(fmtDateLong(u.created_at))}</div></div>
      <div class="text-1">${u.last_login_at ? esc(fmtRelative(u.last_login_at)) : 'Never'}</div></div>
    <div class="setting-row"><div><div class="title">Data range</div><div class="desc">Last import ${r.last_import_at ? esc(fmtRelative(r.last_import_at)) : 'never'}</div></div>
      <div class="text-1">${r.first_txn ? `${esc(fmtDate(r.first_txn, { year: true }))} – ${esc(fmtDate(r.last_txn, { year: true }))}` : '—'}</div></div>
    <div class="setting-row"><div><div class="title">Storage</div>
      <div class="desc">${fmtNumber(s.unique_files || 0)} files${s.disk_scan_ok ? '' : ' · volume unreadable'}</div></div>
      <div class="text-1">${esc(s.disk_scan_ok ? fmtBytes(s.disk_bytes || 0) : fmtBytes(s.source_bytes || 0))}</div></div>
    ${total ? `<h3 class="mt-4 mb-2">How their transactions were categorized</h3>
      <div class="adm-bars">${mix.slice(0, 6).map((m) => bar(`${m.source} · ${m.category_status}`, m.n, total, fmtNumber(m.n))).join('')}</div>` : ''}
    ${data.ai && data.ai.calls ? `<h3 class="mt-4 mb-2">AI</h3>
      <div class="adm-kpis"><div><div class="l">Calls</div><div class="v">${fmtNumber(data.ai.calls)}</div></div>
      <div><div class="l">Tokens</div><div class="v">${fmtNumber((data.ai.prompt_tokens || 0) + (data.ai.completion_tokens || 0))}</div></div>
      <div><div class="l">Errors</div><div class="v">${fmtNumber(data.ai.errors || 0)}</div></div></div>` : ''}
    <h3 class="mt-4 mb-2">Recent activity</h3>
    ${(data.recent_activity || []).length
      ? `<div class="adm-audit">${data.recent_activity.map(auditRow).join('')}</div>
         <a class="btn btn-secondary btn-sm mt-3" href="#activity" data-act="audit-for-user" data-id="${u.id}">See all activity</a>`
      : '<div class="hint">Nothing recorded.</div>'}`);
}

/* ---------- Activity ---------- */

function auditRow(a) {
  const who = a.username || (a.detail && a.detail.username) || (a.user_id ? `#${a.user_id}` : 'system');
  const detail = a.detail && Object.keys(a.detail).length ? JSON.stringify(a.detail) : '';
  return `<div class="adm-audit-item">
    <span class="adm-audit-dot ${a.action.startsWith('user.') ? 'is-user' : ''}"></span>
    <div class="min-w-0"><div class="adm-audit-action">${esc(a.action)}<span class="text-3"> · ${esc(who)}</span></div>
      ${detail ? `<div class="adm-audit-detail">${esc(detail)}</div>` : ''}</div>
    <time class="text-3" datetime="${esc(a.created_at || '')}">${a.created_at ? esc(fmtRelative(a.created_at)) : ''}</time>
  </div>`;
}

async function loadActivity({ more = false } = {}) {
  const host = $('#audit-list');
  if (!more) {
    host.innerHTML = ui.skeletonList(8);
    AD.audit.items = [];
    AD.audit.cursor = null;
  }
  const qs = toQuery({ action: AD.audit.action || undefined, q: AD.audit.q || undefined,
    user_id: AD.audit.userId || undefined, cursor: more ? AD.audit.cursor : undefined, limit: 50 });
  let data;
  try {
    data = await api(`/api/admin/audit${qs}`);
  } catch (err) {
    host.innerHTML = ui.errorBox(err.message, { retry: 'reload-audit' });
    return;
  }
  AD.audit.items = more ? AD.audit.items.concat(data.items) : data.items;
  AD.audit.cursor = data.next_cursor;
  $('[data-act="audit-more"]').hidden = !data.next_cursor;
  $('#a-export').href = `/api/admin/audit${toQuery({ action: AD.audit.action || undefined, q: AD.audit.q || undefined, user_id: AD.audit.userId || undefined, format: 'csv' })}`;
  paintFilterButtons();
  host.innerHTML = AD.audit.items.length
    ? `<div class="adm-audit">${AD.audit.items.map(auditRow).join('')}</div>`
    : ui.emptyState({ icon: 'clock', title: 'No activity recorded' });
}

async function openActionFilter(anchor) {
  if (!AD.audit.actions) {
    try { AD.audit.actions = await api('/api/admin/audit/actions'); } catch { AD.audit.actions = []; }
  }
  ui.menu(anchor, [{ label: 'All actions', checked: !AD.audit.action, onClick: () => { AD.audit.action = ''; paintFilterButtons(); loadActivity(); } },
    { divider: true },
    ...AD.audit.actions.slice(0, 25).map((a) => ({
      label: `${a.action} (${fmtNumber(a.n)})`, checked: AD.audit.action === a.action,
      onClick: () => { AD.audit.action = a.action; paintFilterButtons(); loadActivity(); },
    }))]);
}

/* ---------- Actions ---------- */

async function onAction(e) {
  const el = e.target.closest('[data-act]');
  if (!el) return;
  const act = el.dataset.act;
  const u = AD.users.find((x) => x.id === Number(el.dataset.id));
  switch (act) {
    case 'refresh': return ui.busy(el, () => loadOverview({ force: true }));
    case 'reload-overview': return loadOverview({ force: true });
    case 'reload-users': return loadUsers();
    case 'reload-audit': return loadActivity();
    case 'add-user': return openAddUser();
    case 'user-menu': return userRowMenu(el, u);
    case 'sort': {
      const key = el.dataset.sort;
      AD.filters.dir = AD.filters.sort === key && AD.filters.dir === 'asc' ? 'desc' : 'asc';
      AD.filters.sort = key;
      return loadUsers();
    }
    case 'clear-filters':
      AD.filters = { status: '', role: '', q: '', sort: 'id', dir: 'asc' };
      $('#u-search').value = '';
      paintFilterButtons();
      return loadUsers();
    case 'show-trash':
      AD.filters.status = 'deleted';
      paintFilterButtons();
      return loadUsers();
    case 'audit-for-user':
      AD.audit.userId = Number(el.dataset.id);
      location.hash = '#activity';   // showTab() closes the drawer this link sits in
      return undefined;
    case 'audit-more': return ui.busy(el, () => loadActivity({ more: true }));
    case 'clear-audit-user':
      AD.audit.userId = null;
      paintFilterButtons();
      return loadActivity();
    case 'save-housekeeping':
      return ui.busy(el, async () => {
        await api('/api/admin/settings', { method: 'PUT', body: {
          audit_retention_days: Number($('#hk-audit').value),
          deleted_user_retention_days: Number($('#hk-trash').value),
        } });
        toast('Housekeeping saved', { type: 'success' });
        loadOverview({ force: true });
      });
    default: return undefined;
  }
}

document.addEventListener('click', (e) => {
  if (e.target.closest('#u-status-btn')) {
    ui.menu($('#u-status-btn'), STATUS_FILTERS.map(([v, l]) => ({ label: l, checked: AD.filters.status === v,
      onClick: () => { AD.filters.status = v; paintFilterButtons(); loadUsers(); } })));
  } else if (e.target.closest('#u-role-btn')) {
    ui.menu($('#u-role-btn'), ROLE_FILTERS.map(([v, l]) => ({ label: l, checked: AD.filters.role === v,
      onClick: () => { AD.filters.role = v; paintFilterButtons(); loadUsers(); } })));
  } else if (e.target.closest('#a-action-btn')) {
    openActionFilter($('#a-action-btn'));
  }
});
