/* Admin › System: is the machinery working? One status chip per moving part — imports, email, Stripe,
   backups, storage, server errors — and the details behind each, with the few actions that fix
   things from here (stop a stuck import, compare with Stripe, make a backup). */

const ASY = { data: null };
const SYS_PARTS = [['imports', 'Imports', 'file-text'], ['email', 'Email', 'mail'], ['stripe', 'Stripe', 'credit-card'],
  ['backups', 'Backups', 'database'], ['storage', 'Storage', 'server'], ['errors', 'Server errors', 'alert-triangle']];
const SYS_TONE = { ok: 'success', warn: 'warning', error: 'danger' };
const SYS_WORD = { ok: 'Healthy', warn: 'Needs a look', error: 'Needs attention' };

AdminPanels.register('system', {
  label: 'System', icon: 'server', group: 'operations',
  sub: 'Imports, email, Stripe, backups, storage and server errors',
  actions: '<button type="button" class="btn btn-secondary" data-act="system-refresh" aria-label="Measure again"></button>',
  markup: `
    <div class="adm-health" id="sys-strip"></div>
    <div class="grid grid-2 mt-4">
      <section class="card" id="sys-imports"></section>
      <section class="card" id="sys-errors"></section>
      <section class="card" id="sys-email"></section>
      <section class="card" id="sys-stripe"></section>
      <section class="card" id="sys-backups"></section>
      <section class="card" id="sys-storage"></section>
    </div>`,
  load: loadAdminSystem,
});

async function loadAdminSystem(host, ctx) {
  $('[data-act="system-refresh"]').innerHTML = `${icon('refresh')}<span class="label">Measure again</span>`;
  if (!ASY.data) $('#sys-strip').innerHTML = SYS_PARTS.map(() => `<div class="adm-health-chip is-loading">${ui.skeleton(120, 14)}</div>`).join('');
  let d;
  try {
    d = await api(`/api/admin/system/health${ctx.force ? '?refresh=1' : ''}`);
  } catch (err) {
    $('#sys-strip').innerHTML = ui.errorBox(err.message, { retry: 'system-refresh' });
    return;
  }
  if (!ctx.isCurrent()) return;
  ASY.data = d;
  $('#sys-strip').innerHTML = SYS_PARTS.map(([key, label, ic]) => `
    <a class="adm-health-chip is-${esc(d[key].status)}" href="#sys-${key}" data-scroll="sys-${key}">
      <span class="adm-health-ico">${icon(ic, 'ico-sm')}</span>
      <span class="min-w-0"><b>${esc(label)}</b><span class="sub">${esc(chipSummary(key, d[key]))}</span></span>
      <i class="dot" style="--c:var(--${key === 'stripe' && !d.stripe.enabled ? 'text-4' : SYS_TONE[d[key].status]})" aria-label="${esc(key === 'stripe' && !d.stripe.enabled ? 'Off' : SYS_WORD[d[key].status])}"></i></a>`).join('');
  renderSysImports(d.imports);
  renderSysErrors(d.errors);
  renderSysEmail(d.email);
  renderSysStripe(d.stripe);
  renderSysBackups(d.backups);
  renderSysStorage(d.storage);
}

function chipSummary(key, p) {
  switch (key) {
    case 'imports': return p.stuck.length ? `${plural(p.stuck.length, 'import')} stuck` : `${fmtNumber(p.failed_24h)} failed today`;
    case 'email': return p.configured ? `${fmtNumber(p.sent_24h)} sent, ${fmtNumber(p.failed_24h)} failed today` : 'Not set up';
    case 'stripe': return !p.enabled ? 'Not set up' : (p.failed_7d ? `${plural(p.failed_7d, 'failed webhook')} this week` : (p.last_event_at ? `Last event ${fmtRelative(p.last_event_at)}` : 'No events yet'));
    case 'backups': return p.last_success_at ? `Last ${fmtRelative(p.last_success_at)}` : 'Never backed up';
    case 'storage': return p.volume_free != null ? `${fmtBytes(p.volume_free)} free` : fmtBytes(p.db_bytes + p.source_bytes);
    default: return p.count_24h ? `${fmtNumber(p.count_24h)} today` : 'None today';
  }
}

function sysHead(title, status, off = false) {
  const badge = off ? '<span class="badge badge-neutral">Off</span>'
    : `<span class="badge badge-${SYS_TONE[status]}">${esc(SYS_WORD[status])}</span>`;
  return `<header class="card-head"><h2>${esc(title)}</h2>${badge}</header>`;
}

function renderSysImports(p) {
  $('#sys-imports').innerHTML = `${sysHead('Imports', p.status)}<div class="card-body">
    ${kpis([['Reading now', fmtNumber(p.running)], ['Failed today', fmtNumber(p.failed_24h), p.failed_24h ? 'text-danger' : ''],
      ['Failed this week', fmtNumber(p.failed_7d)], ['Uploaded this week', fmtNumber(p.uploaded_7d)]])}
    ${p.stuck.length ? `<div class="section-label mt-6 mb-2">Stuck for more than 15 minutes</div>
      <ul class="p360-list">${p.stuck.map((s) => `<li><span class="grow">#${s.id} · ${esc(s.bank_profile)} ${esc(s.file_kind || '')} · ${esc(s.status)}
        <span class="text-3">for ${fmtNumber(s.age_min)} min</span></span>
        <button type="button" class="btn btn-ghost btn-xs" data-act="open-person" data-id="${s.user_id}">Person</button>
        <button type="button" class="btn btn-secondary btn-xs" data-act="stop-import" data-id="${s.id}">Stop</button></li>`).join('')}</ul>
      <div class="hint mt-2">Stopping marks it failed and tells the person to upload the file again.</div>` : ''}
    <a class="btn btn-ghost btn-sm mt-4" href="#imports">Import details by bank ${icon('arrow-right', 'ico-sm')}</a></div>`;
}

function renderSysErrors(p) {
  $('#sys-errors').innerHTML = `${sysHead('Server errors', p.status)}<div class="card-body">
    ${kpis([['Today', fmtNumber(p.count_24h), p.count_24h ? 'text-danger' : ''], ['This week', fmtNumber(p.count_7d)]])}
    ${p.groups.length ? `<ul class="adm-errors mt-4">${p.groups.map((g) => `<li>
      <button type="button" class="adm-error-row" data-act="error-detail" data-fp="${esc(g.fingerprint)}" aria-expanded="false">
        <span class="grow min-w-0"><b>${esc(g.error_type)}</b> <span class="text-3">in ${esc(g.location || 'unknown')}</span>
          <span class="sub truncate">${esc(g.message || '')}</span></span>
        <span class="badge badge-neutral">${fmtNumber(g.n)}×</span><span class="text-3">${esc(fmtRelative(g.last_at))}</span></button>
      <div class="adm-error-detail" hidden></div></li>`).join('')}</ul>`
    : `<div class="hint mt-4">${icon('check-circle', 'ico-sm')} Nothing went wrong this week.</div>`}</div>`;
}

function renderSysEmail(p) {
  $('#sys-email').innerHTML = `${sysHead('Email', p.status)}<div class="card-body">
    ${p.configured ? '' : `<div class="notice notice-warning mb-4">${icon('alert-triangle')}<div class="grow">Email is not set up, so confirmation links, password resets and billing notices are not being sent.</div><a class="btn btn-secondary btn-sm" href="#signups">Set up</a></div>`}
    ${kpis([['Sent today', fmtNumber(p.sent_24h)], ['Failed today', fmtNumber(p.failed_24h), p.failed_24h ? 'text-danger' : ''],
      ['Not sent (no email set up)', fmtNumber(p.skipped_24h)], ['Stuck in the queue', fmtNumber(p.stuck), p.stuck ? 'text-danger' : '']])}
    ${p.last_failure ? `<div class="hint mt-3">Last failure ${esc(fmtRelative(p.last_failure.at))} (${esc(p.last_failure.template.replace(/_/g, ' '))}): ${esc(p.last_failure.error || 'no reason given')}</div>` : ''}
    ${p.by_template.length ? `<div class="section-label mt-6 mb-2">This week by message</div>
      <div class="tbl-wrap"><table class="tbl tbl--list"><thead><tr><th>Message</th><th class="right">Sent</th><th class="right">Failed</th></tr></thead>
      <tbody>${p.by_template.map((t) => `<tr><td>${esc(t.template.replace(/_/g, ' '))}</td><td class="right num">${fmtNumber(t.sent)}</td>
        <td class="right num ${t.failed ? 'text-danger' : ''}">${fmtNumber(t.failed)}</td></tr>`).join('')}</tbody></table></div>` : ''}</div>`;
}

function renderSysStripe(p) {
  const r = p.reconcile_last;
  $('#sys-stripe').innerHTML = `${sysHead('Stripe', p.status, !p.enabled)}<div class="card-body">
    ${!p.enabled ? '<div class="hint">Stripe is not set up, so nobody is billed. Add the keys under Billing.</div>' : `
      ${kpis([['Last event', p.last_event_at ? esc(fmtRelative(p.last_event_at)) : 'Never'], ['Events today', fmtNumber(p.events_24h)],
        ['Failed this week', fmtNumber(p.failed_7d), p.failed_7d ? 'text-danger' : ''], ['Slowest 5%', p.p95_ms == null ? '—' : `${fmtNumber(p.p95_ms)}<span class="u"> ms</span>`]])}
      ${p.recent_failed.length ? `<ul class="p360-list mt-4">${p.recent_failed.map((e) => `<li><span class="grow"><b>${esc(e.type)}</b>
        <span class="sub">${esc(e.error || '')}</span></span><span class="text-3">${esc(fmtRelative(e.received_at))}</span></li>`).join('')}</ul>` : ''}
      <div class="hint mt-3">${r ? `Last compared with Stripe ${esc(fmtRelative(r.started_at))}${r.error ? ` — failed: ${esc(r.error)}` : `: ${plural(r.fixed.length, 'record')} corrected`}.` : 'Never compared with Stripe.'}
        <a href="#billing">Revenue records</a></div>`}</div>`;
}

function renderSysBackups(p) {
  $('#sys-backups').innerHTML = `${sysHead('Backups', p.status)}<div class="card-body">
    ${kpis([['Last backup', p.last_success_at ? esc(fmtRelative(p.last_success_at)) : 'Never'], ['Size', p.size_bytes ? fmtBytes(p.size_bytes) : '—']])}
    ${p.last_error ? `<div class="hint mt-3 text-danger">The last failed backup (${esc(fmtRelative(p.last_error_at))}): ${esc(p.last_error)}</div>` : ''}
    <a class="btn btn-secondary btn-sm mt-4" href="#backup">${icon('database', 'ico-sm')}Backups</a></div>`;
}

function renderSysStorage(p) {
  const max = Math.max(...p.tables.map((t) => t.bytes), 1);
  $('#sys-storage').innerHTML = `${sysHead('Storage', p.status)}<div class="card-body">
    ${kpis([['Statement files', p.disk_scan_ok ? fmtBytes(p.disk_bytes) : 'Unreadable'], ['Originals', fmtBytes(p.source_bytes)],
      ['Database', fmtBytes(p.db_bytes)], ['Free on the volume', p.volume_free != null ? fmtBytes(p.volume_free) : '—']])}
    <div class="section-label mt-6 mb-2">Largest tables</div>
    <div class="adm-bars">${p.tables.slice(0, 8).map((t) => bar(t.name, t.bytes, max, `${fmtBytes(t.bytes)} · ~${fmtNumber(t.rows_est)} rows`)).join('')}</div>
    <div class="hint mt-3">Statement files are measured by scanning the volume (at most hourly), so they include scanned-PDF output; originals come from the database.</div></div>`;
}

async function toggleErrorDetail(btn) {
  const box = btn.nextElementSibling;
  const open = btn.getAttribute('aria-expanded') === 'true';
  btn.setAttribute('aria-expanded', String(!open));
  box.hidden = open;
  if (open || box.dataset.loaded) return;
  box.innerHTML = ui.skeletonList(2);
  try {
    const d = await api(`/api/admin/system/errors/${encodeURIComponent(btn.dataset.fp)}`);
    box.dataset.loaded = '1';
    box.innerHTML = `<div class="hint mb-2">${esc(d.latest.source)} · ${esc(fmtDateTime(d.latest.created_at))}${d.latest.status ? ` · HTTP ${d.latest.status}` : ''}
      · ${d.days.map((x) => `${fmtDate(x.day)}: ${x.n}`).join(', ')}</div><pre class="adm-trace">${esc(d.latest.traceback || d.latest.message || '')}</pre>`;
  } catch (err) {
    box.innerHTML = ui.errorBox(err.message);
  }
}

document.addEventListener('click', async (e) => {
  const el = e.target.closest('[data-act], [data-scroll]');
  if (!el || AdminPanels.current() !== 'system') return;
  if (el.dataset.scroll) {
    e.preventDefault();
    document.getElementById(el.dataset.scroll).scrollIntoView({ behavior: 'smooth', block: 'start' });
    return;
  }
  switch (el.dataset.act) {
    case 'system-refresh': ui.busy(el, () => AdminPanels.refresh()); break;
    case 'error-detail': toggleErrorDetail(el); break;
    case 'stop-import':
      if (!(await ui.confirm({ title: 'Stop this import?', body: 'It is marked as failed and the person is asked to upload the file again. Use this only when it has clearly stopped moving.', confirmText: 'Stop import', danger: true }))) return;
      await ui.busy(el, async () => {
        await api(`/api/admin/system/imports/${el.dataset.id}/fail`, { method: 'POST' });
        toast('Import stopped', { type: 'success' });
        AdminPanels.refresh();
      });
      break;
    default: break;
  }
});
