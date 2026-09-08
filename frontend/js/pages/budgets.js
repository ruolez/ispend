/* Budgets: one limit per category and month, progress against this month's spending, copy last month. */
const bud = { month: null, list: null, progress: null, cats: new Map(), catsFlat: [], currency: 'USD', seq: 0, months: [] };
const STATUS = { over: ['Over budget', 'badge-danger'], ahead: ['Ahead of pace', 'badge-warning'], on_track: ['On track', 'badge-neutral'] };

function currentYm() { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`; }
function shiftYm(ym, n) { const [y, m] = ym.split('-').map(Number); const d = new Date(y, m - 1 + n, 1); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`; }
function catOf(id) { return bud.cats.get(Number(id)) || null; }
function catColorOf(c) { return c ? `var(--${c.color || c.parent_color || 'c1'})` : 'var(--chart-muted)'; }

initNav('budgets').then(async () => {
  const q = qs();
  bud.month = /^\d{4}-(0[1-9]|1[0-2])$/.test(q.month || '') ? q.month : currentYm();
  bud.currency = await store.displayCurrency();
  await loadCats();
  $('[data-month="prev"]').innerHTML = icon('chevron-left');
  $('[data-month="next"]').innerHTML = icon('chevron-right');
  $('#btn-add').innerHTML = `${icon('plus', 'ico-sm')}<span class="label">Add budget</span>`;
  $('#btn-copy').innerHTML = `${icon('copy', 'ico-sm')}<span class="label">Copy last month</span>`;
  $('#month-nav').addEventListener('click', (e) => {
    const b = e.target.closest('button'); if (!b) return;
    if (b.dataset.month === 'prev') setMonth(shiftYm(bud.month, -1));
    else if (b.dataset.month === 'next') setMonth(shiftYm(bud.month, 1));
    else if (b.id === 'month-btn') openMonthMenu(b);
  });
  $('#btn-add').addEventListener('click', () => openBudgetModal());
  $('#btn-copy').addEventListener('click', copyLastMonth);
  document.body.addEventListener('click', onAction);
  $('#bud-list').addEventListener('keydown', onListKey);
  $('#bud-list').addEventListener('focusout', (e) => { if (e.target.matches('.bud-amt-input')) commitAmount(e.target); });
  store.on('categories-changed', async () => { await loadCats(); render(); });
  ui.shortcuts.register('n', () => openBudgetModal(), { description: 'Add budget' });
  ui.shortcuts.register('c', () => { if (!$('#btn-copy').hidden) copyLastMonth(); }, { description: 'Copy last month' });
  ui.shortcuts.register('ArrowLeft', () => setMonth(shiftYm(bud.month, -1)), { description: 'Previous month' });
  ui.shortcuts.register('ArrowRight', () => setMonth(shiftYm(bud.month, 1)), { description: 'Next month' });
  window.PAGE_SHORTCUTS = [{ title: 'Budgets', items: [['n', 'Add budget'], ['c', 'Copy last month'], ['← / →', 'Previous / next month']] }];
  await load();
});

async function loadCats() {
  bud.catsFlat = await store.categoriesFlat();
  bud.cats = new Map(bud.catsFlat.map((c) => [c.id, c]));
}
function setMonth(ym) {
  bud.month = ym;
  setQs({ month: ym === currentYm() ? null : ym });
  load();
}
function openMonthMenu(anchor) {
  const items = [];
  let ym = shiftYm(currentYm(), 1);
  for (let i = 0; i < 25; i++) {
    if (ym.endsWith('-12') || i === 0) items.push({ label: ym.slice(0, 4), header: true });
    items.push({ label: fmtMonth(ym, { long: true }).replace(/ \d{4}$/, ''), checked: ym === bud.month, onClick: ((v) => () => setMonth(v))(ym) });
    ym = shiftYm(ym, -1);
  }
  anchor.setAttribute('aria-expanded', 'true');
  ui.menu(anchor, items, { placement: 'bottom-end', onClose: () => anchor.setAttribute('aria-expanded', 'false') });
}

/* ---------- data ---------- */
async function load() {
  const seq = ++bud.seq;
  $('#bud-error').innerHTML = '';
  $$('#bud-stats .stat').forEach((s) => s.classList.add('is-loading'));
  if (!bud.list) $('#bud-list').innerHTML = `<div class="card-body">${ui.skeletonList(4)}</div>`;
  $('#month-btn').innerHTML = `${icon('calendar', 'ico-sm')}<span class="label">${esc(fmtMonth(bud.month, { long: true }))}</span>${icon('chevron-down', 'ico-sm')}`;
  $('#bud-month-label').textContent = fmtMonth(bud.month, { long: true });
  setPageTitle(fmtMonth(bud.month, { long: true }));
  try {
    const [list, progress] = await Promise.all([api(`/api/budgets${toQuery({ month: bud.month })}`), api(`/api/budgets/progress${toQuery({ month: bud.month })}`)]);
    if (seq !== bud.seq) return;
    bud.list = list; bud.progress = progress; bud.months = list.months_with_budgets || [];
  } catch (err) {
    if (seq !== bud.seq) return;
    $('#bud-error').innerHTML = ui.errorBox(err.message, { retry: 'reload' });
    return;
  }
  render();
}

/* ---------- render ---------- */
function render() {
  const p = bud.progress; const cur = bud.currency;
  const t = p.totals;
  const stats = $$('#bud-stats .stat');
  const paint = (el, value, delta, cls) => { el.classList.remove('is-loading'); el.querySelector('.stat-value').textContent = value; const d = el.querySelector('.stat-delta'); d.textContent = delta; d.className = `stat-delta ${cls || ''}`; };
  paint(stats[0], fmtMoney(t.budget, cur), `${fmtNumber(p.items.length)} categor${p.items.length === 1 ? 'y' : 'ies'}`);
  paint(stats[1], fmtMoney(t.spent, cur), t.budget ? `${fmtPct(t.pct / 100)} used · ${fmtPct(p.elapsed_pct / 100)} of the month gone` : 'No budgets yet');
  paint(stats[2], fmtMoney(Math.max(t.remaining, 0), cur), t.remaining < 0 ? `Over by ${fmtMoney(-t.remaining, cur)}` : STATUS[t.pace_status][0], t.pace_status === 'over' ? 'stat-delta--bad' : t.pace_status === 'ahead' ? 'stat-delta--warn' : '');
  const prev = shiftYm(bud.month, -1);
  $('#btn-copy').hidden = !bud.months.includes(prev) || p.items.length >= bud.months.length && bud.months.includes(bud.month) && false;
  $('#bud-hint').textContent = p.items.length ? `${fmtPct(p.elapsed_pct / 100)} of the month has passed` : '';
  const host = $('#bud-list');
  if (!p.items.length) {
    host.innerHTML = `<div class="card-body">${ui.emptyState({ icon: 'target', title: 'No budgets for this month', body: bud.months.includes(prev) ? `Copy ${fmtMonth(prev, { long: true })}'s budgets or add a new one.` : 'Set a monthly limit for a category to see how the month is going.', action: bud.months.includes(prev) ? { label: `Copy ${fmtMonth(prev)}`, act: 'copy-last' } : { label: 'Add budget', act: 'add-budget' } })}</div>`;
  } else {
    host.innerHTML = `<div class="bud-rows">${p.items.map(rowHtml).join('')}</div>`;
  }
  const u = p.unbudgeted;
  $('#bud-unbudgeted').innerHTML = u.count
    ? `<p class="text-2 mb-3">${fmtMoney(u.spent, cur)} across ${fmtNumber(u.count)} categor${u.count === 1 ? 'y' : 'ies'} without a budget.</p><div class="list">${u.categories.map((c) => `<div class="list-item bud-unb"><span class="cat-icon" style="--c:${catColorOf(catOf(c.id))}">${icon(c.icon || 'tag')}</span><div class="grow"><div class="fw-500">${esc(c.name)}</div><div class="fs-sm text-3">${fmtMoney(c.total, cur)} this month</div></div><button type="button" class="btn btn-secondary btn-sm" data-act="set-budget" data-cat="${c.id}" data-amount="${Math.ceil(c.total)}">Set budget</button></div>`).join('')}</div>`
    : ui.emptyState({ icon: 'check-circle', title: p.items.length ? 'Everything is budgeted' : 'Nothing spent yet', body: p.items.length ? 'Every category with spending this month has a limit.' : '' });
}
function rowHtml(it) {
  const cur = bud.currency; const c = catOf(it.category_id);
  const [label, badge] = STATUS[it.pace_status] || STATUS.on_track;
  const width = Math.min(it.pct, 100);
  return `<div class="bud-row is-${it.pace_status}" data-id="${it.budget_id}" data-cat="${it.category_id}">
    <div class="bud-name"><span class="cat-icon" style="--c:${catColorOf(c)}">${icon(it.icon || 'tag')}</span><div class="min-w-0"><div class="bud-title truncate">${esc(it.name)}</div><div class="bud-sub truncate">${esc(c && c.parent_name ? `${c.parent_name} › ` : '')}${it.note ? esc(it.note) : (c && c.parent_name ? 'subcategory' : 'category')}</div></div></div>
    <div class="bud-bar" role="progressbar" aria-label="${esc(it.name)} budget" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${Math.round(width)}"><span class="fill" style="width:${width.toFixed(1)}%"></span>${bud.progress.elapsed_pct > 0 && bud.progress.elapsed_pct < 100 ? `<i class="tick" style="left:${bud.progress.elapsed_pct}%" data-tip="${fmtPct(bud.progress.elapsed_pct / 100)} of the month gone"></i>` : ''}</div>
    <div class="bud-amounts num"><span class="spent">${fmtMoney(it.spent, cur)}</span><span class="text-4"> / </span><button type="button" class="bud-edit" data-act="edit-amount" data-id="${it.budget_id}" data-tip="Change the limit" aria-label="Change the ${esc(it.name)} budget, currently ${esc(fmtMoney(it.budget, cur))}">${fmtMoney(it.budget, cur)}</button></div>
    <div class="bud-left num ${it.remaining < 0 ? 'is-over' : ''}">${it.remaining < 0 ? `Over by ${fmtMoney(-it.remaining, cur)}` : `${fmtMoney(it.remaining, cur)} left`}${it.projected != null && it.pace_status !== 'over' ? `<span class="bud-proj">≈ ${fmtMoney(it.projected, cur)} by month end</span>` : ''}</div>
    <span class="badge ${badge} bud-status">${label}</span>
    <button type="button" class="btn btn-icon btn-ghost btn-xs" data-act="menu" data-id="${it.budget_id}" aria-label="More for ${esc(it.name)}">${icon('more-horizontal')}</button>
  </div>`;
}
function itemById(id) { return (bud.progress.items || []).find((i) => i.budget_id === Number(id)) || null; }

/* ---------- actions ---------- */
async function onAction(e) {
  const b = e.target.closest('[data-act]'); if (!b) return;
  const act = b.dataset.act;
  if (act === 'reload') return load();
  if (act === 'add-budget') return openBudgetModal();
  if (act === 'copy-last') return copyLastMonth();
  if (act === 'set-budget') return openBudgetModal({ category_id: Number(b.dataset.cat), amount: Number(b.dataset.amount) || '' });
  if (act === 'edit-amount') return startAmountEdit(b);
  if (act === 'menu') return openMenu(b, itemById(b.dataset.id));
  return null;
}
function startAmountEdit(btn) {
  const it = itemById(btn.dataset.id); if (!it) return;
  const input = document.createElement('input');
  input.className = 'input input-sm num bud-amt-input'; input.type = 'number'; input.min = '0.01'; input.step = '0.01'; input.inputMode = 'decimal';
  input.value = it.budget.toFixed(2); input.dataset.id = String(it.budget_id); input.setAttribute('aria-label', `${it.name} budget`);
  btn.replaceWith(input); input.focus(); input.select();
}
function onListKey(e) {
  if (!e.target.matches('.bud-amt-input')) return;
  if (e.key === 'Enter') { e.preventDefault(); e.target.blur(); }
  if (e.key === 'Escape') { e.target.dataset.cancel = '1'; e.target.blur(); }
}
async function commitAmount(input) {
  const it = itemById(input.dataset.id); if (!it) return render();
  if (input.dataset.cancel) return render();
  const v = Number(input.value);
  if (!(v > 0)) { ui.fieldError(input, 'Enter an amount above zero'); input.focus(); return; }
  if (Math.abs(v - it.budget) < 0.005) return render();
  try {
    await api(`/api/budgets/${it.budget_id}`, { method: 'PUT', body: { amount: v } });
    const prev = it.budget;
    await load();
    ui.undoable(`${it.name} budget set to ${fmtMoney(v, bud.currency)}`, async () => { await api(`/api/budgets/${it.budget_id}`, { method: 'PUT', body: { amount: prev } }); await load(); });
  } catch (err) { toast(err.message, { type: 'error' }); render(); }
}
function openMenu(anchor, it) {
  if (!it) return;
  ui.menu(anchor, [
    { label: 'Change limit', icon: 'pencil', onClick: () => { const b = $(`.bud-row[data-id="${it.budget_id}"] [data-act="edit-amount"]`); if (b) startAmountEdit(b); } },
    { label: it.note ? 'Edit note…' : 'Add note…', icon: 'file-text', onClick: () => openNoteModal(it) },
    { label: 'View transactions', icon: 'list', href: `/transactions.html${toQuery({ cat: it.category_id, range: bud.month === currentYm() ? 'this-month' : `month:${bud.month}` })}` },
    { divider: true },
    { label: 'Remove budget', icon: 'trash', danger: true, onClick: () => removeBudget(it) },
  ]);
}
async function removeBudget(it) {
  try {
    await api(`/api/budgets/${it.budget_id}`, { method: 'DELETE' });
    await load();
    ui.undoable(`${it.name} budget removed`, async () => { await api('/api/budgets', { method: 'POST', body: { category_id: it.category_id, month: bud.month, amount: it.budget, note: it.note } }); await load(); });
  } catch (err) { toast(err.message, { type: 'error' }); }
}
function openNoteModal(it) {
  const m = ui.modal({
    title: `Note for ${it.name}`,
    html: `<form id="bn-form"><div class="field"><label for="bn-note">Note</label><input id="bn-note" class="input" maxlength="200" value="${esc(it.note || '')}" placeholder="e.g. includes the gym" autofocus></div><button type="submit" hidden></button></form>`,
    actions: [{ label: 'Cancel' }, { label: 'Save', primary: true, onClick: async () => {
      await api(`/api/budgets/${it.budget_id}`, { method: 'PUT', body: { note: m.el.querySelector('#bn-note').value.trim() || null } });
      toast('Note saved', { type: 'success' }); await load();
    } }],
  });
  m.el.querySelector('#bn-form').addEventListener('submit', (e) => { e.preventDefault(); m.el.querySelector('.modal-foot .btn-primary').click(); });
}
function openBudgetModal(preset = {}) {
  let categoryId = preset.category_id || null;
  const m = ui.modal({
    title: 'Add budget',
    html: `<form id="bf-form">
      <div class="field"><label data-required>Category</label><button type="button" class="btn btn-secondary btn-block" id="bf-cat" style="justify-content:space-between"><span id="bf-cat-label" class="row gap-2 text-3">Choose a category…</span>${icon('chevron-down')}</button><div class="hint">A category and its subcategories cannot both have a budget in the same month.</div></div>
      <div class="field"><label for="bf-amount" data-required>Monthly limit</label><input id="bf-amount" class="input num" type="number" min="0.01" step="0.01" inputmode="decimal" placeholder="0.00" value="${preset.amount ? Number(preset.amount).toFixed(2) : ''}"></div>
      <div class="field"><label for="bf-note">Note</label><input id="bf-note" class="input" maxlength="200" placeholder="Optional"></div>
      <div class="hint">For ${esc(fmtMonth(bud.month, { long: true }))}. Use “Copy last month” to carry budgets forward.</div>
      <button type="submit" hidden></button></form>`,
    actions: [{ label: 'Cancel' }, { label: 'Save budget', primary: true, onClick: async () => {
      if (!ui.validate(m.el, [{ sel: '#bf-cat', test: () => !!categoryId || 'Choose a category' }, { sel: '#bf-amount', test: (v) => Number(v) > 0 || 'Enter a limit above zero' }])) return false;
      const r = await api('/api/budgets', { method: 'POST', body: { category_id: categoryId, month: bud.month, amount: Number(m.el.querySelector('#bf-amount').value), note: m.el.querySelector('#bf-note').value.trim() || null } });
      const c = catOf(r.category_id);
      toast(`${c ? c.name : 'Budget'} · ${fmtMoney(r.amount, bud.currency)} a month`, { type: 'success' });
      await load();
    } }],
  });
  const setLabel = () => { const c = categoryId ? catOf(categoryId) : null; m.el.querySelector('#bf-cat-label').innerHTML = c ? `<i class="dot" style="--c:${catColorOf(c)}"></i><span class="text-1">${esc(c.path)}</span>` : '<span class="text-3">Choose a category…</span>'; };
  setLabel();
  m.el.querySelector('#bf-cat').addEventListener('click', (e) => categoryPicker({ anchor: e.currentTarget, value: categoryId, allowCreate: false, onPick: (c) => { if (c && c.kind === 'transfer') { toast('Transfers cannot be budgeted', { type: 'error' }); return; } categoryId = c ? c.id : null; setLabel(); ui.fieldError(m.el.querySelector('#bf-cat'), null); } }));
  m.el.querySelector('#bf-form').addEventListener('submit', (e) => { e.preventDefault(); m.el.querySelector('.modal-foot .btn-primary').click(); });
  if (categoryId) setTimeout(() => m.el.querySelector('#bf-amount').focus(), 30);
}
async function copyLastMonth() {
  const prev = shiftYm(bud.month, -1);
  await ui.busy($('#btn-copy'), async () => {
    const r = await api('/api/budgets/copy', { method: 'POST', body: { from: prev, to: bud.month } });
    toast(`${fmtNumber(r.copied)} budget${r.copied === 1 ? '' : 's'} copied from ${fmtMonth(prev, { long: true })}${r.skipped ? ` · ${fmtNumber(r.skipped)} already set` : ''}`, { type: 'success' });
    await load();
  });
}
