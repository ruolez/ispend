/* Admin › one person: access and money, how they got started, what they have (counts only), their
   story, sign-ins, emails, notes and tags, and every action an operator can take on them.
   Opens as a drawer over any section (full screen on phones) from GET /api/admin/users/<id>. */

const P360 = { id: null, data: null, drawer: null, tab: 'summary' };
const P360_TABS = [['summary', 'Summary'], ['billing', 'Billing'], ['history', 'History'], ['notes', 'Notes']];

async function openUserDrawer(id) {
  P360.id = id;
  P360.drawer = ui.drawer({ title: 'Person', html: ui.skeletonList(8), width: 760,
    onClose: () => { P360.drawer = null; P360.id = null; } });
  return refreshPerson();
}
window.openUserDrawer = openUserDrawer;

async function refreshPerson() {
  const d = P360.drawer;
  if (!d) return;
  try {
    P360.data = await api(`/api/admin/users/${P360.id}`);
  } catch (err) {
    d.setBody(ui.errorBox(err.message));
    return;
  }
  renderPerson();
}
window.addEventListener('ispend:admin-user-changed', () => { if (P360.drawer) refreshPerson(); });

function personLabel(u) { return u.email || u.username; }

function renderPerson() {
  const { user: u, billing: b } = P360.data;
  const d = P360.drawer;
  d.setTitle(personLabel(u));
  const state = b && b.comped_until ? 'comped' : ((P360.data.subscription || {}).ent_state || (b && b.state));
  d.setBody(`
    <div class="p360-head">
      <span class="avatar avatar-lg" aria-hidden="true">${esc(initials(personLabel(u)))}</span>
      <div class="min-w-0 grow">
        <div class="p360-name truncate">${esc(personLabel(u))}</div>
        <div class="p360-badges">
          ${state ? `<span class="badge ${ACCESS_BADGE[state] || 'badge-neutral'}">${esc(ACCESS_LABEL[state] || state)}</span>` : ''}
          ${u.role === 'admin' ? '<span class="badge badge-info">Admin</span>' : ''}
          ${u.status !== 'active' ? `<span class="user-status"><i class="dot" style="--c:var(--${STATUS_COLOR[u.status]})"></i>${esc(STATUS_LABEL[u.status])}</span>` : ''}
          ${u.email ? (u.email_confirmed ? `<span class="text-3">${icon('check', 'ico-sm')} email confirmed</span>` : '<span class="text-warning">email not confirmed</span>') : '<span class="text-4">no email address</span>'}
          ${u.lock_reason ? `<span class="text-3">· ${esc(u.lock_reason)}</span>` : ''}
        </div>
        <div class="p360-tags" id="p360-tags">${tagChips(P360.data.tags)}<button type="button" class="btn btn-ghost btn-xs" data-p360="tags">${icon('tag', 'ico-sm')}Tags</button></div>
      </div>
      <button type="button" class="btn btn-secondary btn-sm" data-p360="menu" aria-haspopup="menu">${icon('more-horizontal')}<span class="label">Actions</span></button>
    </div>
    ${personKpis()}
    <div class="tabs p360-tabs" role="tablist" aria-label="About this person">${P360_TABS.map(([k, l]) =>
      `<button type="button" role="tab" class="tab ${P360.tab === k ? 'active' : ''}" aria-selected="${P360.tab === k}" data-p360-tab="${k}">${esc(l)}${k === 'notes' && P360.data.notes.length ? ` <span class="pill">${P360.data.notes.length}</span>` : ''}</button>`).join('')}</div>
    <div id="p360-tab">${tabHtml(P360.tab)}</div>`);
}

function tagChips(tags) {
  return (tags || []).map((t) => `<span class="adm-tag" style="--c:var(--${esc(t.color)})">${esc(t.name)}</span>`).join('');
}

function personKpis() {
  const { user: u, subscription: s } = P360.data;
  const cur = (s.currency || 'usd').toUpperCase();
  const act = P360.data.activation;
  return kpis([
    ['MRR', s.mrr_cents ? esc(fmtMoney(s.mrr_cents / 100, cur)) : '—'],
    ['Paid so far', s.lifetime_cents ? esc(fmtMoney(s.lifetime_cents / 100, cur)) : '—'],
    ['Last seen', u.last_seen_at ? esc(fmtRelative(u.last_seen_at)) : 'Never'],
    ['Activated', act.activated ? 'Yes' : (act.first_commit_at ? 'Later' : 'Not yet')],
  ], 'p360-kpis');
}

function tabHtml(tab) {
  if (tab === 'billing') return billingTab();
  if (tab === 'history') return historyTab();
  if (tab === 'notes') return notesTab();
  return summaryTab();
}

/* ---------- summary ---------- */

function summaryTab() {
  const { user: u, counts: c, data_range: r, storage: st, activation: a, attribution: at, notes } = P360.data;
  const step = (done, label, when) => `<li class="${done ? 'is-done' : ''}">${icon(done ? 'check-circle' : 'circle', 'ico-sm')}
    <span class="grow">${esc(label)}</span><span class="text-3">${when ? esc(fmtDateLong(when)) : ''}</span></li>`;
  const pinned = notes.filter((n) => n.pinned);
  const tiles = [['Accounts', c.accounts], ['Transactions', c.transactions], ['Statements', c.statements],
    ['Rules', c.rules], ['Categories', c.categories], ['Budgets', c.budgets], ['Tags', c.tags], ['Merchants', c.merchants]];
  return `
    ${pinned.map((n) => `<div class="notice notice-info mb-3">${icon('star')}<div class="grow p360-note-body">${esc(n.body)}</div></div>`).join('')}
    <div class="section-label mb-2">Getting started</div>
    <ol class="p360-steps">
      ${step(true, 'Signed up', u.created_at)}
      ${u.email ? step(!!a.confirmed_at || u.email_confirmed, 'Confirmed their email', a.confirmed_at) : ''}
      ${step(!!a.first_upload_at, 'Uploaded a statement', a.first_upload_at)}
      ${step(!!a.first_commit_at, a.activated ? 'Imported it within the first week' : 'Imported a statement', a.first_commit_at)}
    </ol>
    ${at ? `<div class="hint mt-2">Came from <b>${esc(at.channel)}</b>${at.utm_campaign ? ` · campaign ${esc(at.utm_campaign)}` : ''}${at.referrer_host ? ` · via ${esc(at.referrer_host)}` : ''}.</div>` : ''}
    <div class="section-label mt-6 mb-2">What they have</div>
    <div class="adm-detail-grid">${tiles.map(([l, v]) => `<div class="adm-tile"><div class="l">${esc(l)}</div><div class="v">${fmtNumber(v || 0)}</div></div>`).join('')}</div>
    <div class="setting-row"><div><div class="title">Statements cover</div><div class="desc">Last import ${r.last_import_at ? esc(fmtRelative(r.last_import_at)) : 'never'}</div></div>
      <div class="text-1">${r.first_txn ? `${esc(fmtDate(r.first_txn, { year: true }))} – ${esc(fmtDate(r.last_txn, { year: true }))}` : '—'}</div></div>
    <div class="setting-row"><div><div class="title">Storage</div><div class="desc">${fmtNumber(st.unique_files || 0)} files</div></div>
      <div class="text-1">${esc(fmtBytes(st.disk_scan_ok ? st.disk_bytes || 0 : st.source_bytes || 0))}</div></div>
    ${P360.data.ai && P360.data.ai.calls ? `<div class="setting-row"><div><div class="title">AI</div>
      <div class="desc">${fmtNumber(P360.data.ai.calls)} calls${P360.data.ai.errors ? `, ${fmtNumber(P360.data.ai.errors)} failed` : ''}</div></div>
      <div class="text-1">${P360.data.ai.shared_cost_usd ? `${esc(fmtMoney(P360.data.ai.shared_cost_usd, 'USD'))} on the shared key` : 'Own key'}</div></div>` : ''}
    <div class="hint mt-4">${icon('eye-off', 'ico-sm')} Only counts are shown here — never their transactions, merchants, balances or files.</div>`;
}

/* ---------- billing ---------- */

function billingTab() {
  const { subscription: s, billing: b, user: u } = P360.data;
  const cur = (s.currency || 'usd').toUpperCase();
  const rows = [
    ['Access', b ? (ACCESS_LABEL[b.comped_until ? 'comped' : b.state] || b.state) : '—'],
    ['Plan', s.plan ? `${s.plan === 'yearly' ? 'Yearly' : 'Monthly'}${s.mrr_cents ? ` · ${fmtMoney(s.mrr_cents / 100, cur)} a month` : ''}` : '—'],
    ...(s.trial_end && (b || {}).state === 'trialing' ? [['Trial ends', fmtDateLong(s.trial_end)]] : []),
    ...(s.current_period_end ? [[s.cancel_at_period_end ? 'Ends on' : 'Renews on', fmtDateLong(s.current_period_end)]] : []),
    ...(b && b.comped_until ? [['Complimentary until', b.comped_until === 'forever' ? 'Forever' : fmtDateLong(b.comped_until)]] : []),
    ...(b && b.grace_until ? [['Grace period until', fmtDateLong(b.grace_until)]] : []),
  ];
  const payments = s.payments || [];
  const isAdmin = u.role === 'admin';
  return `
    <div class="p360-facts">${rows.map(([k, v]) => `<div><span class="text-3">${esc(k)}</span><b>${esc(v)}</b></div>`).join('')}</div>
    <div class="row gap-2 wrap mt-4">
      <button type="button" class="btn btn-secondary btn-sm" data-p360="extend-trial" ${isAdmin ? 'disabled' : ''}>${icon('clock', 'ico-sm')}Extend trial</button>
      <button type="button" class="btn btn-secondary btn-sm" data-p360="extend-grace" ${isAdmin ? 'disabled' : ''}>${icon('clock', 'ico-sm')}Extend grace</button>
      <button type="button" class="btn btn-secondary btn-sm" data-p360="comp" ${isAdmin ? 'disabled' : ''}>${icon('gift', 'ico-sm')}${b && b.comped_until ? 'Change complimentary access' : 'Give complimentary access'}</button>
      ${s.stripe_subscription_id ? `<button type="button" class="btn btn-secondary btn-sm" data-p360="sync">${icon('refresh', 'ico-sm')}Refresh from Stripe</button>
        <button type="button" class="btn btn-secondary btn-sm" data-p360="cancel">${icon('x', 'ico-sm')}Cancel subscription</button>` : ''}
      ${s.stripe_customer_url ? `<a class="btn btn-ghost btn-sm" href="${esc(s.stripe_customer_url)}" target="_blank" rel="noopener">${icon('external-link', 'ico-sm')}Open in Stripe</a>` : ''}
    </div>
    ${isAdmin ? '<div class="hint mt-2">Administrators are never billed.</div>' : ''}
    <div class="section-label mt-6 mb-2">Payments</div>
    ${payments.length ? `<div class="tbl-wrap"><table class="tbl tbl--list"><thead><tr><th>Date</th><th>Status</th><th class="right">Amount</th></tr></thead><tbody>
      ${payments.map((p) => `<tr><td>${esc(fmtDateLong(p.paid_at || p.failed_at || p.period_start))}</td>
        <td><span class="badge ${p.status === 'paid' ? 'badge-success' : p.status === 'failed' ? 'badge-danger' : 'badge-neutral'}">${esc(p.status.replace('_', ' '))}</span></td>
        <td class="right num">${esc(fmtMoney((p.amount_paid_cents - (p.amount_refunded_cents || 0)) / 100, (p.currency || 'usd').toUpperCase()))}</td></tr>`).join('')}
      </tbody></table></div>`
    : '<div class="hint">No payments yet.</div>'}`;
}

/* ---------- history ---------- */

function historyTab() {
  const { timeline, logins, emails, admin_actions: acts, recent_activity: recent } = P360.data;
  const tl = timeline.map((t) => `<li class="tl-item"><span class="tl-dot tl-${esc(t.kind)}"></span>
      <div class="grow">${esc(t.kind === 'admin' ? auditTitle(t.label) : t.label)}${t.amount_cents ? ` · ${esc(fmtMoney(t.amount_cents / 100, (t.currency || 'usd').toUpperCase()))}` : ''}${t.by ? ` <span class="text-3">by ${esc(t.by)}</span>` : ''}</div>
      <time class="text-3" datetime="${esc(t.at || '')}" data-tip="${esc(t.at ? fmtDateTime(t.at) : '')}">${t.at ? esc(fmtRelative(t.at)) : ''}</time></li>`).join('');
  const login = (l) => `<tr><td>${esc(fmtDateTime(l.created_at))}</td>
    <td>${l.ok ? '<span class="text-success">Signed in</span>' : `<span class="text-danger">Failed · ${esc((l.reason || '').replace('_', ' '))}</span>`}${l.kind !== 'password' ? ` <span class="text-3">(${esc(l.kind.replace('_', ' '))})</span>` : ''}${l.new_network ? ' <span class="badge badge-warning">new network</span>' : ''}</td>
    <td class="text-3">${esc([l.browser, l.os].filter(Boolean).join(' on ') || l.device || '—')}</td>
    <td class="text-3 num">${esc(l.ip || '—')}${l.country ? ` · ${esc(l.country)}` : ''}</td></tr>`;
  return `
    <div class="section-label mb-2">Story</div>
    <ol class="timeline p360-timeline">${tl}</ol>
    <div class="section-label mt-6 mb-2">Sign-ins</div>
    ${logins.length ? `<div class="tbl-wrap"><table class="tbl tbl--list"><thead><tr><th>When</th><th>Result</th><th>Device</th><th>Address</th></tr></thead>
      <tbody>${logins.map(login).join('')}</tbody></table></div>` : '<div class="hint">No sign-ins recorded yet.</div>'}
    <div class="section-label mt-6 mb-2">Emails</div>
    ${emails.length ? `<ul class="p360-list">${emails.map((m) => `<li><span class="grow">${esc(m.template.replace(/_/g, ' '))}</span>
      <span class="badge ${m.status === 'sent' ? 'badge-success' : m.status === 'failed' ? 'badge-danger' : 'badge-neutral'}" ${m.error ? `data-tip="${esc(m.error)}"` : ''}>${esc(m.status)}</span>
      <span class="text-3">${esc(fmtRelative(m.created_at))}</span></li>`).join('')}</ul>` : '<div class="hint">Nothing sent yet.</div>'}
    <div class="section-label mt-6 mb-2">What administrators did</div>
    ${acts.length ? `<div class="adm-audit adm-audit--compact">${acts.map((a) => auditRow(a, { compact: true })).join('')}</div>` : '<div class="hint">Nothing yet.</div>'}
    <div class="section-label mt-6 mb-2">Their recent activity</div>
    ${recent.length ? `<div class="adm-audit adm-audit--compact">${recent.map((a) => auditRow(a, { compact: true })).join('')}</div>
      <a class="btn btn-secondary btn-sm mt-3" href="#activity" data-act="audit-for-user" data-id="${P360.id}" data-name="${esc(personLabel(P360.data.user))}">See all their activity</a>` : '<div class="hint">Nothing recorded.</div>'}`;
}

/* ---------- notes ---------- */

function notesTab() {
  const notes = P360.data.notes;
  return `
    <form class="p360-note-form" id="p360-note-form">
      <label class="sr-only" for="p360-note">New note</label>
      <textarea id="p360-note" class="input" rows="3" maxlength="4000" placeholder="Add a note only administrators can see…"></textarea>
      <div class="row-between mt-2"><label class="check-row"><input type="checkbox" class="check" id="p360-pin"><span>Pin to the top</span></label>
        <button type="submit" class="btn btn-primary btn-sm">Add note</button></div>
    </form>
    ${notes.length ? `<ul class="p360-notes">${notes.map((n) => `<li class="${n.pinned ? 'is-pinned' : ''}">
      <div class="p360-note-body">${esc(n.body)}</div>
      <div class="row-between mt-1"><span class="text-3">${esc(n.author || 'someone')} · ${esc(fmtRelative(n.created_at))}</span>
        <span class="row gap-1"><button type="button" class="btn btn-ghost btn-xs" data-p360="pin" data-id="${n.id}" data-pinned="${n.pinned}">${n.pinned ? 'Unpin' : 'Pin'}</button>
        <button type="button" class="btn btn-ghost btn-xs" data-p360="delete-note" data-id="${n.id}">Delete</button></span></div></li>`).join('')}</ul>`
    : '<div class="hint mt-4">No notes yet.</div>'}`;
}

/* ---------- actions ---------- */

function personAsRow() {
  const u = P360.data.user;
  const listed = (typeof AU !== 'undefined' && AU.items.find((x) => x.id === u.id)) || {};
  return { ...listed, ...u, is_self: u.id === (window.currentUser || {}).id,
    txn_count: P360.data.counts.transactions, statement_count: P360.data.counts.statements,
    storage_bytes: P360.data.storage.source_bytes };
}

function personMenu(anchor) {
  const u = personAsRow();
  const isMe = u.is_self;
  const extra = [
    ...(u.email && !u.email_confirmed ? [
      { label: 'Resend the confirmation email', icon: 'mail', onClick: () => personPost('resend-confirmation', 'Confirmation email sent') },
      { label: 'Mark email as confirmed', icon: 'check', onClick: () => personPost('mark-confirmed', 'Email marked as confirmed') },
    ] : []),
    { label: u.email ? 'Change email address…' : 'Add an email address…', icon: 'pencil', disabled: isMe, onClick: changeEmail },
    ...(u.email && !isMe ? [{ label: 'Send an invitation to set a password', icon: 'mail', onClick: () => personPost('invite', 'Invitation sent') }] : []),
    { divider: true },
  ];
  ui.menu(anchor, [...extra, ...userMenuItems(u).filter((item) => item.label !== 'Open')]);
}

async function personPost(path, message) {
  await api(`/api/admin/users/${P360.id}/${path}`, { method: 'POST', body: {} });
  toast(message, { type: 'success' });
  afterChange();
}

function changeEmail() {
  const u = P360.data.user;
  const m = ui.modal({
    title: u.email ? 'Change email address' : 'Add an email address',
    html: `<div class="field"><label for="ce-email">Email</label><input id="ce-email" class="input" type="email" value="${esc(u.email || '')}" autofocus>
      <div class="hint">They are sent a link to confirm it; until then the address counts as unconfirmed.</div></div>`,
    actions: [{ label: 'Cancel' }, { label: 'Save', primary: true, onClick: async () => {
      await api(`/api/admin/users/${P360.id}/email`, { method: 'PUT', body: { email: $('#ce-email', m.el).value.trim() } });
      toast('Email updated — a confirmation link is on its way', { type: 'success' });
      afterChange();
    } }],
  });
}

function daysDialog(title, hint, action, path) {
  const m = ui.modal({
    title,
    html: `<div class="field"><label for="dd-days">Extra days</label><input id="dd-days" class="input num-input" type="number" min="1" max="365" value="7" autofocus>
      <div class="hint">${esc(hint)}</div></div>`,
    actions: [{ label: 'Cancel' }, { label: action, primary: true, onClick: async () => {
      await api(`/api/admin/billing/users/${P360.id}/${path}`, { method: 'POST', body: { days: Number($('#dd-days', m.el).value) } });
      toast('Done', { type: 'success' });
      afterChange();
    } }],
  });
}

function compDialog() {
  const b = P360.data.billing || {};
  const m = ui.modal({
    title: 'Complimentary access',
    html: `<div class="field"><label for="cp-mode">Access</label><select id="cp-mode" class="select">
        <option value="forever" ${b.comped_until === 'forever' ? 'selected' : ''}>Free for good</option>
        <option value="until" ${b.comped_until && b.comped_until !== 'forever' ? 'selected' : ''}>Free until a date</option>
        <option value="clear" ${!b.comped_until ? 'selected' : ''}>Not complimentary</option></select></div>
      <div class="field" id="cp-date-field"><label for="cp-until">Until</label><input id="cp-until" class="input" type="date" value="${esc(b.comped_until && b.comped_until !== 'forever' ? b.comped_until.slice(0, 10) : '')}"></div>
      <div class="hint">Complimentary access overrides any trial or subscription state; Stripe is not touched.</div>`,
    actions: [{ label: 'Cancel' }, { label: 'Save', primary: true, onClick: async () => {
      const mode = $('#cp-mode', m.el).value;
      const until = $('#cp-until', m.el).value;
      if (mode === 'until' && !until) { ui.fieldError($('#cp-until', m.el), 'Pick a date'); return false; }
      await api(`/api/admin/billing/users/${P360.id}/comp`, { method: 'POST',
        body: mode === 'forever' ? { forever: true } : mode === 'until' ? { until } : {} });
      toast('Access updated', { type: 'success' });
      afterChange();
      return undefined;
    } }],
  });
  const sync = () => { $('#cp-date-field', m.el).hidden = $('#cp-mode', m.el).value !== 'until'; };
  $('#cp-mode', m.el).addEventListener('change', sync);
  sync();
}

async function cancelSubscription() {
  const now = await ui.confirm({ title: 'Cancel this subscription?',
    body: 'It stops renewing at the end of the period they have paid for. They keep full access until then.',
    confirmText: 'Cancel at period end', danger: true });
  if (!now) return;
  await api(`/api/admin/billing/users/${P360.id}/cancel`, { method: 'POST', body: { immediately: false } });
  toast('Subscription set to end at the period end', { type: 'success' });
  afterChange();
}

async function editTags(anchor) {
  const all = await adminTags(true);
  const mine = new Set(P360.data.tags.map((t) => String(t.id)));
  ui.multiFilter(anchor, {
    title: 'Tags', options: all.map((t) => ({ value: String(t.id), label: t.name })), selected: mine,
    onChange: async (sel) => {
      await api(`/api/admin/users/${P360.id}/tags`, { method: 'PUT', body: { tag_ids: Array.from(sel).map(Number) } });
      P360.data.tags = all.filter((t) => sel.has(String(t.id)));
      $('#p360-tags').innerHTML = `${tagChips(P360.data.tags)}<button type="button" class="btn btn-ghost btn-xs" data-p360="tags">${icon('tag', 'ico-sm')}Tags</button>`;
      if (typeof AU !== 'undefined') AU.facets = null;
    },
  });
  if (!all.length) { ui.closeTop(); newTag().then((t) => { if (t) editTags(anchor); }); }
}

document.addEventListener('click', async (e) => {
  const tab = e.target.closest('[data-p360-tab]');
  if (tab && P360.drawer) {
    P360.tab = tab.dataset.p360Tab;
    $$('[data-p360-tab]').forEach((b) => { const on = b === tab; b.classList.toggle('active', on); b.setAttribute('aria-selected', on); });
    $('#p360-tab').innerHTML = tabHtml(P360.tab);
    return;
  }
  const el = e.target.closest('[data-p360]');
  if (!el || !P360.drawer) return;
  const run = (fn) => ui.busy(el, fn);
  switch (el.dataset.p360) {
    case 'menu': personMenu(el); break;
    case 'tags': editTags(el); break;
    case 'extend-trial': daysDialog('Extend the trial', 'Counted from today, or from the current trial end if that is later.', 'Extend trial', 'extend-trial'); break;
    case 'extend-grace': daysDialog('Extend the grace period', 'More time to fix a failed payment before the account becomes read-only.', 'Extend grace', 'extend-grace'); break;
    case 'comp': compDialog(); break;
    case 'cancel': cancelSubscription(); break;
    case 'sync': run(async () => { await api(`/api/admin/billing/users/${P360.id}/sync`, { method: 'POST', body: {} }); toast('Refreshed from Stripe', { type: 'success' }); afterChange(); }); break;
    case 'pin': run(async () => { await api(`/api/admin/notes/${el.dataset.id}`, { method: 'PUT', body: { pinned: el.dataset.pinned !== 'true' } }); refreshPerson(); }); break;
    case 'delete-note':
      if (await ui.confirm({ title: 'Delete this note?', confirmText: 'Delete', danger: true })) {
        await api(`/api/admin/notes/${el.dataset.id}`, { method: 'DELETE' });
        refreshPerson();
      }
      break;
    default: break;
  }
});

document.addEventListener('submit', async (e) => {
  if (e.target.id !== 'p360-note-form') return;
  e.preventDefault();
  const body = $('#p360-note').value.trim();
  if (!body) { ui.fieldError($('#p360-note'), 'Write something first'); return; }
  await ui.busy(e.target.querySelector('[type="submit"]'), async () => {
    await api(`/api/admin/users/${P360.id}/notes`, { method: 'POST', body: { body, pinned: $('#p360-pin').checked } });
    P360.tab = 'notes';
    await refreshPerson();
  });
});
