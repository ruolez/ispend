/* Admin › Overview: the ten numbers that say how iSpend is doing, how they moved, and what needs a
   person. Everything comes from GET /api/admin/metrics/overview (cached five minutes server side). */

const AOV = { charts: {}, data: null };

AdminPanels.register('overview', {
  label: 'Overview', icon: 'layout-dashboard', group: 'insights', ranged: true,
  sub: 'Revenue, growth and use over the selected period',
  markup: `
    <div id="ov-alerts"></div>
    <div id="ov-tiles"></div>
    <div class="grid grid-2 mt-4">
      <section class="card chart-card">
        <header class="card-head"><h2>Sign-ups and activation</h2><span class="adm-help" tabindex="0" role="note" aria-label="Activated: imported a statement within 7 days of signing up" data-tip="Activated: imported a statement within 7 days of signing up">${icon('help', 'ico-sm')}</span></header>
        <div class="chart-body" style="--h:220px"><canvas id="ch-ov-signups" aria-label="Sign-ups and activated sign-ups per period" role="img"></canvas></div>
        <div class="chart-legend" id="lg-ov-signups"></div>
      </section>
      <section class="card chart-card">
        <header class="card-head"><h2>Active people</h2></header>
        <div class="chart-body" style="--h:220px"><canvas id="ch-ov-active" aria-label="Weekly and monthly active people" role="img"></canvas></div>
        <div class="chart-legend" id="lg-ov-active"></div>
      </section>
    </div>
    <section class="card chart-card mt-4">
      <header class="card-head"><h2>Monthly recurring revenue</h2><span class="hint" id="ov-mrr-note"></span></header>
      <div class="chart-body" style="--h:240px"><canvas id="ch-ov-mrr" aria-label="Monthly recurring revenue over time" role="img"></canvas></div>
      <div class="chart-legend" id="lg-ov-mrr"></div>
    </section>
    <div class="row-between mt-3"><span></span><span class="hint" id="ov-asof"></span></div>`,
  load: loadAdminOverview,
});

async function loadAdminOverview(host, ctx) {
  if (!AOV.data || ctx.force) $('#ov-tiles').innerHTML = adminSkeletonTiles(10);
  let d;
  try {
    d = await api(`/api/admin/metrics/overview${ctx.range}${ctx.force ? '&refresh=1' : ''}`);
  } catch (err) {
    $('#ov-tiles').innerHTML = ui.errorBox(err.message, { retry: 'reload-overview' });
    return;
  }
  if (!ctx.isCurrent()) return;
  AOV.data = d;
  renderOverviewAlerts(d.alerts || []);
  $('#ov-tiles').innerHTML = adminTiles(d.tiles);
  renderOverviewCharts(d);
  $('#ov-asof').innerHTML = adminAsOf(d.as_of);
  $('#ov-mrr-note').textContent = d.other_currencies && d.other_currencies.length
    ? `Shown in ${d.currency.toUpperCase()}; subscriptions in ${d.other_currencies.join(', ').toUpperCase()} are not included` : '';
}

const ALERT_ICON = { error: 'alert-circle', warn: 'alert-triangle', info: 'info' };
const ALERT_TONE = { error: 'danger', warn: 'warning', info: 'info' };

/* One compact list rather than a stack of banners: on a healthy day it is a single line or gone. */
function renderOverviewAlerts(alerts) {
  $('#ov-alerts').innerHTML = alerts.length ? `
    <section class="card adm-alerts mb-4" aria-label="Needs attention">
      ${alerts.map((a) => `<a class="adm-alert adm-alert--${ALERT_TONE[a.level] || 'info'}" href="${esc(a.href)}">
        ${icon(ALERT_ICON[a.level] || 'info', 'ico-sm')}<span class="grow">${esc(a.text)}</span>
        ${a.count > 1 ? `<span class="badge badge-neutral">${fmtNumber(a.count)}</span>` : ''}${icon('chevron-right', 'ico-sm text-4')}</a>`).join('')}
    </section>` : '';
}

function ovLabels(d) {
  return d.series.labels.map((l) => (d.range.bucket === 'month' ? fmtMonth(l.slice(0, 7)) : fmtDate(l)));
}

function renderOverviewCharts(d) {
  const s = d.series;
  const labels = ovLabels(d);
  const prev = s.prev || null;
  const dashed = (color) => ({ borderColor: charts.withAlpha(color, 0.45), borderDash: [4, 4], pointRadius: 0, borderWidth: 1.5, fill: false, tension: 0.3 });

  AOV.charts.signups = makeChart($('#ch-ov-signups'), () => {
    const a = catColor('c1'); const b = catColor('c3');
    return {
      type: 'bar',
      data: { labels, datasets: [
        { label: 'Sign-ups', data: s.signups, backgroundColor: charts.withAlpha(a, 0.85), borderRadius: 3 },
        { label: 'Activated', data: s.activated, backgroundColor: b, borderRadius: 3 },
        ...(prev ? [{ type: 'line', label: 'Sign-ups, previous period', data: prev.signups, ...dashed(a) }] : []),
      ] },
      options: charts.countOptions(),
    };
  });
  charts.htmlLegend($('#lg-ov-signups'), AOV.charts.signups);

  AOV.charts.active = makeChart($('#ch-ov-active'), () => {
    const a = catColor('c5'); const b = catColor('c9');
    return {
      type: 'line',
      data: { labels, datasets: [
        { label: 'Weekly active', data: s.wau, borderColor: a, backgroundColor: (c) => charts.gradientFill(c.chart.ctx, a, { from: 0.22 }), fill: true, tension: 0.3, pointRadius: 0, borderWidth: 2 },
        { label: 'Monthly active', data: s.mau, borderColor: b, fill: false, tension: 0.3, pointRadius: 0, borderWidth: 2 },
        ...(prev ? [{ label: 'Weekly active, previous period', data: prev.wau, ...dashed(a) }] : []),
      ] },
      options: charts.countOptions(),
    };
  });
  charts.htmlLegend($('#lg-ov-active'), AOV.charts.active);

  const cur = (d.currency || 'usd').toUpperCase();
  AOV.charts.mrr = makeChart($('#ch-ov-mrr'), (t) => {
    const a = catColor('c2');
    return {
      type: 'line',
      data: { labels, datasets: [
        { label: 'MRR', data: s.mrr.map((v) => v / 100), borderColor: a, backgroundColor: (c) => charts.gradientFill(c.chart.ctx, a, { from: 0.25 }), fill: true, tension: 0.25, pointRadius: 0, borderWidth: 2 },
        ...(prev ? [{ label: 'MRR, previous period', data: prev.mrr.map((v) => v / 100), ...dashed(a) }] : []),
      ] },
      options: charts.lineOptions(t, { currency: cur }),
    };
  });
  charts.htmlLegend($('#lg-ov-mrr'), AOV.charts.mrr, { currency: cur });
}

document.addEventListener('click', (e) => {
  if (e.target.closest('[data-act="reload-overview"]')) AdminPanels.refresh();
});
