/* Admin › Settings › Data retention: how long the activity log, admin actions and deleted accounts
   are kept, and the record of every account that was erased. */

const ART = { bar: null, settings: null };

AdminSettings.register('retention', {
  label: 'Data retention', icon: 'archive',
  sub: 'How long the activity log, admin actions and deleted accounts are kept',
  load: loadAdminRetention,
});

async function loadAdminRetention(host) {
  ART.host = host;
  if (!ART.bar) {
    ART.bar = adminDirtyBar('settings/retention', { save: saveRetention, discard: () => loadAdminRetention(ART.host) });
    host.addEventListener('input', (e) => { if (e.target.closest('#rt-form')) ART.bar.set(true); });
  }
  if (ART.bar.dirty) return;
  host.innerHTML = ui.skeletonList(4);
  let erasures;
  try {
    [ART.settings, erasures] = await Promise.all([api('/api/admin/settings'), api('/api/admin/erasures')]);
  } catch (err) {
    host.innerHTML = ui.errorBox(err.message, { retry: 'reload-admin-retention' });
    return;
  }
  const h = ART.settings;
  const days = (id, value, min, label) => `<div class="row gap-2 adm-ctl adm-ctl--sm"><input id="${id}" class="input input-sm num-input" type="number"
    inputmode="numeric" min="${min}" max="3650" aria-label="${esc(label)}" value="${value}"><span class="text-3">days</span></div>`;
  ART.bar.set(false);
  host.innerHTML = `
    <section class="settings-section card card-pad" id="rt-form">
      <div class="setting-row">
        <div><div class="title">Activity log</div>
          <div class="desc">${fmtNumber(h.audit_rows || 0)} entries${h.oldest_audit_at ? `, oldest ${fmtRelative(h.oldest_audit_at)}` : ''}. What customers do in their own accounts; older entries are pruned as new ones are written.</div></div>
        ${days('hk-audit', h.audit_retention_days, 7, 'How long to keep the activity log, in days')}
      </div>
      <div class="setting-row">
        <div><div class="title">Admin actions</div>
          <div class="desc">Locks, deletions, billing changes, exports. Kept longer so “who changed my account” can always be answered.</div></div>
        ${days('hk-admin-audit', h.admin_audit_retention_days, 7, 'How long to keep admin actions, in days')}
      </div>
      <div class="setting-row">
        <div><div class="title">Trash</div>
          <div class="desc">Customers moved to the trash are listed for deleting after this long. Nothing is ever deleted automatically — you always confirm.</div></div>
        ${days('hk-trash', h.deleted_user_retention_days, 0, 'Trash retention in days')}
      </div>
    </section>
    <section class="settings-section card">
      <header class="card-head"><h2>Erased accounts</h2></header>
      <div class="card-body"><p class="hint mb-3">Customers deleted at their own request or by you. Only these anonymous records remain.</p>
      ${erasures.length
        ? `<div class="tbl-wrap"><table class="tbl tbl--list"><thead><tr><th>When</th><th>Account</th><th>By</th><th>Reason</th><th class="right">Transactions</th></tr></thead>
          <tbody>${erasures.map((x) => `<tr><td>${esc(fmtDateTime(x.created_at))}</td><td class="text-3">#${x.erased_user_id}</td>
            <td>${x.requested_by === 'self' ? 'Themselves' : esc(x.admin || 'an administrator')}${x.stripe_canceled ? ' <span class="badge badge-neutral">subscription cancelled</span>' : ''}</td>
            <td class="text-3">${esc(x.reason || '—')}</td><td class="right num">${fmtNumber((x.counts || {}).transactions || 0)}</td></tr>`).join('')}</tbody></table></div>`
        : '<div class="hint">Nobody has been erased.</div>'}</div>
    </section>`;
}

async function saveRetention() {
  await api('/api/admin/settings', { method: 'PUT', body: {
    audit_retention_days: Number($('#hk-audit').value),
    admin_audit_retention_days: Number($('#hk-admin-audit').value),
    deleted_user_retention_days: Number($('#hk-trash').value),
  } });
  toast('Retention saved', { type: 'success' });
  ART.bar.set(false);
  return loadAdminRetention(ART.host);
}

document.addEventListener('click', (e) => {
  if (e.target.closest('[data-act="reload-admin-retention"]')) loadAdminRetention(ART.host);
});
