/* Rules: ordered list with inline editing, drag/keyboard reorder, test drawer, new-rule modal with live preview. */
const MATCH_TYPES = [['contains', 'contains'], ['starts_with', 'starts with'], ['equals', 'equals'], ['regex', 'matches regex']];
const MATCH_FIELDS = [['description_clean', 'Description'], ['description_raw', 'Raw description'], ['merchant_key', 'Merchant']];
const word = (n, one, many) => (Number(n) === 1 ? one : (many || one + 's'));
const state = { rules: [], cats: new Map(), accounts: [], q: '', dragId: null, currency: 'USD', filterCat: null, view: 'rules', merchants: [], mq: '', mloading: false };

initNav('rules').then(async (me) => {
  state.currency = await store.displayCurrency();
  $('.rules-search .ico-wrap').innerHTML = icon('search');
  $('[data-act="new-rule"]').innerHTML = `${icon('plus')}<span>New rule</span>`;
  $('[data-act="run-all"]').innerHTML = `${icon('play')}<span>Run all rules</span>`;
  document.body.addEventListener('click', onAction);
  const list = $('#rule-list');
  list.addEventListener('change', onInlineChange);
  list.addEventListener('keydown', onListKey);
  list.addEventListener('focusout', (e) => { if (e.target.matches('.rule-pattern')) commitPattern(e.target); });
  $('#rule-search').addEventListener('input', debounce((e) => {
    if (state.view === 'merchants') { state.mq = e.target.value.trim(); loadMerchants(); return; }
    state.q = e.target.value.trim().toLowerCase(); render();
  }, 200));
  wireDrag();
  const q = qs();
  if (q.tab === 'merchants') state.view = 'merchants';
  $('#view-seg').addEventListener('click', (e) => { const b = e.target.closest('[data-view]'); if (!b || b.dataset.view === state.view) return; state.view = b.dataset.view; setQs({ tab: state.view === 'merchants' ? 'merchants' : null }, { replace: true }); paintView(); if (state.view === 'merchants') loadMerchants(); });
  const mb = $('#merch-body');
  mb.addEventListener('click', onMerchantClick);
  mb.addEventListener('keydown', onMerchantKey);
  mb.addEventListener('focusout', (e) => { if (e.target.matches('.merch-name-input')) commitMerchantName(e.target); });
  paintView();
  if (q.filter_cat) state.filterCat = Number(q.filter_cat) || null;
  document.body.addEventListener('click', (e) => { if (e.target.closest('[data-act="clear-cat-filter"]')) { state.filterCat = null; setQs({ filter_cat: null }, { replace: true }); render(); } });
  await load();
  if (state.view === 'merchants') loadMerchants();
  if (q.cat) openRuleModal({ category_id: Number(q.cat) });
  if (q.new === '1') openRuleModal({});
  window.PAGE_SHORTCUTS = [{ title: 'Rules', items: [['n', 'New rule'], ['Alt ↑ / ↓', 'Move rule'], ['Enter', 'Save pattern']] }];
  ui.shortcuts.register('n', () => openRuleModal({}), { description: 'New rule' });
});

async function load() {
  $('#rules-error').innerHTML = '';
  if (!state.rules.length) $('#rule-list').innerHTML = `<li class="rules-empty">${ui.skeletonList(5)}</li>`;
  try {
    const [rules, flat, accounts] = await Promise.all([api('/api/rules'), store.categoriesFlat(), store.accounts()]);
    state.rules = rules; state.cats = new Map(flat.map((c) => [c.id, c])); state.accounts = accounts;
  } catch (err) { $('#rules-error').innerHTML = ui.errorBox(err.message, { retry: 'reload' }); return; }
  render();
}

function catChip(id, r) {
  if (id == null) {
    if (r && r.set_transfer) return `<button type="button" class="catchip" data-act="pick-cat" data-id="${r.id}"><i class="dot" style="--c:var(--chart-muted)"></i><span class="catchip-label">Mark as transfer</span></button>`;
    if (r && r.set_excluded) return `<button type="button" class="catchip" data-act="pick-cat" data-id="${r.id}"><i class="dot" style="--c:var(--chart-muted)"></i><span class="catchip-label">Exclude from reports</span></button>`;
    return `<button type="button" class="catchip catchip--empty" data-act="pick-cat" data-id="${r ? r.id : ''}"><i class="dot"></i><span class="catchip-label">Choose category</span></button>`;
  }
  const c = state.cats.get(Number(id));
  if (!c) return `<button type="button" class="catchip catchip--empty" data-act="pick-cat" data-id="${r ? r.id : ''}"><i class="dot"></i><span class="catchip-label">Missing category</span></button>`;
  return `<button type="button" class="catchip" data-act="pick-cat" data-id="${r ? r.id : ''}" title="${esc(c.path)}"><i class="dot" style="--c:var(--${esc(c.color || c.parent_color || 'c1')})"></i><span class="catchip-label">${esc(c.parent_name ? `${c.parent_name} › ${c.name}` : c.name)}</span></button>`;
}
function extrasHtml(r) {
  const out = [];
  if (r.amount_min != null || r.amount_max != null) out.push(`<span class="badge badge-neutral" title="Amount range">${r.amount_min != null ? fmtMoney(r.amount_min, state.currency) : '…'} → ${r.amount_max != null ? fmtMoney(r.amount_max, state.currency) : '…'}</span>`);
  if (r.account_id) { const a = state.accounts.find((x) => x.id === r.account_id); out.push(`<span class="badge badge-neutral">${esc(a ? a.name : 'Account')}</span>`); }
  if (r.case_sensitive) out.push('<span class="badge badge-neutral">Aa</span>');
  if (r.category_id != null && r.set_transfer) out.push('<span class="badge badge-neutral">transfer</span>');
  if (r.category_id != null && r.set_excluded) out.push('<span class="badge badge-neutral">excluded</span>');
  return out.length ? `<span class="rule-extra">${out.join('')}</span>` : '';
}
function ruleHtml(r, i) {
  return `<li class="rule ${r.is_active ? '' : 'is-off'}" data-id="${r.id}" draggable="true" tabindex="0" aria-label="Rule ${i + 1}">
    <span class="rule-grip" title="Drag to reorder" aria-hidden="true">${icon('grip-vertical', 'ico-sm')}</span>
    <span class="rule-prio">${i + 1}</span>
    <div class="rule-cond">
      <select class="select" data-field="match_field" aria-label="Field">${MATCH_FIELDS.map(([v, l]) => `<option value="${v}" ${r.match_field === v ? 'selected' : ''}>${l}</option>`).join('')}</select>
      <select class="select" data-field="match_type" aria-label="Match type">${MATCH_TYPES.map(([v, l]) => `<option value="${v}" ${r.match_type === v ? 'selected' : ''}>${l}</option>`).join('')}</select>
      <input class="input rule-pattern" data-field="pattern" value="${esc(r.pattern)}" aria-label="Pattern" spellcheck="false">
      ${extrasHtml(r)}
      ${r.name && r.name !== `${r.match_type.replace('_', ' ')} “${r.pattern}”` ? `<span class="rule-name truncate">${esc(r.name)}</span>` : ''}
    </div>
    <span class="rule-arrow">${icon('arrow-right', 'ico-sm')}</span>
    <div class="rule-then">${catChip(r.category_id, r)}</div>
    <span class="rule-hits ${r.hit_count ? '' : 'is-zero'}" title="${r.last_hit_at ? `Last hit ${fmtRelative(r.last_hit_at)}` : 'Never matched'}">${fmtNumber(r.hit_count || 0)}</span>
    <span class="rule-on"><label class="switch switch-sm" title="${r.is_active ? 'Enabled' : 'Disabled'}"><input type="checkbox" data-field="is_active" ${r.is_active ? 'checked' : ''} aria-label="Enabled"><span class="switch-track"></span></label></span>
    <div class="rule-actions">
      <button type="button" class="btn btn-ghost btn-xs" data-act="test" data-id="${r.id}" title="Show matching transactions">Test</button>
      <button type="button" class="btn btn-icon btn-ghost btn-xs" data-act="menu" data-id="${r.id}" title="More" aria-label="More">${icon('more-horizontal')}</button>
    </div>
  </li>`;
}
function render() {
  const host = $('#rule-list');
  const keep = host.contains(document.activeElement) ? ui.focusKey(document.activeElement, host) : null;
  if (keep) requestAnimationFrame(() => ui.refocus(host, keep));
  if (!state.rules.length) {
    host.innerHTML = `<li class="rules-empty">${ui.emptyState({ icon: 'sliders', title: 'No rules yet', body: 'Create a rule here, or from any transaction with “Create rule from this”. Rules categorize charges automatically on every import.', action: { label: 'New rule', act: 'new-rule' } })}</li>`;
    return;
  }
  const q = state.q;
  const fc = state.filterCat;
  const inCat = (r) => !fc || r.category_id === fc || (state.cats.get(r.category_id) || {}).parent_id === fc;
  const rows = state.rules.map((r, i) => ({ r, i })).filter(({ r }) => inCat(r) && (!q || `${r.pattern} ${r.name || ''} ${(state.cats.get(r.category_id) || {}).path || ''}`.toLowerCase().includes(q)));
  const fcat = fc ? state.cats.get(fc) : null;
  const banner = fc ? `<div class="notice notice-info mb-3" role="status">${icon('filter')}<div class="grow">Showing rules for <b>${esc(fcat ? (fcat.path || fcat.name) : 'this category')}</b> (${fmtNumber(rows.length)})</div><button type="button" class="btn btn-ghost btn-xs" data-act="clear-cat-filter">Show all rules</button></div>` : '';
  host.innerHTML = banner + (rows.length ? rows.map(({ r, i }) => ruleHtml(r, i)).join('') : `<div class="rules-empty hint">${fc ? 'No rules target this category yet.' : `No rules match “${esc(state.q)}”.`}</div>`);
}
function ruleById(id) { return state.rules.find((r) => r.id === Number(id)); }

/* ---------- Inline edits ---------- */
async function saveRule(id, patch, { silent } = {}) {
  const row = $(`.rule[data-id="${id}"]`);
  row && row.classList.add('is-saving');
  try {
    const res = await api(`/api/rules/${id}`, { method: 'PUT', body: patch });
    Object.assign(ruleById(id), patch);
    if (!silent) toast(res.applied ? `Rule saved · applied to ${fmtNumber(res.applied)}` : 'Rule saved', { type: 'success', duration: 1800 });
    return true;
  } catch (err) { toast(err.message, { type: 'error' }); return false; }
  finally { row && row.classList.remove('is-saving'); }
}
async function onInlineChange(e) {
  const el = e.target; const row = el.closest('.rule'); if (!row) return;
  const id = Number(row.dataset.id); const field = el.dataset.field; if (!field) return;
  if (field === 'pattern') return;
  const value = el.type === 'checkbox' ? el.checked : el.value;
  const ok = await saveRule(id, { [field]: value }, { silent: field === 'is_active' });
  if (field === 'is_active') row.classList.toggle('is-off', !value);
  if (!ok) render();
}
async function commitPattern(input) {
  const row = input.closest('.rule'); const r = ruleById(row.dataset.id);
  const v = input.value.trim();
  if (!v) { input.classList.add('is-invalid'); return; }
  if (v === r.pattern) { input.classList.remove('is-invalid'); return; }
  const ok = await saveRule(r.id, { pattern: v });
  input.classList.toggle('is-invalid', !ok);
  if (ok) render();
}
function onListKey(e) {
  const row = e.target.closest('.rule'); if (!row) return;
  if (e.target.matches('.rule-pattern') && e.key === 'Enter') { e.preventDefault(); e.target.blur(); return; }
  if (e.target.matches('.rule-pattern') && e.key === 'Escape') { e.target.value = ruleById(row.dataset.id).pattern; e.target.classList.remove('is-invalid'); e.target.blur(); return; }
  if (e.altKey && (e.key === 'ArrowUp' || e.key === 'ArrowDown')) { e.preventDefault(); moveRule(Number(row.dataset.id), e.key === 'ArrowUp' ? -1 : 1); }
}

/* ---------- Actions ---------- */
async function onAction(e) {
  const btn = e.target.closest('[data-act]'); if (!btn) return;
  const act = btn.dataset.act; const id = btn.dataset.id ? Number(btn.dataset.id) : null;
  if (act === 'reload') return load();
  if (act === 'new-rule') return openRuleModal({});
  if (act === 'test') return openTestDrawer(ruleById(id));
  if (act === 'menu') return openMenu(btn, ruleById(id));
  if (act === 'pick-cat' && id) {
    const r = ruleById(id);
    return categoryPicker({ anchor: btn, value: r.category_id, allowCreate: true, onPick: async (c) => { if (!c) return; if (await saveRule(id, { category_id: c.id })) render(); } });
  }
  if (act === 'run-all') {
    if (!(await ui.confirm({ title: 'Run all rules?', body: 'Applies every enabled rule to transactions that have no category yet. Existing categories are not changed.', confirmText: 'Run rules' }))) return;
    btn.classList.add('is-loading');
    try { const r = await api('/api/rules/run', { method: 'POST', body: { only_uncategorized: true } }); toast(`${plural(r.updated, 'transaction')} categorized`, { type: 'success' }); window.dispatchEvent(new Event('ispend:transactions-changed')); await load(); }
    catch (err) { toast(err.message, { type: 'error' }); } finally { btn.classList.remove('is-loading'); }
  }
}
function openMenu(anchor, r) {
  const i = state.rules.indexOf(r);
  ui.menu(anchor, [
    { label: 'Edit…', icon: 'pencil', onClick: () => openRuleModal(r) },
    { label: 'Test', icon: 'eye', onClick: () => openTestDrawer(r) },
    { label: 'Apply to uncategorized', icon: 'play', onClick: () => applyRule(r, true) },
    { label: 'Duplicate', icon: 'copy', onClick: async () => { try { await api('/api/rules', { method: 'POST', body: { ...r, id: undefined, name: `${r.name || r.pattern} (copy)`, priority: r.priority + 5 } }); toast('Rule duplicated', { type: 'success' }); await load(); } catch (err) { toast(err.message, { type: 'error' }); } } },
    { divider: true },
    { label: 'Move up', icon: 'arrow-up', disabled: i <= 0, onClick: () => moveRule(r.id, -1) },
    { label: 'Move down', icon: 'arrow-down', disabled: i >= state.rules.length - 1, onClick: () => moveRule(r.id, 1) },
    { divider: true },
    { label: 'Delete', icon: 'trash', danger: true, onClick: () => deleteRule(r) },
  ]);
}
async function deleteRule(r) {
  if (!(await ui.confirm({ title: 'Delete rule?', body: `“${r.pattern}” will no longer categorize new imports. Already categorized transactions keep their category.`, confirmText: 'Delete', danger: true }))) return;
  try { await api(`/api/rules/${r.id}`, { method: 'DELETE' }); toast('Rule deleted', { type: 'success' }); await load(); } catch (err) { toast(err.message, { type: 'error' }); }
}
async function applyRule(r, onlyUncategorized) {
  try { const res = await api(`/api/rules/${r.id}/apply`, { method: 'POST', body: { only_uncategorized: onlyUncategorized } }); toast(`${plural(res.updated, 'transaction')} updated`, { type: 'success' }); window.dispatchEvent(new Event('ispend:transactions-changed')); await load(); return res.updated; }
  catch (err) { toast(err.message, { type: 'error' }); }
}

/* ---------- Reorder ---------- */
async function reorder(ids) {
  try { await api('/api/rules/reorder', { method: 'PUT', body: { ids } }); await load(); } catch (err) { toast(err.message, { type: 'error' }); }
}
function moveRule(id, dir) {
  const ids = state.rules.map((r) => r.id); const i = ids.indexOf(id); const j = i + dir;
  if (j < 0 || j >= ids.length) return;
  ids.splice(i, 1); ids.splice(j, 0, id);
  reorder(ids).then(() => { const row = $(`.rule[data-id="${id}"]`); if (row) row.focus(); });
}
function wireDrag() {
  const host = $('#rule-list');
  host.addEventListener('dragstart', (e) => { const row = e.target.closest('.rule'); if (!row || e.target.matches('input,select')) { e.preventDefault(); return; } state.dragId = Number(row.dataset.id); row.classList.add('is-dragging'); e.dataTransfer.effectAllowed = 'move'; try { e.dataTransfer.setData('text/plain', row.dataset.id); } catch { /* ignore */ } });
  host.addEventListener('dragend', () => { state.dragId = null; $$('.rule').forEach((r) => r.classList.remove('is-dragging', 'is-dropover')); });
  host.addEventListener('dragover', (e) => { const row = e.target.closest('.rule'); if (!row || state.dragId == null) return; e.preventDefault(); e.dataTransfer.dropEffect = 'move'; $$('.rule.is-dropover').forEach((r) => r.classList.remove('is-dropover')); row.classList.add('is-dropover'); });
  host.addEventListener('drop', (e) => {
    const row = e.target.closest('.rule'); if (!row || state.dragId == null) return;
    e.preventDefault();
    const ids = state.rules.map((r) => r.id); const from = ids.indexOf(state.dragId); let to = ids.indexOf(Number(row.dataset.id));
    if (from < 0 || to < 0 || from === to) return;
    ids.splice(from, 1); if (from < to) to -= 1; ids.splice(to + (e.offsetY > row.offsetHeight / 2 ? 1 : 0), 0, state.dragId);
    reorder(ids);
  });
}

/* ---------- Rule modal (create / edit) ---------- */
function ruleFormHtml(r) {
  const acc = state.accounts.filter((a) => a.is_active || a.id === r.account_id);
  return `<form class="rule-form" id="rule-form">
    <div class="field-row">
      <div class="field"><label for="rf-field">Field</label><select id="rf-field" class="select">${MATCH_FIELDS.map(([v, l]) => `<option value="${v}" ${(r.match_field || 'description_clean') === v ? 'selected' : ''}>${l}</option>`).join('')}</select></div>
      <div class="field"><label for="rf-type">Match</label><select id="rf-type" class="select">${MATCH_TYPES.map(([v, l]) => `<option value="${v}" ${(r.match_type || 'contains') === v ? 'selected' : ''}>${l}</option>`).join('')}</select></div>
    </div>
    <div class="field"><label for="rf-pattern">Pattern</label><input id="rf-pattern" class="input mono" value="${esc(r.pattern || '')}" placeholder="e.g. STARBUCKS" autofocus spellcheck="false" autocomplete="off"><div class="hint" id="rf-pattern-hint">Matching ignores case unless you enable it below.</div></div>
    <div class="field"><label>Then set category</label><button type="button" class="btn btn-secondary btn-block" id="rf-cat" style="justify-content:space-between"><span id="rf-cat-label" class="row gap-2 text-3">Choose a category…</span>${icon('chevron-down')}</button></div>
    <div class="rule-preview is-empty" id="rf-preview">${icon('search')}<span>Type a pattern to see how many existing transactions match.</span></div>
    <div id="rf-sample" class="rule-sample"></div>
    <details class="mt-3"><summary>${icon('chevron-right', 'ico-sm')}Advanced</summary>
      <div class="field-row">
        <div class="field"><label for="rf-min">Amount from</label><input id="rf-min" class="input num" type="number" step="0.01" value="${r.amount_min ?? ''}" placeholder="any"></div>
        <div class="field"><label for="rf-max">Amount to</label><input id="rf-max" class="input num" type="number" step="0.01" value="${r.amount_max ?? ''}" placeholder="any"></div>
      </div>
      <div class="hint mb-3">Signed amounts: charges are negative (e.g. −50 to −10), income positive.</div>
      <div class="field"><label for="rf-acct">Only for account</label><select id="rf-acct" class="select"><option value="">Any account</option>${acc.map((a) => `<option value="${a.id}" ${r.account_id === a.id ? 'selected' : ''}>${esc(a.name)}</option>`).join('')}</select></div>
      <div class="switch-row">
        <label class="switch switch-sm"><input type="checkbox" id="rf-case" ${r.case_sensitive ? 'checked' : ''}><span class="switch-track"></span>Case sensitive</label>
        <label class="switch switch-sm"><input type="checkbox" id="rf-transfer" ${r.set_transfer ? 'checked' : ''}><span class="switch-track"></span>Mark as transfer</label>
        <label class="switch switch-sm"><input type="checkbox" id="rf-excl" ${r.set_excluded ? 'checked' : ''}><span class="switch-track"></span>Exclude from reports</label>
      </div>
      <div class="field"><label for="rf-name">Rule name (optional)</label><input id="rf-name" class="input" value="${esc(r.name || '')}" placeholder="Shown in the rules list"></div>
    </details>
    <div class="switch-row" style="margin-top:8px">
      <label class="switch switch-sm"><input type="checkbox" id="rf-apply" checked><span class="switch-track"></span>Apply to existing transactions</label>
      <label class="switch switch-sm"><input type="checkbox" id="rf-only" checked><span class="switch-track"></span>Only uncategorized ones</label>
    </div>
    <button type="submit" hidden></button></form>`;
}
function readRuleForm(el) {
  const num = (id) => { const v = el.querySelector(id).value.trim(); return v === '' ? null : Number(v); };
  return {
    match_field: el.querySelector('#rf-field').value, match_type: el.querySelector('#rf-type').value, pattern: el.querySelector('#rf-pattern').value.trim(),
    amount_min: num('#rf-min'), amount_max: num('#rf-max'), account_id: el.querySelector('#rf-acct').value ? Number(el.querySelector('#rf-acct').value) : null,
    case_sensitive: el.querySelector('#rf-case').checked, set_transfer: el.querySelector('#rf-transfer').checked, set_excluded: el.querySelector('#rf-excl').checked,
    name: el.querySelector('#rf-name').value.trim(),
  };
}
function openRuleModal(r) {
  const editing = !!r.id;
  let categoryId = r.category_id || null;
  const m = ui.modal({
    title: editing ? 'Edit rule' : 'New rule', size: 'lg', html: ruleFormHtml(r),
    actions: [{ label: 'Cancel' }, { label: editing ? 'Save rule' : 'Create rule', primary: true, onClick: async () => {
      const body = readRuleForm(m.el);
      if (!body.pattern) throw new Error('Pattern is required');
      if (!categoryId && !body.set_transfer && !body.set_excluded) throw new Error('Choose a category (or mark as transfer / excluded)');
      body.category_id = categoryId;
      body.apply_existing = m.el.querySelector('#rf-apply').checked;
      body.only_uncategorized = m.el.querySelector('#rf-only').checked;
      if (!body.name) delete body.name;
      const res = editing ? await api(`/api/rules/${r.id}`, { method: 'PUT', body }) : await api('/api/rules', { method: 'POST', body });
      toast(res.applied ? `Rule ${editing ? 'saved' : 'created'} · applied to ${plural(res.applied, 'transaction')}` : `Rule ${editing ? 'saved' : 'created'}`, { type: 'success' });
      if (res.applied) window.dispatchEvent(new Event('ispend:transactions-changed'));
      await load();
    } }],
  });
  const el = m.el;
  const setCatLabel = () => {
    const c = categoryId ? state.cats.get(categoryId) : null;
    el.querySelector('#rf-cat-label').innerHTML = c ? `<i class="dot" style="--c:var(--${esc(c.color || c.parent_color || 'c1')})"></i><span class="text-1">${esc(c.path)}</span>` : '<span class="text-3">Choose a category…</span>';
  };
  setCatLabel();
  el.querySelector('#rf-cat').addEventListener('click', (e) => categoryPicker({ anchor: e.currentTarget, value: categoryId, onPick: (c) => { if (c) { categoryId = c.id; if (!state.cats.has(c.id)) state.cats.set(c.id, c); setCatLabel(); } } }));
  const preview = debounce(async () => {
    const body = readRuleForm(el);
    const box = el.querySelector('#rf-preview'); const sample = el.querySelector('#rf-sample');
    if (!body.pattern) { box.className = 'rule-preview is-empty'; box.innerHTML = `${icon('search')}<span>Type a pattern to see how many existing transactions match.</span>`; sample.innerHTML = ''; return; }
    try {
      const res = await api('/api/rules/preview', { method: 'POST', body: { ...body, category_id: categoryId, id: r.id } });
      box.className = 'rule-preview';
      box.innerHTML = `${icon('check-circle')}<span>Matches <b>${fmtNumber(res.count)}</b> existing ${word(res.count, 'transaction')}${res.sample.length ? ` · showing ${res.sample.length}` : ''}</span>`;
      sample.innerHTML = res.sample.slice(0, 5).map((t) => `<div class="list-item"><span class="text-4 fs-xs">${fmtDate(t.txn_date)}</span><span class="truncate">${esc(t.merchant_name || t.description_raw)}</span><span class="amt ${t.amount > 0 ? 'amt--income' : ''}">${fmtMoney(t.amount, state.currency)}</span></div>`).join('');
    } catch (err) {
      box.className = 'rule-preview is-error'; box.innerHTML = `${icon('alert-triangle')}<span>${esc(err.message)}</span>`; sample.innerHTML = '';
    }
  }, 300);
  ['#rf-field', '#rf-type', '#rf-pattern', '#rf-min', '#rf-max', '#rf-acct', '#rf-case'].forEach((id) => el.querySelector(id).addEventListener('input', preview));
  el.querySelector('#rf-apply').addEventListener('change', (e) => { el.querySelector('#rf-only').disabled = !e.target.checked; });
  el.querySelector('#rule-form').addEventListener('submit', (e) => { e.preventDefault(); el.querySelector('.modal-foot .btn-primary').click(); });
  el.querySelector('#rf-type').addEventListener('change', (e) => { el.querySelector('#rf-pattern-hint').textContent = e.target.value === 'regex' ? 'Python regular expression, searched anywhere in the text.' : 'Matching ignores case unless you enable it below.'; });
  if (r.pattern) preview();
}

/* ---------- Test drawer ---------- */
async function openTestDrawer(r) {
  const c = state.cats.get(r.category_id);
  const d = ui.drawer({ title: `Test: ${r.match_type.replace('_', ' ')} “${r.pattern}”`, html: `<div class="list test-list">${ui.skeletonList(6)}</div>`, width: 520 });
  let res;
  try { res = await api('/api/rules/preview', { method: 'POST', body: { ...r, only_uncategorized: false } }); }
  catch (err) { d.setBody(ui.errorBox(err.message)); return; }
  const list = res.sample.map((t) => {
    const tc = t.category_id ? state.cats.get(t.category_id) : null;
    return `<div class="list-item"><span class="tl-date">${fmtDate(t.txn_date)}</span><div class="tl-desc"><div class="truncate fw-500">${esc(t.merchant_name || t.description_raw)}</div><div class="tl-raw">${esc(t.description_raw)}${tc ? ` · <span>${esc(tc.path)}</span>` : ' · <span class="text-warning">uncategorized</span>'}</div></div><span class="amt ${t.amount > 0 ? 'amt--income' : ''}">${fmtMoney(t.amount, state.currency)}</span></div>`;
  }).join('');
  d.setBody(`<div class="notice mb-3">${icon('info')}<div><b>${fmtNumber(res.count)}</b> ${word(res.count, 'transaction')} match this rule${c ? ` → <b>${esc(c.path)}</b>` : ''}.${res.count > res.sample.length ? ` Showing the latest ${res.sample.length}.` : ''}</div></div>
    ${res.count ? `<div class="list test-list">${list}</div>` : ui.emptyState({ icon: 'search', title: 'No matches', body: 'No existing transaction matches this pattern. New imports will still be checked.' })}`);
  if (res.count) {
    d.setFoot(`<button type="button" class="btn btn-ghost" data-drawer-act="apply-unc">Apply to uncategorized</button><button type="button" class="btn btn-primary" data-drawer-act="apply-all">Apply to all ${fmtNumber(res.count)}</button>`);
    d.el.addEventListener('click', async (e) => {
      const b = e.target.closest('[data-drawer-act]'); if (!b) return;
      const all = b.dataset.drawerAct === 'apply-all';
      if (all && !(await ui.confirm({ title: `Apply to ${fmtNumber(res.count)} transactions?`, body: 'Transactions that already have a category will be re-categorized by this rule.', confirmText: 'Apply' }))) return;
      b.classList.add('is-loading');
      const n = await applyRule(r, !all);
      b.classList.remove('is-loading');
      if (n != null) d.close();
    });
  }
}


/* ---------- Remembered merchants ---------- */
function paintView() {
  const m = state.view === 'merchants';
  $$('#view-seg .seg-btn').forEach((b) => { const on = b.dataset.view === state.view; b.classList.toggle('active', on); b.setAttribute('aria-pressed', String(on)); });
  $('#rules-view').hidden = m; $('#merchants-view').hidden = !m;
  $('[data-act="run-all"]').hidden = m; $('[data-act="new-rule"]').hidden = m;
  const search = $('#rule-search');
  search.placeholder = m ? 'Search merchants…' : 'Filter rules…';
  search.value = m ? state.mq : state.q;
  $('#page-sub').textContent = m ? 'What iSpend learned from your choices. Each merchant gets its remembered category on every future import.' : 'Rules run top to bottom on every import; the first match wins.';
}
async function loadMerchants() {
  $('#rules-error').innerHTML = '';
  const tb = $('#merch-body');
  if (!state.merchants.length) tb.innerHTML = ui.skeletonRows(6, 7);
  state.mloading = true;
  try { state.merchants = await api(`/api/merchants${toQuery({ q: state.mq })}`); }
  catch (err) { $('#rules-error').innerHTML = ui.errorBox(err.message, { retry: 'reload-merchants' }); state.mloading = false; return; }
  state.mloading = false;
  renderMerchants();
}
function merchantRow(m) {
  const c = state.cats.get(Number(m.category_id));
  const key = encodeURIComponent(m.merchant_key);
  return `<tr data-key="${esc(m.merchant_key)}">
    <td><div class="merchant"><button type="button" class="merch-name" data-mact="rename" title="Rename this merchant everywhere">${esc(m.display_name || m.merchant_key)}${icon('pencil', 'ico-sm')}</button><span class="merchant-raw">${esc(m.merchant_key)}</span></div></td>
    <td>${m.is_transfer ? `<span class="badge badge-neutral">transfer</span> ` : ''}${c ? `<button type="button" class="catchip" data-mact="pick" title="${esc(c.path)}"><i class="dot" style="--c:var(--${esc(c.color || c.parent_color || 'c1')})"></i><span class="catchip-label">${esc(c.parent_name ? `${c.parent_name} › ${c.name}` : c.name)}</span></button>` : `<button type="button" class="catchip catchip--empty" data-mact="pick"><i class="dot"></i><span class="catchip-label">Missing category</span></button>`}</td>
    <td class="right num col-used">${fmtNumber(m.times_used)}</td>
    <td class="right num">${fmtNumber(m.txn_count)}</td>
    <td class="right num ${m.total > 0 ? 'amt--income' : ''}">${fmtMoney(m.total, state.currency)}</td>
    <td class="col-last text-3">${m.last_used_at ? fmtRelative(m.last_used_at) : '—'}</td>
    <td class="col-actions"><div class="row-actions"><a class="btn btn-icon btn-ghost btn-xs" href="/transactions.html${toQuery({ q: m.display_name || m.merchant_key, range: 'all' })}" title="View transactions" aria-label="View transactions">${icon('list')}</a><button type="button" class="btn btn-icon btn-ghost btn-xs" data-mact="menu" title="More" aria-label="More">${icon('more-horizontal')}</button></div></td>
  </tr>`;
}
function renderMerchants() {
  const tb = $('#merch-body');
  if (!state.merchants.length) {
    tb.innerHTML = `<tr><td colspan="7">${state.mq ? `<div class="hint" style="padding:16px">No remembered merchants match “${esc(state.mq)}”.</div>` : ui.emptyState({ icon: 'repeat', title: 'Nothing remembered yet', body: 'Categorize a charge in Transactions or Review and iSpend will remember that merchant for the next import.', action: { label: 'Go to Review', href: '/review.html' } })}</td></tr>`;
    return;
  }
  tb.innerHTML = state.merchants.map(merchantRow).join('');
}
function merchantFromEvent(e) { const tr = e.target.closest('tr[data-key]'); return tr ? state.merchants.find((m) => m.merchant_key === tr.dataset.key) : null; }
function onMerchantClick(e) {
  const m = merchantFromEvent(e); const b = e.target.closest('[data-mact]');
  if (!m || !b) return;
  switch (b.dataset.mact) {
    case 'rename': return startRename(b, m);
    case 'pick': return categoryPicker({ anchor: b, value: m.category_id, allowCreate: true, onPick: (c) => { if (c) saveMerchant(m, { category_id: c.id }, `${m.display_name || m.merchant_key} → ${c.name}`); } });
    case 'menu': return ui.menu(b, [
      { label: 'Apply to existing transactions', icon: 'play', onClick: () => saveMerchant(m, { category_id: m.category_id, apply_existing: true }, null, true) },
      { label: 'Rename…', icon: 'pencil', onClick: () => { const btn = $(`tr[data-key="${CSS.escape(m.merchant_key)}"] .merch-name`); if (btn) startRename(btn, m); } },
      { label: 'View transactions', icon: 'list', href: `/transactions.html${toQuery({ q: m.display_name || m.merchant_key, range: 'all' })}` },
      { divider: true },
      { label: 'Forget', icon: 'trash', danger: true, onClick: () => forgetMerchant(m) },
    ]);
    default:
  }
}
function startRename(btn, m) {
  const input = document.createElement('input');
  input.className = 'input input-sm merch-name-input'; input.value = m.display_name || m.merchant_key; input.setAttribute('aria-label', 'Merchant name'); input.dataset.orig = input.value;
  btn.replaceWith(input); input.focus(); input.select();
}
function onMerchantKey(e) {
  if (!e.target.matches('.merch-name-input')) return;
  if (e.key === 'Enter') { e.preventDefault(); e.target.blur(); }
  if (e.key === 'Escape') { e.target.dataset.cancel = '1'; e.target.blur(); }
}
async function commitMerchantName(input) {
  const tr = input.closest('tr'); const m = state.merchants.find((x) => x.merchant_key === tr.dataset.key);
  const v = input.value.trim();
  if (input.dataset.cancel || !v || v === input.dataset.orig) { renderMerchants(); return; }
  await saveMerchant(m, { category_id: m.category_id, display_name: v, rename_all: true }, `Renamed to ${v}`);
}
async function saveMerchant(m, body, label, showApplied) {
  try {
    const r = await api(`/api/merchants/${encodeURIComponent(m.merchant_key)}`, { method: 'PUT', body: { category_id: m.category_id, is_transfer: m.is_transfer, ...body } });
    if (showApplied) toast(r.applied ? `${plural(r.applied, 'transaction')} updated` : 'Every transaction already had this category', { type: 'success' });
    else if (label) toast(label, { type: 'success', duration: 1800 });
    if (body.apply_existing || body.rename_all) window.dispatchEvent(new Event('ispend:transactions-changed'));
    await loadMerchants();
  } catch (err) { toast(err.message, { type: 'error' }); renderMerchants(); }
}
async function forgetMerchant(m) {
  if (!(await ui.confirm({ title: `Forget ${m.display_name || m.merchant_key}?`, body: 'iSpend will stop applying this category automatically. Transactions already categorized keep their category.', confirmText: 'Forget', danger: true }))) return;
  try { await api(`/api/merchants/${encodeURIComponent(m.merchant_key)}`, { method: 'DELETE' }); toast('Merchant forgotten', { type: 'success' }); await loadMerchants(); }
  catch (err) { toast(err.message, { type: 'error' }); }
}
document.body.addEventListener('click', (e) => { if (e.target.closest('[data-act="reload-merchants"]')) loadMerchants(); });