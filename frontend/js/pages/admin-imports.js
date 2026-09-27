/* Admin › Imports & AI (interim): which banks' statements are read, what the files take on disk,
   and what AI has been used for. Reads the older /api/admin/stats/overview until the dedicated
   import-health and AI-cost views replace it. */

AdminPanels.register('imports', {
  label: 'Imports & AI', short: 'Imports', icon: 'file-text', group: 'operations', ranged: true,
  sub: 'Statements read by bank, storage, and AI use over the selected period',
  markup: `
    <div class="grid grid-2">
      <section class="card"><header class="card-head"><h2>Banks</h2></header><div class="card-body" id="ov-profiles"></div></section>
      <section class="card"><header class="card-head"><h2>AI usage</h2></header><div class="card-body" id="ov-ai"></div></section>
    </div>
    <section class="card mt-4"><header class="card-head"><h2>Storage</h2></header><div class="card-body" id="ov-storage"></div></section>`,
  load: loadAdminImports,
});

const RANGE_DAYS = { '7d': 7, '30d': 30, '90d': 90, '12m': 365 };

async function loadAdminImports(host, ctx) {
  const days = RANGE_DAYS[adminRange().range] || 30;
  ['#ov-profiles', '#ov-ai', '#ov-storage'].forEach((sel) => { $(sel).innerHTML = ui.skeletonList(3); });
  let d;
  try {
    d = await api(`/api/admin/stats/overview?days=${days}${ctx.force ? '&refresh=1' : ''}`);
  } catch (err) {
    $('#ov-profiles').innerHTML = ui.errorBox(err.message);
    return;
  }
  if (!ctx.isCurrent()) return;
  renderAI(d.ai || {});
  renderStorage(d.storage || {});
  renderProfiles(d.imports || {});
}

function renderAI(ai) {
  if (!ai.calls) {
    $('#ov-ai').innerHTML = ui.emptyState({ icon: 'sparkles', title: 'No AI calls in this range',
      body: 'Category suggestions and written insights show up here once someone uses them.' });
    return;
  }
  const models = ai.by_model || [];
  const max = Math.max(...models.map((m) => m.calls), 1);
  $('#ov-ai').innerHTML = `
    ${kpis([
      ['Calls', fmtNumber(ai.calls)],
      ['Tokens', fmtNumber((ai.prompt_tokens || 0) + (ai.completion_tokens || 0))],
      ['Errors', fmtPct(ai.error_rate || 0), ai.errors ? 'text-danger' : ''],
      ['Avg time', ai.avg_ms ? `${fmtNumber(Math.round(ai.avg_ms))}<span class="u"> ms</span>` : '—'],
    ])}
    <div class="section-label mt-6 mb-2">By model</div>
    <div class="adm-bars">${models.map((m) => bar(m.model, m.calls, max, `${fmtNumber(m.calls)} · ${fmtNumber(m.tokens)} tok`)).join('')}</div>
    <div class="hint mt-3">Token counts only. Prices live in the OpenRouter catalogue and change, so no cost is estimated here.</div>`;
}

function renderStorage(s) {
  if (!s.disk_scan_ok) {
    $('#ov-storage').innerHTML = `${ui.errorBox('The statements volume could not be read, so only the database figure is available.')}
      ${kpis([['Uploaded originals', fmtBytes(s.source_bytes || 0)], ['Files', fmtNumber(s.unique_files || 0)]], 'mt-4')}`;
    return;
  }
  const total = s.disk_bytes || 0;
  const srcPct = total ? Math.round(((s.source_bytes || 0) / total) * 100) : 0;
  $('#ov-storage').innerHTML = `
    ${kpis([
      ['On disk', fmtBytes(total)],
      ['Uploaded originals', fmtBytes(s.source_bytes || 0)],
      ['OCR & orphans', fmtBytes(s.derived_bytes || 0)],
      ['Files', fmtNumber(s.unique_files || 0)],
    ])}
    <div class="adm-storage-bar mt-4" role="img" aria-label="Uploaded originals ${srcPct}% of disk use">
      <i style="width:${srcPct}%;background:var(--c1)"></i><i style="width:${100 - srcPct}%;background:var(--c9)"></i>
    </div>
    <div class="adm-legend mt-2">
      <span><i class="dot" style="--c:var(--c1)"></i>Uploaded originals ${fmtPct(srcPct / 100)}</span>
      <span><i class="dot" style="--c:var(--c9)"></i>OCR &amp; orphans ${fmtPct((100 - srcPct) / 100)}</span>
    </div>
    <div class="hint mt-3">“On disk” is measured by scanning the volume, so it counts OCR output and any orphaned files.
      “Uploaded originals” comes from the database and is de-duplicated by content hash.</div>`;
}

function renderProfiles(imports) {
  const rows = imports.by_profile || [];
  if (!rows.length) {
    $('#ov-profiles').innerHTML = ui.emptyState({ icon: 'file-text', title: 'No statements imported yet',
      body: 'Each bank shows up here with the number of statements it has read.' });
    return;
  }
  const max = Math.max(...rows.map((r) => r.n), 1);
  $('#ov-profiles').innerHTML = `<div class="adm-bars">${rows.map((r) => bar(
    r.profile, r.n, max,
    `${fmtNumber(r.n)}${r.ocr ? ` · ${fmtNumber(r.ocr)} OCR` : ''}${r.errors ? ` · ${fmtNumber(r.errors)} failed` : ''}`)).join('')}</div>
    <div class="section-label mt-6 mb-2">File types</div>
    <div class="adm-kinds">${(imports.by_kind || []).map((k) => `<span class="badge badge-neutral">${esc(k.kind)} · ${fmtNumber(k.n)}</span>`).join(' ')}</div>`;
}
