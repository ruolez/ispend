/* Admin › Retention: how long activity, admin actions and deleted accounts are kept. */

AdminPanels.register('retention', {
  label: 'Retention', icon: 'archive', group: 'settings',
  sub: 'How long the activity log, admin actions and deleted accounts are kept',
  markup: '<section class="settings-section"><div id="ov-housekeeping"></div></section>',
  load: loadAdminRetention,
});

async function loadAdminRetention() {
  $('#ov-housekeeping').innerHTML = ui.skeletonList(3);
  try {
    renderHousekeeping(await api('/api/admin/settings'));
  } catch (err) {
    $('#ov-housekeeping').innerHTML = ui.errorBox(err.message);
  }
}

function renderHousekeeping(h) {
  $('#ov-housekeeping').innerHTML = `
    <div class="setting-row">
      <div><div class="title">Activity log retention</div>
        <div class="desc">${fmtNumber(h.audit_rows || 0)} entries${h.oldest_audit_at ? `, oldest ${fmtRelative(h.oldest_audit_at)}` : ''}. What people do in their own accounts; older entries are pruned as new ones are written.</div></div>
      <div class="row gap-2 adm-ctl adm-ctl--sm"><input id="hk-audit" class="input input-sm num-input" type="number" inputmode="numeric" min="7" max="3650" aria-label="How long to keep the activity log, in days" value="${h.audit_retention_days}"><span class="text-3">days</span></div>
    </div>
    <div class="setting-row">
      <div><div class="title">Admin action retention</div>
        <div class="desc">What administrators did — locks, deletions, billing changes, exports. Kept longer so “who changed my account” can always be answered.</div></div>
      <div class="row gap-2 adm-ctl adm-ctl--sm"><input id="hk-admin-audit" class="input input-sm num-input" type="number" inputmode="numeric" min="7" max="3650" aria-label="How long to keep admin actions, in days" value="${h.admin_audit_retention_days}"><span class="text-3">days</span></div>
    </div>
    <div class="setting-row">
      <div><div class="title">Trash retention</div>
        <div class="desc">Deleted users are listed for purging after this long. Nothing is ever purged automatically — you always confirm.</div></div>
      <div class="row gap-2 adm-ctl adm-ctl--sm"><input id="hk-trash" class="input input-sm num-input" type="number" inputmode="numeric" min="0" max="3650" aria-label="Trash retention in days" value="${h.deleted_user_retention_days}"><span class="text-3">days</span></div>
    </div>
    <div class="row-between mt-4"><span class="hint" id="hk-state">Saved</span>
      <button type="button" class="btn btn-secondary btn-sm" data-act="save-housekeeping" disabled>Save</button></div>`;
  const mark = () => {
    const changed = Number($('#hk-audit').value) !== h.audit_retention_days
      || Number($('#hk-admin-audit').value) !== h.admin_audit_retention_days
      || Number($('#hk-trash').value) !== h.deleted_user_retention_days;
    $('[data-act="save-housekeeping"]').disabled = !changed;
    $('#hk-state').textContent = changed ? 'Unsaved changes' : 'Saved';
  };
  ['#hk-audit', '#hk-admin-audit', '#hk-trash'].forEach((sel) => $(sel).addEventListener('input', mark));
}

document.addEventListener('click', (e) => {
  const el = e.target.closest('[data-act="save-housekeeping"]');
  if (!el) return;
  ui.busy(el, async () => {
    await api('/api/admin/settings', { method: 'PUT', body: {
      audit_retention_days: Number($('#hk-audit').value),
      admin_audit_retention_days: Number($('#hk-admin-audit').value),
      deleted_user_retention_days: Number($('#hk-trash').value),
    } });
    toast('Retention saved', { type: 'success' });
    loadAdminRetention();
  });
});
