/* Admin › Users and Admin › Activity: the user lifecycle and the activity log.
   The shell (admin-shell.js) owns navigation and routing; this file registers two sections. */

const AD = {
  me: null,
  users: [], retention: 30, purgeDue: 0,
  filters: { status: '', role: '', q: '', sort: 'id', dir: 'asc' },
  audit: { items: [], cursor: null, action: '', q: '', actions: null },
  wired: false,
};

const STATUS_LABEL = { active: 'Active', locked: 'Locked', deleted: 'Trash' };
const STATUS_COLOR = { active: 'success', locked: 'warning', deleted: 'text-4' };
const STATUS_FILTERS = [['', 'Active & locked'], ['active', 'Active'], ['locked', 'Locked'], ['deleted', 'Trash'], ['all', 'All']];
const ROLE_FILTERS = [['', 'Any role'], ['admin', 'Admins'], ['user', 'Users']];


AdminPanels.register('users', {
  label: 'Users', icon: 'users', group: 'insights',
  sub: 'Everyone with an account here, their access and what state it is in',
  actions: '<button type="button" class="btn btn-primary" data-act="add-user" aria-label="Add user"></button>',
  markup: `
    <div id="users-notice"></div>
    <div class="tbl-toolbar" id="users-toolbar">
      <div class="input-group adm-search"><span class="ico-wrap"></span>
        <input id="u-search" class="input input-sm" type="search" placeholder="Search users…" aria-label="Search users"></div>
      <button type="button" class="btn btn-secondary btn-sm" id="u-status-btn" aria-haspopup="menu"></button>
      <button type="button" class="btn btn-secondary btn-sm" id="u-role-btn" aria-haspopup="menu"></button>
      <button type="button" class="btn btn-ghost btn-sm" id="u-clear" data-act="clear-filters" hidden>Clear</button>
    </div>
    <div class="tbl-summary" id="users-summary"></div>
    <div id="users-table"></div>`,
  load: () => { wireAdminLists(); openUserFromQuery(); return loadUsers(); },
});

AdminPanels.register('activity', {
  label: 'Activity', icon: 'clock', group: 'operations',
  sub: 'Everything that has happened here, newest first',
  actions: '<a class="btn btn-secondary" id="a-export" href="/api/admin/audit?format=csv" download data-admin-download></a>',
  markup: `
    <div class="tbl-toolbar" id="audit-toolbar">
      <div class="input-group adm-search"><span class="ico-wrap"></span>
        <input id="a-search" class="input input-sm" type="search" placeholder="Search actions…" aria-label="Search activity"></div>
      <button type="button" class="btn btn-secondary btn-sm" id="a-action-btn" aria-haspopup="menu"></button>
      <button type="button" class="btn btn-secondary btn-sm" id="a-admin-btn" data-act="toggle-admin-only" aria-pressed="false"></button>
      <button type="button" class="btn btn-secondary btn-sm" id="a-user-chip" data-act="clear-audit-user" hidden></button>
    </div>
    <div class="tbl-summary" id="audit-summary"></div>
    <div id="audit-list"></div>
    <div class="row center mt-4"><button type="button" class="btn btn-secondary btn-sm" data-act="audit-more" hidden>Load more</button></div>`,
  load: () => { wireAdminLists(); return loadActivity(); },
});

/* Once, the first time either section opens: both live in the DOM from the start. */
function wireAdminLists() {
  if (AD.wired) return;
  AD.wired = true;
  AD.me = window.currentUser;
  $('[data-act="add-user"]').innerHTML = `${icon('plus')}<span class="label">Add user</span>`;
  $('#a-export').innerHTML = `${icon('download')}<span class="label">Export CSV</span>`;
  $$('.adm-search .ico-wrap').forEach((el) => { el.innerHTML = icon('search'); });
  if (qs().admin === '1') AD.audit.adminOnly = true;
  $('#u-search').addEventListener('input', debounce(() => { AD.filters.q = $('#u-search').value.trim(); loadUsers(); }, 250));
  $('#a-search').addEventListener('input', debounce(() => { AD.audit.q = $('#a-search').value.trim(); loadActivity(); }, 250));
  // A row is a link to the user; the username inside it carries the same action for the keyboard.
  $('#users-table').addEventListener('click', (e) => {
    if (e.target.closest('[data-act]')) return;
    const tr = e.target.closest('tr[data-id]');
    if (tr) openUserDrawer(Number(tr.dataset.id));
  });
  document.body.addEventListener('click', onAction);
  paintFilterButtons();
}

/* ?user=<id> opens that person (the command palette and links from other sections use it). */
function openUserFromQuery() {
  const open = Number(qs().user);
  if (!open) return;
  setQs({ user: undefined }, { merge: true });
  openUserDrawer(open);
}

/* ---------- Users ---------- */

function paintFilterButtons() {
  const f = AD.filters;
  const status = STATUS_FILTERS.find(([v]) => v === f.status);
  const role = ROLE_FILTERS.find(([v]) => v === f.role);
  $('#u-status-btn').innerHTML = `${icon('filter')}<span class="label">${esc(status[1])}</span>`;
  $('#u-status-btn').classList.toggle('is-on', !!f.status);
  $('#u-role-btn').innerHTML = `${icon('user')}<span class="label">${esc(role[1])}</span>`;
  $('#u-role-btn').classList.toggle('is-on', !!f.role);
  $('#u-clear').hidden = !(f.status || f.role || f.q);
  $('#a-action-btn').innerHTML = `${icon('filter')}<span class="label">${esc(AD.audit.action ? auditTitle(AD.audit.action) : 'All actions')}</span>`;
  $('#a-action-btn').classList.toggle('is-on', !!AD.audit.action);
  const adminBtn = $('#a-admin-btn');
  adminBtn.innerHTML = `${icon('shield')}<span class="label">Admin actions only</span>`;
  adminBtn.setAttribute('aria-pressed', String(!!AD.audit.adminOnly));
  adminBtn.classList.toggle('is-on', !!AD.audit.adminOnly);
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
  host.innerHTML = `<div class="tbl-wrap"><table class="tbl tbl--cards tbl--list"><tbody>${ui.skeletonRows(4, 8)}</tbody></table></div>`;
  const f = AD.filters;
  const qs = toQuery({ status: f.status || undefined, role: f.role || undefined, q: f.q || undefined, sort: f.sort, dir: f.dir });
  let data;
  try {
    data = await api(`/api/admin/users${qs}`);
  } catch (err) {
    host.innerHTML = ui.errorBox(err.message, { retry: 'reload-users' });
    $('#users-summary').innerHTML = '';
    return;
  }
  AD.users = data.items;
  AD.retention = data.retention_days;
  AD.purgeDue = data.purge_due_count;
  paintFilterButtons();
  renderUsersNotice();
  renderUsersSummary();
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

function renderUsersSummary() {
  const us = AD.users;
  const filtered = !!(AD.filters.q || AD.filters.status || AD.filters.role);
  const count = (fn) => us.filter(fn).length;
  const parts = [`<span><b>${fmtNumber(us.length)}</b> ${us.length === 1 ? 'user' : 'users'}${filtered ? ' matching' : ''}</span>`];
  const admins = count((u) => u.role === 'admin');
  if (admins) parts.push(`<span><b>${fmtNumber(admins)}</b> ${admins === 1 ? 'admin' : 'admins'}</span>`);
  const locked = count((u) => u.status === 'locked');
  if (locked) parts.push(`<span><b>${fmtNumber(locked)}</b> locked</span>`);
  const trashed = count((u) => u.status === 'deleted');
  if (trashed) parts.push(`<span><b>${fmtNumber(trashed)}</b> in trash</span>`);
  const txns = us.reduce((a, u) => a + (u.txn_count || 0), 0);
  parts.push(`<span><b>${fmtNumber(txns)}</b> transactions</span>`);
  parts.push(`<span><b>${fmtBytes(us.reduce((a, u) => a + (u.storage_bytes || 0), 0))}</b> stored</span>`);
  $('#users-summary').innerHTML = us.length ? parts.join('') : '';
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
  host.innerHTML = `<div class="tbl-wrap"><table class="tbl tbl--cards tbl--list"><thead><tr>${head}
    <th class="col-actions"><span class="sr-only">Actions</span></th></tr></thead><tbody>
    ${AD.users.map((u) => `<tr data-id="${u.id}" class="is-clickable ${u.status === 'deleted' ? 'is-trashed' : ''}">
      <td><span class="row gap-2"><span class="avatar" aria-hidden="true">${esc(initials(u.username))}</span>
        <button type="button" class="row-link fw-500" data-act="user-open" data-id="${u.id}">${esc(u.username)}</button>
        ${u.is_self ? '<span class="badge badge-accent">You</span>' : ''}</span></td>
      <td data-label="Role"><span class="badge ${u.role === 'admin' ? 'badge-info' : 'badge-neutral'}">${esc(u.role)}</span></td>
      <td data-label="Status"><span class="user-status"><i class="dot" style="--c:var(--${STATUS_COLOR[u.status]})"></i>${esc(STATUS_LABEL[u.status])}</span>
        ${u.lock_reason ? `<span class="adm-reason" data-tip="${esc(u.lock_reason)}">${icon('info', 'ico-sm')}</span>` : ''}
        ${u.status === 'deleted' && u.deleted_at ? `<span class="sub">${esc(fmtRelative(u.deleted_at))}</span>` : ''}</td>
      <td data-label="Last sign-in" class="text-3">${u.last_login_at ? `<span data-tip="${esc(fmtDateTime(u.last_login_at))}">${esc(fmtRelative(u.last_login_at))}</span>` : 'Never'}</td>
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
    // Greyed-out rows need a reason, or the admin retries the same click.
    ...(guard ? [{ label: guard, header: true }] : []),
    { label: 'View details', icon: 'eye', onClick: () => openUserDrawer(u.id) },
    { label: 'Reset password', icon: 'lock', disabled: isMe, onClick: () => openResetPassword(u) },
    { label: u.role === 'admin' ? 'Make regular user' : 'Make admin', icon: 'shield',
      disabled: !!guard, onClick: () => changeRole(u) },
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
      ? 'Admins manage everyone’s accounts, see all the usage figures, and can share the AI key. This takes effect the next time they do anything.'
      : 'They keep their own data, but lose account management and the shared AI key the next time they do anything.',
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
      <div class="field"><label for="u-role">Role</label><select id="u-role" class="select"><option value="user">User</option><option value="admin">Admin</option></select>
        <div class="hint">Admins manage every account on this server and see all usage statistics.</div></div>
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
    <div class="row gap-2 mb-4 adm-drawer-head">
      <span class="avatar" aria-hidden="true">${esc(initials(u.username))}</span>
      <span class="badge ${u.role === 'admin' ? 'badge-info' : 'badge-neutral'}">${esc(u.role)}</span>
      <span class="user-status"><i class="dot" style="--c:var(--${STATUS_COLOR[u.status]})"></i>${esc(STATUS_LABEL[u.status])}</span>
      ${u.lock_reason ? `<span class="text-3">· ${esc(u.lock_reason)}</span>` : ''}
      <button type="button" class="btn btn-secondary btn-sm ml-auto" data-act="user-menu" data-id="${u.id}"
        aria-label="Actions for ${esc(u.username)}">${icon('more-horizontal')}<span class="label">Actions</span></button>
    </div>
    <div class="section-label mb-2">What they have</div>
    <div class="adm-detail-grid">${tiles.map(([l, v]) =>
      `<div class="adm-tile"><div class="l">${esc(l)}</div><div class="v">${fmtNumber(v || 0)}</div></div>`).join('')}</div>
    <div class="setting-row"><div><div class="title">Signed in</div><div class="desc">Created ${esc(fmtDateLong(u.created_at))}</div></div>
      <div class="text-1">${u.last_login_at ? esc(fmtRelative(u.last_login_at)) : 'Never'}</div></div>
    <div class="setting-row"><div><div class="title">Data range</div><div class="desc">Last import ${r.last_import_at ? esc(fmtRelative(r.last_import_at)) : 'never'}</div></div>
      <div class="text-1">${r.first_txn ? `${esc(fmtDate(r.first_txn, { year: true }))} – ${esc(fmtDate(r.last_txn, { year: true }))}` : '—'}</div></div>
    <div class="setting-row"><div><div class="title">Storage</div>
      <div class="desc">${fmtNumber(s.unique_files || 0)} files${s.disk_scan_ok ? '' : ' · volume unreadable'}</div></div>
      <div class="text-1">${esc(s.disk_scan_ok ? fmtBytes(s.disk_bytes || 0) : fmtBytes(s.source_bytes || 0))}</div></div>
    ${total ? `<div class="section-label mt-6 mb-2">How their transactions were categorized</div>
      <div class="adm-bars">${mix.slice(0, 6).map((m) => bar(`${m.source} · ${m.category_status}`, m.n, total, fmtNumber(m.n))).join('')}</div>` : ''}
    ${data.ai && data.ai.calls ? `<div class="section-label mt-6 mb-2">AI</div>
      ${kpis([['Calls', fmtNumber(data.ai.calls)],
              ['Tokens', fmtNumber((data.ai.prompt_tokens || 0) + (data.ai.completion_tokens || 0))],
              ['Errors', fmtNumber(data.ai.errors || 0), data.ai.errors ? 'text-danger' : '']])}` : ''}
    <div class="section-label mt-6 mb-2">Recent activity</div>
    ${(data.recent_activity || []).length
      ? `<div class="adm-audit adm-audit--compact">${data.recent_activity.map((a) => auditRow(a, { compact: true })).join('')}</div>
         <a class="btn btn-secondary btn-sm mt-3" href="#activity" data-act="audit-for-user" data-id="${u.id}">See all activity</a>`
      : '<div class="hint">Nothing recorded.</div>'}`);
}

/* ---------- Activity ---------- */

/* The log stores machine actions (`statement.upload`). Admins read them all day, so they are
   rendered as a sentence with the family's icon; the raw action stays as the filter value and in
   the tooltip so nothing becomes unsearchable. */
const AUDIT_SUBJECT = {
  auth: 'lock', user: 'user', admin: 'shield', account: 'landmark', statement: 'file-text',
  transaction: 'list', transactions: 'list', category: 'tag', rule: 'sliders', budget: 'target',
  tag: 'tags', merchant: 'shopping-bag', merchants: 'shopping-bag', review: 'inbox-check',
  insights: 'lightbulb', anomaly: 'alert-triangle', recurring: 'repeat', settings: 'settings',
  billing: 'credit-card', backup: 'database', email: 'mail',
};
const AUDIT_VERB = {
  create: 'created', update: 'updated', delete: 'deleted', purge: 'deleted permanently',
  lock: 'locked', unlock: 'unlocked', restore: 'restored', deactivate: 'deactivated',
  upload: 'uploaded', download: 'downloaded', commit: 'committed', apply: 'applied',
  merge: 'merged', reorder: 'reordered', split: 'split', unsplit: 'unsplit', pair: 'paired',
  unpair: 'unpaired', auto_pair: 'auto-paired', bulk: 'bulk edited', resolve: 'resolved',
  dismiss: 'dismissed', undismiss: 'brought back', forget: 'forgotten', generate: 'generated',
  copy: 'copied', learn: 'learned', renormalize: 'renormalized', flip_signs: 'signs flipped',
  reset_defaults: 'reset to defaults', run_all: 'run over everything', password_change: 'password changed',
  blocked: 'blocked', failed: 'failed', sync: 'synced', test: 'tested',
};
const AUDIT_EXACT = {
  'auth.login': 'Signed in', 'auth.logout': 'Signed out', 'auth.login.blocked': 'Sign-in blocked',
  'auth.forgot': 'Password reset requested', 'auth.reset': 'Password reset',
  'auth.admin_new_network': 'Admin signed in from a new network',
  'auth.step_up.locked_out': 'Signed out after wrong passwords',
  'admin.audit.export': 'Activity exported',
};
const AUDIT_DANGER = new Set(['delete', 'purge', 'blocked', 'deactivate', 'failed', 'lock']);

function cap(s) { return s ? s.charAt(0).toUpperCase() + s.slice(1) : s; }
function words(s) { return String(s).replace(/[._]+/g, ' ').trim(); }

function auditTitle(action) {
  if (AUDIT_EXACT[action]) return AUDIT_EXACT[action];
  const parts = String(action).split('.');
  const verb = AUDIT_VERB[parts[parts.length - 1]];
  if (!verb || parts.length < 2) return cap(words(action));
  return `${cap(words(parts.slice(0, -1).join(' ')))} ${verb}`;
}

function auditVerb(action) { return String(action).split('.').pop(); }
function auditIcon(action) { return AUDIT_SUBJECT[String(action).split('.')[0]] || 'circle'; }

function auditDetail(detail, hidden = 0) {
  const entries = Object.entries(detail || {}).filter(([, v]) => v !== null && v !== '');
  const privacy = hidden ? `<span class="adm-kv" data-tip="Names, file names and amounts people entered stay private">${icon('eye-off', 'ico-sm')}<b>${hidden} private</b></span>` : '';
  if (!entries.length) return privacy ? `<div class="adm-audit-detail">${privacy}</div>` : '';
  const val = (v) => (typeof v === 'object' ? JSON.stringify(v) : String(v));
  const shown = entries.slice(0, 5).map(([k, v]) =>
    `<span class="adm-kv"><i>${esc(words(k))}</i><b>${esc(val(v))}</b></span>`).join('');
  const rest = entries.length - 5;
  return `<div class="adm-audit-detail">${shown}${rest > 0
    ? `<span class="adm-kv" data-tip="${esc(JSON.stringify(detail))}">+${rest} more</span>` : ''}${privacy}</div>`;
}

function auditRow(a, { compact = false } = {}) {
  const who = a.username || (a.detail && a.detail.username) || (a.user_id ? `#${a.user_id}` : 'system');
  const danger = AUDIT_DANGER.has(auditVerb(a.action));
  return `<div class="adm-audit-item">
    <span class="adm-audit-ico ${danger ? 'is-danger' : ''}" aria-hidden="true">${icon(auditIcon(a.action), 'ico-sm')}</span>
    <div class="min-w-0">
      <div class="adm-audit-action"><span data-tip="${esc(a.action)}">${esc(auditTitle(a.action))}</span>${a.target_user_id && a.target_user_id !== a.user_id
        ? ` <span class="text-3">·</span> <button type="button" class="row-link" data-act="audit-for-user" data-id="${a.target_user_id}">${esc(a.target_username || `#${a.target_user_id}`)}</button>` : ''}</div>
      ${(a.detail && Object.keys(a.detail).length) || a.hidden_fields ? auditDetail(a.detail, a.hidden_fields) : ''}
    </div>
    ${compact ? '' : `<div class="adm-audit-who">${a.user_id
      ? `<button type="button" class="row-link" data-act="audit-for-user" data-id="${a.user_id}"><span class="avatar avatar-xs" aria-hidden="true">${esc(initials(who))}</span><span class="truncate">${esc(who)}</span></button>`
      : `<span class="text-4">${esc(who)}</span>`}</div>`}
    <time class="text-3" datetime="${esc(a.created_at || '')}" data-tip="${esc(a.created_at ? fmtDateTime(a.created_at) : '')}">${a.created_at ? esc(fmtRelative(a.created_at)) : ''}</time>
  </div>`;
}

function dayLabel(iso) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return 'Earlier';
  const today = new Date();
  const days = Math.round((new Date(today.getFullYear(), today.getMonth(), today.getDate())
    - new Date(d.getFullYear(), d.getMonth(), d.getDate())) / 86400000);
  if (days === 0) return 'Today';
  if (days === 1) return 'Yesterday';
  return fmtDateLong(iso);
}

function auditList(items) {
  let day = null;
  return items.map((a) => {
    const label = a.created_at ? dayLabel(a.created_at) : 'Unknown date';
    const head = label === day ? '' : `<div class="adm-day">${esc(label)}</div>`;
    day = label;
    return head + auditRow(a);
  }).join('');
}

async function loadActivity({ more = false } = {}) {
  const host = $('#audit-list');
  if (!more) {
    host.innerHTML = ui.skeletonList(8);
    AD.audit.items = [];
    AD.audit.cursor = null;
  }
  const qs = toQuery({ action: AD.audit.action || undefined, q: AD.audit.q || undefined,
    user_id: AD.audit.userId || undefined, admin: AD.audit.adminOnly ? 1 : undefined,
    cursor: more ? AD.audit.cursor : undefined, limit: 50 });
  let data;
  try {
    data = await api(`/api/admin/audit${qs}`);
  } catch (err) {
    host.innerHTML = ui.errorBox(err.message, { retry: 'reload-audit' });
    $('#audit-summary').innerHTML = '';
    return;
  }
  AD.audit.items = more ? AD.audit.items.concat(data.items) : data.items;
  AD.audit.cursor = data.next_cursor;
  $('[data-act="audit-more"]').hidden = !data.next_cursor;
  $('#a-export').href = `/api/admin/audit${toQuery({ action: AD.audit.action || undefined, q: AD.audit.q || undefined, user_id: AD.audit.userId || undefined, admin: AD.audit.adminOnly ? 1 : undefined, format: 'csv' })}`;
  paintFilterButtons();
  const n = AD.audit.items.length;
  $('#audit-summary').innerHTML = n
    ? `<span><b>${fmtNumber(n)}</b> ${n === 1 ? 'entry' : 'entries'}${data.next_cursor ? ' so far' : ''}</span>`
    : '';
  host.innerHTML = n
    ? `<div class="adm-audit">${auditList(AD.audit.items)}</div>`
    : ui.emptyState({ icon: 'clock', title: 'No activity recorded',
      body: AD.audit.action || AD.audit.q || AD.audit.userId ? 'Nothing matches these filters.' : 'Actions show up here as people use iSpend.' });
}

async function openActionFilter(anchor) {
  if (!AD.audit.actions) {
    try { AD.audit.actions = await api('/api/admin/audit/actions'); } catch { AD.audit.actions = []; }
  }
  ui.menu(anchor, [{ label: 'All actions', checked: !AD.audit.action, onClick: () => { AD.audit.action = ''; paintFilterButtons(); loadActivity(); } },
    { divider: true },
    ...AD.audit.actions.slice(0, 25).map((a) => ({
      label: `${auditTitle(a.action)} (${fmtNumber(a.n)})`, icon: auditIcon(a.action), checked: AD.audit.action === a.action,
      onClick: () => { AD.audit.action = a.action; paintFilterButtons(); loadActivity(); },
    }))]);
}

/* ---------- Actions ---------- */

/* Exports and backup archives need a recent password; a plain link cannot answer the prompt. */
document.addEventListener('click', (e) => {
  const link = e.target.closest('a[data-admin-download]');
  if (!link || e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey) return;
  e.preventDefault();
  adminDownload(link.href).catch((err) => { if (!err.cancelled) toast(err.message, { type: 'error' }); });
});

async function onAction(e) {
  const el = e.target.closest('[data-act]');
  if (!el) return;
  const act = el.dataset.act;
  const u = AD.users.find((x) => x.id === Number(el.dataset.id));
  switch (act) {
    case 'reload-users': return loadUsers();
    case 'reload-audit': return loadActivity();
    case 'add-user': return openAddUser();
    case 'user-open': return openUserDrawer(Number(el.dataset.id));
    case 'user-menu': return userRowMenu(el, u || { id: Number(el.dataset.id) });
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
    case 'audit-for-user': {
      const wasActivity = location.hash === '#activity';
      AD.audit.userId = Number(el.dataset.id);
      location.hash = '#activity';   // showTab() closes the drawer this link sits in
      if (wasActivity) { paintFilterButtons(); loadActivity(); }   // same hash fires no hashchange
      return undefined;
    }
    case 'audit-more': return ui.busy(el, () => loadActivity({ more: true }));
    case 'clear-audit-user':
      AD.audit.userId = null;
      paintFilterButtons();
      return loadActivity();
    case 'toggle-admin-only':
      AD.audit.adminOnly = !AD.audit.adminOnly;
      setQs({ admin: AD.audit.adminOnly ? '1' : undefined }, { merge: true });
      paintFilterButtons();
      return loadActivity();
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

