/* Import: upload -> poll -> review (detection, account, mapping, sign check, preview) -> commit -> done. */
const ACCEPT = ['.csv', '.xlsx', '.xls', '.pdf'];
const KIND_ICON = { pdf: 'file-text', csv: 'file-spreadsheet', xlsx: 'file-spreadsheet', xls: 'file-spreadsheet' };
const ROLE_OPTIONS = [['', 'Ignore'], ['date', 'Date'], ['posted_date', 'Posted date'], ['description', 'Description'], ['amount', 'Amount'], ['debit', 'Debit'], ['credit', 'Credit'], ['balance', 'Balance']];
const DATE_FORMATS = [['', 'Auto-detect'], ['%m/%d/%Y', 'MM/DD/YYYY'], ['%d/%m/%Y', 'DD/MM/YYYY'], ['%Y-%m-%d', 'YYYY-MM-DD'], ['%m/%d/%y', 'MM/DD/YY'], ['%Y%m%d', 'YYYYMMDD'], ['%b %d, %Y', 'Mon DD, YYYY'], ['%d %b %Y', 'DD Mon YYYY']];
const ACCOUNT_TYPES = [['checking', 'Checking'], ['savings', 'Savings'], ['credit_card', 'Credit card'], ['line_of_credit', 'Line of credit'], ['loan', 'Loan'], ['investment', 'Investment'], ['cash', 'Cash'], ['other', 'Other']];

const imp = {
  step: 'upload',
  files: [],          // {lid, name, size, kind, progress, statementId, statement, error, done}
  active: null,       // lid of the file shown in the review step
  accounts: [],
  cats: new Map(),
  pollTimer: null,
  mappingTimer: null,
  results: [],        // commit results for the Done step
};

initNav('import').then(async () => {
  imp.accounts = (await store.accounts().catch(() => [])).filter((a) => a.is_active);
  (await store.categoriesFlat().catch(() => [])).forEach((c) => imp.cats.set(c.id, c));
  store.on('accounts-changed', async () => { imp.accounts = (await store.accounts()).filter((a) => a.is_active); });
  renderUpload();
  document.body.addEventListener('click', onImportClick);
  document.body.addEventListener('change', onImportChange);
  document.body.addEventListener('input', onImportInput);
  const q = qs();
  if (q.statement) await reopenStatement(Number(q.statement));
});

/* ---------- helpers ---------- */
function fileKind(name) { const ext = (name.split('.').pop() || '').toLowerCase(); return ['csv', 'xlsx', 'xls', 'pdf'].includes(ext) ? ext : null; }
function activeFile() { return imp.files.find((f) => f.lid === imp.active) || null; }
function setStep(step) {
  imp.step = step;
  const order = ['upload', 'review', 'done'];
  $$('#stepper .step').forEach((el) => {
    const i = order.indexOf(el.dataset.step), cur = order.indexOf(step);
    el.classList.toggle('active', i === cur);
    el.classList.toggle('done', i < cur);
    if (i < cur) el.querySelector('.step-num').innerHTML = icon('check', 'ico-sm'); else el.querySelector('.step-num').textContent = String(i + 1);
  });
  ['upload', 'review', 'done'].forEach((s) => { $(`#step-${s}`).hidden = s !== step; });
  window.scrollTo({ top: 0 });
}
function accountName(id) { const a = imp.accounts.find((x) => x.id === Number(id)); return a ? a.name : ''; }
function catChip(id) {
  const c = imp.cats.get(Number(id));
  if (!c) return `<span class="catchip catchip--empty"><i class="dot"></i><span class="catchip-label">Uncategorized</span></span>`;
  return `<span class="catchip" title="${esc(c.path)}"><i class="dot" style="--c:var(--${esc(c.color || c.parent_color || 'c1')})"></i><span class="catchip-label">${esc(c.name)}</span></span>`;
}

/* ---------- Step 1: upload ---------- */
function renderUpload() {
  const host = $('#step-upload');
  host.innerHTML = `
    <label class="dropzone" id="dropzone" tabindex="0" role="button" aria-label="Choose statement files">
      <input type="file" id="file-input" multiple accept="${ACCEPT.join(',')}">
      <div class="dropzone-icon">${icon('upload')}</div>
      <div class="dropzone-title">Drop statements here, or <span class="text-accent">browse</span></div>
      <div class="dropzone-sub">CSV, Excel (.xlsx/.xls) or PDF · several files at once · up to 25 MB each</div>
      <div class="dropzone-banks"><span class="chip">Chase</span><span class="chip">Amex</span><span class="chip">Capital One</span><span class="chip">Bank of America</span><span class="chip">Citi</span><span class="chip">Discover</span><span class="chip">Wells Fargo</span><span class="chip">RBC</span><span class="chip">TD</span><span class="chip">BMO</span><span class="chip">Scotiabank</span><span class="chip">+ any CSV</span></div>
    </label>
    <div class="row mt-3" style="gap:10px;flex-wrap:wrap">
      <span class="text-3 fs-base">Import into</span>
      <select class="select input-sm" id="upload-account" style="max-width:260px"><option value="">Choose during review</option>${imp.accounts.map((a) => `<option value="${a.id}">${esc(a.name)}</option>`).join('')}</select>
      <button type="button" class="btn btn-ghost btn-sm" data-act="new-account">${icon('plus', 'ico-sm')}New account</button>
    </div>
    <div class="file-list" id="file-list"></div>`;
  const dz = $('#dropzone');
  ['dragenter', 'dragover'].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add('is-dragover'); }));
  ['dragleave', 'drop'].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.remove('is-dragover'); }));
  dz.addEventListener('drop', (e) => addFiles(e.dataTransfer.files));
  dz.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); $('#file-input').click(); } });
  $('#file-input').addEventListener('change', (e) => { addFiles(e.target.files); e.target.value = ''; });
  renderFileList();
}

function renderFileList() {
  const host = $('#file-list');
  if (!host) return;
  if (!imp.files.length) { host.innerHTML = ''; return; }
  host.innerHTML = imp.files.map((f) => {
    let status = '';
    if (f.error) status = `<span class="badge badge-danger">Failed</span><span class="text-3 fs-sm">${esc(f.error)}</span><button type="button" class="btn btn-ghost btn-xs" data-act="remove-file" data-lid="${f.lid}">Remove</button>`;
    else if (f.statement && f.statement.status === 'previewed') status = `<span class="badge badge-success">Ready</span>`;
    else if (f.statement && f.statement.status === 'error') status = `<span class="badge badge-danger">Failed</span><button type="button" class="btn btn-ghost btn-xs" data-act="remove-file" data-lid="${f.lid}">Remove</button>`;
    else if (f.statementId) status = `<span class="badge badge-info"><span class="spinner"></span>${f.kind === 'pdf' ? 'Reading PDF' : 'Parsing'}</span>`;
    else status = `<span class="badge badge-neutral"><span class="spinner"></span>Uploading</span>`;
    return `<div class="file-row" data-lid="${f.lid}">
      <span class="file-icon ${esc(f.kind || '')}">${icon(KIND_ICON[f.kind] || 'file')}</span>
      <div style="min-width:0"><div class="file-name">${esc(f.name)}</div>
        <div class="file-meta"><span>${fmtBytes(f.size)}</span>${f.kind === 'pdf' && f.statementId && !f.statement ? '<span>· text extraction, then OCR if the PDF is a scan</span>' : ''}${f.statement && f.statement.duplicate_of ? '<span class="text-warning">· same file was uploaded before</span>' : ''}</div>
        ${!f.statementId && !f.error ? `<div class="progress progress-thin"><span style="width:${Math.round((f.progress || 0) * 100)}%"></span></div>` : ''}</div>
      <div class="file-status">${status}</div>
    </div>`;
  }).join('');
  const ready = imp.files.filter((f) => f.statement && f.statement.status === 'previewed');
  const pending = imp.files.filter((f) => !f.error && !(f.statement && ['previewed', 'error'].includes(f.statement.status)));
  if (ready.length && !pending.length && imp.step === 'upload') {
    host.insertAdjacentHTML('beforeend', `<div class="row mt-3" style="justify-content:flex-end"><button type="button" class="btn btn-primary" data-act="go-review">Review ${ready.length === 1 ? 'statement' : `${ready.length} statements`} ${icon('arrow-right', 'ico-sm')}</button></div>`);
  }
}

async function addFiles(fileList) {
  const files = Array.from(fileList || []);
  if (!files.length) return;
  const accountId = $('#upload-account') ? $('#upload-account').value : '';
  for (const file of files) {
    const kind = fileKind(file.name);
    const entry = { lid: uid(), name: file.name, size: file.size, kind, progress: 0, statementId: null, statement: null, error: null };
    imp.files.push(entry);
    if (!kind) { entry.error = 'Unsupported file type'; continue; }
    if (file.size > 25 * 1024 * 1024) { entry.error = 'File is larger than 25 MB'; continue; }
    const fd = new FormData();
    fd.append('file', file);
    if (accountId) fd.append('account_id', accountId);
    apiUpload('/api/statements', fd, { onProgress: (p) => { entry.progress = p; const bar = $(`.file-row[data-lid="${entry.lid}"] .progress > span`); if (bar) bar.style.width = `${Math.round(p * 100)}%`; } })
      .then((res) => { entry.statementId = res.id; entry.statement = { id: res.id, status: res.status, duplicate_of: res.duplicate_of }; renderFileList(); schedulePoll(); })
      .catch((err) => { entry.error = err.message; renderFileList(); });
  }
  renderFileList();
}

/* ---------- polling ---------- */
function schedulePoll() {
  clearTimeout(imp.pollTimer);
  imp.pollTimer = setTimeout(pollStatements, 1500);
}
async function pollStatements() {
  const pending = imp.files.filter((f) => f.statementId && !f.error && !(f.statement && ['previewed', 'error', 'committed'].includes(f.statement.status)));
  if (!pending.length) return;
  await Promise.all(pending.map(async (f) => {
    try {
      const s = await api(`/api/statements/${f.statementId}?limit=2000`);
      if (['previewed', 'error', 'committed'].includes(s.status)) f.statement = s;
      else f.statement = { ...(f.statement || {}), ...s, rows: undefined };
    } catch (err) { f.error = err.message; }
  }));
  if (imp.step === 'upload') renderFileList();
  else if (imp.step === 'review') renderReview();
  if (imp.files.some((f) => f.statementId && !f.error && !(f.statement && ['previewed', 'error', 'committed'].includes(f.statement.status)))) schedulePoll();
}

async function reloadStatement(f, { silent } = {}) {
  try { f.statement = await api(`/api/statements/${f.statementId}?limit=2000`); }
  catch (err) { if (!silent) toast(err.message, { type: 'error' }); }
}

async function reopenStatement(id) {
  const entry = { lid: uid(), name: '…', size: 0, kind: null, progress: 1, statementId: id, statement: null, error: null };
  imp.files.push(entry);
  try {
    const s = await api(`/api/statements/${id}?limit=2000`);
    entry.statement = s; entry.name = s.original_filename; entry.size = s.file_size; entry.kind = s.file_kind;
    if (s.status === 'committed') { toast('This statement was already imported', { type: 'info' }); location.replace(`/transactions.html?statement=${id}&range=all`); return; }
    if (s.status === 'previewed' || s.status === 'error') { imp.active = entry.lid; setStep('review'); renderReview(); }
    else { renderFileList(); schedulePoll(); }
  } catch (err) { entry.error = err.message; renderFileList(); }
}

/* ---------- Step 2: review ---------- */
function reviewable() { return imp.files.filter((f) => f.statement && ['previewed', 'error', 'parsing', 'uploaded', 'committing'].includes(f.statement.status) && !f.done); }

function renderReview() {
  const host = $('#step-review');
  const files = reviewable();
  if (!files.length) { host.innerHTML = `<div class="card">${ui.emptyState({ icon: 'inbox', title: 'Nothing to review', body: 'All uploaded statements were imported.', action: { label: 'Import another', act: 'restart' } })}</div>`; return; }
  if (!imp.active || !files.some((f) => f.lid === imp.active)) imp.active = files[0].lid;
  const f = activeFile();
  const s = f.statement;
  let tabs = '';
  if (files.length > 1) {
    tabs = `<div class="file-tabs" role="tablist">${files.map((x) => `<button type="button" role="tab" class="file-tab ${x.lid === imp.active ? 'active' : ''}" data-act="pick-file" data-lid="${x.lid}" aria-selected="${x.lid === imp.active}">${icon(KIND_ICON[x.kind] || 'file', 'ico-sm')}<span class="truncate" style="max-width:180px">${esc(x.name)}</span>${x.statement && x.statement.status === 'previewed' ? '' : x.statement && x.statement.status === 'error' ? '<span class="badge badge-danger">Failed</span>' : '<span class="spinner"></span>'}</button>`).join('')}</div>`;
  }
  if (!s || ['parsing', 'uploaded', 'committing'].includes(s.status)) {
    host.innerHTML = `${tabs}<div class="card parsing-card"><span class="spinner spinner-lg"></span><div><div class="fw-600">${s && s.status === 'committing' ? 'Importing' : 'Parsing'} ${esc(f.name)}…</div><div class="text-3 fs-base">${f.kind === 'pdf' ? 'Extracting text; scanned pages go through OCR, which can take a minute.' : 'Detecting the bank format and checking for duplicates.'}</div></div></div>`;
    schedulePoll();
    return;
  }
  if (s.status === 'error') {
    host.innerHTML = `${tabs}<div class="card"><div class="card-body">
      ${ui.errorBox(s.error_message || 'This file could not be parsed.')}
      <div class="row mt-3" style="gap:8px;flex-wrap:wrap"><button type="button" class="btn btn-primary" data-act="reparse">${icon('refresh', 'ico-sm')}Try again</button><button type="button" class="btn btn-secondary" data-act="discard">Discard file</button></div>
    </div></div>`;
    return;
  }
  host.innerHTML = `${tabs}${detectionBanner(f)}
    <div class="review-grid">
      <div class="card"><div class="card-body">
        <div class="section-label mb-2">Import into</div>
        <div class="acct-field"><select class="select" id="rv-account" aria-label="Account">${!s.account_id ? '<option value="">Choose an account…</option>' : ''}${imp.accounts.map((a) => `<option value="${a.id}" ${a.id === s.account_id ? 'selected' : ''}>${esc(a.name)} · ${esc(a.currency)}</option>`).join('')}</select>
          <button type="button" class="btn btn-secondary" data-act="new-account" title="Create account">${icon('plus', 'ico-sm')}<span class="label">New</span></button></div>
        <div class="hint mt-1">${s.account_id ? 'Duplicates are checked against this account.' : 'Pick the account this statement belongs to; duplicates are checked per account.'}${s.period_start ? ` Statement period ${fmtDate(s.period_start, { year: true })} – ${fmtDate(s.period_end || s.period_start, { year: true })}.` : ''}</div>
      </div></div>
      <div class="card"><div class="card-body">${signCheck(s)}</div></div>
    </div>
    ${s.file_kind !== 'pdf' ? mappingEditor(s) : pdfOptions(s)}
    ${previewTable(s)}
    ${commitFooter(s)}`;
}

function detectionBanner(f) {
  const s = f.statement;
  const profiles = s.profile_options || [];
  const label = s.bank_profile ? ((profiles.find((p) => p.key === s.bank_profile) || {}).label || s.bank_profile) : 'Generic format';
  const conf = s.profile_confidence != null ? Number(s.profile_confidence) : null;
  const low = !s.bank_profile || (conf != null && conf < 0.6);
  const confText = conf == null ? '' : conf >= 0.85 ? 'high confidence' : conf >= 0.6 ? 'good match' : 'low confidence';
  const kind = String(s.file_kind || '').toUpperCase();
  const warns = (s.warnings || []).filter(Boolean);
  return `<div class="detect-banner ${low || warns.length ? 'is-warning' : ''}">
    <span class="detect-icon">${icon(low ? 'alert-triangle' : 'check-circle')}</span>
    <div class="detect-text"><div class="detect-title">${low && !s.bank_profile ? `No known bank matched — using generic ${kind} detection` : `Detected: ${esc(label)} ${kind}${s.ocr_applied ? ' (OCR)' : ''}`}${confText ? ` <span class="text-3 fw-500">· ${confText}</span>` : ''}</div>
      <div class="detect-sub">${warns.length ? esc(warns[0]) + (warns.length > 1 ? ` (+${warns.length - 1} more)` : '') : low ? 'Check the column mapping and the sign of the amounts below before importing.' : `${fmtNumber(s.summary.rows_total)} rows found · ${esc(f.name)}`}</div></div>
    <div class="detect-actions"><label class="text-3 fs-sm" for="rv-profile">Bank</label><select class="select input-sm" id="rv-profile"><option value="">Generic / auto</option>${profiles.map((p) => `<option value="${esc(p.key)}" ${p.key === s.bank_profile ? 'selected' : ''}>${esc(p.label)}</option>`).join('')}</select></div>
  </div>`;
}

function signCheck(s) {
  const sm = s.summary || { charges: { n: 0, sum: 0 }, payments: { n: 0, sum: 0 } };
  const cur = (imp.accounts.find((a) => a.id === s.account_id) || {}).currency || 'USD';
  const suspicious = sm.charges.n === 0 && sm.payments.n > 2;
  return `<div class="section-label mb-2">Does this look right?</div>
    <div class="signcheck">
      <div class="sc-item"><span class="sc-label">Charges</span><span class="sc-value amt amt--expense">${fmtMoney(sm.charges.sum, cur)}</span><span class="sc-sub">${plural(sm.charges.n, 'charge')}</span></div>
      <div class="sc-item"><span class="sc-label">Payments / income</span><span class="sc-value amt amt--income">${fmtMoney(sm.payments.sum, cur, { sign: 'always' })}</span><span class="sc-sub">${plural(sm.payments.n, 'payment')}</span></div>
      <div class="grow"></div>
      <button type="button" class="btn btn-secondary btn-sm" data-act="flip-signs" title="Swap charges and payments">${icon('arrow-left-right', 'ico-sm')}Flip signs</button>
    </div>
    ${suspicious ? `<div class="notice notice-warning mt-3">${icon('alert-triangle')}<div>Everything parsed as a payment. If these are purchases, use <b>Flip signs</b>.</div></div>` : ''}`;
}

function pdfOptions(s) {
  const m = s.mapping || {};
  return `<div class="mapping mb-4"><details><summary>${icon('chevron-right', 'ico-sm chev')}PDF options<span class="text-3 fw-500 fs-sm">· sign convention, bank profile</span></summary>
    <div class="mapping-body"><div class="mapping-controls">
      <div class="field"><label>Amount sign</label><div class="radio-inline"><label><input type="radio" name="flip" class="check" data-map="flip_sign" value="0" ${!m.flip_sign ? 'checked' : ''}>Keep as parsed</label><label><input type="radio" name="flip" class="check" data-map="flip_sign" value="1" ${m.flip_sign ? 'checked' : ''}>Flip all amounts</label></div></div>
    </div><div class="hint">Text in the PDF is read line by line. If a bank is detected above, its statement layout is used for the sign of each section.</div></div></details></div>`;
}

function mappingEditor(s) {
  const m = s.mapping || {};
  const header = s.header || [];
  const sample = s.sample || [];
  const ncols = Math.max(header.length, ...sample.map((r) => r.length), 0);
  const roleOf = (i) => {
    if (m.date === i) return 'date';
    if (m.posted_date === i) return 'posted_date';
    if ((m.description || []).includes(i)) return 'description';
    if (m.amount === i) return 'amount';
    if (m.debit === i) return 'debit';
    if (m.credit === i) return 'credit';
    if (m.balance === i) return 'balance';
    if (Object.values(m.currency_columns || {}).includes(i)) return 'amount';
    return '';
  };
  const hasAmount = m.amount != null || Object.keys(m.currency_columns || {}).length > 0;
  const low = !s.bank_profile || (s.profile_confidence != null && Number(s.profile_confidence) < 0.6);
  const cols = Array.from({ length: ncols }, (_, i) => i);
  return `<div class="mapping mb-4"><details ${low ? 'open' : ''}><summary>${icon('chevron-right', 'ico-sm chev')}Column mapping<span class="text-3 fw-500 fs-sm">· ${m.date != null ? 'date' : '<span class="text-danger">no date</span>'}, ${(m.description || []).length ? 'description' : '<span class="text-danger">no description</span>'}, ${hasAmount ? 'amount' : m.debit != null || m.credit != null ? 'debit/credit' : '<span class="text-danger">no amount</span>'}</span></summary>
    <div class="mapping-body">
      <div class="mapping-controls">
        <div class="field"><label>Date format</label><select class="select input-sm" data-map="date_format">${DATE_FORMATS.map(([v, l]) => `<option value="${v}" ${(m.date_format || '') === v ? 'selected' : ''}>${l}</option>`).join('')}</select></div>
        <div class="field"><label>Amount sign</label><div class="radio-inline"><label><input type="radio" name="flip" class="check" data-map="flip_sign" value="0" ${!m.flip_sign ? 'checked' : ''}>Negative = charge</label><label><input type="radio" name="flip" class="check" data-map="flip_sign" value="1" ${m.flip_sign ? 'checked' : ''}>Positive = charge</label></div></div>
        <div class="field"><label>Skip rows above header</label><input type="number" min="0" max="50" class="input input-sm" style="width:90px" data-map="skip_rows" value="${Number(m.skip_rows || 0)}"></div>
        <label class="switch" style="margin-bottom:6px"><input type="checkbox" data-map="has_header" ${m.has_header !== false ? 'checked' : ''}><span class="switch-track"></span>First row is a header</label>
      </div>
      <div class="mapping-table"><table><thead><tr>${cols.map((i) => `<th class="${roleOf(i) ? 'is-mapped' : ''}"><select class="select input-sm" data-col="${i}" aria-label="Role for column ${i + 1}">${ROLE_OPTIONS.map(([v, l]) => `<option value="${v}" ${roleOf(i) === v ? 'selected' : ''}>${l}</option>`).join('')}</select><div class="colname" title="${esc(header[i] || '')}">${esc(header[i] || `Column ${i + 1}`)}</div></th>`).join('')}</tr></thead>
        <tbody>${sample.slice(0, 5).map((r) => `<tr>${cols.map((i) => `<td title="${esc(r[i] || '')}">${esc(r[i] || '')}</td>`).join('')}</tr>`).join('') || '<tr><td class="text-3">No sample rows</td></tr>'}</tbody></table></div>
      <div class="hint mt-2">Changes re-parse the file immediately. Mark two columns as Description to join them; use Debit and Credit when amounts are in separate columns.</div>
    </div></details></div>`;
}

function previewTable(s) {
  const rows = s.rows || [];
  const sm = s.summary || {};
  const cur = (imp.accounts.find((a) => a.id === s.account_id) || {}).currency || 'USD';
  const dupCount = sm.dupes_existing || 0;
  const dupSkipped = rows.filter((r) => r.duplicate_of && !r.include).length;
  const body = rows.map((r) => {
    const excluded = !r.include || !r.is_valid;
    const badges = [];
    if (r.duplicate_of) badges.push(`<span class="badge badge-warning" title="${esc(`Already imported: ${r.duplicate_txn ? `${fmtDate(r.duplicate_txn.txn_date, { year: true })} · ${r.duplicate_txn.description} · ${fmtMoney(r.duplicate_txn.amount, cur)}` : '#' + r.duplicate_of}`)}">Duplicate</span>`);
    if (r.in_file_duplicate) badges.push('<span class="badge badge-neutral" title="The same line appears more than once in this file">In file</span>');
    (r.problems || []).forEach((p) => badges.push(`<span class="badge badge-danger" title="${esc(p)}">${esc(p.length > 24 ? p.slice(0, 22) + '…' : p)}</span>`));
    return `<tr data-row="${r.id}" class="${excluded ? 'is-excluded' : ''}">
      <td class="col-check"><input type="checkbox" class="check" data-row-include="${r.id}" ${r.include && r.is_valid ? 'checked' : ''} ${!r.is_valid ? 'disabled' : ''} aria-label="Include row"></td>
      <td class="num nowrap">${r.txn_date ? fmtDate(r.txn_date, { year: true }) : '<span class="text-danger">—</span>'}</td>
      <td class="col-desc"><div class="merchant"><span class="merchant-name">${esc(r.merchant_name || r.description || '')}</span><span class="merchant-raw">${esc(r.description || '')}</span></div>${badges.length ? `<div class="badges">${badges.join('')}</div>` : ''}</td>
      <td>${r.is_valid ? catChip(r.category_id) : ''}</td>
      <td class="right"><span class="amt ${r.amount > 0 ? 'amt--income' : 'amt--expense'}">${r.amount != null ? fmtMoney(r.amount, cur, { sign: 'always' }) : '<span class="text-danger">—</span>'}</span></td>
    </tr>`;
  }).join('');
  return `<div class="preview-head">
      <div class="tbl-summary" style="margin:0"><span><b>${fmtNumber(sm.included || 0)}</b> to import</span>${dupCount ? `<span><b>${fmtNumber(dupCount)}</b> duplicate${dupCount === 1 ? '' : 's'}</span>` : ''}${sm.invalid ? `<span class="text-danger"><b>${fmtNumber(sm.invalid)}</b> unreadable</span>` : ''}${sm.uncategorized ? `<span><b>${fmtNumber(sm.uncategorized)}</b> without a category yet</span>` : ''}</div>
      <div class="row" style="gap:12px">${dupCount ? `<label class="switch switch-sm"><input type="checkbox" id="skip-dupes" ${dupSkipped === dupCount ? 'checked' : ''}><span class="switch-track"></span>Skip ${fmtNumber(dupCount)} duplicate${dupCount === 1 ? '' : 's'}</label>` : ''}<button type="button" class="btn btn-ghost btn-xs" data-act="rows-all" data-include="1">Include all</button><button type="button" class="btn btn-ghost btn-xs" data-act="rows-all" data-include="0">Exclude all</button></div>
    </div>
    <div class="tbl-wrap"><table class="tbl tbl-preview"><thead><tr><th class="col-check"></th><th>Date</th><th>Description</th><th>Category</th><th class="right">Amount</th></tr></thead>
      <tbody>${body || `<tr><td colspan="5">${ui.emptyState({ icon: 'file-text', title: 'No transactions found', body: s.file_kind === 'pdf' ? 'The PDF text did not contain recognizable transaction lines. If it is a scan, the OCR quality may be too low; try a clearer export or a CSV.' : 'Check the column mapping above.' })}</td></tr>`}</tbody></table>
      ${rows.length >= 2000 ? '<div class="preview-more text-3 fs-sm">Showing the first 2,000 rows.</div>' : ''}</div>`;
}

function commitFooter(s) {
  const sm = s.summary || {};
  const n = sm.included || 0;
  const acct = accountName(s.account_id);
  return `<div class="commit-foot">
    <div class="summary">${n ? `Import <b>${fmtNumber(n)}</b> transaction${n === 1 ? '' : 's'}${acct ? ` into <b>${esc(acct)}</b>` : ' — choose an account first'}` : 'Nothing selected to import'}</div>
    <div class="row" style="gap:8px"><button type="button" class="btn btn-ghost" data-act="discard">Discard</button><button type="button" class="btn btn-primary" data-act="commit" ${n && s.account_id ? '' : 'disabled'}>${icon('check', 'ico-sm')}Import</button></div>
  </div>`;
}

/* ---------- mapping edits ---------- */
function readMappingFromDom(s) {
  const m = { ...(s.mapping || {}) };
  const roles = { date: null, posted_date: null, description: [], amount: null, debit: null, credit: null, balance: null };
  $$('.mapping-table select[data-col]').forEach((sel) => {
    const i = Number(sel.dataset.col), v = sel.value;
    if (!v) return;
    if (v === 'description') roles.description.push(i); else roles[v] = i;
  });
  if ($('.mapping-table')) Object.assign(m, roles);
  const df = $('[data-map="date_format"]'); if (df) m.date_format = df.value || null;
  const flip = $('[data-map="flip_sign"]:checked'); if (flip) m.flip_sign = flip.value === '1';
  const skip = $('[data-map="skip_rows"]'); if (skip) m.skip_rows = Math.max(0, Number(skip.value) || 0);
  const hh = $('[data-map="has_header"]'); if (hh) m.has_header = hh.checked;
  return m;
}
async function applyMapping(f, mapping, { bank_profile } = {}) {
  const host = $('#step-review');
  host.classList.add('is-busy');
  try {
    const body = { mapping };
    if (bank_profile !== undefined) body.bank_profile = bank_profile || null;
    f.statement = await api(`/api/statements/${f.statementId}/mapping`, { method: 'PUT', body });
    if (!f.statement.rows) await reloadStatement(f, { silent: true });
    renderReview();
  } catch (err) { toast(err.message, { type: 'error' }); }
  finally { host.classList.remove('is-busy'); }
}
function scheduleMapping(f) {
  clearTimeout(imp.mappingTimer);
  imp.mappingTimer = setTimeout(() => applyMapping(f, readMappingFromDom(f.statement)), 350);
}

/* ---------- events ---------- */
function onImportChange(e) {
  const f = activeFile();
  const t = e.target;
  if (t.id === 'rv-account' && f) return changeAccount(f, t.value);
  if (t.id === 'rv-profile' && f) return applyMapping(f, readMappingFromDom(f.statement), { bank_profile: t.value });
  if (t.matches('[data-map], .mapping-table select[data-col]') && f) return scheduleMapping(f);
  if (t.matches('[data-row-include]') && f) return setRows(f, { row_ids: [Number(t.dataset.rowInclude)], include: t.checked });
  if (t.id === 'skip-dupes' && f) return setRows(f, { all: true, only: 'dupes', include: !t.checked });
}
function onImportInput(e) {
  const f = activeFile();
  if (e.target.matches('[data-map="skip_rows"]') && f) scheduleMapping(f);
}
async function onImportClick(e) {
  const btn = e.target.closest('[data-act]');
  if (!btn) return;
  const f = activeFile();
  switch (btn.dataset.act) {
    case 'new-account': return openNewAccount();
    case 'remove-file': { imp.files = imp.files.filter((x) => x.lid !== btn.dataset.lid); renderFileList(); return; }
    case 'go-review': { imp.active = null; setStep('review'); renderReview(); return; }
    case 'pick-file': { imp.active = btn.dataset.lid; renderReview(); return; }
    case 'flip-signs': if (f) { const m = readMappingFromDom(f.statement); m.flip_sign = !((f.statement.mapping || {}).flip_sign); return applyMapping(f, m); } return;
    case 'rows-all': if (f) return setRows(f, { all: true, include: btn.dataset.include === '1' }); return;
    case 'reparse': if (f) return reparse(f); return;
    case 'discard': if (f) return discard(f); return;
    case 'commit': if (f) return commit(f); return;
    case 'restart': { location.href = '/import.html'; return; }
    default:
  }
}

async function changeAccount(f, value) {
  const id = value ? Number(value) : null;
  try {
    f.statement = await api(`/api/statements/${f.statementId}/account`, { method: 'PUT', body: { account_id: id } });
    if (!f.statement.rows) await reloadStatement(f, { silent: true });
    renderReview();
  } catch (err) { toast(err.message, { type: 'error' }); }
}
async function setRows(f, body) {
  try {
    const r = await api(`/api/statements/${f.statementId}/rows`, { method: 'PUT', body });
    if (body.all) { await reloadStatement(f); renderReview(); }
    else {
      const row = (f.statement.rows || []).find((x) => x.id === body.row_ids[0]);
      if (row) row.include = body.include;
      f.statement.summary = r.summary || f.statement.summary;
      const tr = $(`tr[data-row="${body.row_ids[0]}"]`); if (tr) tr.classList.toggle('is-excluded', !body.include);
      const foot = $('.commit-foot'); if (foot) foot.outerHTML = commitFooter(f.statement);
      const head = $('.preview-head'); if (head) { const tmp = document.createElement('div'); tmp.innerHTML = previewTable(f.statement); head.outerHTML = tmp.querySelector('.preview-head').outerHTML; }
    }
  } catch (err) { toast(err.message, { type: 'error' }); }
}
async function reparse(f) {
  try {
    await api(`/api/statements/${f.statementId}/reparse`, { method: 'POST', body: {} });
    f.statement = { ...f.statement, status: 'parsing', rows: undefined };
    renderReview();
  } catch (err) { toast(err.message, { type: 'error' }); }
}
async function discard(f) {
  const ok = await ui.confirm({ title: 'Discard this file?', body: `“${esc(f.name)}” will be removed without importing anything.`, confirmText: 'Discard', danger: true });
  if (!ok) return;
  try { await api(`/api/statements/${f.statementId}`, { method: 'DELETE' }); } catch (err) { toast(err.message, { type: 'error' }); return; }
  imp.files = imp.files.filter((x) => x.lid !== f.lid);
  imp.active = null;
  if (reviewable().length) renderReview(); else if (imp.results.length) { setStep('done'); renderDone(); } else { setStep('upload'); renderUpload(); }
}
async function commit(f) {
  const s = f.statement;
  if (!s.account_id) { toast('Choose an account first', { type: 'error' }); return; }
  const btn = $('[data-act="commit"]'); if (btn) btn.classList.add('is-loading');
  try {
    const r = await api(`/api/statements/${f.statementId}/commit`, { method: 'POST', body: { account_id: s.account_id } });
    f.done = true;
    imp.results.push({ name: f.name, account: accountName(s.account_id), statementId: f.statementId, ...r });
    window.dispatchEvent(new Event('ispend:transactions-changed'));
    store.invalidate('accounts');
    toast(`Imported ${fmtNumber(r.imported)} transactions`, { type: 'success' });
    imp.active = null;
    if (reviewable().length) renderReview(); else { setStep('done'); renderDone(); }
  } catch (err) { toast(err.message, { type: 'error' }); if (btn) btn.classList.remove('is-loading'); }
}

/* ---------- Step 3: done ---------- */
function renderDone() {
  const host = $('#step-done');
  const tot = imp.results.reduce((a, r) => ({ imported: a.imported + (r.imported || 0), dup: a.dup + (r.skipped_duplicates || 0), unc: a.unc + ((r.categorized || {}).uncategorized || 0) + ((r.categorized || {}).suggested || 0), rule: a.rule + ((r.categorized || {}).rule || 0) + ((r.categorized || {}).merchant || 0) }), { imported: 0, dup: 0, unc: 0, rule: 0 });
  host.innerHTML = `<div class="card done-card">
      <div class="done-icon">${icon('check')}</div>
      <h2 style="font-size:var(--fs-xl)">Import complete</h2>
      <div class="text-3 mt-1">${imp.results.map((r) => `${esc(r.name)} → ${esc(r.account)}`).join(' · ')}</div>
      <div class="done-stats">
        <div><div class="n">${fmtNumber(tot.imported)}</div><div class="l">transactions imported</div></div>
        <div><div class="n">${fmtNumber(tot.rule)}</div><div class="l">categorized automatically</div></div>
        <div><div class="n ${tot.unc ? 'text-warning' : ''}">${fmtNumber(tot.unc)}</div><div class="l">need a category</div></div>
        ${tot.dup ? `<div><div class="n text-3">${fmtNumber(tot.dup)}</div><div class="l">duplicates skipped</div></div>` : ''}
      </div>
      ${imp.results.some((r) => r.ai_queued) ? `<div class="notice mt-2 mb-4" style="text-align:left">${icon('sparkles')}<div>AI suggestions are being prepared for the remaining charges. They appear in Review shortly.</div></div>` : ''}
      <div class="done-actions">
        ${tot.unc ? `<a class="btn btn-primary" href="/review.html">${icon('inbox', 'ico-sm')}Review ${fmtNumber(tot.unc)} charge${tot.unc === 1 ? '' : 's'}</a>` : `<a class="btn btn-primary" href="/transactions.html?range=all">${icon('list', 'ico-sm')}View transactions</a>`}
        <a class="btn btn-secondary" href="/transactions.html?statement=${imp.results[imp.results.length - 1].statementId}&range=all">See imported rows</a>
        <button type="button" class="btn btn-ghost" data-act="restart">Import another</button>
      </div></div>`;
}

/* ---------- new account modal ---------- */
async function openNewAccount() {
  const inst = await store.get('institutions', '/api/accounts/institutions', { ttl: 600000 }).catch(() => []);
  const f = activeFile();
  const detected = f && f.statement ? f.statement.bank_profile : '';
  const m = ui.modal({
    title: 'New account',
    html: `<form id="na-form">
      <div class="field"><label for="na-name">Account name</label><input id="na-name" class="input" placeholder="e.g. RBC Chequing" required autofocus></div>
      <div class="field-row">
        <div class="field"><label for="na-type">Type</label><select id="na-type" class="select">${ACCOUNT_TYPES.map(([v, l]) => `<option value="${v}">${l}</option>`).join('')}</select></div>
        <div class="field"><label for="na-cur">Currency</label><select id="na-cur" class="select"><option value="USD">USD</option><option value="CAD">CAD</option></select></div>
      </div>
      <div class="field"><label for="na-inst">Institution</label><select id="na-inst" class="select"><option value="">— Not set —</option>${inst.map((i) => `<option value="${esc(i.key)}" ${i.key === detected ? 'selected' : ''}>${esc(i.label)}</option>`).join('')}</select></div>
      <button type="submit" hidden></button></form>`,
    actions: [{ label: 'Cancel' }, { label: 'Create account', primary: true, onClick: async () => {
      const body = { name: $('#na-name', m.el).value.trim(), account_type: $('#na-type', m.el).value, currency: $('#na-cur', m.el).value, institution: $('#na-inst', m.el).value || null };
      if (!body.name) throw new Error('Account name is required');
      const a = await api('/api/accounts', { method: 'POST', body });
      store.invalidate('accounts');
      imp.accounts = (await store.accounts({ force: true })).filter((x) => x.is_active);
      toast('Account created', { type: 'success' });
      if (imp.step === 'review' && f) await changeAccount(f, a.id);
      else { renderUpload(); $('#upload-account').value = String(a.id); }
    } }],
  });
  const instSel = $('#na-inst', m.el);
  const ca = ['rbc', 'td', 'bmo', 'scotiabank'];
  const syncCur = () => { if (ca.includes(instSel.value)) $('#na-cur', m.el).value = 'CAD'; };
  syncCur();
  instSel.addEventListener('change', syncCur);
  $('#na-form', m.el).addEventListener('submit', (e) => { e.preventDefault(); m.el.querySelector('.modal-foot .btn-primary').click(); });
}
