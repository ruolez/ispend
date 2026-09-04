/* Statements: every upload, its status, and actions (continue, view, download, reparse, rollback). */
const STATUS_META = {
  parsing: { label: 'Parsing', cls: 'badge-info', spin: true },
  uploaded: { label: 'Queued', cls: 'badge-neutral', spin: true },
  previewed: { label: 'Needs review', cls: 'badge-warning' },
  committing: { label: 'Importing', cls: 'badge-info', spin: true },
  committed: { label: 'Imported', cls: 'badge-success' },
  error: { label: 'Failed', cls: 'badge-danger' },
  discarded: { label: 'Discarded', cls: 'badge-neutral' },
};
const KIND_ICON = { pdf: 'file-text', csv: 'file-spreadsheet', xlsx: 'file-spreadsheet', xls: 'file-spreadsheet' };
const stState = { rows: [], profiles: [], pollTimer: null };

initNav('statements').then(async () => {
  $('#btn-import').innerHTML = `${icon('upload')}<span class="label">Import statement</span>`;
  document.body.addEventListener('click', onStatementsAction);
  await loadStatements();
});

async function loadStatements({ quiet } = {}) {
  const host = $('#statements-host');
  if (!quiet) host.innerHTML = `<div class="tbl-wrap"><table class="tbl"><thead><tr><th>File</th><th>Bank</th><th>Account</th><th>Period</th><th>Status</th><th class="right">Rows</th><th>Uploaded</th><th>By</th><th></th></tr></thead><tbody>${ui.skeletonRows(4, 9)}</tbody></table></div>`;
  try {
    const [rows, profiles] = await Promise.all([api('/api/statements'), store.get('institutions', '/api/accounts/institutions', { ttl: 600000 }).catch(() => [])]);
    stState.rows = rows; stState.profiles = profiles;
  } catch (err) { host.innerHTML = ui.errorBox(err.message, { retry: 'reload-statements' }); return; }
  renderStatements();
  clearTimeout(stState.pollTimer);
  if (stState.rows.some((s) => ['parsing', 'uploaded', 'committing'].includes(s.status))) {
    stState.pollTimer = setTimeout(() => loadStatements({ quiet: true }), 2000);
  }
}

function bankLabel(key) {
  if (!key) return 'Generic';
  const p = stState.profiles.find((x) => x.key === key);
  return p ? p.label : key;
}

function renderStatements() {
  const host = $('#statements-host');
  if (!stState.rows.length) {
    host.innerHTML = `<div class="card">${ui.emptyState({ icon: 'file-text', title: 'No statements yet', body: 'Upload a CSV, Excel or PDF statement from your bank or card. iSpend detects the format, checks for duplicates and categorizes what it can.', action: { label: 'Import a statement', href: '/import.html' } })}</div>`;
    return;
  }
  host.innerHTML = `<div class="tbl-wrap"><table class="tbl tbl-statements"><thead><tr>
      <th>File</th><th class="hide-mobile">Bank</th><th>Account</th><th class="hide-mobile">Period</th><th>Status</th><th class="right hide-mobile">Rows</th><th class="hide-mobile">Uploaded</th><th class="hide-mobile">By</th><th class="col-actions"></th>
    </tr></thead><tbody>${stState.rows.map(statementRow).join('')}</tbody></table></div>`;
}

function statementRow(s) {
  const st = STATUS_META[s.status] || { label: s.status, cls: 'badge-neutral' };
  const stats = s.stats || {};
  const imported = stats.imported != null ? stats.imported : (s.status === 'committed' ? s.txn_count : null);
  const skipped = (stats.skipped_duplicates || 0) + (stats.skipped_invalid || 0) + (stats.excluded_by_user || 0);
  let rows = '—';
  if (s.status === 'committed') rows = `<span class="st-counts"><b>${fmtNumber(imported)}</b> imported${skipped ? `<span class="muted"> · ${fmtNumber(skipped)} skipped</span>` : ''}</span>`;
  else if (s.status === 'previewed') rows = `<span class="st-counts">${fmtNumber(stats.rows_total || stats.rows_valid || 0)} found${stats.dupes_existing ? `<span class="muted"> · ${fmtNumber(stats.dupes_existing)} dup</span>` : ''}</span>`;
  const period = s.period_start ? `${fmtDate(s.period_start)} – ${fmtDate(s.period_end || s.period_start)}` : '—';
  const isClickable = s.status === 'previewed' || s.status === 'committed';
  return `<tr data-id="${s.id}" class="${isClickable ? 'is-clickable' : ''}">
    <td><div class="st-file"><span class="st-file-icon ${esc(s.file_kind)}">${icon(KIND_ICON[s.file_kind] || 'file')}</span>
      <div class="merchant"><span class="st-file-name" title="${esc(s.original_filename)}">${esc(s.original_filename)}</span><span class="st-file-meta">${esc(String(s.file_kind || '').toUpperCase())} · ${fmtBytes(s.file_size)}${s.ocr_applied ? ' · OCR' : ''}</span></div></div></td>
    <td class="hide-mobile">${esc(bankLabel(s.bank_profile))}</td>
    <td>${s.account_name ? esc(s.account_name) : '<span class="text-4">Not set</span>'}</td>
    <td class="hide-mobile text-2">${period}</td>
    <td><span class="st-status"><span class="badge ${st.cls}" aria-label="Status: ${esc(st.label)}${st.spin ? ', in progress' : ''}"${st.spin ? ' aria-busy="true"' : ''}>${st.spin ? '<span class="spinner" aria-hidden="true"></span>' : ''}${esc(st.label)}</span>${s.status === 'error' && s.error_message ? `<span class="text-3 fs-xs truncate" style="max-width:220px" title="${esc(s.error_message)}">${esc(s.error_message)}</span>` : ''}</span></td>
    <td class="right hide-mobile">${rows}</td>
    <td class="hide-mobile text-3" title="${esc(fmtDateTime(s.created_at))}">${fmtRelative(s.created_at)}</td>
    <td class="hide-mobile text-3">${s.username ? `<span class="row gap-2"><span class="avatar avatar-xs" aria-hidden="true">${esc(initials(s.username))}</span><span>${esc(s.username)}</span></span>` : '—'}</td>
    <td class="col-actions"><div class="row-actions">${s.status === 'previewed' ? `<a class="btn btn-xs btn-secondary" href="/import.html?statement=${s.id}" data-stop>Continue</a>` : ''}<button type="button" class="btn btn-icon btn-ghost btn-xs" data-act="menu" data-id="${s.id}" aria-label="More">${icon('more-horizontal')}</button></div></td>
  </tr>`;
}

function onStatementsAction(e) {
  const act = e.target.closest('[data-act]');
  if (act) {
    const s = stState.rows.find((x) => x.id === Number(act.dataset.id));
    if (act.dataset.act === 'reload-statements') return loadStatements();
    if (act.dataset.act === 'menu' && s) return openStatementMenu(act, s);
    return;
  }
  if (e.target.closest('[data-stop]')) return;
  const tr = e.target.closest('tr[data-id]');
  if (!tr || !tr.classList.contains('is-clickable')) return;
  const s = stState.rows.find((x) => x.id === Number(tr.dataset.id));
  if (!s) return;
  if (s.status === 'previewed') location.href = `/import.html?statement=${s.id}`;
  else if (s.status === 'committed') location.href = `/transactions.html?statement=${s.id}&range=all`;
}

function openStatementMenu(anchor, s) {
  const items = [];
  if (s.status === 'previewed') items.push({ label: 'Continue import', icon: 'upload', href: `/import.html?statement=${s.id}` });
  if (s.status === 'committed') items.push({ label: 'View transactions', icon: 'list', href: `/transactions.html?statement=${s.id}&range=all` });
  items.push({ label: 'Download original', icon: 'download', href: `/api/statements/${s.id}/file` });
  if (['error', 'previewed'].includes(s.status)) items.push({ label: 'Parse again', icon: 'refresh', onClick: () => reparseStatement(s) });
  items.push({ divider: true });
  if (s.status === 'committed') items.push({ label: 'Flip signs…', icon: 'arrow-left-right', onClick: () => flipStatement(s) });
  if (s.status === 'committed') items.push({ label: 'Roll back import…', icon: 'undo', danger: true, onClick: () => rollbackStatement(s) });
  else if (!['parsing', 'committing'].includes(s.status)) items.push({ label: 'Delete', icon: 'trash', danger: true, onClick: () => deleteStatement(s) });
  ui.menu(anchor, items);
}

async function reparseStatement(s) {
  try {
    await api(`/api/statements/${s.id}/reparse`, { method: 'POST', body: { keep_mapping: s.status === 'previewed' } });
    toast('Parsing again…', { type: 'info' });
    loadStatements({ quiet: true });
  } catch (err) { toast(err.message, { type: 'error' }); }
}

async function deleteStatement(s) {
  const ok = await ui.confirm({ title: 'Delete statement?', body: `“${esc(s.original_filename)}” and its preview rows will be removed. Nothing was imported from it.`, confirmText: 'Delete', danger: true });
  if (!ok) return;
  try { await api(`/api/statements/${s.id}`, { method: 'DELETE' }); toast('Statement deleted', { type: 'success' }); loadStatements({ quiet: true }); }
  catch (err) { toast(err.message, { type: 'error' }); }
}

async function rollbackStatement(s) {
  const n = s.txn_count || (s.stats || {}).imported || 0;
  const ok = await ui.confirm({ title: 'Roll back this import?', body: `This deletes the <b>${fmtNumber(n)}</b> transactions imported from “${esc(s.original_filename)}”, including any categories or notes you added to them. The file itself is removed too.`, confirmText: `Delete ${fmtNumber(n)} transactions`, danger: true });
  if (!ok) return;
  try {
    const r = await api(`/api/statements/${s.id}?with_transactions=true`, { method: 'DELETE' });
    toast(`Rolled back · ${fmtNumber(r.deleted_transactions || 0)} transactions deleted`, { type: 'success' });
    window.dispatchEvent(new Event('ispend:transactions-changed'));
    loadStatements({ quiet: true });
  } catch (err) { toast(err.message, { type: 'error' }); }
}


async function flipStatement(s) {
  const ok = await ui.confirm({
    title: 'Flip charges and payments?',
    body: `Every transaction imported from <b>${esc(s.original_filename)}</b> will have its sign reversed: charges become payments and payments become charges. Use this when a card export listed purchases as positive amounts. You can flip again to undo.`,
    confirmText: 'Flip signs',
  });
  if (!ok) return;
  try {
    const r = await api(`/api/statements/${s.id}/flip-signs`, { method: 'POST', body: {} });
    toast(`Flipped ${r.flipped} transactions`, { type: 'success' });
    window.dispatchEvent(new Event('ispend:transactions-changed'));
    loadStatements({ quiet: true });
  } catch (err) { toast(err.message, { type: 'error' }); }
}
