/* Admin › Activity: everything that has happened here, newest first. User-written details are
   hidden by the server (admin_privacy); admin actions are shown whole. auditRow() is shared with the
   person view. */

const AD = { audit: { items: [], cursor: null, actions: null }, wired: false };

/* The filters live in the URL (?action=&q=&user=&who=&admin=1), so a link from anywhere — a
   customer's page, an alert on Home — opens the log already filtered, in this tab or a new one. */
function auditFilters() {
  const q = qs();
  return { action: q.action || '', q: q.q || '', userId: Number(q.user) || null, userName: q.who || '',
    adminOnly: q.admin === '1' };
}
function setAuditFilter(patch) {
  setQs(patch, { merge: true });
  return loadActivity();
}

AdminPanels.register('activity', {
  label: 'Activity log', short: 'Activity', icon: 'clock', group: 'operations', params: ['admin', 'user', 'who', 'action', 'q'],
  sub: 'Everything that has happened here, newest first',
  actions: '<a class="btn btn-secondary" id="a-export" href="/api/admin/audit?format=csv" download data-admin-download></a>',
  markup: `
    <div class="tbl-toolbar" id="audit-toolbar">
      <div class="input-group adm-search"><span class="ico-wrap"></span>
        <input id="a-search" class="input input-sm" type="search" placeholder="Search actions…" aria-label="Search activity"></div>
      <button type="button" class="btn btn-secondary btn-sm" id="a-action-btn" data-act="audit-action-filter" aria-haspopup="menu"></button>
      <button type="button" class="btn btn-secondary btn-sm" id="a-admin-btn" data-act="toggle-admin-only" aria-pressed="false"></button>
      <button type="button" class="btn btn-secondary btn-sm" id="a-user-chip" data-act="clear-audit-user" hidden></button>
    </div>
    <div class="tbl-summary" id="audit-summary"></div>
    <div id="audit-list"></div>
    <div class="row center mt-4"><button type="button" class="btn btn-secondary btn-sm" data-act="audit-more" hidden>Load more</button></div>`,
  load: () => { wireActivity(); return loadActivity(); },
});

function wireActivity() {
  if (AD.wired) return;
  AD.wired = true;
  $('#a-export').innerHTML = `${icon('download')}<span class="label">Export CSV</span>`;
  $('#audit-toolbar .ico-wrap').innerHTML = icon('search');
  $('#a-search').addEventListener('input', debounce(() => setAuditFilter({ q: $('#a-search').value.trim() || undefined }), 250));
}

function paintAuditButtons() {
  const f = auditFilters();
  if (document.activeElement !== $('#a-search')) $('#a-search').value = f.q;
  $('#a-action-btn').innerHTML = `${icon('filter')}<span class="label">${esc(f.action ? auditTitle(f.action) : 'All actions')}</span>`;
  $('#a-action-btn').classList.toggle('is-on', !!f.action);
  const adminBtn = $('#a-admin-btn');
  adminBtn.innerHTML = `${icon('shield')}<span class="label">Admin actions only</span>`;
  adminBtn.setAttribute('aria-pressed', String(f.adminOnly));
  adminBtn.classList.toggle('is-on', f.adminOnly);
  // Reached from a customer's page; without a visible chip the filter is invisible and stuck.
  const chip = $('#a-user-chip');
  chip.hidden = !f.userId;
  if (f.userId) chip.innerHTML = `${icon('user')}<span class="label">${esc(f.userName || `#${f.userId}`)}</span>${icon('x', 'ico-sm')}`;
}

/* ---------- Activity ---------- */

/* The log stores machine actions (`statement.upload`). Admins read them all day, so they are
   rendered as a sentence with the family's icon; the raw action stays as the filter value and in
   the tooltip so nothing becomes unsearchable. */
const AUDIT_SUBJECT = {
  auth: 'lock', user: 'user', admin: 'shield', account: 'landmark', statement: 'file-text',
  transaction: 'list', transactions: 'list', category: 'tag', rule: 'sliders', budget: 'target',
  tag: 'tags', merchant: 'shopping-bag', merchants: 'shopping-bag', review: 'inbox-check',
  insights: 'lightbulb', anomaly: 'alert-triangle', recurring: 'repeat', settings: 'settings',
  billing: 'credit-card', backup: 'database', email: 'mail',
};
const AUDIT_VERB = {
  create: 'created', update: 'updated', delete: 'deleted', purge: 'deleted permanently',
  lock: 'locked', unlock: 'unlocked', restore: 'restored', deactivate: 'deactivated',
  upload: 'uploaded', download: 'downloaded', commit: 'committed', apply: 'applied',
  merge: 'merged', reorder: 'reordered', split: 'split', unsplit: 'unsplit', pair: 'paired',
  unpair: 'unpaired', auto_pair: 'auto-paired', bulk: 'bulk edited', resolve: 'resolved',
  dismiss: 'dismissed', undismiss: 'brought back', forget: 'forgotten', generate: 'generated',
  copy: 'copied', learn: 'learned', renormalize: 'renormalized', flip_signs: 'signs flipped',
  reset_defaults: 'reset to defaults', run_all: 'run over everything', password_change: 'password changed',
  blocked: 'blocked', failed: 'failed', sync: 'synced', test: 'tested',
};
const AUDIT_EXACT = {
  'auth.login': 'Signed in', 'auth.logout': 'Signed out', 'auth.login.blocked': 'Sign-in blocked',
  'auth.forgot': 'Password reset requested', 'auth.reset': 'Password reset',
  'auth.admin_new_network': 'Admin signed in from a new network',
  'auth.step_up.locked_out': 'Signed out after wrong passwords',
  'admin.audit.export': 'Activity exported',
};
const AUDIT_DANGER = new Set(['delete', 'purge', 'blocked', 'deactivate', 'failed', 'lock']);

function cap(s) { return s ? s.charAt(0).toUpperCase() + s.slice(1) : s; }
function words(s) { return String(s).replace(/[._]+/g, ' ').trim(); }

function auditTitle(action) {
  if (AUDIT_EXACT[action]) return AUDIT_EXACT[action];
  const parts = String(action).split('.');
  const verb = AUDIT_VERB[parts[parts.length - 1]];
  if (!verb || parts.length < 2) return cap(words(action));
  return `${cap(words(parts.slice(0, -1).join(' ')))} ${verb}`;
}

function auditVerb(action) { return String(action).split('.').pop(); }
function auditIcon(action) { return AUDIT_SUBJECT[String(action).split('.')[0]] || 'circle'; }

function auditDetail(detail, hidden = 0) {
  const entries = Object.entries(detail || {}).filter(([, v]) => v !== null && v !== '');
  const privacy = hidden ? `<span class="adm-kv" data-tip="Names, file names and amounts people entered stay private">${icon('eye-off', 'ico-sm')}<b>${hidden} private</b></span>` : '';
  if (!entries.length) return privacy ? `<div class="adm-audit-detail">${privacy}</div>` : '';
  const val = (v) => (typeof v === 'object' ? JSON.stringify(v) : String(v));
  const shown = entries.map(([k, v], i) =>
    `<span class="adm-kv ${i >= 5 ? 'is-extra' : ''}" title="${esc(`${words(k)}: ${val(v)}`)}"><i>${esc(words(k))}</i><b>${esc(val(v))}</b></span>`).join('');
  const rest = entries.length - 5;
  return `<div class="adm-audit-detail">${shown}${rest > 0
    ? `<button type="button" class="adm-kv adm-kv-more" data-act="audit-expand" aria-expanded="false">+${rest} more</button>` : ''}${privacy}</div>`;
}

function auditRow(a, { compact = false } = {}) {
  const who = a.username || (a.detail && a.detail.username) || (a.user_id ? `#${a.user_id}` : 'system');
  const danger = AUDIT_DANGER.has(auditVerb(a.action));
  return `<div class="adm-audit-item">
    <span class="adm-audit-ico ${danger ? 'is-danger' : ''}" aria-hidden="true">${icon(auditIcon(a.action), 'ico-sm')}</span>
    <div class="min-w-0">
      <div class="adm-audit-action"><span data-tip="${esc(a.action)}">${esc(auditTitle(a.action))}</span>${a.target_user_id && a.target_user_id !== a.user_id
        ? ` <span class="text-3">·</span> <button type="button" class="row-link" data-act="audit-for-user" data-id="${a.target_user_id}">${esc(a.target_username || `#${a.target_user_id}`)}</button>` : ''}</div>
      ${(a.detail && Object.keys(a.detail).length) || a.hidden_fields ? auditDetail(a.detail, a.hidden_fields) : ''}
    </div>
    ${compact ? '' : `<div class="adm-audit-who">${a.user_id
      ? `<button type="button" class="row-link" data-act="audit-for-user" data-id="${a.user_id}"><span class="avatar avatar-xs" aria-hidden="true">${esc(initials(who))}</span><span class="truncate">${esc(who)}</span></button>`
      : `<span class="text-4">${esc(who)}</span>`}</div>`}
    <time class="text-3" datetime="${esc(a.created_at || '')}" data-tip="${esc(a.created_at ? fmtDateTime(a.created_at) : '')}">${a.created_at ? esc(fmtRelative(a.created_at)) : ''}</time>
  </div>`;
}

function dayLabel(iso) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return 'Earlier';
  const today = new Date();
  const days = Math.round((new Date(today.getFullYear(), today.getMonth(), today.getDate())
    - new Date(d.getFullYear(), d.getMonth(), d.getDate())) / 86400000);
  if (days === 0) return 'Today';
  if (days === 1) return 'Yesterday';
  return fmtDateLong(iso);
}

function auditList(items) {
  let day = null;
  return items.map((a) => {
    const label = a.created_at ? dayLabel(a.created_at) : 'Unknown date';
    const head = label === day ? '' : `<div class="adm-day">${esc(label)}</div>`;
    day = label;
    return head + auditRow(a);
  }).join('');
}

async function loadActivity({ more = false } = {}) {
  const host = $('#audit-list');
  if (!more) {
    host.innerHTML = ui.skeletonList(8);
    AD.audit.items = [];
    AD.audit.cursor = null;
  }
  const f = auditFilters();
  const filters = { action: f.action || undefined, q: f.q || undefined, user_id: f.userId || undefined,
    admin: f.adminOnly ? 1 : undefined };
  paintAuditButtons();
  let data;
  try {
    data = await api(`/api/admin/audit${toQuery({ ...filters, cursor: more ? AD.audit.cursor : undefined, limit: 50 })}`);
  } catch (err) {
    host.innerHTML = ui.errorBox(err.message, { retry: 'reload-audit' });
    $('#audit-summary').innerHTML = '';
    return;
  }
  AD.audit.items = more ? AD.audit.items.concat(data.items) : data.items;
  AD.audit.cursor = data.next_cursor;
  $('[data-act="audit-more"]').hidden = !data.next_cursor;
  $('#a-export').href = `/api/admin/audit${toQuery({ ...filters, format: 'csv' })}`;
  const n = AD.audit.items.length;
  $('#audit-summary').innerHTML = n
    ? `<span><b>${fmtNumber(n)}</b> ${n === 1 ? 'entry' : 'entries'}${data.next_cursor ? ' so far' : ''}</span>`
    : '';
  host.innerHTML = n
    ? `<div class="adm-audit">${auditList(AD.audit.items)}</div>`
    : ui.emptyState({ icon: 'clock', title: 'No activity recorded',
      body: f.action || f.q || f.userId || f.adminOnly ? 'Nothing matches these filters.' : 'Actions show up here as people use iSpend.' });
}

async function openActionFilter(anchor) {
  if (!AD.audit.actions) {
    try { AD.audit.actions = await api('/api/admin/audit/actions'); } catch { AD.audit.actions = []; }
  }
  const cur = auditFilters().action;
  ui.menu(anchor, [{ label: 'All actions', checked: !cur, onClick: () => setAuditFilter({ action: undefined }) },
    { divider: true },
    ...AD.audit.actions.slice(0, 60).map((a) => ({
      label: `${auditTitle(a.action)} (${fmtNumber(a.n)})`, icon: auditIcon(a.action), checked: cur === a.action,
      onClick: () => setAuditFilter({ action: a.action }),
    }))]);
}

/* ---------- Actions ---------- */

/* Exports and backup archives need a recent password; a plain link cannot answer the prompt. */
document.addEventListener('click', (e) => {
  const link = e.target.closest('a[data-admin-download]');
  if (!link || e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey) return;
  e.preventDefault();
  adminDownload(link.href).catch((err) => { if (!err.cancelled) toast(err.message, { type: 'error' }); });
});

document.addEventListener('click', (e) => {
  const el = e.target.closest('[data-act]');
  if (!el) return undefined;
  switch (el.dataset.act) {
    case 'reload-audit': return loadActivity();
    case 'audit-for-user': {
      e.preventDefault();
      const params = { user: el.dataset.id, who: el.dataset.name || undefined };
      if (AdminPanels.current() === 'activity') return setAuditFilter(params);
      location.href = adminHref('activity', { params });
      return undefined;
    }
    case 'audit-more': return ui.busy(el, () => loadActivity({ more: true }));
    case 'audit-expand': {
      const box = el.closest('.adm-audit-detail');
      const open = !box.classList.contains('is-open');
      box.classList.toggle('is-open', open);
      el.setAttribute('aria-expanded', String(open));
      el.textContent = open ? 'Show less' : `+${box.querySelectorAll('.is-extra').length} more`;
      return undefined;
    }
    case 'clear-audit-user': return setAuditFilter({ user: undefined, who: undefined });
    case 'toggle-admin-only': return setAuditFilter({ admin: auditFilters().adminOnly ? undefined : '1' });
    case 'audit-action-filter': return openActionFilter(el);
    default: return undefined;
  }
});
