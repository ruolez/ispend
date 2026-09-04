/* Categories: two-level tree with inline rename, color/icon popovers, add/merge/delete, reorder, side panel trend. */
const SLOTS = Array.from({ length: 12 }, (_, i) => `c${i + 1}`);
const KIND_LABEL = { expense: 'Expense', income: 'Income', transfer: 'Transfer' };
const state = { tree: [], flat: [], totals: {}, counts: {}, selected: null, currency: 'USD', dragId: null };
const COLLAPSED_KEY = 'ispend.catCollapsed';
let collapsed = new Set();
try { collapsed = new Set(JSON.parse(localStorage.getItem(COLLAPSED_KEY) || '[]')); } catch { collapsed = new Set(); }
function persistCollapsed() { try { localStorage.setItem(COLLAPSED_KEY, JSON.stringify(Array.from(collapsed))); } catch { /* ignore */ } }

initNav('categories').then(async (me) => {
  state.currency = await store.displayCurrency();
  $('[data-act="add-category"]').innerHTML = `${icon('plus')}<span>Add category</span>`;
  document.body.addEventListener('click', onAction);
  $('#cat-tree').addEventListener('dblclick', (e) => { const row = e.target.closest('.cat-row'); if (row && !e.target.closest('input')) startRename(Number(row.dataset.id)); });
  $('#cat-tree').addEventListener('keydown', onTreeKey);
  $('#cat-tree').addEventListener('focusin', (e) => { const row = e.target.closest('.cat-row'); if (row && e.target === row) setRoving(row); });
  wireDrag();
  const q = qs();
  if (q.id) state.selected = Number(q.id);
  await load();
});

async function load() {
  $('#cat-error').innerHTML = '';
  if (!state.tree.length) $('#cat-tree').innerHTML = `<div class="cat-empty">${ui.skeletonList(8)}</div>`;
  try {
    const [tree, top, sub] = await Promise.all([
      store.categories({ force: true }),
      api('/api/reports/by-category?range=this-month&level=top').catch(() => ({ categories: [] })),
      api('/api/reports/by-category?range=this-month&level=sub').catch(() => ({ categories: [] })),
    ]);
    state.tree = tree;
    state.flat = await store.categoriesFlat();
    state.totals = {};
    top.categories.forEach((c) => { if (c.id != null) state.totals[c.id] = c.total; });
    sub.categories.forEach((c) => { if (c.id != null && !(c.id in state.totals)) state.totals[c.id] = c.total; });
  } catch (err) {
    $('#cat-error').innerHTML = ui.errorBox(err.message, { retry: 'reload' });
    return;
  }
  render();
  renderSide();
}

function countOf(c) { return (c.txn_count || 0) + (c.children || []).reduce((s, k) => s + (k.txn_count || 0), 0); }

function rowHtml(c, isChild) {
  const total = state.totals[c.id] || 0;
  const count = isChild ? (c.txn_count || 0) : countOf(c);
  const hasKids = !isChild && c.children && c.children.length > 0;
  const expanded = hasKids ? !collapsed.has(c.id) : null;
  return `<div class="cat-row ${isChild ? 'cat-row--child' : 'cat-row--parent'} ${state.selected === c.id ? 'is-selected' : ''}" role="treeitem" tabindex="-1" data-id="${c.id}" data-parent="${c.parent_id || ''}" draggable="true" aria-selected="${state.selected === c.id}" aria-level="${isChild ? 2 : 1}" ${hasKids ? `aria-expanded="${expanded}" aria-owns="cat-group-${c.id}"` : ''}>
    <div class="cat-main">
      <span class="cat-grip" data-act="grip" title="Drag to reorder" aria-hidden="true">${icon('grip-vertical', 'ico-sm')}</span>
      ${hasKids ? `<button type="button" class="cat-chevron" data-act="toggle" data-id="${c.id}" tabindex="-1" aria-label="${expanded ? 'Collapse' : 'Expand'} ${esc(c.name)}" aria-expanded="${expanded}">${icon('chevron-down', 'ico-sm')}</button>` : ''}
      <button type="button" class="cat-icon" data-act="color" data-id="${c.id}" style="--c:${catColor(c.color)}" title="Change color or icon" aria-label="Change color">${icon(c.icon || 'tag')}</button>
      <span class="cat-name" data-name>${esc(c.name)}</span>
      ${!isChild && c.children && c.children.length ? `<span class="cat-sub-count">${c.children.length}</span>` : ''}
      ${!isChild && c.kind !== 'expense' ? `<span class="badge ${c.kind === 'income' ? 'badge-success' : 'badge-neutral'} cat-kind">${KIND_LABEL[c.kind]}</span>` : ''}
    </div>
    <span class="cat-count ${count ? '' : 'is-zero'}">${fmtNumber(count)}</span>
    <span class="cat-total ${total ? '' : 'is-zero'}">${total ? fmtMoney(total, state.currency) : '—'}</span>
    <div class="cat-actions">
      ${!isChild ? `<button type="button" class="btn btn-icon btn-ghost btn-xs" data-act="add-sub" data-id="${c.id}" title="Add subcategory">${icon('plus')}</button>` : ''}
      <button type="button" class="btn btn-icon btn-ghost btn-xs" data-act="rename" data-id="${c.id}" title="Rename">${icon('pencil')}</button>
      <button type="button" class="btn btn-icon btn-ghost btn-xs" data-act="menu" data-id="${c.id}" title="More">${icon('more-horizontal')}</button>
    </div>
  </div>`;
}

function render() {
  const host = $('#cat-tree');
  if (!state.tree.length) {
    host.innerHTML = `<div class="cat-empty">${ui.emptyState({ icon: 'tags', title: 'No categories', body: 'Add a category or restore the default set.', action: { label: 'Reset defaults', act: 'reset-defaults' } })}</div>`;
    return;
  }
  host.innerHTML = state.tree.map((p) => rowHtml(p, false) + ((p.children || []).length ? `<div role="group" id="cat-group-${p.id}" ${collapsed.has(p.id) ? 'hidden' : ''}>${p.children.map((c) => rowHtml(c, true)).join('')}</div>` : '')).join('');
  setRoving(host.querySelector(`.cat-row[data-id="${state.selected}"]`) || host.querySelector('.cat-row'));
}

/* ---------- Tree keyboard model (WAI-ARIA tree): roving tabindex, arrows, expand/collapse ---------- */
function setRoving(row) {
  if (!row) return;
  $$('.cat-row').forEach((r) => r.setAttribute('tabindex', r === row ? '0' : '-1'));
}
function visibleRows() { return $$('.cat-row').filter((r) => !r.closest('[hidden]')); }
function toggleParent(id, force) {
  const row = $(`.cat-row[data-id="${id}"]`); const group = document.getElementById(`cat-group-${id}`);
  if (!row || !group) return;
  const open = force != null ? force : collapsed.has(id);
  if (open) collapsed.delete(id); else collapsed.add(id);
  persistCollapsed();
  group.hidden = !open;
  row.setAttribute('aria-expanded', open);
  const chev = row.querySelector('.cat-chevron'); if (chev) { chev.setAttribute('aria-expanded', open); chev.setAttribute('aria-label', `${open ? 'Collapse' : 'Expand'} ${findCat(id).name}`); }
}
function onTreeKey(e) {
  const row = e.target.closest('.cat-row'); if (!row || e.target.matches('input')) return;
  const id = Number(row.dataset.id);
  if (e.altKey && (e.key === 'ArrowUp' || e.key === 'ArrowDown')) { e.preventDefault(); move(id, e.key === 'ArrowUp' ? -1 : 1); return; }
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  const rows = visibleRows(); const i = rows.indexOf(row);
  const focusRow = (r) => { if (r) { setRoving(r); r.focus(); } };
  switch (e.key) {
    case 'ArrowDown': e.preventDefault(); focusRow(rows[i + 1]); break;
    case 'ArrowUp': e.preventDefault(); focusRow(rows[i - 1]); break;
    case 'Home': e.preventDefault(); focusRow(rows[0]); break;
    case 'End': e.preventDefault(); focusRow(rows[rows.length - 1]); break;
    case 'ArrowRight': {
      e.preventDefault();
      if (row.getAttribute('aria-expanded') === 'false') toggleParent(id, true);
      else if (row.getAttribute('aria-expanded') === 'true') focusRow(document.getElementById(`cat-group-${id}`).querySelector('.cat-row'));
      break;
    }
    case 'ArrowLeft': {
      e.preventDefault();
      if (row.getAttribute('aria-expanded') === 'true') toggleParent(id, false);
      else if (row.dataset.parent) focusRow($(`.cat-row[data-id="${row.dataset.parent}"]`));
      break;
    }
    case 'Enter': case ' ': e.preventDefault(); select(id); break;
    case 'F2': e.preventDefault(); startRename(id); break;
    default:
  }
}

function findCat(id) { return state.flat.find((c) => c.id === Number(id)); }
function siblingsOf(cat) { return cat.parent_id ? (state.tree.find((p) => p.id === cat.parent_id) || {}).children || [] : state.tree; }

async function onAction(e) {
  const btn = e.target.closest('[data-act]');
  if (!btn) {
    const row = e.target.closest('.cat-row');
    if (row && !e.target.closest('input')) select(Number(row.dataset.id));
    return;
  }
  const id = btn.dataset.id ? Number(btn.dataset.id) : null;
  const act = btn.dataset.act;
  if (act === 'reload') return load();
  if (act === 'add-category') return openCategoryModal(null);
  if (act === 'add-sub') return openCategoryModal(findCat(id));
  if (act === 'rename') return startRename(id);
  if (act === 'color') return openStylePopover(btn, findCat(id));
  if (act === 'menu') return openMenu(btn, findCat(id));
  if (act === 'reset-defaults') {
    if (!(await ui.confirm({ title: 'Restore default categories?', body: 'Any default category you deleted will be re-created. Your own categories and transactions are not changed.', confirmText: 'Restore' }))) return;
    try { await api('/api/categories/reset-defaults', { method: 'POST' }); store.invalidate('categories'); toast('Default categories restored', { type: 'success' }); await load(); } catch (err) { toast(err.message, { type: 'error' }); }
    return;
  }
  if (act === 'side-color' || act === 'side-icon') { const cat = findCat(id); const anchor = btn; return openStylePopover(anchor, cat, act === 'side-icon' ? 'icon' : 'color'); }
  if (act === 'side-merge') return openMerge(findCat(id));
  if (act === 'side-delete') return deleteCategory(findCat(id));
  if (act === 'grip') return;
  if (act === 'toggle') { toggleParent(id); return; }
}

/* ---------- Select + side panel ---------- */
function select(id) {
  state.selected = state.selected === id ? null : id;
  setQs({ id: state.selected }, { replace: true, merge: true });
  $$('.cat-row').forEach((r) => { const on = Number(r.dataset.id) === state.selected; r.classList.toggle('is-selected', on); r.setAttribute('aria-selected', on); });
  if (state.selected) setRoving($(`.cat-row[data-id="${state.selected}"]`));
  renderSide();
}

async function renderSide() {
  const host = $('#cat-side');
  const cat = state.selected ? findCat(state.selected) : null;
  if (!cat) {
    host.innerHTML = `<div class="card"><div class="card-body">${ui.emptyState({ icon: 'tags', title: 'Select a category', body: 'See its recent trend, counts and actions here.' })}</div></div>`;
    return;
  }
  const total = state.totals[cat.id] || 0;
  const count = cat.depth === 0 ? countOf(state.tree.find((p) => p.id === cat.id) || cat) : (cat.txn_count || 0);
  host.innerHTML = `<div class="card"><div class="card-body">
    <div class="side-head"><button type="button" class="cat-icon cat-icon-lg" data-act="side-icon" data-id="${cat.id}" style="--c:${catColor(cat.color)}" title="Change icon">${icon(cat.icon || 'tag')}</button>
      <div class="grow"><div class="side-title">${esc(cat.name)}</div><div class="side-sub">${esc(cat.parent_name ? `${cat.parent_name} › subcategory` : `${KIND_LABEL[cat.kind] || 'Expense'} category`)}</div></div></div>
    <div class="side-stats"><div class="side-stat"><div class="l">This month</div><div class="v">${fmtMoney(total, state.currency)}</div></div><div class="side-stat"><div class="l">Transactions</div><div class="v">${fmtNumber(count)}</div></div></div>
    <div class="section-label mb-2">Last 6 months</div>
    <div class="side-chart is-loading chart-body" style="--h:140px;padding:0"><canvas id="side-chart"></canvas></div>
    <div class="side-actions">
      <a class="btn btn-secondary btn-sm" href="/transactions.html${toQuery({ cat: cat.id, range: 'all' })}">${icon('list')}View transactions</a>
      <a class="btn btn-ghost btn-sm" href="/rules.html${toQuery({ filter_cat: cat.id })}">${icon('sliders')}Rules for this category</a>
      <button type="button" class="btn btn-ghost btn-sm" data-act="side-color" data-id="${cat.id}">${icon('circle')}Change color</button>
      <button type="button" class="btn btn-ghost btn-sm" data-act="side-merge" data-id="${cat.id}">${icon('split')}Merge into another category</button>
      <button type="button" class="btn btn-ghost btn-sm text-danger" data-act="side-delete" data-id="${cat.id}">${icon('trash')}Delete</button>
    </div></div></div>`;
  loadSideTrend(cat);
}

function lastMonths(n) {
  const out = []; const now = new Date();
  for (let i = n - 1; i >= 0; i--) { const d = new Date(now.getFullYear(), now.getMonth() - i, 1); out.push(`${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`); }
  return out;
}
function monthRange(ym) { const [y, m] = ym.split('-').map(Number); return { from: `${ym}-01`, to: `${ym}-${String(new Date(y, m, 0).getDate()).padStart(2, '0')}` }; }

async function loadSideTrend(cat) {
  const months = lastMonths(6);
  const level = cat.depth === 0 ? 'top' : 'sub';
  let values = null;
  if (level === 'top') {
    // One call: the monthly report already carries the top-level series (top 7 + Other).
    try {
      const data = await api('/api/reports/monthly?months=6');
      const s = (data.series || []).find((x) => x.category_id === cat.id);
      if (s) values = months.map((ym) => { const j = data.months.indexOf(ym); return j >= 0 ? (s.values[j] || 0) : 0; });
    } catch { values = null; }
  }
  if (!values) {
    try {
      const res = await Promise.all(months.map((ym) => { const r = monthRange(ym); return api(`/api/reports/by-category${toQuery({ from: r.from, to: r.to, level })}`); }));
      values = res.map((r) => { const row = r.categories.find((c) => c.id === cat.id); return row ? row.total : 0; });
    } catch { values = months.map(() => 0); }
  }
  if (state.selected !== cat.id) return;
  const canvas = $('#side-chart'); if (!canvas) return;
  canvas.closest('.chart-body').classList.remove('is-loading');
  charts.makeChart(canvas, (t) => ({
    type: 'bar',
    data: { labels: months.map((m) => fmtMonth(m)), datasets: [{ data: values, backgroundColor: catColor(cat.color), borderRadius: 3, maxBarThickness: 26 }] },
    options: { ...charts.barOptions(t, { currency: state.currency }), scales: { x: { grid: { display: false }, border: { display: false }, ticks: { maxRotation: 0, font: { size: 10 } } }, y: { display: false, beginAtZero: true } }, plugins: { tooltip: { callbacks: charts.currencyTooltip(state.currency) } } },
  }));
}

/* ---------- Rename ---------- */
function startRename(id) {
  const row = $(`.cat-row[data-id="${id}"]`); if (!row) return;
  const span = row.querySelector('[data-name]'); if (!span || span.querySelector('input')) return;
  const cat = findCat(id);
  const input = document.createElement('input');
  input.className = 'cat-name-input'; input.value = cat.name; input.setAttribute('aria-label', 'Category name');
  span.textContent = ''; span.appendChild(input); input.focus(); input.select();
  let done = false;
  const finish = async (save) => {
    if (done) return; done = true;
    const name = input.value.trim();
    if (!save || !name || name === cat.name) { span.textContent = cat.name; return; }
    span.textContent = name;
    try { await api(`/api/categories/${id}`, { method: 'PUT', body: { name } }); store.invalidate('categories'); toast('Renamed', { type: 'success' }); await load(); }
    catch (err) { span.textContent = cat.name; toast(err.message, { type: 'error' }); }
  };
  input.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); finish(true); } if (e.key === 'Escape') { e.preventDefault(); finish(false); } e.stopPropagation(); });
  input.addEventListener('blur', () => finish(true));
  input.addEventListener('click', (e) => e.stopPropagation());
}

/* ---------- Color / icon popover ---------- */
function openStylePopover(anchor, cat, mode = 'color') {
  const el = document.createElement('div');
  el.className = mode === 'icon' ? 'icon-pop' : 'color-pop';
  const isParent = !cat.parent_id;
  const render = () => {
    el.innerHTML = mode === 'icon'
      ? `<div class="row-between mb-2"><span class="section-label">Icon</span><button type="button" class="btn btn-ghost btn-xs" data-mode="color">Color</button></div>
         <div class="icon-grid">${CATEGORY_ICONS.map((n) => `<button type="button" class="${cat.icon === n ? 'active' : ''}" data-icon="${esc(n)}" title="${esc(n)}">${icon(n)}</button>`).join('')}</div>`
      : `<div class="row-between mb-2"><span class="section-label">Color</span><button type="button" class="btn btn-ghost btn-xs" data-mode="icon">Icon</button></div>
         <div class="swatches">${SLOTS.map((s) => `<button type="button" class="swatch ${cat.color === s ? 'active' : ''}" data-color="${s}" style="--c:var(--${s})" aria-label="${s}"></button>`).join('')}</div>
         ${isParent ? `<label class="switch switch-sm"><input type="checkbox" id="prop-color" checked><span class="switch-track"></span><span class="fs-sm">Apply to subcategories</span></label>` : ''}`;
  };
  render();
  const pop = ui.popover(anchor, el, { placement: 'bottom-start' });
  el.addEventListener('click', async (e) => {
    const m = e.target.closest('[data-mode]'); if (m) { mode = m.dataset.mode; render(); pop.position(); return; }
    const sw = e.target.closest('[data-color]'); const ic = e.target.closest('[data-icon]');
    if (!sw && !ic) return;
    const body = sw ? { color: sw.dataset.color, propagate_color: isParent && (el.querySelector('#prop-color') || {}).checked !== false } : { icon: ic.dataset.icon };
    pop.close();
    try { await api(`/api/categories/${cat.id}`, { method: 'PUT', body }); store.invalidate('categories'); await load(); }
    catch (err) { toast(err.message, { type: 'error' }); }
  });
}

/* ---------- Row menu ---------- */
function openMenu(anchor, cat) {
  const sibs = siblingsOf(cat); const i = sibs.findIndex((s) => s.id === cat.id);
  ui.menu(anchor, [
    { label: 'View transactions', icon: 'list', href: `/transactions.html${toQuery({ cat: cat.id, range: 'all' })}` },
    { label: 'Rename', icon: 'pencil', onClick: () => startRename(cat.id) },
    { label: 'Change color or icon', icon: 'circle', onClick: () => openStylePopover(anchor, cat) },
    ...(cat.parent_id ? [] : [{ label: 'Add subcategory', icon: 'plus', onClick: () => openCategoryModal(cat) }]),
    { divider: true },
    { label: 'Move up', icon: 'arrow-up', disabled: i <= 0, onClick: () => move(cat.id, -1) },
    { label: 'Move down', icon: 'arrow-down', disabled: i >= sibs.length - 1, onClick: () => move(cat.id, 1) },
    { divider: true },
    { label: 'Merge into…', icon: 'split', onClick: () => openMerge(cat) },
    { label: 'Delete', icon: 'trash', danger: true, onClick: () => deleteCategory(cat) },
  ]);
}

/* ---------- Reorder ---------- */
async function reorderTo(ids) {
  try { await api('/api/categories/reorder', { method: 'PUT', body: { ids } }); store.invalidate('categories'); await load(); }
  catch (err) { toast(err.message, { type: 'error' }); }
}
function move(id, dir) {
  const cat = findCat(id); if (!cat) return;
  const sibs = siblingsOf(cat).map((s) => s.id); const i = sibs.indexOf(id); const j = i + dir;
  if (j < 0 || j >= sibs.length) return;
  sibs.splice(i, 1); sibs.splice(j, 0, id);
  reorderTo(sibs).then(() => { const row = $(`.cat-row[data-id="${id}"]`); if (row) row.focus(); });
}
function wireDrag() {
  const host = $('#cat-tree');
  host.addEventListener('dragstart', (e) => { const row = e.target.closest('.cat-row'); if (!row) return; state.dragId = Number(row.dataset.id); row.classList.add('is-dragging'); e.dataTransfer.effectAllowed = 'move'; try { e.dataTransfer.setData('text/plain', String(state.dragId)); } catch { /* ignore */ } });
  host.addEventListener('dragend', () => { state.dragId = null; $$('.cat-row').forEach((r) => r.classList.remove('is-dragging', 'is-dropover')); });
  host.addEventListener('dragover', (e) => {
    const row = e.target.closest('.cat-row'); if (!row || state.dragId == null) return;
    const src = findCat(state.dragId); const dst = findCat(Number(row.dataset.id));
    if (!src || !dst || (src.parent_id || null) !== (dst.parent_id || null)) return;
    e.preventDefault(); e.dataTransfer.dropEffect = 'move';
    $$('.cat-row.is-dropover').forEach((r) => r.classList.remove('is-dropover')); row.classList.add('is-dropover');
  });
  host.addEventListener('drop', (e) => {
    const row = e.target.closest('.cat-row'); if (!row || state.dragId == null) return;
    e.preventDefault();
    const src = findCat(state.dragId); const dstId = Number(row.dataset.id);
    if (!src || src.id === dstId) return;
    const sibs = siblingsOf(src).map((s) => s.id);
    const from = sibs.indexOf(src.id); let to = sibs.indexOf(dstId);
    if (from < 0 || to < 0) return;
    sibs.splice(from, 1); if (from < to) to -= 1; sibs.splice(to + (e.offsetY > row.offsetHeight / 2 ? 1 : 0), 0, src.id);
    reorderTo(sibs);
  });
}

/* ---------- Add / edit modal ---------- */
function openCategoryModal(parent) {
  const isSub = !!parent;
  let color = parent ? parent.color : SLOTS[(state.tree.length) % 12];
  let iconName = parent ? (parent.icon || 'tag') : 'tag';
  const html = `<form id="cat-form">
    <div class="field"><label for="cf-name">${isSub ? 'Subcategory name' : 'Name'}</label><input id="cf-name" class="input" required autofocus placeholder="${isSub ? `e.g. ${parent.name} › Something` : 'e.g. Kids'}"></div>
    ${isSub ? `<div class="hint mb-3">Inside <b>${esc(parent.name)}</b>. Inherits its color unless you pick another.</div>` : `<div class="field"><label for="cf-kind">Kind</label><select id="cf-kind" class="select"><option value="expense">Expense</option><option value="income">Income</option><option value="transfer">Transfer (excluded from spending)</option></select></div>`}
    <div class="field"><label>Color</label><div class="swatches" id="cf-colors">${SLOTS.map((s) => `<button type="button" class="swatch ${color === s ? 'active' : ''}" data-color="${s}" style="--c:var(--${s})" aria-label="${s}"></button>`).join('')}</div></div>
    <div class="field"><label>Icon</label><div class="icon-grid" id="cf-icons" style="max-height:150px;overflow-y:auto">${CATEGORY_ICONS.map((n) => `<button type="button" class="${iconName === n ? 'active' : ''}" data-icon="${esc(n)}" title="${esc(n)}">${icon(n)}</button>`).join('')}</div></div>
    <button type="submit" hidden></button></form>`;
  const m = ui.modal({
    title: isSub ? 'Add subcategory' : 'Add category', html,
    actions: [{ label: 'Cancel' }, { label: 'Create', primary: true, onClick: async () => {
      const name = m.el.querySelector('#cf-name').value.trim();
      if (!name) throw new Error('Name is required');
      const body = { name, color, icon: iconName, parent_id: parent ? parent.id : null };
      if (!isSub) body.kind = m.el.querySelector('#cf-kind').value;
      const cat = await api('/api/categories', { method: 'POST', body });
      store.invalidate('categories');
      toast(`Created ${cat.name}`, { type: 'success' });
      state.selected = cat.id;
      await load();
    } }],
  });
  m.el.querySelector('#cf-colors').addEventListener('click', (e) => { const b = e.target.closest('.swatch'); if (!b) return; color = b.dataset.color; $$('.swatch', m.el).forEach((s) => s.classList.toggle('active', s === b)); });
  m.el.querySelector('#cf-icons').addEventListener('click', (e) => { const b = e.target.closest('[data-icon]'); if (!b) return; iconName = b.dataset.icon; $$('#cf-icons button', m.el).forEach((s) => s.classList.toggle('active', s === b)); });
  m.el.querySelector('#cat-form').addEventListener('submit', (e) => { e.preventDefault(); m.el.querySelector('.modal-foot .btn-primary').click(); });
}

/* ---------- Merge ---------- */
function openMerge(cat) {
  let target = null;
  const m = ui.modal({
    title: `Merge “${cat.name}”`,
    html: `<p class="mb-3">Move every transaction, rule and remembered merchant from <b>${esc(cat.name)}</b>${cat.children && cat.children.length ? ' and its subcategories' : ''} into another category, then delete it.</p>
      <div class="field"><label>Merge into</label><button type="button" class="btn btn-secondary btn-block" id="merge-target" style="justify-content:space-between"><span class="text-3">Choose a category…</span>${icon('chevron-down')}</button></div>`,
    actions: [{ label: 'Cancel' }, { label: 'Merge', danger: true, primary: true, onClick: async () => {
      if (!target) throw new Error('Choose a category to merge into');
      const r = await api(`/api/categories/${cat.id}/merge`, { method: 'POST', body: { into: target.id } });
      store.invalidate('categories');
      toast(`Merged into ${target.name} (${fmtNumber(r.moved)} transactions moved)`, { type: 'success' });
      if (state.selected === cat.id) state.selected = target.id;
      await load();
    } }],
  });
  const btn = m.el.querySelector('#merge-target');
  btn.addEventListener('click', () => categoryPicker({ anchor: btn, allowCreate: false, onPick: (c) => {
    if (!c || c.id === cat.id || c.parent_id === cat.id) { toast('Pick a different category', { type: 'error' }); return; }
    target = c;
    btn.innerHTML = `<span class="row gap-2"><i class="dot" style="--c:${catColor(c.color || c.parent_color)}"></i>${esc(c.path || c.name)}</span>${icon('chevron-down')}`;
  } }));
}

/* ---------- Delete ---------- */
async function deleteCategory(cat) {
  const n = cat.depth === 0 ? countOf(state.tree.find((p) => p.id === cat.id) || cat) : (cat.txn_count || 0);
  if (n > 0) {
    const ok = await ui.confirm({ title: `${esc(cat.name)} is in use`, body: `${fmtNumber(n)} transactions use this category. Merge it into another category instead so nothing becomes uncategorized.`, confirmText: 'Merge instead' });
    if (ok) openMerge(cat);
    return;
  }
  if (!(await ui.confirm({ title: `Delete ${cat.name}?`, body: cat.children && cat.children.length ? 'Its subcategories will be deleted too.' : 'This cannot be undone.', confirmText: 'Delete', danger: true }))) return;
  try {
    await api(`/api/categories/${cat.id}`, { method: 'DELETE' });
    store.invalidate('categories');
    if (state.selected === cat.id) state.selected = null;
    toast('Category deleted', { type: 'success' });
    await load();
  } catch (err) {
    if (err.status === 409) { const ok = await ui.confirm({ title: 'Category is in use', body: err.message, confirmText: 'Merge instead' }); if (ok) openMerge(cat); }
    else toast(err.message, { type: 'error' });
  }
}
