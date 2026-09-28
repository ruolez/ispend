/* Admin › Imports & AI: how well statements are read, bank by bank, and what AI is used for and
   costs. File names never appear here; failure reasons are the generic messages people saw. */

const AIM = { charts: {} };

AdminPanels.register('imports', {
  label: 'Imports & AI', short: 'Imports', icon: 'file-text', group: 'operations', ranged: true,
  sub: 'How statements are read, bank by bank, and what AI is used for',
  markup: `
    <div id="im-tiles"></div>
    <section class="card chart-card mt-4">
      <header class="card-head"><h2>Statements uploaded</h2></header>
      <div class="chart-body" style="--h:200px"><canvas id="ch-im" role="img" aria-label="Statements imported and failed per period"></canvas></div>
      <div class="chart-legend" id="lg-im"></div>
    </section>
    <div class="grid grid-2 mt-4">
      <section class="card"><header class="card-head"><h2>By bank</h2></header><div id="im-banks"></div></section>
      <section class="card"><header class="card-head"><h2>Why imports failed</h2></header><div class="card-body" id="im-reasons"></div></section>
    </div>
    <section class="card mt-4"><header class="card-head"><h2>AI</h2><span class="hint" id="ai-note"></span></header><div class="card-body" id="im-ai"></div></section>
    <div class="row-between mt-3"><span></span><span class="hint" id="im-asof"></span></div>`,
  load: loadAdminImports,
});

async function loadAdminImports(host, ctx) {
  if (!AIM.data || ctx.force) $('#im-tiles').innerHTML = adminSkeletonTiles(6);
  let d, ai;
  try {
    [d, ai] = await Promise.all([api(`/api/admin/imports${ctx.range}${ctx.force ? '&refresh=1' : ''}`),
      api(`/api/admin/ai${ctx.range}${ctx.force ? '&refresh=1' : ''}`)]);
  } catch (err) {
    $('#im-tiles').innerHTML = ui.errorBox(err.message, { retry: 'reload-imports' });
    return;
  }
  if (!ctx.isCurrent()) return;
  AIM.data = d;
  const t = d.totals;
  const a = ai.totals;
  $('#im-tiles').innerHTML = adminTiles([
    { key: 'uploaded', label: 'Statements uploaded', value: t.uploaded, spark: d.series.n, agg: 'sum', help: 'Files people uploaded in this period.' },
    { key: 'committed', label: 'Imported', value: t.committed, spark: d.series.committed, agg: 'sum', help: 'Uploads that became transactions.' },
    { key: 'failed', label: 'Could not be read', value: t.failure_rate, unit: 'pct', good: 'down', help: `${plural(t.failed, 'upload')} in this period failed to read.` },
    { key: 'read_time', label: 'Typical reading time', value: t.p50_ms == null ? null : t.p50_ms / 1000, unit: 'secs', good: 'down',
      help: `Median time to read a file. The slowest 5% take ${t.p95_ms == null ? '—' : `${fmtNumber(t.p95_ms / 1000, { decimals: 1 })} s`} (scanned PDFs).` },
    { key: 'ai_calls', label: 'AI requests', value: a.calls, spark: ai.series.calls, agg: 'sum', help: `${plural(a.users, 'person')} used AI; ${fmtPct(a.error_rate || 0)} of requests failed.` },
    { key: 'ai_cost', label: 'AI cost on the shared key', value: Math.round(a.shared_cost_usd * 100), unit: 'money', currency: 'usd', good: 'neutral',
      help: 'What the shared OpenRouter key spent. People who use their own key pay for their own requests.' },
  ].map((x) => ({ good: 'up', ...x })).map(adminTileFormatSeconds));
  renderImportChart(d);
  renderBanks(d.by_profile, d.by_kind);
  renderReasons(d.error_messages);
  renderAiReport(ai);
  $('#im-asof').innerHTML = adminAsOf(d.as_of);
}

/* Reading time is shown in seconds with one decimal, which the shared tile formatter does not know. */
function adminTileFormatSeconds(t) {
  if (t.unit !== 'secs') return t;
  return { ...t, unit: 'count', value: t.value == null ? null : Math.round(t.value * 10) / 10, suffix: ' s' };
}

function renderImportChart(d) {
  const labels = d.series.labels.map((l) => bucketLabel(l, d.range.bucket));
  const other = d.series.n.map((n, i) => Math.max(0, n - d.series.committed[i] - d.series.failed[i]));
  AIM.charts.im = makeChart($('#ch-im'), () => ({
    type: 'bar',
    data: { labels, datasets: [
      { label: 'Imported', data: d.series.committed, backgroundColor: charts.css('--success'), borderRadius: 2, stack: 's' },
      { label: 'Waiting or discarded', data: other, backgroundColor: charts.withAlpha(charts.css('--accent'), 0.35), borderRadius: 2, stack: 's' },
      { label: 'Could not be read', data: d.series.failed, backgroundColor: charts.css('--danger'), borderRadius: 2, stack: 's' },
    ] },
    options: charts.countOptions({ stacked: true }),
  }));
  charts.htmlLegend($('#lg-im'), AIM.charts.im);
}

function renderBanks(rows, kinds) {
  if (!rows.length) {
    $('#im-banks').innerHTML = `<div class="card-body">${ui.emptyState({ icon: 'landmark', title: 'No statements in this period', body: 'Each bank shows here with how its files were read.' })}</div>`;
    return;
  }
  const secs = (ms) => (ms == null ? '—' : `${fmtNumber(ms / 1000, { decimals: 1 })} s`);
  $('#im-banks').innerHTML = `<div class="tbl-wrap"><table class="tbl tbl--list"><thead><tr><th>Bank</th><th class="right">Files</th>
    <th class="right">Failed</th><th class="right">Scanned</th><th class="right">Typical</th><th class="right">Slowest 5%</th></tr></thead>
    <tbody>${rows.map((r) => `<tr><td>${esc(r.profile === 'unknown' ? 'Not recognised' : r.profile)}${r.confidence != null && r.confidence < 0.6 ? ' <span class="badge badge-warning" data-tip="Recognised with low confidence">unsure</span>' : ''}</td>
      <td class="right num">${fmtNumber(r.n)}</td><td class="right num ${r.errors ? 'text-danger' : ''}">${fmtNumber(r.errors)}</td>
      <td class="right num">${fmtNumber(r.ocr)}</td><td class="right num">${secs(r.p50_ms)}</td><td class="right num">${secs(r.p95_ms)}</td></tr>`).join('')}</tbody></table></div>
    <div class="card-body adm-kinds">${kinds.map((k) => `<span class="badge badge-neutral">${esc(k.kind)} · ${fmtNumber(k.n)}${k.errors ? ` · ${fmtNumber(k.errors)} failed` : ''}</span>`).join(' ')}</div>`;
}

function renderReasons(rows) {
  const max = Math.max(...rows.map((r) => r.n), 1);
  $('#im-reasons').innerHTML = rows.length
    ? `<div class="adm-bars">${rows.map((r) => bar(r.message, r.n, max, plural(r.n, 'time'))).join('')}</div>`
    : `<div class="hint">${icon('check-circle', 'ico-sm')} Every file in this period was read.</div>`;
}

function renderAiReport(ai) {
  const t = ai.totals;
  $('#ai-note').textContent = t.cost_is_partial ? 'Some costs are estimates or unknown' : '';
  if (!t.calls) {
    $('#im-ai').innerHTML = ui.emptyState({ icon: 'sparkles', title: 'No AI requests in this period', body: 'Category suggestions, statement reading and written insights show up here once someone uses them.' });
    return;
  }
  const table = (title, rows, name) => `<div><div class="section-label mb-2">${esc(title)}</div>
    <div class="tbl-wrap"><table class="tbl tbl--list"><thead><tr><th>${esc(name)}</th><th class="right">Requests</th><th class="right">Failed</th><th class="right">Cost</th></tr></thead>
    <tbody>${rows.map((r) => `<tr><td class="truncate">${esc(AI_LABEL[r.key] || r.key || 'unknown')}</td><td class="right num">${fmtNumber(r.calls)}</td>
      <td class="right num ${r.errors ? 'text-danger' : ''}">${fmtNumber(r.errors)}</td><td class="right num">${esc(fmtMoney(r.cost_usd, 'USD'))}</td></tr>`).join('')}</tbody></table></div></div>`;
  $('#im-ai').innerHTML = `
    ${kpis([['Requests', fmtNumber(t.calls)], ['Tokens', fmtNumber(t.tokens)], ['Total cost', esc(fmtMoney(t.cost_usd, 'USD'))],
      ['Typical time', t.avg_ms ? `${fmtNumber(t.avg_ms)}<span class="u"> ms</span>` : '—']])}
    <div class="grid grid-2 mt-6">${table('What for', ai.by_purpose, 'Use')}${table('Whose key', ai.by_key_source, 'Key')}</div>
    <div class="mt-6">${table('Models', ai.by_model, 'Model')}</div>`;
}

const AI_LABEL = { categorize: 'Suggesting categories', extract: 'Reading statements', insights: 'Written insights', test: 'Connection tests',
  shared: 'Shared key', own: 'Their own key', unknown: 'Before this was recorded' };

document.addEventListener('click', (e) => {
  if (e.target.closest('[data-act="reload-imports"]')) AdminPanels.refresh();
});
