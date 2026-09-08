/* Backup & restore, mounted into the Admin page through window.AdminPanels. */

const BK = { jobs: [], poll: null, upload: null, restoreJob: null, restoreToken: null };
const POLL_MS = 1500;
const RESTORE_TOKEN_KEY = 'ispend.restoreJob';

AdminPanels.register('backup', { label: 'Backup', icon: 'database', load: loadBackup });

async function loadBackup(host) {
  BK.host = host;
  render();
  await refreshJobs();
  // A restore signs this admin out partway through; the token lets the page keep polling.
  try {
    const saved = JSON.parse(sessionStorage.getItem(RESTORE_TOKEN_KEY) || 'null');
    if (saved) { BK.restoreJob = saved.id; BK.restoreToken = saved.token; pollRestore(); }
  } catch { /* nothing saved */ }
}

function render() {
  BK.host.innerHTML = `
    <div class="settings-section">
      <h2>Back up this server</h2>
      <div class="sub">One archive with every user, all their data and every uploaded statement.
        Use it to move iSpend to another server.</div>
      <div class="notice notice-warning mb-4">${icon('alert-triangle')}<div>
        <b>This archive contains everything.</b> Every user's transactions, their password hashes and
        your OpenRouter API key in plain text. Anyone who obtains the file can restore it onto their
        own server &mdash; store it like a password database. It does <b>not</b> contain this server's
        secret key or database password.</div></div>
      <div class="row gap-2 mb-4">
        <button type="button" class="btn btn-primary" data-act="start-backup">${icon('database')}<span class="label">Create a backup</span></button>
        <button type="button" class="btn btn-secondary btn-icon" data-act="reload-backups" aria-label="Refresh">${icon('refresh')}</button>
      </div>
      <div id="bk-jobs"></div>
    </div>

    <div class="settings-section">
      <h2>Restore onto this server</h2>
      <div class="sub">Replaces <b>everything</b> on this server with the contents of an archive.
        A backup of the current data is taken automatically first.</div>
      <div id="bk-restore"></div>
    </div>`;
  renderRestorePanel();
}

/* ---------- backups ---------- */

async function refreshJobs() {
  const host = $('#bk-jobs');
  try {
    BK.jobs = await api('/api/admin/backup');
  } catch (err) {
    host.innerHTML = ui.errorBox(err.message, { retry: 'reload-backups' });
    return;
  }
  const running = BK.jobs.find((j) => j.status === 'queued' || j.status === 'running');
  renderJobs();
  clearInterval(BK.poll);
  BK.poll = running ? setInterval(refreshJobs, POLL_MS) : null;
}

function renderJobs() {
  const host = $('#bk-jobs');
  if (!BK.jobs.length) {
    host.innerHTML = ui.emptyState({ icon: 'database', title: 'No backups yet',
      body: 'Create one before migrating this server or making a large change.' });
    return;
  }
  host.innerHTML = `<div class="tbl-wrap"><table class="tbl tbl--cards"><thead><tr>
      <th>Archive</th><th>Kind</th><th>Status</th><th class="right">Size</th><th>Created</th>
      <th class="col-actions"><span class="sr-only">Actions</span></th></tr></thead><tbody>
    ${BK.jobs.map(jobRow).join('')}</tbody></table></div>`;
}

const KIND_LABEL = { backup: 'Manual', pre_restore: 'Before a restore', restore: 'Restore' };

function jobRow(j) {
  const busy = j.status === 'queued' || j.status === 'running';
  const status = busy
    ? `<div class="bk-progress"><div class="progress"><span style="width:${Math.round((j.progress || 0) * 100)}%"></span></div>
       <span class="sub">${esc(j.message || j.phase || 'Working')}</span></div>`
    : j.status === 'error'
      ? `<span class="text-danger" data-tip="${esc(j.error_message || '')}">${icon('alert-triangle', 'ico-sm')} Failed</span>`
      : `<span class="user-status"><i class="dot" style="--c:var(--success)"></i>Done</span>`;
  const rows = j.stats && j.stats.tables ? Object.values(j.stats.tables).reduce((a, b) => a + b, 0) : null;
  return `<tr data-id="${j.id}">
    <td><div class="fw-500">${esc(j.filename || '—')}</div>
      ${rows != null ? `<div class="sub">${fmtNumber(rows)} rows · ${fmtNumber((j.stats.files || {}).count || 0)} files</div>` : ''}
      ${(j.warnings || []).length ? `<div class="sub text-warning">${esc(plural(j.warnings.length, 'warning'))}</div>` : ''}</td>
    <td data-label="Kind"><span class="badge badge-neutral">${esc(KIND_LABEL[j.kind] || j.kind)}</span></td>
    <td data-label="Status">${status}</td>
    <td class="right num" data-label="Size">${j.size_bytes ? fmtBytes(j.size_bytes) : '—'}</td>
    <td class="text-3" data-label="Created">${esc(fmtRelative(j.created_at))}</td>
    <td class="col-actions"><div class="row-actions">
      ${j.downloadable ? `<a class="btn btn-secondary btn-xs" href="/api/admin/backup/${j.id}/download" download>${icon('download', 'ico-sm')}<span class="label">Download</span></a>` : ''}
      ${busy ? '' : `<button type="button" class="btn btn-icon btn-ghost btn-xs" data-act="delete-backup" data-id="${j.id}" aria-label="Delete backup">${icon('trash')}</button>`}
    </div></td></tr>`;
}

/* ---------- restore ---------- */

function renderRestorePanel() {
  const host = $('#bk-restore');
  if (!host) return;
  if (BK.restoreJob) return;
  if (!BK.upload) {
    host.innerHTML = `
      <div class="bk-drop" id="bk-drop" tabindex="0" role="button" aria-label="Choose a backup archive">
        ${icon('upload', 'ico-lg')}
        <div class="fw-500">Choose a backup archive</div>
        <div class="sub">Nothing is changed until you confirm on the next screen.</div>
        <input type="file" id="bk-file" accept=".zip,application/zip" hidden>
      </div>`;
    return;
  }
  const u = BK.upload;
  const m = u.manifest || {};
  const rows = Object.entries(u.will_replace || {});
  host.innerHTML = `
    <div class="card mb-4"><div class="card-body">
      <div class="row-between mb-3"><div>
        <div class="fw-500">Archive from ${esc(fmtDateTime(m.created_at))}</div>
        <div class="sub">${esc((m.source || {}).hostname || 'unknown host')} ·
          ${esc(fmtBytes(u.size_bytes))} · ${fmtNumber((m.files || {}).count || 0)} files ·
          schema ${esc((m.app || {}).migration_head || '?')}</div>
      </div><button type="button" class="btn btn-ghost btn-sm" data-act="cancel-restore">Choose a different file</button></div>
      ${u.problems.length
        ? `<div class="notice notice-warning">${icon('alert-triangle')}<div>${u.problems.map(esc).join('<br>')}</div></div>`
        : ''}
      ${(u.warnings || []).length
        ? `<div class="notice mt-3">${icon('info')}<div>${u.warnings.map((w) => esc(w.detail || w.kind)).join('<br>')}</div></div>`
        : ''}
    </div></div>
    <div class="tbl-wrap mb-4"><table class="tbl"><thead><tr><th>Table</th>
      <th class="right">On this server now</th><th class="right">In the archive</th></tr></thead><tbody>
      ${rows.map(([t, c]) => `<tr><td>${esc(t)}</td>
        <td class="right num ${c.live ? 'text-danger' : 'text-3'}">${fmtNumber(c.live)}</td>
        <td class="right num">${fmtNumber(c.archive)}</td></tr>`).join('')}
    </tbody></table></div>
    <label class="check mb-4"><input type="checkbox" id="bk-delete-extra">
      <span>Also delete uploaded files this archive does not reference</span></label>
    <div class="row gap-2">
      <button type="button" class="btn btn-danger-solid" data-act="confirm-restore" ${u.compatible ? '' : 'disabled'}>
        ${icon('rotate-ccw')}<span class="label">Restore this archive</span></button>
    </div>`;
}

async function onFile(file) {
  if (!file) return;
  const host = $('#bk-restore');
  host.innerHTML = `<div class="bk-drop"><div class="fw-500">Uploading ${esc(file.name)}…</div>
    <div class="progress mt-3"><span id="bk-up" style="width:0%"></span></div></div>`;
  try {
    BK.upload = await apiUploadRaw('/api/admin/backup/upload', file, {
      onProgress: (p) => { const el = $('#bk-up'); if (el) el.style.width = `${Math.round(p * 100)}%`; },
    });
  } catch (err) {
    BK.upload = null;
    host.innerHTML = ui.errorBox(err.message);
    return;
  }
  renderRestorePanel();
}

function confirmRestore() {
  const deleteExtra = !!($('#bk-delete-extra') || {}).checked;
  const m = ui.modal({
    title: 'Replace everything on this server?',
    html: `<p>Every user, transaction, statement and setting on this server is deleted and replaced
        with the contents of the archive. A backup of the current data is taken first.</p>
      <p class="mt-3">You will be signed out when it finishes, because the user accounts are replaced.</p>
      <div class="field mt-4"><label for="bk-confirm">Type <b>RESTORE</b> to confirm</label>
        <input id="bk-confirm" class="input" autocomplete="off" spellcheck="false" autofocus></div>`,
    actions: [{ label: 'Cancel' }, { label: 'Restore', primary: true, danger: true, onClick: async () => {
      const typed = m.el.querySelector('#bk-confirm').value.trim();
      if (typed !== 'RESTORE') { ui.fieldError(m.el.querySelector('#bk-confirm'), 'Type RESTORE to confirm'); return false; }
      const r = await api('/api/admin/backup/restore', { method: 'POST', body: {
        upload_id: BK.upload.upload_id, confirm: 'RESTORE', delete_extra_files: deleteExtra } });
      BK.restoreJob = r.id;
      BK.restoreToken = r.token;
      try { sessionStorage.setItem(RESTORE_TOKEN_KEY, JSON.stringify({ id: r.id, token: r.token })); } catch { /* ignore */ }
      BK.upload = null;
      pollRestore();
      return undefined;
    } }],
  });
  const input = m.el.querySelector('#bk-confirm');
  const btn = m.el.querySelector('.modal-foot .btn-primary');
  btn.disabled = true;
  input.addEventListener('input', () => { btn.disabled = input.value.trim() !== 'RESTORE'; });
}

async function pollRestore() {
  const host = $('#bk-restore');
  let job;
  try {
    job = await api(`/api/admin/backup/restore/${BK.restoreJob}?token=${encodeURIComponent(BK.restoreToken)}`);
  } catch (err) {
    host.innerHTML = ui.errorBox(`Lost track of the restore: ${err.message}`);
    return;
  }
  if (job.status === 'queued' || job.status === 'running') {
    host.innerHTML = `<div class="card"><div class="card-body">
      <div class="fw-500 mb-2">${esc(job.message || job.phase || 'Restoring')}…</div>
      <div class="progress"><span style="width:${Math.round((job.progress || 0) * 100)}%"></span></div>
      <div class="sub mt-3">Everyone is locked out of iSpend until this finishes. Leave this page open.</div>
    </div></div>`;
    setTimeout(pollRestore, POLL_MS);
    return;
  }
  try { sessionStorage.removeItem(RESTORE_TOKEN_KEY); } catch { /* ignore */ }
  if (job.status === 'error') {
    host.innerHTML = `${ui.errorBox(job.error_message || 'The restore failed.')}
      <p class="sub mt-3">Nothing was changed: the database load runs in a single transaction, so a
      failure leaves the previous data in place.</p>
      <button type="button" class="btn btn-secondary mt-3" data-act="cancel-restore">Start over</button>`;
    BK.restoreJob = null;
    return;
  }
  const warnings = job.warnings || [];
  host.innerHTML = `<div class="card"><div class="card-body">
    <div class="row gap-2 mb-3">${icon('check-circle')}<b>Restore complete.</b></div>
    ${warnings.length ? `<div class="notice notice-warning mb-3">${icon('alert-triangle')}<div>
      ${warnings.map((w) => esc(w.detail || `${w.kind}${w.path ? `: ${w.path}` : ''}`)).join('<br>')}</div></div>` : ''}
    <p>You have been signed out because the user accounts were replaced. Sign in with a username and
      password from the restored data.</p>
    <a class="btn btn-primary mt-3" href="/login.html">Sign in</a>
  </div></div>`;
  BK.restoreJob = null;
}

/* ---------- actions ---------- */

document.addEventListener('click', async (e) => {
  const el = e.target.closest('[data-act]');
  if (!el) return;
  const id = Number(el.dataset.id);
  switch (el.dataset.act) {
    case 'start-backup':
      await ui.busy(el, async () => { await api('/api/admin/backup', { method: 'POST', body: {} }); });
      return refreshJobs();
    case 'reload-backups': return refreshJobs();
    case 'delete-backup': {
      const job = BK.jobs.find((j) => j.id === id);
      if (!(await ui.confirm({ title: 'Delete this backup?', body: `${job.filename} is removed from the server. Any copy you already downloaded is unaffected.`, confirmText: 'Delete', danger: true }))) return undefined;
      await api(`/api/admin/backup/${id}`, { method: 'DELETE' });
      toast('Backup deleted');
      return refreshJobs();
    }
    case 'cancel-restore':
      BK.upload = null;
      BK.restoreJob = null;
      return renderRestorePanel();
    case 'confirm-restore': return confirmRestore();
    default: return undefined;
  }
});

document.addEventListener('click', (e) => {
  if (e.target.closest('#bk-drop')) $('#bk-file').click();
});
document.addEventListener('change', (e) => {
  if (e.target.id === 'bk-file') onFile(e.target.files[0]);
});
document.addEventListener('dragover', (e) => { if (e.target.closest('#bk-drop')) e.preventDefault(); });
document.addEventListener('drop', (e) => {
  const zone = e.target.closest('#bk-drop');
  if (!zone) return;
  e.preventDefault();
  onFile(e.dataTransfer.files[0]);
});
