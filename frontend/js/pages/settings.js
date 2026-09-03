/* Settings page: Accounts, AI (admin), Appearance, Users (admin), My account. */
const TAB_META = {
  accounts: { label: 'Accounts', icon: 'landmark' },
  ai: { label: 'AI', icon: 'sparkles' },
  appearance: { label: 'Appearance', icon: 'sun' },
  users: { label: 'Users', icon: 'users' },
  account: { label: 'My account', icon: 'user' },
};
const ACCOUNT_TYPES = [['checking', 'Checking'], ['savings', 'Savings'], ['credit_card', 'Credit card'], ['line_of_credit', 'Line of credit'], ['loan', 'Loan'], ['investment', 'Investment'], ['cash', 'Cash'], ['other', 'Other']];
const MASK = '••••••••';
const state = { me: null, accounts: [], institutions: [], settings: null, models: null, users: [], dirty: false };

initNav('settings').then(async (me) => {
  state.me = me;
  $('#me-name').textContent = me.username;
  $$('#settings-nav .nav-item').forEach((a) => {
    const t = TAB_META[a.dataset.tab];
    a.innerHTML = `${icon(t.icon)}<span class="label">${t.label}</span>`;
    if (a.hasAttribute('data-admin') && me.role !== 'admin') a.remove();
  });
  $('[data-act="add-account"]').innerHTML = `${icon('plus')}<span class="label">Add account</span>`;
  $('[data-act="add-user"]').innerHTML = `${icon('plus')}<span class="label">Add user</span>`;
  window.addEventListener('hashchange', showTab);
  showTab();
  document.body.addEventListener('click', onAction);
  window.addEventListener('beforeunload', (e) => { if (state.dirty) { e.preventDefault(); e.returnValue = ''; } });
});

function showTab() {
  if (modelPop) modelPop.close();
  let tab = (location.hash || '#accounts').slice(1);
  if (!TAB_META[tab] || (['ai', 'users'].includes(tab) && state.me.role !== 'admin')) tab = 'accounts';
  $$('#settings-nav .nav-item').forEach((a) => { if (a.dataset.tab === tab) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current'); });
  $$('.tab-panel').forEach((p) => p.classList.toggle('active', p.dataset.panel === tab));
  ({ accounts: loadAccounts, ai: loadAI, appearance: renderAppearance, users: loadUsers, account: renderAccount })[tab]();
}

/* ---------- Accounts ---------- */
async function loadAccounts() {
  const host = $('#accounts-table');
  host.innerHTML = `<div class="tbl-wrap"><table class="tbl"><thead><tr><th>Account</th><th>Institution</th><th>Type</th><th>Currency</th><th class="right">Transactions</th><th>Last import</th><th></th></tr></thead><tbody>${ui.skeletonRows(3, 7)}</tbody></table></div>`;
  try {
    [state.accounts, state.institutions] = await Promise.all([api('/api/accounts?all=1'), api('/api/accounts/institutions').catch(() => [])]);
  } catch (err) { host.innerHTML = ui.errorBox(err.message, { retry: 'reload-accounts' }); return; }
  if (!state.accounts.length) {
    host.innerHTML = `<div class="card">${ui.emptyState({ icon: 'landmark', title: 'No accounts yet', body: 'Create an account for each bank or card you import statements from. You can also create one during import.', action: { label: 'Add account', act: 'add-account' } })}</div>`;
    return;
  }
  const instLabel = (k) => (state.institutions.find((i) => i.key === k) || {}).label || k || '—';
  host.innerHTML = `<div class="tbl-wrap"><table class="tbl"><thead><tr><th>Account</th><th>Institution</th><th>Type</th><th>Currency</th><th class="right">Transactions</th><th>Last import</th><th class="col-actions"></th></tr></thead><tbody>
    ${state.accounts.map((a) => `<tr data-id="${a.id}" class="${a.is_active ? '' : 'text-3'}">
      <td><span class="acct"><i class="acct-mark" style="--c:var(--${esc(a.color || 'c1')})">${esc(initials(a.name).slice(0, 1))}</i><span class="text-1 fw-500">${esc(a.name)}</span>${a.last4 ? `<span class="text-4 mono">•${esc(a.last4)}</span>` : ''}${a.is_active ? '' : '<span class="badge badge-neutral">Archived</span>'}</span></td>
      <td>${esc(instLabel(a.institution))}</td>
      <td>${esc((ACCOUNT_TYPES.find((t) => t[0] === a.account_type) || [])[1] || a.account_type)}</td>
      <td>${esc(a.currency)}</td>
      <td class="right num">${fmtNumber(a.txn_count)}</td>
      <td class="text-3">${a.last_import_at ? fmtRelative(a.last_import_at) : '—'}</td>
      <td class="col-actions"><div class="row-actions"><button type="button" class="btn btn-icon btn-ghost btn-xs" data-act="edit-account" data-id="${a.id}" title="Edit">${icon('pencil')}</button><button type="button" class="btn btn-icon btn-ghost btn-xs" data-act="account-menu" data-id="${a.id}" title="More">${icon('more-horizontal')}</button></div></td>
    </tr>`).join('')}</tbody></table></div>`;
}

function accountForm(a = {}) {
  const inst = state.institutions;
  const known = inst.some((i) => i.key === a.institution);
  return `<form id="acct-form">
    <div class="field"><label for="acct-name">Account name</label><input id="acct-name" class="input" value="${esc(a.name || '')}" placeholder="e.g. Chase Sapphire" required autofocus></div>
    <div class="field-row">
      <div class="field"><label for="acct-type">Type</label><select id="acct-type" class="select">${ACCOUNT_TYPES.map(([v, l]) => `<option value="${v}" ${a.account_type === v ? 'selected' : ''}>${l}</option>`).join('')}</select></div>
      <div class="field"><label for="acct-cur">Currency</label><select id="acct-cur" class="select"><option value="USD" ${a.currency === 'USD' ? 'selected' : ''}>USD</option><option value="CAD" ${a.currency === 'CAD' ? 'selected' : ''}>CAD</option></select></div>
    </div>
    <div class="field-row">
      <div class="field"><label for="acct-inst">Institution</label><select id="acct-inst" class="select"><option value="">— Not set —</option>${inst.map((i) => `<option value="${esc(i.key)}" ${a.institution === i.key ? 'selected' : ''}>${esc(i.label)}</option>`).join('')}<option value="__other" ${a.institution && !known ? 'selected' : ''}>Other…</option></select></div>
      <div class="field"><label for="acct-last4">Last 4 digits</label><input id="acct-last4" class="input mono" maxlength="4" inputmode="numeric" value="${esc(a.last4 || '')}" placeholder="1234"></div>
    </div>
    <div class="field" id="acct-inst-other" ${a.institution && !known ? '' : 'hidden'}><label for="acct-inst-text">Institution name</label><input id="acct-inst-text" class="input" value="${esc(!known ? (a.institution || '') : '')}"></div>
    <div class="field"><label>Color</label><div class="swatches" id="acct-colors">${Array.from({ length: 12 }, (_, i) => `c${i + 1}`).map((c) => `<button type="button" class="swatch ${(a.color || 'c1') === c ? 'active' : ''}" data-color="${c}" style="--c:var(--${c})" aria-label="${c}"></button>`).join('')}</div></div>
    <button type="submit" hidden></button></form>`;
}
function readAccountForm(el) {
  let institution = el.querySelector('#acct-inst').value;
  if (institution === '__other') institution = el.querySelector('#acct-inst-text').value.trim();
  return {
    name: el.querySelector('#acct-name').value.trim(), account_type: el.querySelector('#acct-type').value, currency: el.querySelector('#acct-cur').value,
    institution: institution || null, last4: el.querySelector('#acct-last4').value.trim(), color: (el.querySelector('.swatch.active') || {}).dataset?.color || 'c1',
  };
}
function wireAccountForm(m) {
  m.el.querySelector('#acct-inst').addEventListener('change', (e) => { m.el.querySelector('#acct-inst-other').hidden = e.target.value !== '__other'; });
  m.el.querySelector('#acct-colors').addEventListener('click', (e) => { const b = e.target.closest('.swatch'); if (!b) return; $$('.swatch', m.el).forEach((s) => s.classList.remove('active')); b.classList.add('active'); });
  m.el.querySelector('#acct-form').addEventListener('submit', (e) => { e.preventDefault(); m.el.querySelector('.modal-foot .btn-primary').click(); });
}
function openAccountModal(a) {
  const m = ui.modal({
    title: a ? 'Edit account' : 'Add account', html: accountForm(a || {}),
    actions: [{ label: 'Cancel' }, { label: a ? 'Save' : 'Create account', primary: true, onClick: async () => {
      const body = readAccountForm(m.el);
      if (!body.name) throw new Error('Account name is required');
      if (a) await api(`/api/accounts/${a.id}`, { method: 'PUT', body }); else await api('/api/accounts', { method: 'POST', body });
      store.invalidate('accounts');
      toast(a ? 'Account saved' : 'Account created', { type: 'success' });
      loadAccounts();
    } }],
  });
  wireAccountForm(m);
}

/* ---------- AI ---------- */
async function loadAI() {
  const host = $('#ai-panel');
  host.innerHTML = `<div class="col gap-3">${ui.skeleton('40%', 14)}${ui.skeleton('100%', 36)}${ui.skeleton('60%', 14)}${ui.skeleton('100%', 36)}</div>`;
  try { state.settings = await api('/api/settings'); } catch (err) { host.innerHTML = ui.errorBox(err.message, { retry: 'reload-ai' }); return; }
  const s = state.settings;
  host.innerHTML = `
    <div class="field"><label for="or-key">OpenRouter API key</label>
      <div class="input-group">${icon('key')}<input id="or-key" class="input has-trailing mono" type="password" value="${esc(s.openrouter_api_key || '')}" placeholder="sk-or-v1-…" autocomplete="off" spellcheck="false">
        <button type="button" class="btn btn-icon btn-ghost btn-sm trailing" data-act="toggle-key" aria-label="Show key">${icon('eye')}</button></div>
      <div class="hint">Get a key at openrouter.ai/keys. Stored on the server, masked in responses.</div></div>
    <div class="field"><label for="or-model">Model</label>
      <div class="model-combo"><div class="input-group">${icon('sparkles')}<input id="or-model" class="input has-trailing mono" value="${esc(s.openrouter_model || '')}" placeholder="Choose a model…" autocomplete="off" spellcheck="false" role="combobox" aria-expanded="false">
        <button type="button" class="btn btn-icon btn-ghost btn-sm trailing" data-act="open-models" aria-label="Browse models">${icon('chevron-down')}</button></div></div>
      <div class="hint">Type to search the OpenRouter catalog. Models that support structured JSON output work best (e.g. anthropic/claude-sonnet-5, google/gemini-3.8-flash).</div></div>
    <div class="row gap-2 mb-4"><button type="button" class="btn btn-secondary" data-act="test-ai">${icon('play')}Test connection</button><span id="ai-test-result"></span></div>
    <div class="divider"></div>
    <div class="setting-row"><div><div class="title">Suggest categories after import</div><div class="desc">Unknown charges get an AI-suggested category you confirm in Review.</div></div>
      <label class="switch"><input type="checkbox" id="or-cat" ${s.ai_categorize_enabled === '1' ? 'checked' : ''}><span class="switch-track"></span></label></div>
    <div class="setting-row"><div><div class="title">Monthly insights</div><div class="desc">Generate written observations and recommendations on the Insights page.</div></div>
      <label class="switch"><input type="checkbox" id="or-ins" ${s.ai_insights_enabled === '1' ? 'checked' : ''}><span class="switch-track"></span></label></div>`;
  ['#or-key', '#or-model', '#or-cat', '#or-ins'].forEach((id) => $(id).addEventListener('input', () => setDirty(true)));
  $('#or-model').addEventListener('focus', () => openModelList());
  $('#or-model').addEventListener('input', debounce(() => openModelList(), 120));
}
let modelPop = null;
async function openModelList() {
  const input = $('#or-model');
  if (!input) return;
  if (!state.models) {
    state.models = [];
    try { state.models = await api('/api/settings/openrouter/models'); } catch (err) { toast(`Could not load model list: ${err.message}`, { type: 'error' }); return; }
  }
  const q = input.value.trim().toLowerCase();
  const rows = state.models.filter((m) => !q || m.id.toLowerCase().includes(q) || (m.name || '').toLowerCase().includes(q)).slice(0, 60);
  const html = `<div class="menu model-list" role="listbox">${rows.length ? rows.map((m) => `<div class="model-item" role="option" data-id="${esc(m.id)}">
      <div class="name">${esc(m.name)}${m.structured ? '<span class="badge badge-info" title="Supports structured JSON output">JSON</span>' : ''}</div>
      <div class="meta"><span>${esc(m.id)}</span><span>${m.context_length ? fmtNumber(m.context_length, { compact: true }) + ' ctx' : ''}</span><span>${m.prompt_price != null ? `$${m.prompt_price.toFixed(2)} / $${(m.completion_price || 0).toFixed(2)} per 1M` : ''}</span></div>
    </div>`).join('') : '<div class="palette-empty">No models match</div>'}
    <div class="menu-divider"></div><div class="row" style="padding:2px 4px 4px"><span class="hint">${fmtNumber(state.models.length)} models</span><button type="button" class="btn btn-ghost btn-xs ml-auto" data-act="refresh-models">${icon('refresh', 'ico-sm')}Refresh</button></div></div>`;
  if (modelPop) { modelPop.el.innerHTML = html; modelPop.position(); }
  else {
    const el = document.createElement('div'); el.innerHTML = html; el.style.width = `${input.getBoundingClientRect().width}px`;
    modelPop = ui.popover(input.parentElement, el, { onClose: () => { modelPop = null; input.setAttribute('aria-expanded', 'false'); } });
    modelPop.onEsc = true;
    input.setAttribute('aria-expanded', 'true');
    el.addEventListener('mousedown', (e) => e.preventDefault());
    el.addEventListener('click', async (e) => {
      const it = e.target.closest('.model-item');
      if (it) { input.value = it.dataset.id; setDirty(true); modelPop.close(); return; }
      if (e.target.closest('[data-act="refresh-models"]')) { state.models = null; try { state.models = await api('/api/settings/openrouter/models?refresh=1'); } catch (err) { toast(err.message, { type: 'error' }); } openModelList(); }
    });
  }
}
async function testAI() {
  const out = $('#ai-test-result');
  out.innerHTML = '<span class="spinner"></span>';
  try {
    const r = await api('/api/settings/openrouter/test', { method: 'POST', body: { api_key: $('#or-key').value, model: $('#or-model').value.trim() } });
    out.innerHTML = `<span class="chip chip-ok">${icon('check')}Connected · ${fmtNumber(r.latency_ms)} ms</span>`;
  } catch (err) { out.innerHTML = `<span class="chip chip-err">${icon('alert-circle')}${esc(err.message)}</span>`; }
}
async function saveAI() {
  const body = { openrouter_api_key: $('#or-key').value.trim(), openrouter_model: $('#or-model').value.trim(), ai_categorize_enabled: $('#or-cat').checked, ai_insights_enabled: $('#or-ins').checked };
  await api('/api/settings', { method: 'PUT', body });
  store.invalidate('settings');
  setDirty(false);
  toast('AI settings saved', { type: 'success' });
}
let savebar = null;
function setDirty(v) {
  state.dirty = v;
  if (v && !savebar) {
    savebar = document.createElement('div'); savebar.className = 'floatbar'; savebar.setAttribute('role', 'status');
    savebar.innerHTML = `<span>Unsaved changes</span><span class="sep"></span><button type="button" class="btn btn-ghost btn-sm" data-act="discard-ai">Discard</button><button type="button" class="btn btn-primary btn-sm" data-act="save-ai">Save</button>`;
    document.body.appendChild(savebar);
  } else if (!v && savebar) { savebar.remove(); savebar = null; }
}

/* ---------- Appearance ---------- */
function renderAppearance() {
  const host = $('#appearance-panel');
  const mode = Theme.get(), density = Theme.density();
  const opt = (name, value, title, desc, checked) => `<label class="radio-item ${checked ? 'is-checked' : ''}"><input type="radio" name="${name}" value="${value}" ${checked ? 'checked' : ''}><div><div class="radio-title">${title}</div><div class="radio-desc">${desc}</div></div></label>`;
  host.innerHTML = `
    <div class="label mb-2">Theme</div>
    <div class="radio-list mb-6" id="theme-radios">${opt('theme', 'system', 'System', 'Follow your device setting', mode === 'system')}${opt('theme', 'light', 'Light', 'Bright surfaces, dark text', mode === 'light')}${opt('theme', 'dark', 'Dark', 'Easy on the eyes at night', mode === 'dark')}</div>
    <div class="label mb-2">Density</div>
    <div class="radio-list" id="density-radios">${opt('density', 'comfortable', 'Comfortable', 'Roomier rows in tables', density !== 'compact')}${opt('density', 'compact', 'Compact', 'More rows on screen', density === 'compact')}</div>`;
  host.querySelector('#theme-radios').addEventListener('change', (e) => { setThemePref(e.target.value); paintRadios(host.querySelector('#theme-radios')); });
  host.querySelector('#density-radios').addEventListener('change', (e) => { Theme.setDensity(e.target.value); api('/api/auth/me/preferences', { method: 'PUT', body: { density: e.target.value } }).catch(() => {}); paintRadios(host.querySelector('#density-radios')); });
}
function paintRadios(group) { $$('.radio-item', group).forEach((l) => l.classList.toggle('is-checked', l.querySelector('input').checked)); }

/* ---------- Users ---------- */
async function loadUsers() {
  const host = $('#users-table');
  host.innerHTML = `<div class="tbl-wrap"><table class="tbl"><thead><tr><th>User</th><th>Role</th><th>Status</th><th class="right">Accounts</th><th class="right">Transactions</th><th>Created</th><th></th></tr></thead><tbody>${ui.skeletonRows(3, 7)}</tbody></table></div>`;
  try { state.users = await api('/api/users'); } catch (err) { host.innerHTML = ui.errorBox(err.message, { retry: 'reload-users' }); return; }
  host.innerHTML = `<div class="tbl-wrap"><table class="tbl"><thead><tr><th>User</th><th>Role</th><th>Status</th><th class="right">Accounts</th><th class="right">Transactions</th><th>Created</th><th class="col-actions"></th></tr></thead><tbody>
    ${state.users.map((u) => `<tr data-id="${u.id}">
      <td><span class="row gap-2"><span class="avatar">${esc(initials(u.username))}</span><span class="fw-500">${esc(u.username)}</span>${u.id === state.me.id ? '<span class="badge badge-accent">You</span>' : ''}</span></td>
      <td><span class="badge ${u.role === 'admin' ? 'badge-info' : 'badge-neutral'}">${esc(u.role)}</span></td>
      <td><span class="user-status"><i class="dot" style="--c:var(--${u.is_active ? 'success' : 'text-4'})"></i>${u.is_active ? 'Active' : 'Deactivated'}</span></td>
      <td class="right num">${fmtNumber(u.account_count)}</td><td class="right num">${fmtNumber(u.txn_count)}</td>
      <td class="text-3">${fmtDate(u.created_at, { year: true })}</td>
      <td class="col-actions"><div class="row-actions"><button type="button" class="btn btn-icon btn-ghost btn-xs" data-act="user-menu" data-id="${u.id}" title="More">${icon('more-horizontal')}</button></div></td>
    </tr>`).join('')}</tbody></table></div>`;
}
function openUserModal() {
  const m = ui.modal({
    title: 'Add user',
    html: `<form id="user-form">
      <div class="field"><label for="u-name">Username</label><input id="u-name" class="input" autocomplete="off" required autofocus spellcheck="false"></div>
      <div class="field"><label for="u-pass">Password</label><input id="u-pass" class="input" type="password" minlength="6" autocomplete="new-password" required><div class="hint">At least 6 characters. The user can change it later.</div></div>
      <div class="field"><label for="u-role">Role</label><select id="u-role" class="select"><option value="user">User</option><option value="admin">Admin</option></select></div>
      <button type="submit" hidden></button></form>`,
    actions: [{ label: 'Cancel' }, { label: 'Create user', primary: true, onClick: async () => {
      await api('/api/users', { method: 'POST', body: { username: $('#u-name').value.trim(), password: $('#u-pass').value, role: $('#u-role').value } });
      toast('User created', { type: 'success' }); loadUsers();
    } }],
  });
  m.el.querySelector('#user-form').addEventListener('submit', (e) => { e.preventDefault(); m.el.querySelector('.modal-foot .btn-primary').click(); });
}
function openUserMenuFor(anchor, u) {
  const isMe = u.id === state.me.id;
  ui.menu(anchor, [
    { label: u.role === 'admin' ? 'Make regular user' : 'Make admin', icon: 'shield', disabled: isMe, onClick: async () => { await api(`/api/users/${u.id}`, { method: 'PUT', body: { role: u.role === 'admin' ? 'user' : 'admin' } }); loadUsers(); } },
    { label: 'Reset password', icon: 'lock', onClick: () => {
      const m = ui.modal({ title: `Reset password for ${u.username}`, html: `<div class="field"><label for="rp">New password</label><input id="rp" class="input" type="password" minlength="6" autofocus></div>`,
        actions: [{ label: 'Cancel' }, { label: 'Reset', primary: true, onClick: async () => { await api(`/api/users/${u.id}/password`, { method: 'PUT', body: { password: m.el.querySelector('#rp').value } }); toast('Password reset', { type: 'success' }); } }] });
    } },
    { divider: true },
    { label: u.is_active ? 'Deactivate' : 'Activate', icon: u.is_active ? 'zap-off' : 'zap', disabled: isMe, onClick: async () => { await api(`/api/users/${u.id}`, { method: 'PUT', body: { is_active: !u.is_active } }); loadUsers(); } },
    { label: 'Delete permanently', icon: 'trash', danger: true, disabled: isMe, onClick: async () => {
      if (!(await ui.confirm({ title: `Delete ${u.username}?`, body: `This permanently deletes the user and all ${fmtNumber(u.txn_count)} of their transactions, accounts, categories and rules.`, confirmText: 'Delete user', danger: true }))) return;
      await api(`/api/users/${u.id}?permanent=true`, { method: 'DELETE' }); toast('User deleted'); loadUsers();
    } },
  ]);
}

/* ---------- My account ---------- */
function renderAccount() {
  $('#account-panel').innerHTML = `
    <form id="me-pw-form" style="max-width:380px">
      <div class="field"><label for="me-cur">Current password</label><input id="me-cur" class="input" type="password" autocomplete="current-password" required></div>
      <div class="field"><label for="me-new">New password</label><input id="me-new" class="input" type="password" autocomplete="new-password" minlength="6" required></div>
      <div class="field"><label for="me-conf">Confirm new password</label><input id="me-conf" class="input" type="password" autocomplete="new-password" required></div>
      <button type="submit" class="btn btn-primary">Update password</button>
    </form>`;
  $('#me-pw-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const btn = e.target.querySelector('button[type=submit]');
    if ($('#me-new').value !== $('#me-conf').value) return toast('New passwords do not match', { type: 'error' });
    btn.classList.add('is-loading');
    try { await api('/api/auth/me/password', { method: 'PUT', body: { current_password: $('#me-cur').value, password: $('#me-new').value } }); toast('Password updated', { type: 'success' }); e.target.reset(); }
    catch (err) { toast(err.message, { type: 'error' }); }
    finally { btn.classList.remove('is-loading'); }
  });
}

/* ---------- Delegated actions ---------- */
async function onAction(e) {
  const el = e.target.closest('[data-act]');
  if (!el) return;
  const act = el.dataset.act, id = Number(el.dataset.id);
  const acct = state.accounts.find((a) => a.id === id);
  const user = state.users.find((u) => u.id === id);
  switch (act) {
    case 'add-account': return openAccountModal(null);
    case 'edit-account': return openAccountModal(acct);
    case 'account-menu': return ui.menu(el, [
      { label: 'Edit', icon: 'pencil', onClick: () => openAccountModal(acct) },
      { label: 'View transactions', icon: 'list', href: `/transactions.html?acct=${acct.id}` },
      { divider: true },
      { label: acct.is_active ? 'Archive' : 'Restore', icon: acct.is_active ? 'inbox' : 'rotate-ccw', onClick: async () => { await api(`/api/accounts/${acct.id}`, { method: 'PUT', body: { is_active: !acct.is_active } }); store.invalidate('accounts'); loadAccounts(); } },
      { label: 'Delete', icon: 'trash', danger: true, onClick: async () => {
        const body = acct.txn_count ? `This deletes the account and its ${fmtNumber(acct.txn_count)} transactions. This cannot be undone.` : 'This account has no transactions.';
        if (!(await ui.confirm({ title: `Delete ${acct.name}?`, body, confirmText: 'Delete', danger: true }))) return;
        await api(`/api/accounts/${acct.id}?force=true`, { method: 'DELETE' }); store.invalidate('accounts'); toast('Account deleted'); loadAccounts();
      } },
    ]);
    case 'reload-accounts': return loadAccounts();
    case 'reload-ai': return loadAI();
    case 'reload-users': return loadUsers();
    case 'toggle-key': { const i = $('#or-key'); i.type = i.type === 'password' ? 'text' : 'password'; el.innerHTML = icon(i.type === 'password' ? 'eye' : 'eye-off'); return; }
    case 'open-models': return $('#or-model').focus();
    case 'test-ai': return testAI();
    case 'save-ai': el.classList.add('is-loading'); try { await saveAI(); } catch (err) { toast(err.message, { type: 'error' }); } finally { el.classList.remove('is-loading'); } return;
    case 'discard-ai': setDirty(false); return loadAI();
    case 'add-user': return openUserModal();
    case 'user-menu': return openUserMenuFor(el, user);
    default: return null;
  }
}
