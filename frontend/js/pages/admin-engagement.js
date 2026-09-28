/* Admin › Engagement: from sign-up to habit. The activation funnel (with where sign-ups came from), how
   many people use iSpend and for what, and whether they come back — as counts of people and days,
   never what they looked at. */

const AEN = { charts: {}, mode: 'weekly' };

AdminPanels.register('engagement', {
  label: 'Engagement', icon: 'activity', group: 'business', ranged: true,
  sub: 'From sign-up to habit: activation, active people and who comes back',
  markup: `
    <div id="en-tiles"></div>
    <div class="grid grid-2 mt-4">
      <section class="card"><header class="card-head"><h2>Getting started</h2><span class="hint">Of the people who signed up in this period</span></header>
        <div class="card-body" id="en-funnel"></div></section>
      <section class="card"><header class="card-head"><h2>By source</h2></header><div id="en-sources"></div></section>
    </div>
    <div class="grid grid-2 mt-4">
      <section class="card chart-card">
        <header class="card-head"><h2>Active people</h2></header>
        <div class="chart-body" style="--h:220px"><canvas id="ch-en-active" role="img" aria-label="Daily, weekly and monthly active people"></canvas></div>
        <div class="chart-legend" id="lg-en-active"></div>
      </section>
      <section class="card"><header class="card-head"><h2>What people did</h2><span class="hint">In this period</span></header>
        <div class="card-body" id="en-kinds"></div></section>
    </div>
    <section class="card mt-4">
      <header class="card-head"><h2>Who comes back</h2>
        <div class="seg" id="en-mode" role="radiogroup" aria-label="Retention by">
          <button type="button" class="seg-btn active" role="radio" aria-checked="true" data-mode="weekly">Weekly, any use</button>
          <button type="button" class="seg-btn" role="radio" aria-checked="false" data-mode="monthly">Monthly, imports</button></div></header>
      <div class="card-body" id="en-retention"></div>
    </section>
    <div class="row-between mt-3"><span></span><span class="hint" id="en-asof"></span></div>`,
  load: loadAdminEngagement,
});

async function loadAdminEngagement(host, ctx) {
  if (!AEN.wired) {
    AEN.wired = true;
    ui.segmented($('#en-mode'), { onChange: (b) => { AEN.mode = b.dataset.mode; AdminPanels.refresh(); } });
  }
  if (!AEN.data || ctx.force) $('#en-tiles').innerHTML = adminSkeletonTiles(6);
  let d, f;
  try {
    [d, f] = await Promise.all([
      api(`/api/admin/metrics/engagement${ctx.range}&mode=${AEN.mode}`),
      api(`/api/admin/metrics/funnel${ctx.range}`),
    ]);
  } catch (err) {
    $('#en-tiles').innerHTML = ui.errorBox(err.message, { retry: 'reload-engagement' });
    return;
  }
  if (!ctx.isCurrent()) return;
  AEN.data = d;
  $('#en-tiles').innerHTML = adminTiles(d.tiles);
  renderFunnel(f);
  renderSources(f.by_source);
  renderActiveChart(d);
  renderKinds(d.by_kind);
  renderRetention(d.retention);
  $('#en-asof').innerHTML = adminAsOf(d.as_of);
}

function dayBefore(iso) {
  const d = new Date(`${iso}T00:00:00`);
  d.setDate(d.getDate() - 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

function fmtHours(h) {
  if (h == null) return '';
  if (h < 1) return `${Math.max(1, Math.round(h * 60))} min`;
  if (h < 48) return `${Math.round(h)} h`;
  return `${Math.round(h / 24)} days`;
}

const FUNNEL_DRILL = { signed_up: {}, confirmed: { confirmed: '1' }, uploaded: {}, activated: { activated: '1' }, paid: { state: 'active' } };

function renderFunnel(f) {
  const first = f.steps[0].n;
  if (!first) {
    $('#en-funnel').innerHTML = ui.emptyState({ icon: 'users', title: 'No sign-ups in this period', body: 'Each step from sign-up to paying shows here with how many made it and how long it took.' });
    return;
  }
  const r = f.range;
  const signup = { signup_from: r.start, signup_to: dayBefore(r.end) };
  $('#en-funnel').innerHTML = `<ol class="adm-funnel">${f.steps.map((s, i) => {
    const pct = s.pct_first == null ? 0 : Math.round(s.pct_first * 100);
    const href = adminHref('customers', { params: { ...signup, ...FUNNEL_DRILL[s.key] } });
    return `<li>
      <div class="adm-funnel-head"><span class="grow">${esc(s.label)}</span>
        <a class="row-link num" href="${esc(href)}">${fmtNumber(s.n)}</a></div>
      <div class="adm-funnel-bar" style="--p:${pct}%"><span></span></div>
      <div class="adm-funnel-foot text-3">${i === 0 ? 'Everyone' : `${esc(fmtPct(s.pct_prev || 0))} of the step before`}${s.median_hours != null ? ` · typically ${esc(fmtHours(s.median_hours))}` : ''}</div>
    </li>`;
  }).join('')}</ol>
  ${f.confirmation_required ? '' : '<div class="hint mt-2">Email confirmation is off, so that step is skipped.</div>'}`;
}

function renderSources(rows) {
  if (!rows.length) { $('#en-sources').innerHTML = `<div class="card-body">${ui.emptyState({ icon: 'globe', title: 'No sign-ups yet', body: 'Where people came from shows here.' })}</div>`; return; }
  $('#en-sources').innerHTML = `<div class="tbl-wrap"><table class="tbl tbl--list"><thead><tr><th>Source</th><th class="right">Sign-ups</th>
    <th class="right">Activated</th><th class="right">Paid</th></tr></thead><tbody>
    ${rows.map((s) => `<tr><td><a class="row-link" href="${esc(adminHref('customers', { params: { source: s.channel } }))}">${esc(s.channel)}</a></td>
      <td class="right num">${fmtNumber(s.signups)}</td>
      <td class="right num">${esc(fmtPct(s.activation_rate || 0, { decimals: 0 }))}</td>
      <td class="right num">${esc(fmtPct(s.paid_rate || 0, { decimals: 0 }))}</td></tr>`).join('')}</tbody></table></div>`;
}

function renderActiveChart(d) {
  const labels = d.series.labels.map((l) => bucketLabel(l, d.range.bucket));
  AEN.charts.active = makeChart($('#ch-en-active'), () => {
    const a = catColor('c5'); const b = catColor('c9'); const c = catColor('c1');
    return { type: 'line', data: { labels, datasets: [
      { label: 'Monthly active', data: d.series.mau, borderColor: b, fill: false, tension: 0.3, pointRadius: 0, borderWidth: 2 },
      { label: 'Weekly active', data: d.series.wau, borderColor: a, backgroundColor: (x) => charts.gradientFill(x.chart.ctx, a, { from: 0.2 }), fill: true, tension: 0.3, pointRadius: 0, borderWidth: 2 },
      { label: 'Monthly importers', data: d.series.importers, borderColor: c, borderDash: [4, 4], fill: false, tension: 0.3, pointRadius: 0, borderWidth: 1.5 },
    ] }, options: charts.countOptions() };
  });
  charts.htmlLegend($('#lg-en-active'), AEN.charts.active);
}

const USE_KIND_LABEL = [['import', 'Imported a statement'], ['categorize', 'Sorted transactions or made rules'],
  ['report', 'Read reports, budgets or insights'], ['dashboard', 'Opened the dashboard'], ['seen', 'Opened iSpend at all']];

function renderKinds(k) {
  const max = Math.max(k.seen || 0, 1);
  $('#en-kinds').innerHTML = k.seen
    ? `<div class="adm-bars">${USE_KIND_LABEL.map(([key, label]) => bar(label, k[key] || 0, max, people(k[key] || 0))).join('')}</div>`
    : ui.emptyState({ icon: 'activity', title: 'No activity recorded in this period', body: 'Days people use iSpend are counted from the first request after this update.' });
}

function renderRetention(r) {
  if (!r.rows.length) {
    $('#en-retention').innerHTML = ui.emptyState({ icon: 'repeat', title: 'Not enough history yet', body: 'Cohorts appear once people have signed up in at least one full week.' });
    return;
  }
  const unit = r.unit === 'week' ? 'Week' : 'Month';
  $('#en-retention').innerHTML = `<div class="adm-heat-wrap"><table class="adm-heat">
    <thead><tr><th scope="col">Signed up</th><th scope="col" class="right">People</th>
      ${Array.from({ length: r.periods }, (_, i) => `<th scope="col">${unit.charAt(0)}${i}</th>`).join('')}</tr></thead>
    <tbody>${r.rows.map((row) => `<tr><th scope="row">${esc(r.unit === 'week' ? fmtDate(row.cohort) : fmtMonth(row.cohort.slice(0, 7)))}</th>
      <td class="right num">${fmtNumber(row.size)}</td>
      ${row.cells.map((c, i) => (c == null ? '<td class="is-future"></td>'
        : `<td class="${c.partial ? 'is-partial' : ''} ${(c.pct || 0) >= 0.55 ? 'is-strong' : ''}" style="--p:${Math.round((c.pct || 0) * 100)}" data-tip="${esc(`${unit} ${i}: ${people(c.n)} of ${row.size}${c.partial ? ' — still in progress' : ''}`)}">${row.size ? Math.round((c.pct || 0) * 100) : '—'}</td>`)).join('')}</tr>`).join('')}
    </tbody></table></div>
    <div class="hint mt-2">Share of each sign-up ${r.unit} who ${r.mode === 'weekly' ? 'imported, sorted transactions or read a report' : 'imported a statement'} in each ${r.unit} after signing up. Faded cells are still in progress.</div>`;
}

document.addEventListener('click', (e) => {
  if (e.target.closest('[data-act="reload-engagement"]')) AdminPanels.refresh();
});
