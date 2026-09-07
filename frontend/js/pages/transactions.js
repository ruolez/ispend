/* Transactions: URL-state filters, cursor pagination, selection + bulk bar,
   inline category picker, keyboard navigation, detail drawer, rule modal. */
const STATUS_OPTS = [['all', 'All'], ['uncategorized', 'Uncategorized'], ['suggested', 'Suggested'], ['transfer', 'Transfers'], ['excluded', 'Excluded']];
const SORT_DEFAULT = '-date';
const EVENT_TEXT = {
  imported: (d) => d && d.manual ? 'Added manually' : `Imported${d && d.filename ? ` from ${d.filename}` : ''}`,
  rule: (d, c) => `Rule set ${c(d)}`,
  merchant: (d, c) => `Remembered merchant → ${c(d)}`,
  builtin: (d, c) => `Suggested ${c(d)} (built-in hints)`,
  ai: (d, c) => `AI suggested ${c(d)}${d && d.confidence != null ? ` (${Math.round(d.confidence * 100)}%)` : ''}`,
  manual: (d, c) => d && d.accepted ? `Suggestion accepted → ${c(d)}` : d && d.rejected ? 'Suggestion rejected' : d && d.category_id == null ? 'Category cleared' : `Changed to ${c(d)}`,
  transfer: (d) => d && d.is_transfer === false ? 'Unmarked as transfer' : 'Marked as transfer',
  note: () => 'Note updated',
  excluded: (d) => d && d.is_excluded ? 'Excluded from reports' : 'Included in reports',
};
const EVENT_ICON = { imported: 'upload', rule: 'sliders', merchant: 'repeat', builtin: 'tag', ai: 'sparkles', manual: 'user', transfer: 'arrow-left-right', note: 'pencil', excluded: 'eye-off' };

const tx = {
  filters: { range: { preset: 'this-month' }, acct: [], cat: [], status: 'all', flow: '', q: '', sort: SORT_DEFAULT, statement: '', transfers: '' },
  items: [], byId: new Map(), cursor: null, total: 0, sumIn: 0, sumOut: 0, facets: null,
  loading: false, done: false, seq: 0,
  selection: new Set(), focus: -1,
  cats: new Map(), accounts: new Map(), catsFlat: [],
  observer: null, drawer: null,
};

initNav('transactions').then(async () => {
  await loadRefs();
  tx.displayCurrency = await store.displayCurrency();
  readUrl();
  $('#btn-export').innerHTML = `${icon('download', 'ico-sm')}<span class="label">Export CSV</span>`;
  paintDensity();
  $('#btn-density').addEventListener('click', () => { const d = Theme.density() === 'compact' ? 'comfortable' : 'compact'; Theme.setDensity(d); paintDensity(); api('/api/auth/me/preferences', { method: 'PUT', body: { density: d } }).catch(() => {}); });
  $('#btn-add').innerHTML = `${icon('plus', 'ico-sm')}<span class="label">Add transaction</span>`;
  $('#btn-add').addEventListener('click', openAddModal);
  $('.tx-search .ico-wrap').innerHTML = icon('search');
  $('#f-q').value = tx.filters.q;
  $('#f-q').addEventListener('input', debounce(() => { tx.filters.q = $('#f-q').value.trim(); applyFilters(); }, 250));
  $('#f-q').addEventListener('keydown', (e) => { if (e.key === 'Enter') { tx.filters.q = $('#f-q').value.trim(); applyFilters(); } if (e.key === 'Escape') { e.target.blur(); } });
  $('#f-clear').addEventListener('click', clearFilters);
  $('#f-accounts').addEventListener('click', openAccountFilter);
  $('#f-categories').addEventListener('click', openCategoryFilter);
  $('#f-status').addEventListener('click', (e) => { const b = e.target.closest('[data-status]'); if (!b) return; tx.filters.status = b.dataset.status; applyFilters(); });
  $('#f-flow').addEventListener('click', (e) => { const b = e.target.closest('[data-flow]'); if (!b) return; tx.filters.flow = tx.filters.flow === b.dataset.flow ? '' : b.dataset.flow; applyFilters(); });
  $('#tx-table thead').addEventListener('click', (e) => { const th = e.target.closest('th.sortable'); if (th) toggleSort(th.dataset.sort); });
  $('#tx-check-all').addEventListener('change', (e) => { if (e.target.checked) tx.items.forEach((i) => tx.selection.add(i.id)); else tx.selection.clear(); paintSelection(); });
  $('#tx-body').addEventListener('click', onRowClick);
  $('#tx-body').addEventListener('change', (e) => { const cb = e.target.closest('input[data-select]'); if (cb) { toggleSelect(Number(cb.dataset.select), cb.checked); } });
  document.body.addEventListener('click', (e) => { const b = e.target.closest('[data-act="reload"]'); if (b) reload(); });
  window.addEventListener('popstate', () => { readUrl(); reload(); });
  store.on('categories-changed', async () => { await loadRefs(); rerenderAll(); });
  setupObserver();
  window.addEventListener('resize', debounce(setupObserver, 200));
  registerShortcuts();
  paintToolbar();
  await reload();
  const q = qs();
  if (q.open) openDrawer(Number(q.open));
}).catch((err) => { $('#tx-body').innerHTML = `<tr><td colspan="7">${ui.errorBox(err.message, { retry: 'reload' })}</td></tr>`; });

function setupObserver() {
  const wrap = $('#tx-wrap');
  const scrollsInside = window.innerWidth > 768 && getComputedStyle(wrap).overflowY !== 'visible';
  const root = scrollsInside ? wrap : null;
  if (tx.observer && tx.observerRoot === root) return;
  if (tx.observer) tx.observer.disconnect();
  tx.observerRoot = root;
  // an observation taken while the skeleton was showing must not load page 2 right after page 1 renders
  tx.observer = new IntersectionObserver((entries) => { if (entries.some((x) => x.isIntersecting && x.time >= (tx.renderedAt || 0))) loadMore(); }, { root, rootMargin: '400px' });
  tx.observer.observe($('#tx-sentinel'));
}

/* ---------- refs ---------- */
async function loadRefs() {
  const [flat, accts] = await Promise.all([store.categoriesFlat(), store.accounts()]);
  tx.catsFlat = flat;
  tx.cats = new Map(flat.map((c) => [c.id, c]));
  tx.accounts = new Map(accts.map((a) => [a.id, a]));
}
function catOf(id) { return tx.cats.get(Number(id)) || null; }
function acctOf(id) { return tx.accounts.get(Number(id)) || null; }
function currencyOf(item) { const a = acctOf(item.account_id); return item.currency || (a && a.currency) || 'USD'; }

/* ---------- URL state ---------- */
function readUrl() {
  const q = qs();
  tx.filters.range = initialRange(q, { preset: 'this-month' });
  if (q.statement && !(q.range || q.from || q.to)) tx.filters.range = { preset: 'all' };
  tx.filters.acct = (q.acct || '').split(',').filter(Boolean).map(Number);
  tx.filters.cat = (q.cat || '').split(',').filter(Boolean);
  tx.filters.status = STATUS_OPTS.some(([k]) => k === q.status) ? q.status : 'all';
  tx.filters.flow = ['in', 'out'].includes(q.flow) ? q.flow : '';
  tx.filters.q = q.q || '';
  tx.filters.sort = q.sort || SORT_DEFAULT;
  tx.filters.statement = q.statement || '';
  tx.filters.transfers = q.transfers || '';
}
function writeUrl() {
  const f = tx.filters;
  setQs({ ...rangeToQuery(f.range), acct: f.acct, cat: f.cat, status: f.status === 'all' ? null : f.status, flow: f.flow || null, q: f.q, sort: f.sort === SORT_DEFAULT ? null : f.sort, statement: f.statement, transfers: f.transfers, open: null });
}
function queryParams(extra = {}) {
  const f = tx.filters;
  return { ...rangeToQuery(f.range), account_id: f.acct, category_id: f.cat, status: f.status === 'all' ? null : f.status, flow: f.flow || null, q: f.q, sort: f.sort, statement_id: f.statement, transfers: f.transfers, ...extra };
}
function hasFilters() {
  const f = tx.filters;
  return f.acct.length || f.cat.length || f.status !== 'all' || f.flow || f.q || f.statement || f.transfers || (f.range && !['this-month', 'all'].includes(f.range.preset));
}
function emptyListHtml() {
  if (hasFilters()) return ui.emptyState({ icon: 'filter', title: 'No transactions match', body: 'Try widening the date range or clearing filters.', action: { label: 'Clear filters', act: 'clear-filters' } });
  if (tx.filters.range && tx.filters.range.preset === 'this-month') return ui.emptyState({ icon: 'calendar', title: 'No transactions this month', body: 'Import a statement, or show all time to see older transactions.', action: { label: 'Show all time', act: 'show-all' } });
  return ui.emptyState({ icon: 'list', title: 'No transactions yet', body: 'Import a statement to get started.', action: { label: 'Import a statement', href: '/import.html' } });
}
function clearFilters() {
  tx.filters = { range: { preset: 'all' }, acct: [], cat: [], status: 'all', flow: '', q: '', sort: SORT_DEFAULT, statement: '', transfers: '' }; // clearing shows everything, not just this month
  $('#f-q').value = '';
  applyFilters();
}
function applyFilters() { writeUrl(); paintToolbar(); reload(); }
function toggleSort(key) {
  const cur = tx.filters.sort;
  if (key === 'date') tx.filters.sort = cur === '-date' ? 'date' : '-date';
  else if (key === 'amount') tx.filters.sort = cur === '-amount' ? 'amount' : '-amount';
  else if (key === 'merchant') tx.filters.sort = cur === 'merchant' ? '-merchant' : 'merchant';
  applyFilters();
}

/* ---------- toolbar ---------- */
function paintToolbar() {
  const f = tx.filters;
  mountRangeButton($('#f-range'), f.range, (v) => { f.range = v; periodSet(v); applyFilters(); });
  const acctBtn = $('#f-accounts');
  acctBtn.innerHTML = `${icon('landmark', 'ico-sm')}<span>${f.acct.length ? (f.acct.length === 1 && acctOf(f.acct[0]) ? esc(acctOf(f.acct[0]).name) : `${f.acct.length} accounts`) : 'All accounts'}</span>${icon('chevron-down', 'ico-sm')}`;
  acctBtn.classList.toggle('active', !!f.acct.length);
  const catBtn = $('#f-categories');
  let catLabel = 'All categories';
  if (f.cat.length === 1) catLabel = f.cat[0] === 'none' ? 'Uncategorized' : (catOf(f.cat[0]) || {}).name || 'Category';
  else if (f.cat.length > 1) catLabel = `${f.cat.length} categories`;
  catBtn.innerHTML = `${icon('tags', 'ico-sm')}<span>${esc(catLabel)}</span>${icon('chevron-down', 'ico-sm')}`;
  const st = tx.facets ? tx.facets.status : null;
  $('#f-status').innerHTML = STATUS_OPTS.map(([k, l]) => {
    const n = st && k === 'uncategorized' ? st.uncategorized : st && k === 'suggested' ? st.suggested : st && k === 'transfer' ? st.transfer : null;
    return `<button type="button" class="seg-btn ${f.status === k ? 'active' : ''}" data-status="${k}" aria-pressed="${f.status === k}">${l}${n ? `<span class="pill pill-soft count">${fmtNumber(n)}</span>` : ''}</button>`;
  }).join('');
  $$('#f-flow .seg-btn').forEach((b) => { const on = b.dataset.flow === f.flow; b.classList.toggle('active', on); b.setAttribute('aria-pressed', String(on)); });
  $('#f-clear').hidden = !hasFilters();
  $$('#tx-table th.sortable').forEach((th) => {
    const k = th.dataset.sort;
    const s = f.sort;
    th.setAttribute('aria-sort', s === `-${k}` ? 'descending' : s === k ? 'ascending' : 'none');
  });
  $('#btn-export').href = '/api/transactions/export' + toQuery(queryParams());
  $('#tx-sub').textContent = f.statement ? 'Rows imported from one statement.' : 'Every charge across your accounts.';
}
function openAccountFilter() {
  const counts = new Map(((tx.facets || {}).accounts || []).map((a) => [a.id, a.n]));
  const selected = new Set(tx.filters.acct.map(String));
  ui.multiFilter($('#f-accounts'), {
    title: 'Accounts',
    options: Array.from(tx.accounts.values()).filter((a) => a.is_active || counts.has(a.id)).map((a) => ({ value: String(a.id), label: a.name, count: counts.get(a.id) || 0, color: a.color })),
    selected,
    onChange: (set) => { tx.filters.acct = Array.from(set).map(Number); applyFilters(); },
  });
}
function openCategoryFilter() {
  const counts = new Map(((tx.facets || {}).categories || []).map((c) => [c.id == null ? 'none' : String(c.id), c.n]));
  const selected = new Set(tx.filters.cat);
  const opts = [{ value: 'none', label: 'Uncategorized', count: counts.get('none') || 0 }];
  tx.catsFlat.forEach((c) => {
    let n = counts.get(String(c.id)) || 0;
    if (c.depth === 0) tx.catsFlat.filter((x) => x.parent_id === c.id).forEach((x) => { n += counts.get(String(x.id)) || 0; });
    opts.push({ value: String(c.id), label: c.name, indent: c.depth || 0, count: n, color: c.color || c.parent_color });
  });
  ui.multiFilter($('#f-categories'), { title: 'Categories', options: opts, selected, searchable: true, onChange: (set) => { tx.filters.cat = Array.from(set); applyFilters(); } });
}
function paintDensity() { const t = Theme.density() === 'compact' ? 'Comfortable rows' : 'Compact rows'; $('#btn-density').innerHTML = icon(Theme.density() === 'compact' ? 'list' : 'menu'); $('#btn-density').title = t; $('#btn-density').setAttribute('aria-label', t); }

/* ---------- loading ---------- */
async function reload() {
  tx.seq += 1; tx.loading = false; // a reload supersedes any load still in flight
  tx.items = []; tx.byId.clear(); tx.cursor = null; tx.done = false; tx.selection.clear(); tx.focus = -1;
  $('#tx-body').innerHTML = ui.skeletonRows(8, 7);
  $('#tx-foot').innerHTML = '';
  $('#tx-summary').innerHTML = `${ui.skeleton(220, 12)}`;
  paintSelection();
  await loadMore(true);
}
async function loadMore(first = false) {
  if (tx.loading || tx.done) return;
  tx.loading = true;
  const seq = ++tx.seq;
  const params = queryParams({ limit: 100, cursor: tx.cursor });
  try {
    const r = await api('/api/transactions' + toQuery(params));
    if (seq !== tx.seq) return;
    tx.total = r.total; tx.sumIn = r.sum_in; tx.sumOut = r.sum_out; tx.skipped = r.skipped || { count: 0, sum: 0 }; tx.facets = r.facets; tx.currencies = r.currencies || [];
    tx.cursor = r.next_cursor; tx.done = !r.next_cursor;
    const startIdx = tx.items.length;
    r.items.forEach((it) => { tx.items.push(it); tx.byId.set(it.id, it); });
    if (first) { $('#tx-body').innerHTML = ''; paintToolbar(); paintSummary(); tx.renderedAt = performance.now(); }
    if (!tx.items.length) {
      $('#tx-body').innerHTML = `<tr><td colspan="7">${emptyListHtml()}</td></tr>`;
    } else {
      $('#tx-body').insertAdjacentHTML('beforeend', r.items.map((it, i) => rowHtml(it, startIdx + i)).join(''));
    }
    $('#tx-foot').innerHTML = tx.done ? (tx.items.length ? `<div class="tx-foot-msg">${fmtNumber(tx.items.length)} of ${fmtNumber(tx.total)} shown</div>` : '') : `<div class="tx-foot-msg"><span class="spinner"></span> Loading more…</div>`;
  } catch (err) {
    if (seq !== tx.seq) return;
    if (first) $('#tx-body').innerHTML = `<tr><td colspan="7">${ui.errorBox(err.message, { retry: 'reload' })}</td></tr>`;
    else $('#tx-foot').innerHTML = `<div class="tx-foot-msg">${ui.errorBox(err.message, { retry: 'reload' })}</div>`;
  } finally { if (seq === tx.seq) tx.loading = false; }
}
/* Totals and facets for the current filters without re-rendering the list (after edits that move money). */
async function refreshTotals() {
  const seq = tx.seq;
  try {
    const r = await api('/api/transactions' + toQuery(queryParams({ limit: 1 })));
    if (seq !== tx.seq) return;
    tx.total = r.total; tx.sumIn = r.sum_in; tx.sumOut = r.sum_out; tx.skipped = r.skipped || { count: 0, sum: 0 }; tx.facets = r.facets; tx.currencies = r.currencies || [];
    paintSummary();
  } catch { /* the summary refreshes with the next reload */ }
}
function paintSummary() {
  const curs = tx.currencies && tx.currencies.length ? tx.currencies : (tx.items.length ? [currencyOf(tx.items[0])] : []);
  const cur = curs.length === 1 ? curs[0] : (tx.displayCurrency || curs[0] || 'USD');
  const mixed = curs.length > 1;
  $('#tx-summary').innerHTML = `<span><b>${fmtNumber(tx.total)}</b> transaction${tx.total === 1 ? '' : 's'}</span>${tx.filters.flow !== 'in' ? `<span>Spent <b>${fmtMoney(Math.abs(tx.sumOut), cur)}</b></span>` : ''}${tx.filters.flow !== 'out' ? `<span>Received <b>${fmtMoney(tx.sumIn, cur)}</b></span>` : ''}${!tx.filters.flow ? `<span>Net <b class="${tx.sumIn + tx.sumOut >= 0 ? 'text-success' : ''}">${fmtMoney(tx.sumIn + tx.sumOut, cur, { sign: 'always' })}</b></span>` : ''}${tx.skipped.count ? `<span class="text-3" title="Transfers between your own accounts and excluded transactions are not counted as spent or received">${plural(tx.skipped.count, 'transfer/excluded row')} · ${fmtMoney(tx.skipped.sum, cur)} not counted</span>` : ''}${mixed ? `<span class="badge badge-warning" title="Totals add up ${esc(curs.join(' and '))} amounts without conversion">${icon('alert-triangle', 'ico-sm')}Mixed currencies (${esc(curs.join(', '))})</span>` : ''}`;
}
$('#tx-body') && $('#tx-body').addEventListener('click', (e) => { if (e.target.closest('[data-act="clear-filters"]')) clearFilters(); if (e.target.closest('[data-act="show-all"]')) { tx.filters.range = { preset: 'all' }; periodSet(tx.filters.range); applyFilters(); } });

/* ---------- rows ---------- */
function catCellHtml(it) {
  const c = catOf(it.category_id);
  if (it.is_transfer && !c) return `<div class="catcell"><button type="button" class="catchip" data-cat-pick="${it.id}"><i class="dot" style="--c:var(--c10)"></i><span class="catchip-label">Transfer</span></button></div>`;
  if (!c) return `<div class="catcell"><button type="button" class="catchip catchip--empty" data-cat-pick="${it.id}" aria-label="Choose category"><i class="dot"></i><span class="catchip-label">Categorize</span></button></div>`;
  const color = c.color || c.parent_color || 'c1';
  if (it.category_status === 'suggested') {
    return `<div class="catcell"><button type="button" class="catchip catchip--suggested" data-cat-pick="${it.id}" title="Suggested by ${it.category_source === 'ai' ? 'AI' : 'iSpend'}${it.category_confidence != null ? ` · ${Math.round(it.category_confidence * 100)}%` : ''} — click to change">${icon('sparkles')}<span class="catchip-label">${esc(c.name)}</span></button>
      <span class="sugg-act"><button type="button" class="btn btn-icon btn-ghost btn-xs btn-accept" data-accept="${it.id}" title="Accept suggestion" aria-label="Accept suggestion">${icon('check')}</button><button type="button" class="btn btn-icon btn-ghost btn-xs btn-reject" data-reject="${it.id}" title="Reject suggestion" aria-label="Reject suggestion">${icon('x')}</button></span></div>`;
  }
  return `<div class="catcell"><button type="button" class="catchip" data-cat-pick="${it.id}" title="${esc(c.path)} — click to change"><i class="dot" style="--c:var(--${esc(color)})"></i><span class="catchip-label">${esc(c.name)}</span></button></div>`;
}
function rowHtml(it, idx) {
  const a = acctOf(it.account_id);
  const cur = currencyOf(it);
  const amtCls = it.is_transfer ? 'amt--transfer' : it.is_excluded ? 'amt--excluded' : it.amount > 0 ? 'amt--income' : 'amt--expense';
  return `<tr class="tx-row ${it.is_transfer ? 'is-transfer' : ''} ${it.is_excluded ? 'is-excluded' : ''} ${idx === tx.focus ? 'is-focused' : ''}" data-id="${it.id}" data-idx="${idx}" aria-selected="${tx.selection.has(it.id)}" tabindex="-1">
    <td class="col-check"><input type="checkbox" class="check" data-select="${it.id}" ${tx.selection.has(it.id) ? 'checked' : ''} aria-label="Select"></td>
    <td class="col-date num">${fmtDate(it.txn_date)}</td>
    <td class="col-merchant"><div class="merchant"><span class="merchant-name">${esc(it.merchant_name)}${it.is_transfer ? '<span class="badge badge-neutral badge-mini">Transfer</span>' : ''}${it.is_excluded && !it.is_transfer ? '<span class="badge badge-neutral badge-mini">Excluded</span>' : ''}${it.notes ? `<span class="badge badge-mini badge-neutral" title="${esc(it.notes)}">${icon('pencil', 'ico-sm')}</span>` : ''}</span><span class="merchant-raw" data-date="${esc(fmtDate(it.txn_date))}" title="${esc(it.description_raw)}">${esc(it.description_raw)}</span></div></td>
    <td class="col-cat">${catCellHtml(it)}</td>
    <td class="col-acct">${a ? `<span class="acct"><i class="acct-mark" style="--c:var(--${esc(a.color || 'c1')})">${esc(initials(a.name).slice(0, 1))}</i><span class="truncate">${esc(a.name)}</span></span>` : ''}</td>
    <td class="col-amt right"><span class="amt ${amtCls}">${fmtMoney(it.amount, cur, { sign: 'always' })}</span></td>
    <td class="col-actions"><div class="row-actions"><button type="button" class="btn btn-icon btn-ghost btn-xs" data-open="${it.id}" title="Details" aria-label="Details">${icon('eye')}</button><button type="button" class="btn btn-icon btn-ghost btn-xs" data-menu="${it.id}" title="More" aria-label="More">${icon('more-horizontal')}</button></div></td>
  </tr>`;
}
function rerenderRow(id) {
  const it = tx.byId.get(id);
  const tr = $(`tr[data-id="${id}"]`);
  if (!it || !tr) return;
  const idx = Number(tr.dataset.idx);
  const tmp = document.createElement('tbody');
  tmp.innerHTML = rowHtml(it, idx);
  tr.replaceWith(tmp.firstElementChild);
}
function rerenderAll() { $('#tx-body').innerHTML = tx.items.map((it, i) => rowHtml(it, i)).join(''); }
function removeRows(ids) {
  ids.forEach((id) => { const tr = $(`tr[data-id="${id}"]`); if (tr) tr.remove(); tx.byId.delete(id); tx.selection.delete(id); });
  tx.items = tx.items.filter((it) => !ids.includes(it.id));
  tx.items.forEach((it, i) => { const tr = $(`tr[data-id="${it.id}"]`); if (tr) tr.dataset.idx = String(i); });
  if (tx.focus >= tx.items.length) tx.focus = tx.items.length - 1;
  paintSelection();
}

/* ---------- selection & focus ---------- */
function toggleSelect(id, on) {
  if (on == null) on = !tx.selection.has(id);
  if (on) tx.selection.add(id); else tx.selection.delete(id);
  const tr = $(`tr[data-id="${id}"]`);
  if (tr) { tr.setAttribute('aria-selected', String(on)); const cb = tr.querySelector('input[data-select]'); if (cb) cb.checked = on; }
  paintSelection();
}
function paintSelection() {
  const n = tx.selection.size;
  const all = $('#tx-check-all');
  all.checked = n > 0 && n === tx.items.length;
  all.indeterminate = n > 0 && n < tx.items.length;
  $$('#tx-body tr[data-id]').forEach((tr) => {
    const on = tx.selection.has(Number(tr.dataset.id));
    if ((tr.getAttribute('aria-selected') === 'true') !== on) tr.setAttribute('aria-selected', String(on));
    const cb = tr.querySelector('input[data-select]'); if (cb && cb.checked !== on) cb.checked = on;
  });
  let bar = $('#tx-bulk');
  if (!n) { if (bar) bar.remove(); return; }
  const partial = tx.total > tx.items.length && n === tx.items.length ? ` <span class="text-3">· all ${fmtNumber(tx.items.length)} loaded of ${fmtNumber(tx.total)}</span>` : '';
  if (!bar) { bar = document.createElement('div'); bar.id = 'tx-bulk'; bar.className = 'floatbar'; bar.setAttribute('role', 'status'); document.body.appendChild(bar); bar.addEventListener('click', onBulkClick); }
  bar.innerHTML = `<span><span class="n">${fmtNumber(n)}</span> selected${partial}</span><span class="sep"></span>
    <button type="button" class="btn btn-primary btn-sm" data-bulk="categorize">${icon('tag', 'ico-sm')}Categorize</button>
    <button type="button" class="btn btn-ghost btn-sm" data-bulk="set_transfer">${icon('arrow-left-right', 'ico-sm')}Transfer</button>
    <button type="button" class="btn btn-ghost btn-sm" data-bulk="exclude">${icon('eye-off', 'ico-sm')}Exclude</button>
    <button type="button" class="btn btn-ghost btn-sm" data-bulk="more">${icon('more-horizontal', 'ico-sm')}</button>
    <span class="sep"></span><button type="button" class="btn btn-ghost btn-sm" data-bulk="clear" title="Clear selection (Esc)">${icon('x', 'ico-sm')}</button>`;
}
function setFocus(idx, { scroll = true } = {}) {
  const prev = $('tr.is-focused'); if (prev) prev.classList.remove('is-focused');
  tx.focus = Math.max(-1, Math.min(idx, tx.items.length - 1));
  if (tx.focus < 0) return;
  const tr = $(`tr[data-idx="${tx.focus}"]`);
  if (!tr) return;
  tr.classList.add('is-focused');
  if (scroll) tr.scrollIntoView({ block: 'nearest' });
  const it = tx.items[tx.focus];
  const c = catOf(it.category_id);
  announce(`Row ${tx.focus + 1} of ${tx.items.length}. ${it.merchant_name}, ${fmtMoney(it.amount, currencyOf(it))}, ${c ? c.name : 'uncategorized'}${tx.selection.has(it.id) ? ', selected' : ''}`);
  if (tx.focus >= tx.items.length - 5) loadMore();
}
function announce(text) { const l = $('#tx-live'); l.textContent = ''; setTimeout(() => { l.textContent = text; }, 30); }
function focusedItem() { return tx.focus >= 0 ? tx.items[tx.focus] : null; }

function registerShortcuts() {
  const noLayer = () => !ui.layers.length;
  ui.shortcuts.register('j', () => setFocus(tx.focus + 1), { when: noLayer, description: 'Next row' });
  ui.shortcuts.register('k', () => setFocus(Math.max(0, tx.focus - 1)), { when: noLayer, description: 'Previous row' });
  ui.shortcuts.register('ArrowDown', () => setFocus(tx.focus + 1), { when: noLayer });
  ui.shortcuts.register('ArrowUp', () => setFocus(Math.max(0, tx.focus - 1)), { when: noLayer });
  ui.shortcuts.register('x', () => { const it = focusedItem(); if (it) toggleSelect(it.id); }, { when: noLayer, description: 'Select row' });
  ui.shortcuts.register('c', () => { const it = focusedItem(); if (it) openPickerFor(it.id); }, { when: noLayer, description: 'Categorize row' });
  ui.shortcuts.register('Enter', () => { const it = focusedItem(); if (it) openDrawer(it.id); }, { when: noLayer, description: 'Open details' });
  ui.shortcuts.register('t', () => { const it = focusedItem(); if (it) updateItem(it.id, { is_transfer: !it.is_transfer }, it.is_transfer ? 'Unmarked as transfer' : 'Marked as transfer'); }, { when: noLayer, description: 'Toggle transfer' });
  ui.shortcuts.register('n', () => { const it = focusedItem(); if (it) openDrawer(it.id, { focusNotes: true }); }, { when: noLayer, description: 'Edit note' });
  ui.shortcuts.register('a', () => { const it = focusedItem(); if (it && it.category_status === 'suggested') bulk([it.id], 'accept_suggestion'); }, { when: noLayer, description: 'Accept suggestion' });
  ui.shortcuts.register('Escape', () => { if (tx.selection.size) { tx.selection.clear(); rerenderAll(); paintSelection(); } else if (tx.focus >= 0) setFocus(-1); }, { when: noLayer });
  window.PAGE_SHORTCUTS = [{ title: 'Transactions', items: [['j / k', 'Move between rows'], ['x', 'Select row'], ['c', 'Change category'], ['a', 'Accept suggestion'], ['↵', 'Open details'], ['t', 'Toggle transfer'], ['n', 'Edit note'], ['Esc', 'Clear selection']] }];
}

/* ---------- row interactions ---------- */
function onRowClick(e) {
  const t = e.target;
  const pick = t.closest('[data-cat-pick]'); if (pick) { e.stopPropagation(); return openPickerFor(Number(pick.dataset.catPick), pick); }
  const acc = t.closest('[data-accept]'); if (acc) { e.stopPropagation(); return bulk([Number(acc.dataset.accept)], 'accept_suggestion'); }
  const rej = t.closest('[data-reject]'); if (rej) { e.stopPropagation(); return bulk([Number(rej.dataset.reject)], 'reject_suggestion'); }
  const open = t.closest('[data-open]'); if (open) { e.stopPropagation(); return openDrawer(Number(open.dataset.open)); }
  const menu = t.closest('[data-menu]'); if (menu) { e.stopPropagation(); return openRowMenu(menu, Number(menu.dataset.menu)); }
  if (t.closest('input, button, a')) return;
  const tr = t.closest('tr[data-id]');
  if (!tr) return;
  const idx = Number(tr.dataset.idx);
  if (e.shiftKey && tx.focus >= 0) {
    const [a, b] = [Math.min(tx.focus, idx), Math.max(tx.focus, idx)];
    for (let i = a; i <= b; i++) tx.selection.add(tx.items[i].id);
    rerenderAll(); paintSelection(); setFocus(idx, { scroll: false });
    return;
  }
  setFocus(idx, { scroll: false });
  openDrawer(Number(tr.dataset.id));
}
function openRowMenu(anchor, id) {
  const it = tx.byId.get(id);
  if (!it) return;
  ui.menu(anchor, [
    { label: 'Details', icon: 'eye', onClick: () => openDrawer(id) },
    { label: 'Change category', icon: 'tag', onClick: () => openPickerFor(id) },
    it.category_status === 'suggested' ? { label: 'Accept suggestion', icon: 'check', onClick: () => bulk([id], 'accept_suggestion') } : null,
    { label: it.is_transfer ? 'Not a transfer' : 'Mark as transfer', icon: 'arrow-left-right', onClick: () => updateItem(id, { is_transfer: !it.is_transfer }, it.is_transfer ? 'Unmarked as transfer' : 'Marked as transfer') },
    { label: it.is_excluded ? 'Include in reports' : 'Exclude from reports', icon: it.is_excluded ? 'eye' : 'eye-off', onClick: () => updateItem(id, { is_excluded: !it.is_excluded }, it.is_excluded ? 'Included in reports' : 'Excluded from reports') },
    { label: 'Create rule from this…', icon: 'sliders', onClick: () => openRuleModal(id) },
    { label: `All from ${it.merchant_name}`, icon: 'search', onClick: () => { $('#f-q').value = it.merchant_name; tx.filters.q = it.merchant_name; applyFilters(); } },
    { divider: true },
    { label: 'Delete', icon: 'trash', danger: true, onClick: () => deleteItems([id]) },
  ].filter(Boolean));
}
async function openPickerFor(id, anchor) {
  const it = tx.byId.get(id);
  if (!it) return;
  anchor = anchor || $(`tr[data-id="${id}"] [data-cat-pick]`);
  if (!anchor) return;
  categoryPicker({ anchor, value: it.category_id, suggestedId: it.category_status === 'suggested' ? it.category_id : null, allowNone: !!it.category_id, onPick: (cat) => setCategory([id], cat ? cat.id : null) });
}

/* ---------- mutations ---------- */
async function setCategory(ids, categoryId) {
  const prev = ids.map((id) => ({ id, category_id: tx.byId.get(id)?.category_id ?? null, status: tx.byId.get(id)?.category_status, source: tx.byId.get(id)?.category_source }));
  ids.forEach((id) => { const it = tx.byId.get(id); if (it) { it.category_id = categoryId; it.category_status = categoryId ? 'confirmed' : 'none'; it.category_source = categoryId ? 'manual' : null; rerenderRow(id); } });
  const c = catOf(categoryId);
  try {
    if (ids.length === 1) await api(`/api/transactions/${ids[0]}`, { method: 'PUT', body: { category_id: categoryId } });
    else await api('/api/transactions/bulk', { method: 'POST', body: { ids, action: categoryId ? 'categorize' : 'uncategorize', category_id: categoryId } });
    toast(`${ids.length === 1 ? 'Categorized' : `${fmtNumber(ids.length)} categorized`} as ${c ? c.name : 'uncategorized'}`, { type: 'success', action: { label: 'Undo', fn: () => undoCategory(prev) } });
    afterChange();
  } catch (err) {
    prev.forEach((p) => { const it = tx.byId.get(p.id); if (it) { it.category_id = p.category_id; it.category_status = p.status; it.category_source = p.source; rerenderRow(p.id); } });
    toast(err.message, { type: 'error', action: { label: 'Retry', fn: () => setCategory(ids, categoryId) } });
  }
}
async function undoCategory(prev) {
  const groups = new Map();
  prev.forEach((p) => { const k = String(p.category_id); if (!groups.has(k)) groups.set(k, []); groups.get(k).push(p.id); });
  try {
    for (const [k, ids] of groups) {
      const cid = k === 'null' ? null : Number(k);
      await api('/api/transactions/bulk', { method: 'POST', body: { ids, action: cid ? 'categorize' : 'uncategorize', category_id: cid, learn: false } });
      ids.forEach((id) => { const it = tx.byId.get(id); const p = prev.find((x) => x.id === id); if (it) { it.category_id = cid; it.category_status = p.status; it.category_source = p.source; rerenderRow(id); } });
    }
    toast('Undone', { type: 'info' });
    afterChange();
  } catch (err) { toast(err.message, { type: 'error' }); }
}
async function updateItem(id, body, msg) {
  try {
    const updated = await api(`/api/transactions/${id}`, { method: 'PUT', body });
    Object.assign(tx.byId.get(id) || {}, updated);
    rerenderRow(id);
    if (msg) toast(msg, { type: 'success' });
    afterChange();
    if ('is_transfer' in body || 'is_excluded' in body) refreshTotals();
    return updated;
  } catch (err) { toast(err.message, { type: 'error' }); return null; }
}
async function bulk(ids, action, extra = {}) {
  try {
    const r = await api('/api/transactions/bulk', { method: 'POST', body: { ids, action, ...extra } });
    if (action === 'delete') { removeRows(ids); tx.total -= ids.length; paintSummary(); refreshTotals(); }
    else if (ids.length > 20) { await reload(); }
    else { await refreshItems(ids); refreshTotals(); }
    const labels = { accept_suggestion: 'Suggestion accepted', reject_suggestion: 'Suggestion rejected', set_transfer: 'Marked as transfer', unset_transfer: 'Unmarked as transfer', exclude: 'Excluded from reports', include: 'Included in reports', delete: 'Deleted', categorize: 'Categorized', uncategorize: 'Category cleared' };
    toast(`${labels[action] || 'Updated'}${ids.length > 1 ? ` · ${fmtNumber(r.updated)} rows` : ''}`, { type: 'success' });
    afterChange();
  } catch (err) { toast(err.message, { type: 'error' }); }
}
async function refreshItems(ids) {
  await Promise.all(ids.map(async (id) => {
    try { const it = await api(`/api/transactions/${id}`); const cur = tx.byId.get(id); if (cur) { Object.assign(cur, it); rerenderRow(id); } } catch { /* removed */ }
  }));
}
async function deleteItems(ids) {
  const ok = await ui.confirm({ title: ids.length === 1 ? 'Delete this transaction?' : `Delete ${fmtNumber(ids.length)} transactions?`, body: 'They will be removed from every report. Re-importing the statement brings them back.', confirmText: 'Delete', danger: true });
  if (!ok) return;
  await bulk(ids, 'delete');
}
function afterChange() { window.dispatchEvent(new Event('ispend:transactions-changed')); }
function onBulkClick(e) {
  const b = e.target.closest('[data-bulk]'); if (!b) return;
  const ids = Array.from(tx.selection);
  switch (b.dataset.bulk) {
    case 'categorize': return categoryPicker({ anchor: b, allowNone: true, onPick: (cat) => setCategory(ids, cat ? cat.id : null) });
    case 'set_transfer': return bulk(ids, 'set_transfer');
    case 'exclude': return bulk(ids, 'exclude');
    case 'clear': { tx.selection.clear(); rerenderAll(); paintSelection(); return; }
    case 'more': return ui.menu(b, [
      { label: 'Accept suggestions', icon: 'check', onClick: () => bulk(ids, 'accept_suggestion') },
      { label: 'Reject suggestions', icon: 'x', onClick: () => bulk(ids, 'reject_suggestion') },
      { label: 'Not a transfer', icon: 'arrow-left-right', onClick: () => bulk(ids, 'unset_transfer') },
      { label: 'Include in reports', icon: 'eye', onClick: () => bulk(ids, 'include') },
      { label: 'Flip sign (charge ⇄ payment)', icon: 'arrow-left-right', onClick: () => bulk(ids, 'flip_sign') },
      { divider: true },
      { label: 'Delete…', icon: 'trash', danger: true, onClick: () => deleteItems(ids) },
    ], { placement: 'top-end' });
    default:
  }
}

/* ---------- drawer ---------- */
async function openDrawer(id, { focusNotes } = {}) {
  const local = tx.byId.get(id);
  let flushNotes = null;
  const d = ui.drawer({ title: local ? local.merchant_name : 'Transaction', width: 500, html: `<div class="col gap-3">${ui.skeleton('40%', 28)}${ui.skeleton('60%', 14)}${ui.skeleton('100%', 80)}</div>`, onClose: () => { if (flushNotes) flushNotes(); } });
  tx.drawer = d;
  let it;
  try { it = await api(`/api/transactions/${id}`); } catch (err) { d.setBody(ui.errorBox(err.message)); return; }
  if (local) Object.assign(local, it, { events: undefined, merchant_others: undefined, statement: undefined });
  d.setTitle(it.merchant_name);
  d.setBody(drawerHtml(it));
  d.el.addEventListener('click', (e) => onDrawerClick(e, it, d));
  d.el.addEventListener('change', (e) => onDrawerChange(e, it, d));
  // Notes autosave: delegated so it survives setBody() re-renders; the draft lives outside
  // the DOM and is flushed on blur, after a typing pause, and when the drawer closes.
  let draft = null, saving = null;
  const saveNotes = async () => {
    const ta = d.el.querySelector('#txd-notes');
    const v = (draft != null ? draft : (ta ? ta.value : (it.notes || ''))).trim();
    if (v === (it.notes || '').trim()) { draft = null; return; }
    if (saving) return saving;
    saving = (async () => {
      try {
        const u = await updateItem(it.id, { notes: v }, 'Note saved');
        if (!u) return;
        it.notes = u.notes || '';
        if (draft != null && draft.trim() === (it.notes || '').trim()) draft = null;
        const ta2 = d.el.querySelector('#txd-notes');
        if (ta2 && document.activeElement !== ta2 && ta2.value !== (it.notes || '')) ta2.value = it.notes || '';
      } catch { /* toast shown */ }
      finally { saving = null; }
    })();
    return saving;
  };
  const debouncedSave = debounce(saveNotes, 800);
  flushNotes = () => { if (draft != null) saveNotes(); };
  d.el.addEventListener('input', (e) => { if (e.target && e.target.id === 'txd-notes') { draft = e.target.value; debouncedSave(); } });
  d.el.addEventListener('focusout', (e) => { if (e.target && e.target.id === 'txd-notes') { draft = e.target.value; saveNotes(); } });
  if (focusNotes) { const ta = d.el.querySelector('#txd-notes'); if (ta) ta.focus(); }
  if (qs().open) setQs({ open: null });
}
function drawerHtml(it) {
  const a = acctOf(it.account_id);
  const cur = currencyOf(it);
  const c = catOf(it.category_id);
  const catBtn = c
    ? `<button type="button" class="catchip ${it.category_status === 'suggested' ? 'catchip--suggested' : ''}" data-dact="pick">${it.category_status === 'suggested' ? icon('sparkles') : `<i class="dot" style="--c:var(--${esc(c.color || c.parent_color || 'c1')})"></i>`}<span class="catchip-label">${esc(c.path)}</span></button>`
    : `<button type="button" class="catchip catchip--empty" data-dact="pick"><i class="dot"></i><span class="catchip-label">Choose a category</span></button>`;
  const events = (it.events || []).map((ev) => {
    const cName = (dd) => { const cc = catOf(dd && dd.category_id); return cc ? cc.name : 'a category'; };
    const fn = EVENT_TEXT[ev.kind] || (() => ev.kind);
    return `<div class="tl-item"><span class="tl-dot">${icon(EVENT_ICON[ev.kind] || 'circle')}</span><div><div class="tl-text">${esc(fn(ev.detail || {}, cName))}${ev.username && ['manual', 'note', 'transfer', 'excluded'].includes(ev.kind) ? ` <span class="text-4">by ${esc(ev.username)}</span>` : ''}</div><div class="tl-time">${esc(fmtDateTime(ev.created_at))}</div></div></div>`;
  }).join('');
  const others = (it.merchant_others || []).map((o) => `<div class="list-item" data-open-other="${o.id}"><span class="text-3 num" style="width:64px">${fmtDate(o.txn_date)}</span><span class="grow truncate">${esc(o.description_raw)}</span><span class="amt ${o.amount > 0 ? 'amt--income' : ''}">${fmtMoney(o.amount, currencyOf(o))}</span></div>`).join('');
  return `
    <div class="txd-head">
      <div class="txd-amount ${it.amount > 0 ? 'text-success' : ''}">${fmtMoney(it.amount, cur, { sign: 'always' })}</div>
      <div class="meta"><span>${esc(fmtDateLong(it.txn_date))}</span>${it.posted_date && it.posted_date !== it.txn_date ? `<span class="text-4">· posted ${fmtDate(it.posted_date)}</span>` : ''}${a ? `<span class="acct"><i class="acct-mark" style="--c:var(--${esc(a.color || 'c1')})">${esc(initials(a.name).slice(0, 1))}</i>${esc(a.name)}</span>` : ''}</div>
      ${it.category_status === 'suggested' ? `<div class="row mt-2" style="gap:6px"><span class="text-3 fs-sm">Suggested${it.category_confidence != null ? ` · ${Math.round(it.category_confidence * 100)}% confidence` : ''}</span><button type="button" class="btn btn-xs btn-secondary" data-dact="accept">${icon('check', 'ico-sm')}Accept</button><button type="button" class="btn btn-xs btn-ghost" data-dact="reject">Reject</button></div>` : ''}
    </div>
    <div class="txd-section"><div class="section-label">Category</div><div class="row" style="gap:8px;flex-wrap:wrap">${catBtn}<button type="button" class="btn btn-ghost btn-sm" data-dact="rule">${icon('sliders', 'ico-sm')}Create rule from this</button></div></div>
    <div class="txd-section"><div class="section-label">Merchant</div><div class="txd-merchant-edit"><input class="input input-sm" id="txd-merchant" value="${esc(it.merchant_name)}" aria-label="Merchant name"><button type="button" class="btn btn-sm btn-secondary" data-dact="rename" title="Rename this merchant everywhere">Rename all</button></div>
      <div class="txd-raw mt-2" title="Original statement text">${esc(it.description_raw)}</div></div>
    <div class="txd-section"><div class="section-label">Notes</div><textarea class="textarea" id="txd-notes" rows="2" aria-label="Notes" placeholder="Add a note…">${esc(it.notes || '')}</textarea></div>
    <div class="txd-section txd-flags">
      <label class="switch"><span>Transfer between my accounts</span><input type="checkbox" data-dflag="is_transfer" ${it.is_transfer ? 'checked' : ''}><span class="switch-track"></span></label>
      <label class="switch"><span>Exclude from spending reports</span><input type="checkbox" data-dflag="is_excluded" ${it.is_excluded ? 'checked' : ''} ${it.is_transfer ? 'disabled' : ''}><span class="switch-track"></span></label>
    </div>
    <div class="txd-section"><div class="section-label">History</div><div class="timeline">${events || '<div class="text-3 fs-sm">No history recorded.</div>'}</div></div>
    ${others ? `<div class="txd-section txd-others"><div class="section-label">Other charges from ${esc(it.merchant_name)}</div><div class="list">${others}</div><a class="btn btn-ghost btn-sm mt-2" href="/transactions.html?q=${encodeURIComponent(it.merchant_name)}&range=all">See all</a></div>` : ''}
    ${it.statement ? `<div class="txd-section text-3 fs-sm">Imported from <a href="/transactions.html?statement=${it.statement.id}&range=all">${esc(it.statement.original_filename)}</a></div>` : ''}
    <div class="txd-danger"><button type="button" class="btn btn-danger btn-sm" data-dact="delete">${icon('trash', 'ico-sm')}Delete transaction</button></div>`;
}
function onDrawerClick(e, it, d) {
  const b = e.target.closest('[data-dact]');
  const other = e.target.closest('[data-open-other]');
  if (other) return openDrawer(Number(other.dataset.openOther));
  if (!b) return;
  switch (b.dataset.dact) {
    case 'pick': return categoryPicker({ anchor: b, value: it.category_id, allowNone: !!it.category_id, suggestedId: it.category_status === 'suggested' ? it.category_id : null, onPick: async (cat) => { await setCategory([it.id], cat ? cat.id : null); await refreshDrawer(it, d); } });
    case 'accept': return bulk([it.id], 'accept_suggestion').then(() => refreshDrawer(it, d));
    case 'reject': return bulk([it.id], 'reject_suggestion').then(() => refreshDrawer(it, d));
    case 'rule': return openRuleModal(it.id);
    case 'rename': { const name = d.el.querySelector('#txd-merchant').value.trim(); if (!name || name === it.merchant_name) return; return updateItem(it.id, { merchant_name: name, rename_all: true }, `Renamed to ${name}`).then((u) => { if (!u) return; it.merchant_name = name; d.setTitle(name); tx.items.filter((x) => x.merchant_key === it.merchant_key).forEach((x) => { x.merchant_name = name; rerenderRow(x.id); }); }); }
    case 'delete': return deleteItems([it.id]).then(() => { if (!tx.byId.has(it.id)) d.close(); });
    default:
  }
}
async function onDrawerChange(e, it, d) {
  const cb = e.target.closest('[data-dflag]');
  if (!cb) return;
  const key = cb.dataset.dflag;
  try {
    const u = await updateItem(it.id, { [key]: cb.checked }, key === 'is_transfer' ? (cb.checked ? 'Marked as transfer' : 'Unmarked as transfer') : (cb.checked ? 'Excluded from reports' : 'Included in reports'));
    if (!u) { cb.checked = !cb.checked; return; }
    await refreshDrawer(it, d);
  } catch { cb.checked = !cb.checked; }
}
/* Re-fetch the full record (history, other charges, statement) after an edit so the open drawer
   never shows a stale timeline; falls back to the list row when the fetch fails. */
async function refreshDrawer(it, d) {
  try {
    const fresh = await api(`/api/transactions/${it.id}`);
    Object.assign(it, fresh);
    const local = tx.byId.get(it.id);
    if (local) Object.assign(local, fresh, { events: undefined, merchant_others: undefined, statement: undefined });
  } catch {
    const u = tx.byId.get(it.id);
    if (u) Object.keys(u).forEach((k) => { if (u[k] !== undefined) it[k] = u[k]; });
  }
  if (tx.drawer === d) d.setBody(drawerHtml(it));
}

/* ---------- rule modal ---------- */
async function openRuleModal(id) {
  let draft;
  try { draft = await api(`/api/transactions/${id}/rule-draft`, { method: 'POST' }); } catch (err) { toast(err.message, { type: 'error' }); return; }
  const it = tx.byId.get(id) || {};
  let categoryId = draft.category_id || it.category_id || null;
  let previewTimer = null;
  const m = ui.modal({
    title: 'Create a rule', size: 'lg',
    html: `<form class="rule-form" id="rule-form">
      <div class="field"><label for="rf-name">Name</label><input id="rf-name" class="input" value="${esc(draft.name || '')}" placeholder="e.g. Starbucks → Coffee"></div>
      <div class="field-row">
        <div class="field"><label for="rf-field">When</label><select id="rf-field" class="select"><option value="merchant_key" ${draft.match_field === 'merchant_key' ? 'selected' : ''}>Merchant</option><option value="description_clean" ${draft.match_field === 'description_clean' ? 'selected' : ''}>Description</option><option value="description_raw" ${draft.match_field === 'description_raw' ? 'selected' : ''}>Raw statement text</option></select></div>
        <div class="field"><label for="rf-type">&nbsp;</label><select id="rf-type" class="select"><option value="equals" ${draft.match_type === 'equals' ? 'selected' : ''}>equals</option><option value="contains" ${draft.match_type === 'contains' ? 'selected' : ''}>contains</option><option value="starts_with" ${draft.match_type === 'starts_with' ? 'selected' : ''}>starts with</option><option value="regex" ${draft.match_type === 'regex' ? 'selected' : ''}>matches regex</option></select></div>
      </div>
      <div class="field"><label for="rf-pattern">Pattern</label><input id="rf-pattern" class="input mono" value="${esc(draft.pattern || '')}" spellcheck="false"><div class="hint">Merchant key of this charge: <code>${esc(draft.pattern || '')}</code>${draft.alt_pattern ? ` · cleaned description: <code>${esc(draft.alt_pattern)}</code>` : ''}</div></div>
      <div class="field"><label>Then set category</label><div><button type="button" class="catchip catbtn" id="rf-cat"></button></div></div>
      <div class="field-row">
        <div class="field"><label for="rf-account">Only for account</label><select id="rf-account" class="select"><option value="">Any account</option>${Array.from(tx.accounts.values()).filter((a) => a.is_active).map((a) => `<option value="${a.id}" ${it.account_id === a.id && draft.account_id ? 'selected' : ''}>${esc(a.name)}</option>`).join('')}</select></div>
        <div class="field"><label>Options</label><label class="switch"><input type="checkbox" id="rf-apply" checked><span class="switch-track"></span>Apply to existing uncategorized charges</label></div>
      </div>
      <div class="rule-preview" id="rf-preview">Checking how many transactions match…</div>
      <button type="submit" hidden></button></form>`,
    actions: [{ label: 'Cancel' }, { label: 'Create rule', primary: true, onClick: async () => {
      const body = readRuleForm(m.el, categoryId);
      if (!body.category_id) throw new Error('Choose a category for this rule');
      const r = await api('/api/rules', { method: 'POST', body });
      toast(`Rule created${r.applied ? ` · applied to ${fmtNumber(r.applied)} transactions` : ''}`, { type: 'success' });
      if (r.applied) { await reload(); afterChange(); }
    } }],
  });
  const paintCat = () => { const c = catOf(categoryId); const b = $('#rf-cat', m.el); b.className = `catchip catbtn ${c ? '' : 'catchip--empty'}`; b.innerHTML = c ? `<i class="dot" style="--c:var(--${esc(c.color || c.parent_color || 'c1')})"></i><span class="catchip-label">${esc(c.path)}</span>` : '<i class="dot"></i><span class="catchip-label">Choose a category</span>'; };
  paintCat();
  $('#rf-cat', m.el).addEventListener('click', (e) => categoryPicker({ anchor: e.currentTarget, value: categoryId, onPick: (cat) => { categoryId = cat ? cat.id : null; paintCat(); } }));
  const preview = async () => {
    const body = readRuleForm(m.el, categoryId);
    const host = $('#rf-preview', m.el);
    if (!body.pattern) { host.textContent = 'Enter a pattern to see matching transactions.'; return; }
    try {
      const r = await api('/api/rules/preview', { method: 'POST', body: { ...body, only_uncategorized: false } });
      const rU = await api('/api/rules/preview', { method: 'POST', body: { ...body, only_uncategorized: true } });
      host.innerHTML = `<b>${fmtNumber(r.count)}</b> existing transaction${r.count === 1 ? '' : 's'} match (${fmtNumber(rU.count)} uncategorized)${r.sample.length ? `<div class="sample">${r.sample.slice(0, 5).map((s) => `<span>${fmtDate(s.txn_date)} · ${esc(s.description_raw)} · ${fmtMoney(s.amount, currencyOf(s))}</span>`).join('')}</div>` : ''}`;
    } catch (err) { host.innerHTML = `<span class="text-danger">${esc(err.message)}</span>`; }
  };
  const schedule = () => { clearTimeout(previewTimer); previewTimer = setTimeout(preview, 300); };
  ['#rf-field', '#rf-type', '#rf-pattern', '#rf-account'].forEach((s) => { $(s, m.el).addEventListener('input', schedule); $(s, m.el).addEventListener('change', schedule); });
  $('#rule-form', m.el).addEventListener('submit', (e) => { e.preventDefault(); m.el.querySelector('.modal-foot .btn-primary').click(); });
  preview();
}
function readRuleForm(el, categoryId) {
  return {
    name: $('#rf-name', el).value.trim(), match_field: $('#rf-field', el).value, match_type: $('#rf-type', el).value, pattern: $('#rf-pattern', el).value.trim(),
    category_id: categoryId, account_id: $('#rf-account', el).value ? Number($('#rf-account', el).value) : null,
    apply_existing: $('#rf-apply', el).checked, only_uncategorized: true,
  };
}


/* ---------- manual entry ---------- */
function openAddModal() {
  const accounts = Array.from(tx.accounts.values()).filter((a) => a.is_active);
  const preferred = tx.filters.acct.length === 1 ? tx.filters.acct[0] : (accounts[0] || {}).id;
  const startNew = !accounts.length;
  let categoryId = null; let kind = 'charge';
  const ACCOUNT_TYPES = [['checking', 'Checking'], ['savings', 'Savings'], ['credit_card', 'Credit card'], ['line_of_credit', 'Line of credit'], ['loan', 'Loan'], ['investment', 'Investment'], ['cash', 'Cash'], ['other', 'Other']];
  const m = ui.modal({
    title: 'Add transaction',
    html: `<form id="add-form" class="col gap-3">
      <div class="field-row">
        <div class="field"><label for="ad-acct">Account</label><select id="ad-acct" class="select">${accounts.map((a) => `<option value="${a.id}" ${a.id === preferred ? 'selected' : ''}>${esc(a.name)} · ${esc(a.currency)}</option>`).join('')}<option value="__new__" ${startNew ? 'selected' : ''}>+ New account…</option></select></div>
        <div class="field"><label for="ad-date">Date</label><input id="ad-date" class="input" type="date" value="${toISODate(new Date())}" required></div>
      </div>
      <div id="ad-newacct" class="col gap-3 card-sunken" ${startNew ? '' : 'hidden'}>
        <div class="field"><label for="na-name">Account name</label><input id="na-name" class="input" placeholder="e.g. Cash wallet, BILT Card" autocomplete="off"></div>
        <div class="field-row">
          <div class="field"><label for="na-type">Type</label><select id="na-type" class="select">${ACCOUNT_TYPES.map(([v, l]) => `<option value="${v}">${l}</option>`).join('')}</select></div>
          <div class="field"><label for="na-cur">Currency</label><select id="na-cur" class="select"><option value="USD">USD</option><option value="CAD">CAD</option></select></div>
        </div>
        <div class="field"><label for="na-inst">Institution</label><select id="na-inst" class="select"><option value="">— Not set —</option></select></div>
      </div>
      <div class="field"><label for="ad-amount">Amount</label>
        <div class="row gap-2"><div class="seg" id="ad-kind" role="group" aria-label="Kind"><button type="button" class="seg-btn active" data-kind="charge" aria-pressed="true">Charge</button><button type="button" class="seg-btn" data-kind="income" aria-pressed="false">Income</button></div><input id="ad-amount" class="input num grow" type="number" step="0.01" min="0.01" placeholder="0.00" inputmode="decimal" required></div>
        <div class="hint">Charges are stored as money out, income as money in.</div></div>
      <div class="field"><label for="ad-desc">Description</label><input id="ad-desc" class="input" placeholder="e.g. Farmers market" required autocomplete="off"></div>
      <div class="field"><label>Category</label><button type="button" class="btn btn-secondary btn-block" id="ad-cat" style="justify-content:space-between"><span id="ad-cat-label" class="row gap-2 text-3">Choose a category (optional)…</span>${icon('chevron-down')}</button></div>
      <div class="field"><label for="ad-notes">Notes</label><textarea id="ad-notes" class="textarea" rows="2" placeholder="Optional"></textarea></div>
      <button type="submit" hidden></button></form>`,
    actions: [{ label: 'Cancel' }, { label: 'Add transaction', primary: true, onClick: async () => {
      const el = m.el;
      const amount = Number(el.querySelector('#ad-amount').value);
      const description = el.querySelector('#ad-desc').value.trim();
      const txn_date = el.querySelector('#ad-date').value;
      if (!txn_date) throw new Error('Pick a date');
      if (!(amount > 0)) throw new Error('Enter an amount greater than zero');
      if (!description) throw new Error('Enter a description');
      let accountId = el.querySelector('#ad-acct').value;
      if (accountId === '__new__') {
        const name = el.querySelector('#na-name').value.trim();
        if (!name) throw new Error('Enter a name for the new account');
        const a = await api('/api/accounts', { method: 'POST', body: { name, account_type: el.querySelector('#na-type').value, currency: el.querySelector('#na-cur').value, institution: el.querySelector('#na-inst').value || null } });
        store.invalidate('accounts');
        tx.accounts.set(a.id, { ...a, is_active: true });
        accountId = a.id;
        toast(`Account “${name}” created`, { type: 'success' });
      }
      const body = { account_id: Number(accountId), txn_date, amount: kind === 'charge' ? -amount : amount, description, category_id: categoryId, notes: el.querySelector('#ad-notes').value.trim() || null };
      const created = await api('/api/transactions', { method: 'POST', body });
      toast(`Added ${fmtMoney(created.amount, created.currency || currencyOf(created), { sign: 'always' })} · ${created.merchant_name}`, { type: 'success' });
      window.dispatchEvent(new Event('ispend:transactions-changed'));
      await reload();
      const row = $(`tr[data-id="${created.id}"]`);
      if (row) { const idx = tx.items.findIndex((i) => i.id === created.id); if (idx >= 0) setFocus(idx); }
      else toast('Added outside the current filter · switch the range to see it', { type: 'info' });
    } }],
  });
  const el = m.el;
  el.querySelector('#ad-kind').addEventListener('click', (e) => { const b = e.target.closest('[data-kind]'); if (!b) return; kind = b.dataset.kind; $$('#ad-kind .seg-btn', el).forEach((x) => { const on = x === b; x.classList.toggle('active', on); x.setAttribute('aria-pressed', String(on)); }); });
  const setCatLabel = () => { const c = categoryId ? catOf(categoryId) : null; el.querySelector('#ad-cat-label').innerHTML = c ? `<i class="dot" style="--c:var(--${esc(c.color || c.parent_color || 'c1')})"></i><span class="text-1">${esc(c.path)}</span>` : '<span class="text-3">Choose a category (optional)…</span>'; };
  el.querySelector('#ad-cat').addEventListener('click', (e) => categoryPicker({ anchor: e.currentTarget, value: categoryId, allowNone: !!categoryId, onPick: (c) => { categoryId = c ? c.id : null; if (c && !tx.cats.has(c.id)) tx.cats.set(c.id, c); setCatLabel(); } }));
  el.querySelector('#add-form').addEventListener('submit', (e) => { e.preventDefault(); el.querySelector('.modal-foot .btn-primary').click(); });
  attachDescriptionSuggest(el, {
    onPick: (sug) => {
      if (sug.category_id != null) { categoryId = sug.category_id; if (!tx.cats.has(categoryId)) { const c = catOf(categoryId); if (c) tx.cats.set(categoryId, c); } setCatLabel(); }
      const amt = el.querySelector('#ad-amount');
      if (!amt.value && sug.last_amount != null) {
        amt.value = Math.abs(Number(sug.last_amount)).toFixed(2);
        kind = Number(sug.last_amount) < 0 ? 'charge' : 'income';
        $$('#ad-kind .seg-btn', el).forEach((x) => { const on = x.dataset.kind === kind; x.classList.toggle('active', on); x.setAttribute('aria-pressed', String(on)); });
      }
    },
  });
  const acctSel = el.querySelector('#ad-acct'); const newBox = el.querySelector('#ad-newacct');
  const syncNew = () => { const on = acctSel.value === '__new__'; newBox.hidden = !on; if (on) setTimeout(() => el.querySelector('#na-name').focus(), 20); };
  acctSel.addEventListener('change', syncNew);
  store.get('institutions', '/api/accounts/institutions', { ttl: 600000 }).then((inst) => {
    const sel = el.querySelector('#na-inst'); if (!sel) return;
    sel.innerHTML = `<option value="">— Not set —</option>${(inst || []).map((i) => `<option value="${esc(i.key)}">${esc(i.label)}</option>`).join('')}`;
    sel.addEventListener('change', () => { if (['rbc', 'td', 'bmo', 'scotiabank'].includes(sel.value)) el.querySelector('#na-cur').value = 'CAD'; });
  }).catch(() => {});
  setTimeout(() => (startNew ? el.querySelector('#na-name') : el.querySelector('#ad-amount')).focus(), 30);
}

/* ---------- description autocomplete (manual entry) ---------- */
function attachDescriptionSuggest(root, { onPick }) {
  const input = root.querySelector('#ad-desc');
  if (!input) return;
  input.setAttribute('role', 'combobox'); input.setAttribute('aria-autocomplete', 'list'); input.setAttribute('aria-expanded', 'false');
  let pop = null, list = null, items = [], active = -1, seq = 0;
  const close = () => { if (pop) { const p = pop; pop = null; p.close(); } input.setAttribute('aria-expanded', 'false'); input.removeAttribute('aria-activedescendant'); active = -1; };
  const paint = () => {
    list.innerHTML = items.map((s, i) => {
      const c = s.category_id ? catOf(s.category_id) : null;
      return `<div class="sug-opt${i === active ? ' active' : ''}" id="sug-opt-${i}" role="option" aria-selected="${i === active}" data-i="${i}">
        <div class="sug-main"><span class="sug-name">${esc(s.merchant_name)}</span><span class="sug-meta">${esc(s.last_description || '')}</span></div>
        <div class="sug-side">${c ? `<span class="catchip catchip--static"><i class="dot" style="--c:var(--${esc(c.color || c.parent_color || 'c1')})"></i>${esc(c.name)}</span>` : '<span class="text-4 fs-sm">no category</span>'}<span class="sug-amt num">${s.last_amount != null ? fmtMoney(s.last_amount, s.currency || 'USD', { sign: 'always' }) : ''}</span><span class="text-4 fs-xs">×${s.n}</span></div>
      </div>`;
    }).join('');
    if (active >= 0) input.setAttribute('aria-activedescendant', `sug-opt-${active}`); else input.removeAttribute('aria-activedescendant');
  };
  const open = () => {
    if (pop) { paint(); pop.position(); return; }
    list = document.createElement('div'); list.className = 'popover sug-list'; list.setAttribute('role', 'listbox'); list.id = 'sug-list';
    input.setAttribute('aria-controls', 'sug-list');
    list.addEventListener('mousedown', (e) => e.preventDefault());
    list.addEventListener('click', (e) => { const o = e.target.closest('.sug-opt'); if (o) pick(Number(o.dataset.i)); });
    pop = ui.popover(input, list, { placement: 'bottom-start', matchWidth: true, closeOnOutside: true, onClose: () => { pop = null; } });
    pop.allowShortcuts = true;
    input.setAttribute('aria-expanded', 'true');
    paint();
  };
  const pick = (i) => { const s = items[i]; if (!s) return; input.value = s.merchant_name; close(); onPick(s); input.dispatchEvent(new Event('change')); };
  const search = debounce(async () => {
    const q = input.value.trim(); const my = ++seq;
    if (q.length < 2) { close(); return; }
    let res = [];
    try { res = await api(`/api/transactions/suggest?q=${encodeURIComponent(q)}`); } catch { res = []; }
    if (my !== seq || document.activeElement !== input) return;
    items = res; active = -1;
    if (!items.length) { close(); return; }
    open();
  }, 180);
  input.addEventListener('input', search);
  input.addEventListener('keydown', (e) => {
    if (!pop) { if (e.key === 'ArrowDown' && items.length) { e.preventDefault(); open(); } return; }
    if (e.key === 'ArrowDown') { e.preventDefault(); active = Math.min(items.length - 1, active + 1); paint(); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); active = Math.max(0, active - 1); paint(); }
    else if (e.key === 'Enter') { if (active >= 0) { e.preventDefault(); e.stopPropagation(); pick(active); } else close(); }
    else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close(); }
    else if (e.key === 'Tab') close();
  });
  input.addEventListener('blur', () => setTimeout(() => { if (document.activeElement !== input) close(); }, 120));
}
