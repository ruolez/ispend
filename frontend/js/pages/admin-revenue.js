/* Admin › Revenue: MRR and how it moved (new, returning, upgrades, downgrades, cancellations), churn and
   retention of revenue, what trials turn into, the plan mix and recent payments. Computed from
   iSpend's own revenue ledger; compare it with Stripe under Billing › Revenue records. */

const ARV = { charts: {} };

AdminPanels.register('revenue', {
  label: 'Revenue', icon: 'trending-up', group: 'insights', ranged: true,
  sub: 'Recurring revenue, how it moved, and what trials turn into',
  markup: `
    <div id="rv-note"></div>
    <div id="rv-tiles"></div>
    <div class="grid grid-2 mt-4">
      <section class="card chart-card">
        <header class="card-head"><h2>MRR</h2></header>
        <div class="chart-body" style="--h:240px"><canvas id="ch-rv-mrr" role="img" aria-label="Monthly recurring revenue over time"></canvas></div>
      </section>
      <section class="card chart-card">
        <header class="card-head"><h2>How MRR moved</h2><span class="hint" id="rv-bridge-note"></span></header>
        <div class="chart-body" style="--h:240px"><canvas id="ch-rv-bridge" role="img" aria-label="New, returning, upgraded, downgraded and cancelled MRR per period"></canvas></div>
        <div class="chart-legend" id="lg-rv-bridge"></div>
      </section>
    </div>
    <div class="grid grid-2 mt-4">
      <section class="card"><header class="card-head"><h2>Trials to paid</h2><span class="hint" id="rv-trial-note"></span></header>
        <div class="card-body" id="rv-trials"></div></section>
      <section class="card"><header class="card-head"><h2>Plan mix</h2></header><div class="card-body" id="rv-plans"></div></section>
    </div>
    <section class="card mt-4"><header class="card-head"><h2>Payments in this period</h2></header><div id="rv-payments"></div></section>
    <div class="row-between mt-3"><span></span><span class="hint" id="rv-asof"></span></div>`,
  load: loadAdminRevenue,
});

async function loadAdminRevenue(host, ctx) {
  if (!ARV.data || ctx.force) $('#rv-tiles').innerHTML = adminSkeletonTiles(11);
  let d, t;
  try {
    [d, t] = await Promise.all([
      api(`/api/admin/metrics/revenue${ctx.range}${ctx.force ? '&refresh=1' : ''}`),
      api(`/api/admin/metrics/trial-cohorts${ctx.range}${ctx.force ? '&refresh=1' : ''}`),
    ]);
  } catch (err) {
    $('#rv-tiles').innerHTML = ui.errorBox(err.message, { retry: 'reload-revenue' });
    return;
  }
  if (!ctx.isCurrent()) return;
  ARV.data = d;
  const cur = (d.currency || 'usd').toUpperCase();
  $('#rv-note').innerHTML = !d.history_from
    ? `<div class="notice notice-info mb-4">${icon('info')}<div class="grow">No subscriptions yet. Revenue shows here from the first
        Stripe subscription; if Stripe already has customers, compare under Billing › Revenue records to bring their history in.</div>
        <a class="btn btn-secondary btn-sm" href="#billing">Billing</a></div>`
    : (d.history_from > d.range.start
      ? `<div class="hint mb-3">${icon('info', 'ico-sm')} Revenue records begin ${esc(fmtDateLong(d.history_from))}; earlier days show nothing.</div>` : '');
  $('#rv-tiles').innerHTML = adminTiles(d.tiles);
  renderRevenueCharts(d, cur);
  renderTrialCohorts(t);
  renderPlanMix(d.plan_mix, cur);
  renderPayments(d.payments);
  $('#rv-asof').innerHTML = adminAsOf(d.as_of);
}

function bucketLabel(iso, bucket) {
  return bucket === 'month' ? fmtMonth(iso.slice(0, 7)) : fmtDate(iso);
}

function renderRevenueCharts(d, cur) {
  const labels = d.series.labels.map((l) => bucketLabel(l, d.range.bucket));
  ARV.charts.mrr = makeChart($('#ch-rv-mrr'), (t) => {
    const a = catColor('c2');
    return { type: 'line', data: { labels, datasets: [
      { label: 'MRR', data: d.series.mrr.map((v) => v / 100), borderColor: a, backgroundColor: (c) => charts.gradientFill(c.chart.ctx, a, { from: 0.25 }), fill: true, tension: 0.25, pointRadius: 0, borderWidth: 2 },
    ] }, options: charts.lineOptions(t, { currency: cur }) };
  });
  const br = d.bridge;
  $('#rv-bridge-note').textContent = br.bucket === 'week' && d.range.bucket === 'day' ? 'By week' : '';
  const blabels = br.labels.map((l) => bucketLabel(l, br.bucket));
  const neg = (xs) => xs.map((v) => -v / 100);
  const pos = (xs) => xs.map((v) => v / 100);
  ARV.charts.bridge = makeChart($('#ch-rv-bridge'), (t) => ({
    type: 'bar',
    data: { labels: blabels, datasets: [
      { label: 'New', data: pos(br.new), backgroundColor: charts.css('--success'), borderRadius: 2, stack: 'm' },
      { label: 'Returning', data: pos(br.reactivation), backgroundColor: charts.withAlpha(charts.css('--success'), 0.5), borderRadius: 2, stack: 'm' },
      { label: 'Upgrades', data: pos(br.expansion), backgroundColor: charts.css('--accent'), borderRadius: 2, stack: 'm' },
      { label: 'Downgrades', data: neg(br.contraction), backgroundColor: charts.withAlpha(charts.css('--danger'), 0.5), borderRadius: 2, stack: 'm' },
      { label: 'Cancelled', data: neg(br.churned), backgroundColor: charts.css('--danger'), borderRadius: 2, stack: 'm' },
    ] },
    options: charts.barOptions(t, { currency: cur, stacked: true }),
  }));
  charts.htmlLegend($('#lg-rv-bridge'), ARV.charts.bridge);
}

function renderTrialCohorts(t) {
  $('#rv-trial-note').textContent = `Paid within ${t.window_days} days of signing up`;
  if (!t.rows.length) {
    $('#rv-trials').innerHTML = ui.emptyState({ icon: 'users', title: 'No sign-ups in this period', body: 'Each week’s or month’s sign-ups show here with how many became paying customers.' });
    return;
  }
  const tot = t.total;
  $('#rv-trials').innerHTML = `
    ${kpis([['Trial to paid', tot.rate == null ? '—' : esc(fmtPct(tot.rate))], ['Added a card', fmtNumber(tot.added_card)],
      ['Still in their window', fmtNumber(tot.maturing)]])}
    <div class="tbl-wrap mt-4"><table class="tbl tbl--list adm-cohorts"><thead><tr>
      <th>Signed up</th><th class="right">People</th><th class="right">Paid in time</th><th class="right">Rate</th><th class="right">Median days</th></tr></thead>
      <tbody>${t.rows.map((r) => `<tr class="${r.maturing === r.signups ? 'is-maturing' : ''}">
        <td>${esc(t.bucket === 'month' ? fmtMonth(r.cohort.slice(0, 7)) : `Week of ${fmtDate(r.cohort)}`)}</td>
        <td class="right num"><a class="row-link" href="#users?signup_from=${esc(r.cohort)}&signup_to=${esc(cohortEnd(r.cohort, t.bucket))}" data-drill-users>${fmtNumber(r.signups)}</a></td>
        <td class="right num">${fmtNumber(r.paid_in_window)}</td>
        <td class="right num" ${r.maturing ? `data-tip="${esc(people(r.maturing))} still inside the window"` : ''}>${r.rate == null ? '—' : esc(fmtPct(r.rate))}${r.maturing ? '<span class="adm-maturing">*</span>' : ''}</td>
        <td class="right num">${r.median_days_to_paid == null ? '—' : fmtNumber(r.median_days_to_paid, { decimals: 1 })}</td></tr>`).join('')}</tbody></table></div>
    ${tot.maturing ? '<div class="hint mt-2">* Some of these people are still inside their trial, so the rate can still rise.</div>' : ''}`;
}

function cohortEnd(iso, bucket) {
  const d = new Date(`${iso}T00:00:00`);
  if (bucket === 'month') d.setMonth(d.getMonth() + 1); else d.setDate(d.getDate() + 7);
  d.setDate(d.getDate() - 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

function renderPlanMix(rows, cur) {
  if (!rows.length) {
    $('#rv-plans').innerHTML = ui.emptyState({ icon: 'credit-card', title: 'No paying customers yet', body: 'Monthly and yearly plans show here with the revenue each brings in.' });
    return;
  }
  const max = Math.max(...rows.map((r) => r.mrr_cents), 1);
  $('#rv-plans').innerHTML = `<div class="adm-bars">${rows.map((r) => bar(
    `${r.plan === 'yearly' ? 'Yearly' : r.plan === 'monthly' ? 'Monthly' : 'Other'} · ${plural(r.customers, 'customer')}`,
    r.mrr_cents, max, fmtMoney(r.mrr_cents / 100, cur))).join('')}</div>`;
}

function renderPayments(rows) {
  if (!rows.length) {
    $('#rv-payments').innerHTML = `<div class="card-body">${ui.emptyState({ icon: 'receipt', title: 'No payments in this period', body: 'Every charge Stripe makes shows here, paid or failed.' })}</div>`;
    return;
  }
  $('#rv-payments').innerHTML = `<div class="tbl-wrap"><table class="tbl tbl--list"><thead><tr><th>When</th><th>Person</th><th>Status</th><th>Plan</th><th class="right">Amount</th></tr></thead>
    <tbody>${rows.map((p) => `<tr>
      <td class="text-3">${esc(fmtDateTime(p.at))}</td>
      <td>${p.user_id ? `<button type="button" class="row-link" data-act="open-person" data-id="${p.user_id}">${esc(p.email || p.username || `#${p.user_id}`)}</button>` : '<span class="text-4">deleted account</span>'}</td>
      <td><span class="badge ${p.status === 'paid' ? 'badge-success' : p.status === 'failed' ? 'badge-danger' : 'badge-neutral'}">${esc(p.status.replace('_', ' '))}</span></td>
      <td class="text-3">${esc(p.plan || '—')}</td>
      <td class="right num">${esc(fmtMoney((p.amount_paid_cents - (p.amount_refunded_cents || 0)) / 100, (p.currency || 'usd').toUpperCase()))}</td></tr>`).join('')}</tbody></table></div>`;
}

/* Links from a chart or table into the people list carry their filters in the hash's query part;
   move them into the real query so the list picks them up. */
document.addEventListener('click', (e) => {
  const drill = e.target.closest('a[data-drill-users]');
  if (drill) {
    e.preventDefault();
    const [, query] = drill.getAttribute('href').split('?');
    const next = Object.fromEntries(new URLSearchParams(query));
    setQs(Object.fromEntries(FILTER_KEYS.map((k) => [k, next[k]])), { merge: true });
    location.hash = '#users';
    return;
  }
  const person = e.target.closest('[data-act="open-person"]');
  if (person) { openUserDrawer(Number(person.dataset.id)); return; }
  if (e.target.closest('[data-act="reload-revenue"]')) AdminPanels.refresh();
});
