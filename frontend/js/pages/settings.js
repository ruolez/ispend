/* Settings page: Accounts, AI (per user), Appearance, Users (admin), My account. */
const TAB_META = {
  accounts: { label: 'Accounts', icon: 'landmark' },
  ai: { label: 'AI', icon: 'sparkles' },
  appearance: { label: 'Appearance', icon: 'sun' },
  users: { label: 'Users', icon: 'users' },
  account: { label: 'My account', icon: 'user' },
};
const ACCOUNT_TYPES = [['checking', 'Checking'], ['savings', 'Savings'], ['credit_card', 'Credit card'], ['line_of_credit', 'Line of credit'], ['loan', 'Loan'], ['investment', 'Investment'], ['cash', 'Cash'], ['other', 'Other']];
const state = { me: null, accounts: [], institutions: [], settings: null, models: null, users: [], dirty: false, aiStatus: null, modelIdx: -1, modelRows: [] };

initNav('settings').then(async (me) => {
  state.me = me;
  $('#me-name').textContent = me.username;
  $$('#settings-nav .nav-item').forEach((a) => {
    const t = TAB_META[a.dataset.tab];
    a.innerHTML = `${icon(t.icon)}<span class="label">${t.label}</span>`;
    if (a.hasAttribute('data-admin') && me.role !== 'admin') a.remove();
  });
  $('[data-act="add-account"]').innerHTML = `${icon('plus')}<span class="label">Add account</span>`;
  $('[data-act="renormalize"]').innerHTML = `${icon('sparkles')}<span class="label">Re-detect merchant names</span>`;
  $('[data-act="learn-history"]').innerHTML = `${icon('book')}<span class="label">Learn from history</span>`;
  $('[data-act="add-user"]').innerHTML = `${icon('plus')}<span class="label">Add user</span>`;
  window.addEventListener('hashchange', showTab);
  showTab();
  document.body.addEventListener('click', onAction);
  window.addEventListener('beforeunload', (e) => { if (state.dirty) { e.preventDefault(); e.returnValue = ''; } });
});

function showTab() {
  if (modelPop) modelPop.close();
  let tab = (location.hash || '#accounts').slice(1);
  if (!TAB_META[tab] || (tab === 'users' && state.me.role !== 'admin')) tab = 'accounts';
  $$('#settings-nav .nav-item').forEach((a) => { if (a.dataset.tab === tab) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current'); });
  $$('.tab-panel').forEach((p) => p.classList.toggle('active', p.dataset.panel === tab));
  setPageTitle(TAB_META[tab].label);
  ({ accounts: loadAccounts, ai: loadAI, appearance: renderAppearance, users: loadUsers, account: renderAccount })[tab]();
}

/* ---------- Accounts ---------- */
async function loadAccounts() {
  const host = $('#accounts-table');
  host.innerHTML = `<div class="tbl-wrap"><table class="tbl"><thead><tr><th>Account</th><th>Institution</th><th>Type</th><th>Currency</th><th class="right">Transactions</th><th>Last import</th><th><span class="sr-only">Actions</span></th></tr></thead><tbody>${ui.skeletonRows(3, 7)}</tbody></table></div>`;
  try {
    [state.accounts, state.institutions] = await Promise.all([api('/api/accounts?all=1'), api('/api/accounts/institutions').catch(() => [])]);
  } catch (err) { host.innerHTML = ui.errorBox(err.message, { retry: 'reload-accounts' }); return; }
  if (!state.accounts.length) {
    host.innerHTML = `<div class="card">${ui.emptyState({ icon: 'landmark', title: 'No accounts yet', body: 'Create an account for each bank or card you import statements from. You can also create one during import.', action: { label: 'Add account', act: 'add-account' } })}</div>`;
    return;
  }
  const instLabel = (k) => (state.institutions.find((i) => i.key === k) || {}).label || k || '—';
  host.innerHTML = `<div class="tbl-wrap"><table class="tbl"><thead><tr><th>Account</th><th>Institution</th><th>Type</th><th>Currency</th><th class="right">Transactions</th><th>Last import</th><th class="col-actions"><span class="sr-only">Actions</span></th></tr></thead><tbody>
    ${state.accounts.map((a) => `<tr data-id="${a.id}" class="${a.is_active ? '' : 'text-3'}">
      <td><span class="acct"><i class="acct-mark" style="--c:var(--${esc(a.color || 'c1')})">${esc(initials(a.name).slice(0, 1))}</i><span class="text-1 fw-500">${esc(a.name)}</span>${a.last4 ? `<span class="text-4 mono">•${esc(a.last4)}</span>` : ''}${a.is_active ? '' : '<span class="badge badge-neutral">Archived</span>'}</span></td>
      <td data-label="Institution">${esc(instLabel(a.institution))}</td>
      <td data-label="Type">${esc((ACCOUNT_TYPES.find((t) => t[0] === a.account_type) || [])[1] || a.account_type)}</td>
      <td data-label="Currency">${esc(a.currency)}</td>
      <td class="right num" data-label="Transactions">${fmtNumber(a.txn_count)}</td>
      <td class="text-3" data-label="Last import">${a.last_import_at ? fmtRelative(a.last_import_at) : '—'}</td>
      <td class="col-actions"><div class="row-actions"><button type="button" class="btn btn-icon btn-ghost btn-xs" data-act="edit-account" data-id="${a.id}" aria-label="Edit">${icon('pencil')}</button><button type="button" class="btn btn-icon btn-ghost btn-xs" data-act="account-menu" data-id="${a.id}" aria-label="More">${icon('more-horizontal')}</button></div></td>
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
      if (!ui.validate(m.el, [{ sel: '#acct-name', message: 'Account name is required' }])) return false;
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
  loadAIStatus();
  try { state.settings = await api('/api/settings'); } catch (err) { host.innerHTML = ui.errorBox(err.message, { retry: 'reload-ai' }); return; }
  const s = state.settings;
  host.innerHTML = `
    <div class="field"><label for="or-key">OpenRouter API key</label>
      <div class="input-group">${icon('key')}<input id="or-key" class="input has-trailing mono" type="password" value="${esc(s.openrouter_api_key || '')}" placeholder="sk-or-v1-…" autocomplete="off" spellcheck="false">
        <button type="button" class="btn btn-icon btn-ghost btn-sm trailing" data-act="toggle-key" aria-label="Show key">${icon('eye')}</button></div>
      <div class="hint">Get a key at openrouter.ai/keys. Stored on the server, masked in responses.</div></div>
    <div class="field"><label for="or-model">Model</label>
      <div class="model-combo"><div class="input-group">${icon('sparkles')}<input id="or-model" class="input has-trailing mono" value="${esc(s.openrouter_model || '')}" placeholder="Choose a model…" autocomplete="off" spellcheck="false" role="combobox" aria-expanded="false" aria-autocomplete="list" aria-controls="or-model-list" aria-haspopup="listbox">
        <button type="button" class="btn btn-icon btn-ghost btn-sm trailing" data-act="open-models" aria-label="Browse models">${icon('chevron-down')}</button></div></div>
      <div class="hint">Type to search the OpenRouter catalog; use ↑ ↓ and Enter to pick. Models that support structured JSON output work best (e.g. anthropic/claude-sonnet-5, google/gemini-3.8-flash).</div></div>
    <div class="row gap-2 mb-4" style="flex-wrap:wrap"><button type="button" class="btn btn-secondary" data-act="test-ai">${icon('play')}Test connection</button><span id="ai-test-result"></span><span class="hint">Tests the key and model typed above, even before saving.</span></div>
    <div class="divider"></div>
    <div class="setting-row"><div><div class="title">Suggest categories after import</div><div class="desc">Unknown charges get an AI-suggested category you confirm in Review.</div></div>
      <label class="switch"><input type="checkbox" id="or-cat" ${s.ai_categorize_enabled === '1' ? 'checked' : ''}><span class="switch-track"></span></label></div>
    <div class="setting-row"><div><div class="title">Monthly insights</div><div class="desc">Generate written observations and recommendations on the Insights page.</div></div>
      <label class="switch"><input type="checkbox" id="or-ins" ${s.ai_insights_enabled === '1' ? 'checked' : ''}><span class="switch-track"></span></label></div>
    ${s.is_admin ? `<div class="setting-row"><div><div class="title">Share this key with all users</div><div class="desc">Publishes your saved key and model as the fallback for every user who has not entered their own. ${s.shared_available ? `Currently shared${s.shared_model ? ` · <span class="mono">${esc(s.shared_model)}</span>` : ''}.` : 'Not shared yet.'}</div></div>
      <label class="switch"><input type="checkbox" id="or-shared" ${s.shared_available ? 'checked' : ''}><span class="switch-track"></span></label></div>` : ''}`;
  if (!s.is_admin && s.shared_available && !s.openrouter_api_key) {
    host.insertAdjacentHTML('afterbegin', `<div class="notice notice-info mb-4">${icon('info')}<div>Using the key shared by your administrator${s.shared_model ? ` (model: <span class="mono">${esc(s.shared_model)}</span>)` : ''}. Enter your own key below to override it.</div></div>`);
  }
  const sharedSw = $('#or-shared');
  if (sharedSw) sharedSw.addEventListener('change', async (e) => {
    const on = e.target.checked;
    if (on && state.dirty) { e.target.checked = false; toast('Save your key and model first, then share them', { type: 'error' }); return; }
    if (on && !(s.openrouter_api_key && s.openrouter_model)) { e.target.checked = false; toast('Enter and save a key and a model before sharing', { type: 'error' }); return; }
    try {
      await api('/api/settings', { method: 'PUT', body: on ? { shared: true } : { clear_shared: true } });
      toast(on ? 'Key shared with all users' : 'Shared key revoked', { type: 'success' });
      store.invalidate('settings'); loadAI();
    } catch (err) { e.target.checked = !on; toast(err.message, { type: 'error' }); }
  });
  ['#or-key', '#or-model', '#or-cat', '#or-ins'].forEach((id) => $(id).addEventListener('input', () => setDirty(true)));
  $('#or-model').addEventListener('focus', () => openModelList());
  $('#or-model').addEventListener('input', debounce(() => openModelList(), 120));
  $('#or-model').addEventListener('keydown', onModelKey);
}

/* ---- AI status card ---- */
async function loadAIStatus() {
  const host = $('#ai-status');
  if (!host) return;
  host.innerHTML = `<div class="card"><div class="card-body">${ui.skeleton('60%', 14)}</div></div>`;
  try { state.aiStatus = await api('/api/ai/status'); } catch (err) { host.innerHTML = ui.errorBox(err.message, { retry: 'ai-refresh' }); return; }
  renderAIStatus();
}
function renderAIStatus() {
  const st = state.aiStatus; const host = $('#ai-status');
  if (!st || !host) return;
  const on = st.enabled || st.insights_enabled;
  const last = st.last_call;
  host.innerHTML = `<div class="card ai-status ${on ? 'is-on' : ''}"><div class="card-body">
    <div class="row-between" style="gap:12px;flex-wrap:wrap">
      <div class="row gap-3"><span class="ai-status-mark">${icon('sparkles')}</span><div>
        <div class="fw-600">${on ? `AI is on · <span class="mono fs-sm">${esc(st.model || '')}</span>` : (st.configured || st.model ? 'AI is configured but switched off' : 'AI is off — add an OpenRouter key below')}</div>
        <div class="text-3 fs-sm">${st.enabled ? 'Category suggestions on import' : 'Suggestions off'} · ${st.insights_enabled ? 'insights on' : 'insights off'}${last ? ` · last call ${fmtRelative(last.created_at)}${last.status === 'error' ? ` <span class="text-danger">(${esc(last.error_message || 'failed')})</span>` : ''}` : ''}</div>
      </div></div>
      <div class="row gap-2"><button type="button" class="btn btn-ghost btn-sm" data-act="ai-refresh" data-tip="Refresh" aria-label="Refresh AI status">${icon('refresh', 'ico-sm')}</button>
        <button type="button" class="btn btn-secondary btn-sm" data-act="ai-suggest" ${st.enabled ? '' : 'disabled data-tip="Turn on suggestions and save first"'}>${icon('sparkles', 'ico-sm')}Suggest categories for uncategorized</button></div>
    </div>
    <div class="ai-stats mt-3"><div><div class="l">Suggestions waiting</div><div class="v num">${fmtNumber(st.pending_suggestions || 0)}</div></div><div><div class="l">Calls today</div><div class="v num">${fmtNumber(st.calls_today || 0)}</div></div><div><div class="l">Tokens today</div><div class="v num">${fmtNumber(st.tokens_today || 0, { compact: true })}</div></div></div>
  </div></div>`;
}
async function suggestUncategorized(btn) {
  await ui.busy(btn, async () => {
    const r = await api('/api/ai/categorize', { method: 'POST', body: { scope: 'uncategorized' } });
    if (r && r.queued != null) toast(`Queued ${fmtNumber(r.queued)} charges for AI suggestions. They appear in Review as they arrive.`, { type: 'success', duration: 7000 });
    else if (r && r.suggested != null) toast(r.suggested ? `${fmtNumber(r.suggested)} suggestion${r.suggested === 1 ? '' : 's'} added — review them in the Review queue.` : 'Nothing to suggest: every charge already has a category.', { type: 'success', duration: 7000, action: r.suggested ? { label: 'Open Review', fn: () => { location.href = '/review.html?mode=merchant'; } } : undefined });
    else toast('Suggestions requested', { type: 'success' });
    window.dispatchEvent(new Event('ispend:transactions-changed'));
    loadAIStatus();
  });
}

/* ---- model combobox keyboard ---- */
function setModelActive(i, { scroll = true } = {}) {
  const input = $('#or-model');
  state.modelIdx = i;
  if (!modelPop) return;
  $$('.model-item', modelPop.el).forEach((el, j) => { el.classList.toggle('is-active', j === i); el.setAttribute('aria-selected', String(j === i)); });
  const active = i >= 0 ? modelPop.el.querySelector(`#mi-${i}`) : null;
  if (active) { input.setAttribute('aria-activedescendant', active.id); if (scroll) active.scrollIntoView({ block: 'nearest' }); }
  else input.removeAttribute('aria-activedescendant');
}
function pickModel(id) {
  const input = $('#or-model');
  input.value = id; setDirty(true);
  if (modelPop) modelPop.close();
  input.focus();
}
function onModelKey(e) {
  const n = state.modelRows.length;
  if (e.key === 'ArrowDown') { e.preventDefault(); if (!modelPop) return openModelList(); setModelActive(n ? (state.modelIdx + 1) % n : -1); }
  else if (e.key === 'ArrowUp') { e.preventDefault(); if (!modelPop) return openModelList(); setModelActive(n ? (state.modelIdx - 1 + n) % n : -1); }
  else if (e.key === 'Enter') { if (modelPop && state.modelIdx >= 0 && state.modelRows[state.modelIdx]) { e.preventDefault(); pickModel(state.modelRows[state.modelIdx].id); } else if (modelPop) { modelPop.close(); } }
  else if (e.key === 'Escape') { if (modelPop) { e.preventDefault(); e.stopPropagation(); modelPop.close(); } }
  else if (e.key === 'Tab') { if (modelPop) modelPop.close(); }
}
let modelPop = null;
async function openModelList() {
  const input = $('#or-model');
  if (!input) return;
  if (!state.models) {
    if (state.modelsLoading) return;
    state.modelsLoading = true;
    try { state.models = await api('/api/settings/openrouter/models'); }
    catch (err) { toast(`Could not load model list: ${err.message}`, { type: 'error' }); return; }
    finally { state.modelsLoading = false; }
  }
  const q = input.value.trim().toLowerCase();
  const rows = state.models.filter((m) => !q || m.id.toLowerCase().includes(q) || (m.name || '').toLowerCase().includes(q)).slice(0, 60);
  state.modelRows = rows;
  const exact = rows.findIndex((m) => m.id.toLowerCase() === q);
  state.modelIdx = exact >= 0 ? exact : (rows.length ? 0 : -1);
  const html = `<div class="menu model-list" role="listbox" id="or-model-list">${rows.length ? rows.map((m, i) => `<div class="model-item ${i === state.modelIdx ? 'is-active' : ''}" role="option" id="mi-${i}" aria-selected="${i === state.modelIdx}" data-id="${esc(m.id)}">
      <div class="name">${esc(m.name)}${m.structured ? '<span class="badge badge-info" title="Supports structured JSON output">JSON</span>' : ''}</div>
      <div class="meta"><span>${esc(m.id)}</span><span>${m.context_length ? fmtNumber(m.context_length, { compact: true }) + ' ctx' : ''}</span><span>${m.prompt_price != null ? `$${m.prompt_price.toFixed(2)} / $${(m.completion_price || 0).toFixed(2)} per 1M` : ''}</span></div>
    </div>`).join('') : '<div class="palette-empty">No models match</div>'}
    <div class="menu-divider"></div><div class="row" style="padding:2px 4px 4px"><span class="hint">${fmtNumber(state.models.length)} models</span><button type="button" class="btn btn-ghost btn-xs ml-auto" data-act="refresh-models">${icon('refresh', 'ico-sm')}Refresh</button></div></div>`;
  if (modelPop) { modelPop.el.innerHTML = html; modelPop.position(); setModelActive(state.modelIdx, { scroll: false }); }
  else {
    const el = document.createElement('div'); el.innerHTML = html; el.style.width = `${input.getBoundingClientRect().width}px`;
    modelPop = ui.popover(input.parentElement, el, { onClose: () => { modelPop = null; input.setAttribute('aria-expanded', 'false'); input.removeAttribute('aria-activedescendant'); } });
    modelPop.onEsc = true;
    input.setAttribute('aria-expanded', 'true');
    setModelActive(state.modelIdx, { scroll: false });
    el.addEventListener('mousedown', (e) => e.preventDefault());
    el.addEventListener('mousemove', (e) => { const it = e.target.closest('.model-item'); if (it) { const i = $$('.model-item', el).indexOf(it); if (i !== state.modelIdx) setModelActive(i, { scroll: false }); } });
    el.addEventListener('click', async (e) => {
      const it = e.target.closest('.model-item');
      if (it) { pickModel(it.dataset.id); return; }
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
  } catch (err) { out.innerHTML = `<span class="chip chip-err" title="${esc(err.message)}">${icon('alert-circle')}${esc(err.message)}</span>`; }
}
async function saveAI() {
  const body = { openrouter_api_key: $('#or-key').value.trim(), openrouter_model: $('#or-model').value.trim(), ai_categorize_enabled: $('#or-cat').checked, ai_insights_enabled: $('#or-ins').checked };
  await api('/api/settings', { method: 'PUT', body });
  store.invalidate('settings');
  setDirty(false);
  toast('AI settings saved', { type: 'success' });
  loadAIStatus();
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
  const prefCur = (state.me.preferences && state.me.preferences.currency) || '';
  const opt = (name, value, title, desc, checked) => `<label class="radio-item ${checked ? 'is-checked' : ''}"><input type="radio" name="${name}" value="${value}" ${checked ? 'checked' : ''}><div><div class="radio-title">${title}</div><div class="radio-desc">${desc}</div></div></label>`;
  host.innerHTML = `
    <div class="label mb-2">Theme</div>
    <div class="radio-list mb-6" id="theme-radios">${opt('theme', 'system', 'System', 'Follow your device setting', mode === 'system')}${opt('theme', 'light', 'Light', 'Bright surfaces, dark text', mode === 'light')}${opt('theme', 'dark', 'Dark', 'Easy on the eyes at night', mode === 'dark')}</div>
    <div class="label mb-2">Density</div>
    <div class="radio-list mb-6" id="density-radios">${opt('density', 'comfortable', 'Comfortable', 'Roomier rows in tables', density !== 'compact')}${opt('density', 'compact', 'Compact', 'More rows on screen', density === 'compact')}</div>
    <div class="label mb-2">Display currency</div>
    <div class="field" style="max-width:320px"><select id="pref-currency" class="select" aria-label="Display currency"><option value="" ${!prefCur ? 'selected' : ''}>Auto (from your accounts)</option><option value="USD" ${prefCur === 'USD' ? 'selected' : ''}>USD · US dollar</option><option value="CAD" ${prefCur === 'CAD' ? 'selected' : ''}>CAD · Canadian dollar</option></select><div class="hint">Used for dashboard, report and insight totals. Each transaction always shows its account's currency.</div></div>`;
  host.querySelector('#theme-radios').addEventListener('change', (e) => { setThemePref(e.target.value); paintRadios(host.querySelector('#theme-radios')); });
  host.querySelector('#density-radios').addEventListener('change', (e) => { Theme.setDensity(e.target.value); api('/api/auth/me/preferences', { method: 'PUT', body: { density: e.target.value } }).catch(() => {}); paintRadios(host.querySelector('#density-radios')); });
  host.querySelector('#pref-currency').addEventListener('change', async (e) => {
    try {
      const prefs = await api('/api/auth/me/preferences', { method: 'PUT', body: { currency: e.target.value } });
      state.me.preferences = prefs; if (window.currentUser) window.currentUser.preferences = prefs;
      toast(e.target.value ? `Totals now shown in ${e.target.value}` : 'Display currency follows your accounts', { type: 'success' });
    } catch (err) { toast(err.message, { type: 'error' }); }
  });
}
function paintRadios(group) { $$('.radio-item', group).forEach((l) => l.classList.toggle('is-checked', l.querySelector('input').checked)); }

/* ---------- Users ---------- */
async function loadUsers() {
  const host = $('#users-table');
  host.innerHTML = `<div class="tbl-wrap"><table class="tbl"><thead><tr><th>User</th><th>Role</th><th>Status</th><th class="right">Accounts</th><th class="right">Transactions</th><th>Created</th><th><span class="sr-only">Actions</span></th></tr></thead><tbody>${ui.skeletonRows(3, 7)}</tbody></table></div>`;
  try { state.users = await api('/api/users'); } catch (err) { host.innerHTML = ui.errorBox(err.message, { retry: 'reload-users' }); return; }
  host.innerHTML = `<div class="tbl-wrap"><table class="tbl"><thead><tr><th>User</th><th>Role</th><th>Status</th><th class="right">Accounts</th><th class="right">Transactions</th><th>Created</th><th class="col-actions"><span class="sr-only">Actions</span></th></tr></thead><tbody>
    ${state.users.map((u) => `<tr data-id="${u.id}">
      <td><span class="row gap-2"><span class="avatar">${esc(initials(u.username))}</span><span class="fw-500">${esc(u.username)}</span>${u.id === state.me.id ? '<span class="badge badge-accent">You</span>' : ''}</span></td>
      <td data-label="Role"><span class="badge ${u.role === 'admin' ? 'badge-info' : 'badge-neutral'}">${esc(u.role)}</span></td>
      <td data-label="Status"><span class="user-status"><i class="dot" style="--c:var(--${u.is_active ? 'success' : 'text-4'})"></i>${u.is_active ? 'Active' : 'Deactivated'}</span></td>
      <td class="right num" data-label="Accounts">${fmtNumber(u.account_count)}</td><td class="right num" data-label="Transactions">${fmtNumber(u.txn_count)}</td>
      <td class="text-3" data-label="Created">${fmtDate(u.created_at, { year: true })}</td>
      <td class="col-actions"><div class="row-actions"><button type="button" class="btn btn-icon btn-ghost btn-xs" data-act="user-menu" data-id="${u.id}" aria-label="More">${icon('more-horizontal')}</button></div></td>
    </tr>`).join('')}</tbody></table></div>`;
}
function openUserModal() {
  const m = ui.modal({
    title: 'Add user',
    html: `<form id="user-form">
      <div class="field"><label for="u-name">Username</label><input id="u-name" class="input" autocomplete="off" required autofocus spellcheck="false"></div>
      <div class="field"><label for="u-pass">Password</label><input id="u-pass" class="input" type="password" minlength="10" autocomplete="new-password" required><div class="hint">At least 10 characters. The user can change it later.</div></div>
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
      const m = ui.modal({ title: `Reset password for ${u.username}`, html: `<div class="field"><label for="rp">New password</label><input id="rp" class="input" type="password" minlength="10" autofocus></div>`,
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
      <div class="field"><label for="me-new">New password</label><input id="me-new" class="input" type="password" autocomplete="new-password" minlength="10" required></div>
      <div class="field"><label for="me-conf">Confirm new password</label><input id="me-conf" class="input" type="password" autocomplete="new-password" required></div>
      <button type="submit" class="btn btn-primary">Update password</button>
    </form>`;
  $('#me-pw-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const btn = e.target.querySelector('button[type=submit]');
    if (!ui.validate(e.target, [{ sel: '#me-conf', test: (v) => v === $('#me-new').value || 'New passwords do not match' }])) return;
    await ui.busy(btn, async () => { await api('/api/auth/me/password', { method: 'PUT', body: { current_password: $('#me-cur').value, password: $('#me-new').value } }); toast('Password updated', { type: 'success' }); e.target.reset(); });
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
      { label: acct.is_active ? 'Archive' : 'Restore', icon: acct.is_active ? 'inbox' : 'rotate-ccw', onClick: async () => { await api(`/api/accounts/${acct.id}`, { method: 'PUT', body: { is_active: !acct.is_active } }); store.invalidate('accounts'); toast(acct.is_active ? 'Account archived' : 'Account restored', { type: 'success' }); loadAccounts(); } },
      { label: 'Delete', icon: 'trash', danger: true, onClick: async () => {
        const body = acct.txn_count ? `This deletes the account and its ${plural(acct.txn_count, 'transaction')}. This cannot be undone.` : 'This account has no transactions.';
        if (!(await ui.confirm({ title: `Delete ${acct.name}?`, body, confirmText: 'Delete', danger: true }))) return;
        await api(`/api/accounts/${acct.id}?force=true`, { method: 'DELETE' }); store.invalidate('accounts'); toast('Account deleted'); loadAccounts();
      } },
    ]);
    case 'reload-accounts': return loadAccounts();
    case 'learn-history': {
      await ui.busy(el, async () => {
        const r = await api('/api/merchants/learn', { method: 'POST', body: {} });
        toast(`${fmtNumber(r.merchants_seen)} merchants reviewed · ${fmtNumber(r.memory_added)} newly remembered`, { type: 'success', duration: 7000 });
      });
      return;
    }
    case 'renormalize': {
      if (!(await ui.confirm({ title: 'Re-detect merchant names?', body: 'Merchant names on all your transactions will be recomputed. Rules and remembered merchants are updated to match, so nothing stops working. This may take a few seconds.', confirmText: 'Re-detect' }))) return;
      await ui.busy(el, async () => {
        const r = await api('/api/merchants/renormalize', { method: 'POST', body: {} });
        toast(`${fmtNumber(r.transactions_updated)} transactions renamed, ${fmtNumber(r.rules_updated)} rules updated${r.stripped_phrases && r.stripped_phrases.length ? ` · stripped “${r.stripped_phrases.join('”, “')}”` : ''}`, { type: 'success', duration: 8000 });
        window.dispatchEvent(new Event('ispend:transactions-changed'));
      });
      return;
    }
    case 'reload-ai': return loadAI();
    case 'ai-refresh': return loadAIStatus();
    case 'ai-suggest': return suggestUncategorized(el);
    case 'reload-users': return loadUsers();
    case 'toggle-key': { const i = $('#or-key'); i.type = i.type === 'password' ? 'text' : 'password'; el.innerHTML = icon(i.type === 'password' ? 'eye' : 'eye-off'); el.setAttribute('aria-label', i.type === 'password' ? 'Show key' : 'Hide key'); return; }
    case 'open-models': return $('#or-model').focus();
    case 'test-ai': return testAI();
    case 'save-ai': await ui.busy(el, saveAI); return;
    case 'discard-ai': setDirty(false); return loadAI();
    case 'add-user': return openUserModal();
    case 'user-menu': return openUserMenuFor(el, user);
    default: return null;
  }
}
