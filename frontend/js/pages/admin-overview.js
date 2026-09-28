/* Admin › Home: what needs a person first, whether the machinery is working, then the numbers that
   say how iSpend is doing and how they moved. Numbers and the inbox come from GET
   /api/admin/metrics/overview (cached five minutes server side); the status strip from
   /api/admin/system/health. Every tile and inbox row opens the list or page behind it. */

const AOV = { charts: {}, data: null };

AdminPanels.register('overview', {
  label: 'Home', icon: 'home', group: 'home', ranged: true, compare: true,
  sub: 'What needs you, and how iSpend is doing over the selected period',
  markup: `
    <div id="ov-alerts"></div>
    <div class="adm-status" id="ov-status" aria-label="System status"></div>
    <div id="ov-tiles"></div>
    <div class="grid grid-2 mt-4">
      <section class="card chart-card">
        <header class="card-head"><h2>Sign-ups and activation</h2><span class="adm-help" tabindex="0" role="note" aria-label="Activated: imported a statement within 7 days of signing up" data-tip="Activated: imported a statement within 7 days of signing up">${icon('help', 'ico-sm')}</span></header>
        <div class="chart-body" style="--h:220px"><canvas id="ch-ov-signups" aria-label="Sign-ups and activated sign-ups per period" role="img"></canvas></div>
        <div class="chart-legend" id="lg-ov-signups"></div>
      </section>
      <section class="card chart-card">
        <header class="card-head"><h2>Active customers</h2></header>
        <div class="chart-body" style="--h:220px"><canvas id="ch-ov-active" aria-label="Weekly and monthly active customers" role="img"></canvas></div>
        <div class="chart-legend" id="lg-ov-active"></div>
      </section>
    </div>
    <div class="row-between mt-3"><span class="hint" id="ov-mrr-note"></span><span class="hint" id="ov-asof"></span></div>`,
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
  renderOverviewAlerts(d.alerts || [], AOV.leftover);
  loadOverviewExtras(ctx);
  $('#ov-tiles').innerHTML = adminTiles(d.tiles);
  renderOverviewCharts(d);
  $('#ov-asof').innerHTML = adminAsOf(d.as_of);
  $('#ov-mrr-note').textContent = d.other_currencies && d.other_currencies.length
    ? `Shown in ${d.currency.toUpperCase()}; subscriptions in ${d.other_currencies.join(', ').toUpperCase()} are not included` : '';
}

const ALERT_ICON = { error: 'alert-circle', warn: 'alert-triangle', info: 'info' };
const ALERT_TONE = { error: 'danger', warn: 'warning', info: 'info' };

/* The inbox: one compact list, most urgent first, each row going where it can be dealt with. On a
   healthy day it says so in one line. */
function renderOverviewAlerts(alerts, leftover) {
  const rows = [...alerts];
  if (leftover) rows.push({ level: 'info', count: 0, href: '#settings/account',
    text: 'Your admin account still holds finance data from before. Delete it in Settings › My account.' });
  $('#ov-alerts').innerHTML = `<section class="card adm-alerts mb-4" aria-labelledby="ov-inbox-h">
    <header class="adm-inbox-head"><h2 id="ov-inbox-h">Needs attention</h2>${rows.length ? `<span class="badge badge-neutral">${fmtNumber(rows.length)}</span>` : ''}</header>
    ${rows.length ? rows.map((a) => `<a class="adm-alert adm-alert--${ALERT_TONE[a.level] || 'info'}" href="${esc(a.href)}">
        ${icon(ALERT_ICON[a.level] || 'info', 'ico-sm')}<span class="grow">${esc(a.text)}</span>
        ${a.count > 1 ? `<span class="badge badge-neutral">${fmtNumber(a.count)}</span>` : ''}${icon('chevron-right', 'ico-sm text-4')}</a>`).join('')
    : `<div class="adm-alert adm-alert--clear">${icon('check-circle', 'ico-sm')}<span class="grow">All clear — nothing needs you right now.</span></div>`}
  </section>`;
}

/* The status strip and the leftover-data reminder load beside the numbers, never in their way. */
async function loadOverviewExtras(ctx) {
  const [health, leftover] = await Promise.all([
    api(`/api/admin/system/health${ctx.force ? '?refresh=1' : ''}`).catch(() => null),
    AOV.leftover === undefined ? api('/api/admin/me/leftover-data').then((r) => r.any).catch(() => false) : AOV.leftover,
  ]);
  if (!ctx.isCurrent()) return;
  if (leftover !== AOV.leftover) { AOV.leftover = leftover; renderOverviewAlerts(AOV.data.alerts || [], leftover); }
  $('#ov-status').innerHTML = health ? SYS_PARTS.filter(([k]) => k !== 'storage').map(([key, label]) => {
    const off = key === 'stripe' && !health.stripe.enabled;
    const st = health[key].status;
    return `<a class="adm-status-item" href="#system" data-tip="${esc(chipSummary(key, health[key]))}">
      <i class="dot" style="--c:var(--${off ? 'text-4' : SYS_TONE[st]})"></i><span>${esc(label)}</span>
      <span class="sr-only">: ${esc(off ? 'off' : SYS_WORD[st])}, ${esc(chipSummary(key, health[key]))}</span></a>`;
  }).join('') : '';
}
window.addEventListener('ispend:admin-leftover-cleared', () => { AOV.leftover = false; });

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

}

document.addEventListener('click', (e) => {
  if (e.target.closest('[data-act="reload-overview"]')) AdminPanels.refresh();
});
