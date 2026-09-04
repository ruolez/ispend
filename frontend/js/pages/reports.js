/* Reports: category (stacked monthly + table), trend (lines), merchants (leaderboard), compare (month over month). */
const TABS = ['category', 'trend', 'merchants', 'compare'];
const state = { tab: 'category', range: { preset: 'this-month' }, accounts: new Set(), months: 12, pct: false, month: null, sort: { key: 'total', dir: 'desc' }, trendSel: null, currency: 'USD', cache: {}, accountsList: [] };
const acctQuery = () => (state.accounts.size ? { account_id: Array.from(state.accounts).join(',') } : {});

initNav('reports').then(async (me) => {
  state.currency = await store.displayCurrency();
  const q = qs();
  state.tab = TABS.includes(q.tab) ? q.tab : 'category';
  state.range = rangeFromQuery(q, { preset: 'this-month' });
  if (q.acct) q.acct.split(',').forEach((a) => state.accounts.add(a));
  if (q.months && [6, 12, 24].includes(Number(q.months))) state.months = Number(q.months);
  state.month = q.month || currentMonth();
  state.accountsList = await store.accounts().catch(() => []);
  $('[data-act="export"]').innerHTML = `${icon('download')}<span>Export CSV</span>`;
  $('#report-tabs').addEventListener('click', (e) => { const t = e.target.closest('[data-tab]'); if (t) switchTab(t.dataset.tab); });
  paintMonths();
  $('#months-seg').addEventListener('click', (e) => { const b = e.target.closest('[data-months]'); if (!b) return; state.months = Number(b.dataset.months); paintMonths(); sync(); loadTab(true); });
  document.body.addEventListener('click', onAction);
  mountRangeButton($('#range-btn'), state.range, (v) => { state.range = v; sync(); loadTab(true); });
  renderAcctBtn();
  $('#acct-btn').addEventListener('click', () => ui.multiFilter($('#acct-btn'), {
    title: 'Accounts', options: state.accountsList.map((a) => ({ value: String(a.id), label: a.name, color: a.color })), selected: state.accounts,
    onChange: () => { renderAcctBtn(); sync(); state.cache = {}; loadTab(true); },
  }));
  window.addEventListener('popstate', () => { const q2 = qs(); state.tab = TABS.includes(q2.tab) ? q2.tab : 'category'; state.months = [6, 12, 24].includes(Number(q2.months)) ? Number(q2.months) : 12; paintMonths(); switchTab(state.tab, true); });
  switchTab(state.tab, true);
});

function paintMonths() { $$('#months-seg .seg-btn').forEach((b) => { const on = Number(b.dataset.months) === state.months; b.classList.toggle('active', on); b.setAttribute('aria-pressed', String(on)); }); }
function currentMonth() { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`; }
function renderAcctBtn() {
  const n = state.accounts.size;
  const label = n === 0 ? 'All accounts' : n === 1 ? ((state.accountsList.find((a) => String(a.id) === Array.from(state.accounts)[0]) || {}).name || '1 account') : `${n} accounts`;
  $('#acct-btn').innerHTML = `${icon('landmark', 'ico-sm')}<span>${esc(label)}</span>${icon('chevron-down', 'ico-sm')}`;
}
function sync() {
  setQs({ tab: state.tab === 'category' ? null : state.tab, ...rangeToQuery(state.range), acct: state.accounts.size ? Array.from(state.accounts).join(',') : null, months: state.months === 12 ? null : state.months, month: state.tab === 'compare' && state.month !== currentMonth() ? state.month : null }, { replace: true });
}
function switchTab(tab, noPush) {
  state.tab = tab;
  $$('#report-tabs .tab').forEach((t) => { const on = t.dataset.tab === tab; t.classList.toggle('active', on); t.setAttribute('aria-selected', on); });
  $$('.tab-panel').forEach((p) => p.classList.toggle('active', p.dataset.panel === tab));
  const usesRange = tab === 'category' || tab === 'merchants';
  $('#range-btn').hidden = !usesRange;
  $('#months-seg').hidden = !(tab === 'category' || tab === 'trend');
  if (!noPush) sync();
  loadTab();
}
function loadTab(force) {
  $('#report-error').innerHTML = '';
  ({ category: loadCategory, trend: loadTrend, merchants: loadMerchants, compare: loadCompare })[state.tab](force);
}
async function cached(key, url, force) {
  if (!force && state.cache[key]) return state.cache[key];
  const data = await api(url);
  state.cache[key] = data;
  return data;
}
function onAction(e) {
  const b = e.target.closest('[data-act]'); if (!b) return;
  const act = b.dataset.act;
  if (act === 'reload') return loadTab(true);
  if (act === 'export') return exportCsv();
  if (act === 'pct') { state.pct = !state.pct; return loadCategory(); }
  if (act === 'compare-go') { state.month = $('#cmp-month').value || currentMonth(); sync(); return loadCompare(true); }
  if (act === 'cmp-prev' || act === 'cmp-next') { const [y, m] = state.month.split('-').map(Number); const d = new Date(y, m - 1 + (act === 'cmp-next' ? 1 : -1), 1); state.month = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`; sync(); return loadCompare(true); }
}
function exportCsv() {
  const rq = rangeToQuery(state.range);
  const urls = {
    category: `/api/reports/by-category${toQuery({ ...rq, level: 'top', ...acctQuery(), format: 'csv' })}`,
    trend: `/api/reports/monthly${toQuery({ months: state.months, ...acctQuery(), format: 'csv' })}`,
    merchants: `/api/reports/top-merchants${toQuery({ ...rq, limit: 200, ...acctQuery(), format: 'csv' })}`,
    compare: `/api/reports/month-over-month${toQuery({ month: state.month, ...acctQuery(), format: 'csv' })}`,
  };
  const a = document.createElement('a'); a.href = urls[state.tab]; a.download = ''; document.body.appendChild(a); a.click(); a.remove();
}
function seriesColor(s) { return s.color === 'muted' || !s.color ? charts.theme().muted : catColor(s.color); }

/* ---------- By category ---------- */
async function loadCategory(force) {
  const host = $('#panel-category');
  if (!host.querySelector('#ch-stack')) {
    host.innerHTML = `<section class="card chart-card"><header class="card-head"><h2>Spending by month</h2><div class="card-actions chart-toolbar"><span class="hint" id="stack-hint">Click a category to isolate it</span><div class="seg"><button type="button" class="seg-btn ${state.pct ? '' : 'active'}" data-act="pct" data-mode="amt">$</button><button type="button" class="seg-btn ${state.pct ? 'active' : ''}" data-act="pct" data-mode="pct">%</button></div></div></header>
      <div class="chart-body is-loading" style="--h:320px"><canvas id="ch-stack"></canvas></div><footer class="chart-legend" id="ch-stack-legend"></footer></section>
      <section class="card mt-4"><header class="card-head"><h2>Categories · <span id="cat-range-label" class="text-3 fw-500"></span></h2><div class="card-actions"><a class="btn btn-ghost btn-xs" href="/categories.html">Manage categories</a></div></header><div class="tbl-wrap" style="border:0;box-shadow:none;border-radius:0 0 var(--r-lg) var(--r-lg)"><table class="tbl report-tbl"><thead><tr><th>Category</th><th>Share</th><th class="right col-count">Transactions</th><th class="right">Total</th></tr></thead><tbody id="cat-table">${ui.skeletonRows(6, 4)}</tbody></table></div></section>`;
  }
  $$('#panel-category [data-act="pct"]').forEach((b) => b.classList.toggle('active', (b.dataset.mode === 'pct') === state.pct));
  const rq = rangeToQuery(state.range);
  try {
    const [monthly, byCat] = await Promise.all([
      cached(`monthly-${state.months}`, `/api/reports/monthly${toQuery({ months: state.months, ...acctQuery() })}`, force),
      cached(`bycat-${JSON.stringify(rq)}`, `/api/reports/by-category${toQuery({ ...rq, level: 'top', ...acctQuery() })}`, force),
    ]);
    renderStack(monthly);
    renderCatTable(byCat);
  } catch (err) { $('#report-error').innerHTML = ui.errorBox(err.message, { retry: 'reload' }); }
}
let isolated = null;
function renderStack(data) {
  const body = $('#ch-stack').closest('.chart-body'); body.classList.remove('is-loading');
  const legend = $('#ch-stack-legend');
  if (!data.series.length) { charts.destroyChart($('#ch-stack')); body.innerHTML = ui.emptyState({ icon: 'bar-chart', title: 'No spending yet', body: 'Import a statement to see monthly spending here.', action: { label: 'Import statement', href: '/import.html' } }); legend.innerHTML = ''; return; }
  if (!body.querySelector('canvas')) body.innerHTML = '<canvas id="ch-stack"></canvas>';
  const cur = state.currency;
  const values = (s) => state.pct ? s.values.map((v, j) => (data.totals[j] ? v / data.totals[j] * 100 : 0)) : s.values;
  const chart = charts.makeChart($('#ch-stack'), (t) => ({
    type: 'bar',
    data: { labels: data.months.map((m) => fmtMonth(m)), datasets: data.series.map((s, i) => ({ label: s.name, data: values(s), backgroundColor: seriesColor(s), borderColor: t.surface, borderWidth: { top: 2 }, borderSkipped: false, maxBarThickness: 44, hidden: isolated != null && isolated !== i })) },
    options: {
      ...charts.barOptions(t, { currency: cur, stacked: true }),
      plugins: { tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ${state.pct ? fmtPct(c.parsed.y / 100) : fmtMoney(c.parsed.y, cur)}`, footer: (items) => state.pct ? '' : `Total: ${fmtMoney(items.reduce((s, it) => s + it.parsed.y, 0), cur)}` } } },
      scales: { x: { stacked: true, grid: { display: false }, border: { display: false }, ticks: { maxRotation: 0, autoSkip: true } }, y: { stacked: true, beginAtZero: true, border: { display: false }, max: state.pct ? 100 : undefined, ticks: state.pct ? { callback: (v) => `${v}%`, maxTicksLimit: 5 } : charts.currencyTicks(cur) } },
      onClick: (_e, els) => { if (!els.length) return; const s = data.series[els[0].datasetIndex]; const [y, m] = data.months[els[0].index].split('-').map(Number); const to = `${data.months[els[0].index]}-${String(new Date(y, m, 0).getDate()).padStart(2, '0')}`; location.href = `/transactions.html${toQuery({ cat: s.category_id == null ? (s.name === 'Other' ? null : 'none') : s.category_id, from: `${data.months[els[0].index]}-01`, to })}`; },
    },
  }));
  legend.innerHTML = data.series.map((s, i) => `<button type="button" class="legend-item ${isolated != null && isolated !== i ? 'is-off' : ''}" data-i="${i}"><span class="legend-name"><i class="dot" style="--c:${seriesColor(s)}"></i>${esc(s.name)}</span><span class="legend-val">${fmtMoney(s.total, cur)}</span></button>`).join('');
  legend.onclick = (e) => { const b = e.target.closest('[data-i]'); if (!b) return; const i = Number(b.dataset.i); isolated = isolated === i ? null : i; chart.data.datasets.forEach((d, j) => { chart.setDatasetVisibility(j, isolated == null || isolated === j); }); chart.update(); $$('.legend-item', legend).forEach((l, j) => l.classList.toggle('is-off', isolated != null && isolated !== j)); };
}
function renderCatTable(res) {
  $('#cat-range-label').textContent = rangeLabel(state.range);
  const tb = $('#cat-table'); const cur = state.currency; const rq = rangeToQuery(state.range);
  if (!res.categories.length) { tb.innerHTML = `<tr><td colspan="4">${ui.emptyState({ icon: 'pie-chart', title: 'Nothing in this period' })}</td></tr>`; return; }
  const max = Math.max(...res.categories.map((c) => c.total));
  tb.innerHTML = res.categories.map((c) => `<tr class="is-clickable" data-href="/transactions.html${toQuery({ cat: c.id == null ? 'none' : c.id, ...rq })}" tabindex="0">
    <td><span class="cat-cell"><span class="cat-icon" style="--c:${c.color === 'muted' ? charts.theme().muted : catColor(c.color)}">${icon(c.icon || 'tag')}</span><span class="name">${esc(c.name)}</span></span></td>
    <td><span class="share-cell"><span class="share-bar" style="--c:${c.color === 'muted' ? charts.theme().muted : catColor(c.color)}"><span style="width:${max ? (c.total / max * 100).toFixed(1) : 0}%"></span></span><span class="pct">${fmtPct(c.pct / 100)}</span></span></td>
    <td class="right num col-count">${fmtNumber(c.count)}</td><td class="right num fw-500">${fmtMoney(c.total, cur)}</td></tr>`).join('') + `<tr class="totals-row"><td>Total</td><td></td><td class="right num col-count">${fmtNumber(res.categories.reduce((s, c) => s + c.count, 0))}</td><td class="right num">${fmtMoney(res.total, cur)}</td></tr>`;
  wireRowLinks(tb);
}
function wireRowLinks(tb) {
  tb.onclick = (e) => { const tr = e.target.closest('tr[data-href]'); if (tr && !e.target.closest('a,button')) location.href = tr.dataset.href; };
  tb.onkeydown = (e) => { const tr = e.target.closest('tr[data-href]'); if (tr && e.key === 'Enter') location.href = tr.dataset.href; };
}

/* ---------- Trend ---------- */
async function loadTrend(force) {
  const host = $('#panel-trend');
  if (!host.querySelector('#ch-trend')) host.innerHTML = `<section class="card chart-card"><header class="card-head"><h2>Category trend</h2><div class="card-actions"><span class="hint">Pick up to 4 categories</span></div></header><div class="card-body" style="padding-bottom:0"><div class="trend-chips" id="trend-chips"></div></div><div class="chart-body is-loading" style="--h:320px"><canvas id="ch-trend"></canvas></div></section>`;
  try {
    const data = await cached(`monthly-${state.months}`, `/api/reports/monthly${toQuery({ months: state.months, ...acctQuery() })}`, force);
    renderTrend(data);
  } catch (err) { $('#report-error').innerHTML = ui.errorBox(err.message, { retry: 'reload' }); }
}
function renderTrend(data) {
  const body = $('#ch-trend').closest('.chart-body'); body.classList.remove('is-loading');
  const chipsEl = $('#trend-chips'); const cur = state.currency;
  if (!data.series.length) { charts.destroyChart($('#ch-trend')); body.innerHTML = ui.emptyState({ icon: 'trending-up', title: 'No spending yet' }); chipsEl.innerHTML = ''; return; }
  if (!body.querySelector('canvas')) body.innerHTML = '<canvas id="ch-trend"></canvas>';
  const all = [{ name: 'Total', color: null, values: data.totals, key: 'total' }, ...data.series.map((s, i) => ({ ...s, key: String(i) }))];
  if (!state.trendSel) state.trendSel = new Set(['total', ...data.series.slice(0, 2).map((_, i) => String(i))]);
  const colorOf = (s) => s.key === 'total' ? charts.theme().text2 : seriesColor(s);
  chipsEl.innerHTML = all.map((s) => `<button type="button" class="chip ${state.trendSel.has(s.key) ? 'active' : ''}" data-key="${s.key}" style="--c:${colorOf(s)}" aria-pressed="${state.trendSel.has(s.key)}"><i class="dot" style="--c:${colorOf(s)}"></i>${esc(s.name)}</button>`).join('');
  chipsEl.onclick = (e) => { const b = e.target.closest('[data-key]'); if (!b) return; const k = b.dataset.key; if (state.trendSel.has(k)) state.trendSel.delete(k); else { if (state.trendSel.size >= 4) { toast('Up to 4 lines at a time', { type: 'info', duration: 1500 }); return; } state.trendSel.add(k); } renderTrend(data); };
  const sel = all.filter((s) => state.trendSel.has(s.key));
  charts.makeChart($('#ch-trend'), (t) => ({
    type: 'line',
    data: { labels: data.months.map((m) => fmtMonth(m)), datasets: sel.map((s) => { const hex = colorOf(s); return { label: s.name, data: s.values, borderColor: hex, backgroundColor: (c) => charts.gradientFill(c.chart.ctx, hex, { from: sel.length === 1 ? 0.2 : 0.06 }), fill: true, borderDash: s.key === 'total' ? [5, 4] : [], pointRadius: 2, pointHoverRadius: 5 }; }) },
    options: charts.lineOptions(t, { currency: cur }),
  }));
}

/* ---------- Merchants ---------- */
async function loadMerchants(force) {
  const host = $('#panel-merchants');
  if (!host.querySelector('#merch-table')) host.innerHTML = `<section class="card"><header class="card-head"><h2>Top merchants · <span id="merch-range-label" class="text-3 fw-500"></span></h2></header><div class="tbl-wrap" style="border:0;box-shadow:none;border-radius:0 0 var(--r-lg) var(--r-lg)"><table class="tbl report-tbl"><thead><tr><th class="sortable" data-sort="merchant_name">Merchant</th><th>Category</th><th class="right sortable col-count" data-sort="count">Visits</th><th class="right sortable col-avg" data-sort="avg">Avg</th><th class="right sortable" data-sort="total" aria-sort="descending">Total</th><th>Share</th><th class="spark-cell">6 months</th></tr></thead><tbody id="merch-table">${ui.skeletonRows(8, 7)}</tbody></table></div></section>`;
  const rq = rangeToQuery(state.range);
  try {
    const [res, flat] = await Promise.all([cached(`merch-${JSON.stringify(rq)}`, `/api/reports/top-merchants${toQuery({ ...rq, limit: 50, ...acctQuery() })}`, force), store.categoriesFlat()]);
    renderMerchants(res, new Map(flat.map((c) => [c.id, c])));
  } catch (err) { $('#report-error').innerHTML = ui.errorBox(err.message, { retry: 'reload' }); }
}
function renderMerchants(res, cats) {
  $('#merch-range-label').textContent = rangeLabel(state.range);
  const tb = $('#merch-table'); const cur = state.currency;
  const { key, dir } = state.sort;
  const rows = [...res.merchants].sort((a, b) => { const va = a[key], vb = b[key]; const r = typeof va === 'string' ? va.localeCompare(vb) : (va - vb); return dir === 'asc' ? r : -r; });
  $$('#panel-merchants th.sortable').forEach((th) => { th.setAttribute('aria-sort', th.dataset.sort === key ? (dir === 'asc' ? 'ascending' : 'descending') : 'none'); th.onclick = () => { state.sort = { key: th.dataset.sort, dir: state.sort.key === th.dataset.sort && state.sort.dir === 'desc' ? 'asc' : 'desc' }; renderMerchants(res, cats); }; });
  if (!rows.length) { tb.innerHTML = `<tr><td colspan="7">${ui.emptyState({ icon: 'search', title: 'No merchants in this period' })}</td></tr>`; return; }
  const max = Math.max(...rows.map((m) => m.total));
  tb.innerHTML = rows.map((m, i) => { const c = cats.get(m.category_id); const color = c ? (c.color || c.parent_color) : null; return `<tr class="is-clickable" data-href="/transactions.html${toQuery({ q: m.merchant_name, range: 'all' })}" tabindex="0">
    <td><span class="cat-cell"><span class="text-4 fs-xs num" style="width:18px">${i + 1}</span><span class="name">${esc(m.merchant_name)}</span></span></td>
    <td>${c ? `<span class="catchip"><i class="dot" style="--c:var(--${esc(color)})"></i><span class="catchip-label">${esc(c.name)}</span></span>` : '<span class="text-4">—</span>'}</td>
    <td class="right num col-count">${fmtNumber(m.count)}</td><td class="right num col-avg">${fmtMoney(m.avg, cur)}</td><td class="right num fw-500">${fmtMoney(m.total, cur)}</td>
    <td><span class="share-cell"><span class="share-bar" style="--c:${color ? catColor(color) : 'var(--accent)'}"><span style="width:${max ? (m.total / max * 100).toFixed(1) : 0}%"></span></span><span class="pct">${fmtPct(m.pct / 100)}</span></span></td>
    <td class="spark-cell"><canvas data-spark="${i}" width="96" height="28"></canvas></td></tr>`; }).join('');
  rows.forEach((m, i) => { const cv = tb.querySelector(`canvas[data-spark="${i}"]`); const c = cats.get(m.category_id); charts.sparkline(cv, m.sparkline, c ? catColor(c.color || c.parent_color) : charts.theme().accent); });
  wireRowLinks(tb);
}

/* ---------- Compare ---------- */
async function loadCompare(force) {
  const host = $('#panel-compare');
  if (!host.querySelector('#ch-compare')) host.innerHTML = `<div class="compare-row mb-4"><button type="button" class="btn btn-icon btn-secondary" data-act="cmp-prev" aria-label="Previous month">${icon('chevron-left')}</button><input type="month" id="cmp-month" class="input" value="${esc(state.month)}"><button type="button" class="btn btn-icon btn-secondary" data-act="cmp-next" aria-label="Next month">${icon('chevron-right')}</button><button type="button" class="btn btn-secondary" data-act="compare-go">Compare</button><span class="hint" id="cmp-hint"></span></div>
    <section class="card chart-card"><header class="card-head"><h2 id="cmp-title">This month vs last</h2></header><div class="chart-body is-loading" style="--h:300px"><canvas id="ch-compare"></canvas></div><footer class="chart-legend" id="ch-compare-legend"></footer></section>
    <section class="card mt-4"><div class="tbl-wrap" style="border:0;box-shadow:none"><table class="tbl report-tbl"><thead><tr><th>Category</th><th class="right" id="cmp-h-prev">Previous</th><th class="right" id="cmp-h-cur">Current</th><th class="right">Change</th></tr></thead><tbody id="cmp-table">${ui.skeletonRows(6, 4)}</tbody></table></div></section>`;
  $('#cmp-month').value = state.month;
  $('#cmp-month').onchange = (e) => { state.month = e.target.value || currentMonth(); sync(); loadCompare(true); };
  try { const data = await cached(`mom-${state.month}`, `/api/reports/month-over-month${toQuery({ month: state.month, ...acctQuery() })}`, force); renderCompare(data); }
  catch (err) { $('#report-error').innerHTML = ui.errorBox(err.message, { retry: 'reload' }); }
}
function deltaHtml(delta, pct) {
  const dir = Math.abs(delta) < 0.005 ? 'flat' : (delta > 0 ? 'up' : 'down');
  return `<span class="delta delta--${dir}">${dir === 'flat' ? '' : icon(dir === 'up' ? 'arrow-up' : 'arrow-down')}${dir === 'flat' ? '—' : `${fmtMoney(Math.abs(delta), state.currency)}${pct != null ? ` (${fmtPct(Math.abs(pct) / 100)})` : ''}`}</span>`;
}
function renderCompare(data) {
  const body = $('#ch-compare').closest('.chart-body'); body.classList.remove('is-loading');
  const cur = state.currency;
  $('#cmp-title').textContent = `${fmtMonth(data.month, { long: true })} vs ${fmtMonth(data.previous_month, { long: true })}`;
  $('#cmp-h-prev').textContent = fmtMonth(data.previous_month); $('#cmp-h-cur').textContent = fmtMonth(data.month);
  const t = data.totals;
  $('#cmp-hint').innerHTML = t.previous || t.current ? `Spent ${fmtMoney(t.current, cur)} vs ${fmtMoney(t.previous, cur)} · ${deltaHtml(t.delta, t.pct)}` : '';
  const cats = data.categories.slice(0, 10);
  if (!cats.length) { charts.destroyChart($('#ch-compare')); body.innerHTML = ui.emptyState({ icon: 'bar-chart', title: 'Nothing to compare', body: 'No spending in either month.' }); $('#cmp-table').innerHTML = ''; $('#ch-compare-legend').innerHTML = ''; return; }
  if (!body.querySelector('canvas')) body.innerHTML = '<canvas id="ch-compare"></canvas>';
  const chart = charts.makeChart($('#ch-compare'), (th) => ({
    type: 'bar',
    data: { labels: cats.map((c) => c.name), datasets: [{ label: fmtMonth(data.previous_month), data: cats.map((c) => c.previous), backgroundColor: th.muted, maxBarThickness: 26 }, { label: fmtMonth(data.month), data: cats.map((c) => c.current), backgroundColor: th.accent, maxBarThickness: 26 }] },
    options: { ...charts.barOptions(th, { currency: cur }), scales: { x: { grid: { display: false }, border: { display: false }, ticks: { maxRotation: 0, autoSkip: false, callback: (v, i) => { const l = cats[i].name; return l.length > 12 ? l.slice(0, 11) + '…' : l; } } }, y: { beginAtZero: true, border: { display: false }, ticks: charts.currencyTicks(cur) } } },
  }));
  charts.htmlLegend($('#ch-compare-legend'), chart, { currency: cur });
  $('#cmp-table').innerHTML = data.categories.map((c) => `<tr class="is-clickable" data-href="/transactions.html${toQuery({ cat: c.id == null ? 'none' : c.id, from: `${data.month}-01`, to: monthEnd(data.month) })}" tabindex="0"><td><span class="cat-cell"><span class="cat-icon" style="--c:${c.color === 'muted' ? charts.theme().muted : catColor(c.color)}">${icon(c.icon || 'tag')}</span><span class="name">${esc(c.name)}</span></span></td><td class="right num text-3">${fmtMoney(c.previous, cur)}</td><td class="right num fw-500">${fmtMoney(c.current, cur)}</td><td class="right">${deltaHtml(c.delta, c.pct)}</td></tr>`).join('')
    + `<tr class="totals-row"><td>Total</td><td class="right num">${fmtMoney(t.previous, cur)}</td><td class="right num">${fmtMoney(t.current, cur)}</td><td class="right">${deltaHtml(t.delta, t.pct)}</td></tr>`;
  wireRowLinks($('#cmp-table'));
}
function monthEnd(ym) { const [y, m] = ym.split('-').map(Number); return `${ym}-${String(new Date(y, m, 0).getDate()).padStart(2, '0')}`; }
