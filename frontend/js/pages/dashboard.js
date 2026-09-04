/* Dashboard: one aggregate call, KPIs with deltas + sparklines, monthly bar, category donut, recent, needs attention. */
const RANGES = ['this-month', 'last-month', 'last-90'];
const state = { range: 'this-month', data: null, currency: 'USD', me: null };

initNav('dashboard').then(async (me) => {
  state.me = me;
  const q = qs();
  state.range = RANGES.includes(q.range) ? q.range : 'this-month';
  state.currency = await store.displayCurrency();
  $('#range-seg').addEventListener('click', (e) => {
    const b = e.target.closest('[data-range]'); if (!b) return;
    state.range = b.dataset.range;
    setQs({ range: state.range === 'this-month' ? null : state.range }, { replace: true, merge: true });
    load();
  });
  document.body.addEventListener('click', (e) => {
    const a = e.target.closest('[data-act]'); if (!a) return;
    if (a.dataset.act === 'reload') load();
  });
  window.addEventListener('popstate', () => { const r = qs().range; state.range = RANGES.includes(r) ? r : 'this-month'; load(); });
  load();
});

function renderSeg() {
  $$('#range-seg .seg-btn').forEach((b) => { const on = b.dataset.range === state.range; b.classList.toggle('active', on); b.setAttribute('aria-selected', on); });
}

async function load() {
  renderSeg();
  $('#dash-error').innerHTML = '';
  $$('#kpis .stat').forEach((s) => s.classList.add('is-loading'));
  $$('.chart-body').forEach((c) => c.classList.add('is-loading'));
  $('#recent-list').innerHTML = `<div class="list">${ui.skeletonList(6)}</div>`;
  $('#attention').innerHTML = `<div class="col gap-3">${ui.skeleton('100%', 56)}${ui.skeleton('60%', 14)}${ui.skeleton('100%', 14)}${ui.skeleton('100%', 14)}</div>`;
  let data;
  try {
    data = await api(`/api/reports/dashboard?range=${encodeURIComponent(state.range)}`);
  } catch (err) {
    $('#dash-error').innerHTML = ui.errorBox(err.message, { retry: 'reload' });
    return;
  }
  state.data = data;
  const hasAny = data.kpis.txn_count > 0 || data.monthly.some((m) => m.spent || m.income) || data.recent.length > 0;
  $('#dash-body').hidden = !hasAny;
  $('#dash-empty').hidden = hasAny;
  if (!hasAny) {
    $('#dash-empty').className = 'card mt-4 dash-empty';
    $('#dash-empty').innerHTML = ui.emptyState({
      icon: 'upload', title: 'Import your first statement',
      body: 'Upload a CSV, Excel or PDF statement and iSpend will organize your spending by category.',
      action: { label: 'Import statement', href: '/import.html' },
    });
    renderKpis(data);
    return;
  }
  $('#dash-sub').textContent = `${data.range.label} · ${fmtDate(data.range.start, { year: true })} – ${fmtDate(data.range.end, { year: true })}`;
  renderKpis(data);
  renderMonthly(data);
  renderDonut(data);
  renderRecent(data);
  renderAttention(data);
}

/* ---------- KPIs ---------- */
function renderKpis(data) {
  const k = data.kpis;
  const cur = state.currency;
  const spark = {
    spent: data.monthly.map((m) => m.spent),
    income: data.monthly.map((m) => m.income),
    net: data.monthly.map((m) => m.net),
  };
  const defs = [
    { key: 'spent', label: 'Spent', value: fmtMoney(k.spent, cur), delta: fmtDelta(k.spent, k.spent_prev), goodWhen: 'down', color: '--accent' },
    { key: 'income', label: 'Income', value: fmtMoney(k.income, cur), delta: fmtDelta(k.income, k.income_prev), goodWhen: 'up', color: '--success' },
    { key: 'net', label: 'Net', value: fmtMoney(k.net, cur, { sign: 'always' }), delta: fmtDelta(k.net, k.net_prev), goodWhen: 'up', color: k.net >= 0 ? '--success' : '--danger' },
    { key: 'txn', label: 'Transactions', value: fmtNumber(k.txn_count), delta: null },
  ];
  defs.forEach((d) => {
    const el = $(`#kpis .stat[data-kpi="${d.key}"]`);
    el.querySelector('.stat-label').textContent = d.label + (d.key !== 'txn' ? '' : ` · ${data.range.label.toLowerCase()}`);
    el.querySelector('.stat-value').textContent = d.value;
    const deltaEl = el.querySelector('.stat-delta');
    if (!d.delta || d.delta.dir === 'flat' || d.delta.pct == null) {
      deltaEl.className = 'stat-delta';
      deltaEl.innerHTML = d.delta && d.delta.dir === 'flat' && d.delta.pct != null ? `${icon('minus')} No change <span class="stat-delta-vs">vs previous</span>` : `<span class="stat-delta-vs">${d.key === 'txn' ? 'in this period' : 'No previous period'}</span>`;
    } else {
      const good = d.delta.dir === d.goodWhen;
      deltaEl.className = `stat-delta ${good ? 'stat-delta--good' : 'stat-delta--bad'}`;
      deltaEl.innerHTML = `${icon(d.delta.dir === 'up' ? 'arrow-up' : 'arrow-down')} ${esc(d.delta.text || fmtPct(Math.abs(d.delta.pct)))} <span class="stat-delta-vs">vs previous</span>`;
    }
    const canvas = el.querySelector('.stat-spark');
    if (canvas && spark[d.key]) {
      const values = spark[d.key];
      const color = d.color;
      charts.makeChart(canvas, (t) => {
        const hex = getComputedStyle(document.documentElement).getPropertyValue(color).trim() || t.accent;
        return {
          type: 'line',
          data: { labels: values.map((_, i) => i), datasets: [{ data: values, borderColor: hex, borderWidth: 1.5, fill: true, backgroundColor: (c) => charts.gradientFill(c.chart.ctx, hex, { from: 0.22 }), pointRadius: 0, tension: 0.35 }] },
          options: { animation: false, plugins: { tooltip: { enabled: false } }, scales: { x: { display: false }, y: { display: false } }, elements: { point: { hitRadius: 0 } } },
        };
      });
    }
    el.classList.remove('is-loading');
  });
}

/* ---------- Monthly bar ---------- */
function monthBounds(ym) {
  const [y, m] = ym.split('-').map(Number);
  const from = `${ym}-01`;
  const last = new Date(y, m, 0).getDate();
  return { from, to: `${ym}-${String(last).padStart(2, '0')}` };
}
function renderMonthly(data) {
  const body = $('#ch-monthly').closest('.chart-body');
  body.classList.remove('is-loading');
  const labels = data.monthly.map((m) => fmtMonth(m.month));
  const spent = data.monthly.map((m) => m.spent);
  const income = data.monthly.map((m) => m.income);
  const cur = state.currency;
  const chart = charts.makeChart($('#ch-monthly'), (t) => ({
    type: 'bar',
    data: {
      labels,
      datasets: [
        { label: 'Spent', data: spent, backgroundColor: t.accent, hoverBackgroundColor: charts.withAlpha(t.accent, 0.85), borderRadius: 4, maxBarThickness: 34 },
        { label: 'Income', data: income, backgroundColor: charts.withAlpha(t.success, 0.55), hoverBackgroundColor: t.success, borderRadius: 4, maxBarThickness: 34 },
      ],
    },
    options: {
      ...charts.barOptions(t, { currency: cur }),
      onClick: (_evt, els) => {
        if (!els.length) return;
        const m = data.monthly[els[0].index];
        const { from, to } = monthBounds(m.month);
        location.href = `/transactions.html${toQuery({ from, to })}`;
      },
      onHover: (evt, els) => { evt.native.target.style.cursor = els.length ? 'pointer' : 'default'; },
    },
  }));
  charts.htmlLegend($('#ch-monthly-legend'), chart, { currency: cur });
}

/* ---------- Donut ---------- */
function renderDonut(data) {
  const body = $('#ch-donut').closest('.chart-body');
  body.classList.remove('is-loading');
  const cats = data.top_categories;
  const legend = $('#ch-donut-legend');
  const cur = state.currency;
  if (!cats.length) {
    charts.destroyChart($('#ch-donut'));
    body.innerHTML = `<div class="empty" style="padding:20px 0"><div class="empty-icon">${icon('pie-chart')}</div><div class="empty-body">No spending in this period</div></div>`;
    legend.innerHTML = '';
    return;
  }
  if (!body.querySelector('canvas')) body.innerHTML = '<canvas id="ch-donut" role="img" aria-label="Spending by category"></canvas>';
  const total = cats.reduce((s, c) => s + c.total, 0);
  const chart = charts.makeChart($('#ch-donut'), (t) => ({
    type: 'doughnut',
    data: { labels: cats.map((c) => c.name), datasets: [{ data: cats.map((c) => c.total), backgroundColor: cats.map((c) => catColor(c.color)), hoverOffset: 6, spacing: 2 }] },
    options: { cutout: '72%', plugins: { tooltip: { callbacks: { label: (c) => `${c.label}: ${fmtMoney(c.parsed, cur)} (${fmtPct(c.parsed / total)})` } } }, onClick: (_e, els) => { if (els.length) goCategory(cats[els[0].index]); } },
    plugins: [charts.donutCenterPlugin(() => fmtMoney(total, cur, { compact: total >= 100000 }), data.range.label.toLowerCase())],
  }));
  legend.innerHTML = cats.map((c, i) => `<button type="button" class="legend-item" data-i="${i}" title="View transactions">
    <span class="legend-name"><i class="dot" style="--c:${catColor(c.color)}"></i><span class="truncate">${esc(c.name)}</span><span class="legend-pct">${fmtPct(c.pct / 100)}</span></span>
    <span class="legend-val">${fmtMoney(c.total, cur)}</span></button>`).join('');
  legend.onclick = (e) => { const b = e.target.closest('[data-i]'); if (b) goCategory(cats[Number(b.dataset.i)]); };
  legend.onmouseover = (e) => { const b = e.target.closest('[data-i]'); if (!b) return; chart.setActiveElements([{ datasetIndex: 0, index: Number(b.dataset.i) }]); chart.update(); };
  legend.onmouseleave = () => { chart.setActiveElements([]); chart.update(); };
  window.addEventListener('ispend:theme', () => { legend.querySelectorAll('.dot').forEach((d, i) => { d.style.setProperty('--c', catColor(cats[i].color)); }); });
}
function goCategory(c) {
  const range = state.data.range;
  if (c.id == null && c.name === 'Other') { location.href = `/reports.html${toQuery({ tab: 'category', range: state.range })}`; return; }
  location.href = `/transactions.html${toQuery({ cat: c.id == null ? 'none' : c.id, from: range.start, to: range.end })}`;
}

/* ---------- Recent ---------- */
function renderRecent(data) {
  const host = $('#recent-list');
  if (!data.recent.length) { host.innerHTML = ui.emptyState({ icon: 'list', title: 'No transactions yet', body: 'Imported charges will show up here.' }); return; }
  host.innerHTML = `<div class="list recent">${data.recent.map((t) => {
    const color = t.is_transfer ? 'muted' : (t.category_color || 'muted');
    const cls = t.is_transfer ? 'amt--transfer' : (t.amount > 0 ? 'amt--income' : 'amt--expense');
    return `<div class="list-item is-clickable" data-open="${t.id}" role="link" tabindex="0">
      <span class="cat-icon" style="--c:${catColor(color)}">${icon(t.is_transfer ? 'arrow-left-right' : (t.category_icon || (t.category_id ? 'tag' : 'help')))}</span>
      <div class="rt-main"><span class="rt-name">${esc(t.merchant_name || t.description_clean)}</span>
        <span class="rt-meta"><span class="truncate">${esc(t.is_transfer ? 'Transfer' : (t.category_name || 'Uncategorized'))}</span>${t.category_status === 'suggested' ? `<span class="badge badge-info">${icon('sparkles')}Suggested</span>` : ''}<span>·</span><span class="truncate">${esc(t.account_name)}</span></span></div>
      <div><div class="rt-amt amt ${cls}">${fmtMoney(t.amount, t.currency || state.currency, { sign: 'always' })}</div><div class="rt-date">${fmtDate(t.txn_date)}</div></div>
    </div>`;
  }).join('')}</div>`;
  const go = (el) => { location.href = `/transactions.html${toQuery({ open: el.dataset.open, range: 'all' })}`; };
  host.onclick = (e) => { const el = e.target.closest('[data-open]'); if (el) go(el); };
  host.onkeydown = (e) => { const el = e.target.closest('[data-open]'); if (el && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); go(el); } };
}

/* ---------- Needs attention ---------- */
function daysUntil(iso) {
  const d = new Date(iso + 'T00:00:00'); const today = new Date(); today.setHours(0, 0, 0, 0);
  return Math.round((d - today) / 86400000);
}
function dueLabel(iso) {
  const n = daysUntil(iso);
  if (n < 0) return `${Math.abs(n)}d overdue`;
  if (n === 0) return 'Today';
  if (n === 1) return 'Tomorrow';
  return `in ${n} days`;
}
function renderAttention(data) {
  const rc = data.review_count;
  const total = (rc.uncategorized || 0) + (rc.suggested || 0);
  const cur = state.currency;
  const rec = data.recurring;
  const parts = [];
  parts.push(total
    ? `<a class="attn-cta" href="/review.html"><span class="attn-ico">${icon('inbox')}</span><div class="grow"><div class="attn-title">${plural(total, 'charge')} need a category</div><div class="attn-sub">${rc.suggested ? `${fmtNumber(rc.suggested)} suggested` : ''}${rc.suggested && rc.uncategorized ? ' · ' : ''}${rc.uncategorized ? `${fmtNumber(rc.uncategorized)} unknown` : ''}</div></div>${icon('chevron-right')}</a>`
    : `<div class="attn-cta attn-cta--ok"><span class="attn-ico">${icon('check-circle')}</span><div class="grow"><div class="attn-title">All caught up</div><div class="attn-sub">Every charge has a category.</div></div></div>`);
  parts.push(`<div class="attn-block"><div class="attn-head"><span class="section-label">Recurring</span><a href="/insights.html">Details</a></div>
    ${rec.count ? `<div class="attn-row"><span>${plural(rec.count, 'active subscription')}</span><b>${fmtMoney(rec.monthly_total, cur)}<span class="text-4 fw-500"> / mo</span></b></div>
      ${rec.next.map((n) => `<div class="attn-row"><span><span class="fw-500">${esc(n.merchant_name)}</span> <span class="sub">${esc(n.cadence)}</span></span><span class="row gap-2"><span class="sub">${esc(dueLabel(n.next_expected))}</span><b class="num">${fmtMoney(n.amount, cur)}</b></span></div>`).join('')}`
      : '<div class="hint">No recurring charges detected yet. They appear after a few months of statements.</div>'}</div>`);
  if (data.accounts.length) {
    parts.push(`<div class="attn-block"><div class="attn-head"><span class="section-label">Accounts</span><a href="/settings.html#accounts">Manage</a></div>
      ${data.accounts.map((a) => `<div class="attn-row"><span class="acct"><i class="acct-mark" style="--c:var(--${esc(a.color || 'c1')})">${esc(initials(a.name).slice(0, 1))}</i><span class="text-1">${esc(a.name)}</span></span>
        <span class="row gap-2">${a.last_txn_date ? `<span class="sub">${esc(fmtDate(a.last_txn_date, { year: true }))}</span>` : ''}${a.balance != null ? `<b class="num">${fmtMoney(a.balance, a.currency)}</b>` : '<span class="sub">—</span>'}</span></div>`).join('')}</div>`);
  }
  $('#attention').innerHTML = `<div class="attn">${parts.join('')}</div>`;
}
