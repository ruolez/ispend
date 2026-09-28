/* Admin › Customers › one customer, as a page of its own: #customers/<id>/<tab>. Access and money,
   how they got started, what they have (counts only), one activity feed, notes and tags, and every
   action an operator can take on them. Everything comes from GET /api/admin/users/<id>. */

const P360 = { id: null, data: null, open: false, tab: 'overview', feed: 'all', seq: 0 };
const P360_TABS = [['overview', 'Overview'], ['billing', 'Billing'], ['activity', 'Activity'], ['notes', 'Notes']];

function p360Host() { return $('#cust-person'); }

/* Called by the Customers section when the route names a customer. Switching tabs repaints from
   what is loaded; opening another customer (or a reload) fetches. */
async function showCustomer(id, tab) {
  const same = P360.open && P360.id === id && P360.data;
  P360.open = true;
  P360.id = id;
  P360.tab = P360_TABS.some(([k]) => k === tab) ? tab : 'overview';
  adminSetHead(null);
  if (same) { renderPerson(); return; }
  P360.data = null;
  P360.feed = 'all';
  p360Host().innerHTML = `<div class="cust">${ui.skeleton(120, 14)}<div class="mt-4">${ui.skeletonList(8)}</div></div>`;
  await refreshPerson();
}

function closeCustomer() {
  P360.open = false;
  P360.data = null;
}

async function refreshPerson() {
  if (!P360.open) return;
  const seq = ++P360.seq;
  const id = P360.id;
  let data;
  try {
    data = await api(`/api/admin/users/${id}`);
  } catch (err) {
    if (seq !== P360.seq) return;
    p360Host().innerHTML = `<div class="cust">${custCrumb()}${err.status === 404
      ? ui.emptyState({ icon: 'user', title: 'No such customer', body: 'They may have been erased, or the link is wrong.', action: { label: 'All customers', href: '#customers' } })
      : ui.errorBox(err.message, { retry: 'reload-person' })}</div>`;
    return;
  }
  if (seq !== P360.seq || !P360.open || P360.id !== id) return;
  P360.data = data;
  renderPerson();
}
window.addEventListener('ispend:admin-user-changed', () => { if (P360.open) refreshPerson(); });

function personLabel(u) { return u.email || u.username; }

function custCrumb() {
  // Hash-only, so the list's filters (in the query) come back with it.
  return `<nav class="adm-crumb" aria-label="Breadcrumb"><a href="#customers">${icon('chevron-left', 'ico-sm')}Customers</a></nav>`;
}

function personState() {
  const { billing: b, subscription: s } = P360.data;
  return b && b.comped_until ? 'comped' : ((s || {}).ent_state || (b && b.state));
}

/* The one access action that fits where they are; everything else is in the ⋯ menu. */
function primaryAccessAction() {
  const { subscription: s, billing: b } = P360.data;
  const state = personState();
  if (state === 'trialing') return `<button type="button" class="btn btn-secondary" data-p360="extend-trial">${icon('clock')}<span class="label">Extend trial</span></button>`;
  if (state === 'grace') return `<button type="button" class="btn btn-secondary" data-p360="extend-grace">${icon('clock')}<span class="label">Extend grace</span></button>`;
  if (s.stripe_customer_url) return `<a class="btn btn-secondary" href="${esc(s.stripe_customer_url)}" target="_blank" rel="noopener">${icon('external-link')}<span class="label">Open in Stripe</span></a>`;
  return `<button type="button" class="btn btn-secondary" data-p360="comp">${icon('gift')}<span class="label">${b && b.comped_until ? 'Change free access' : 'Give free access'}</span></button>`;
}

function renderPerson() {
  const { user: u } = P360.data;
  const state = personState();
  const label = personLabel(u);
  $('#tb-title').textContent = label;
  setPageTitle(label);
  p360Host().innerHTML = `<div class="cust">
    ${custCrumb()}
    <header class="cust-head">
      <span class="avatar avatar-lg" aria-hidden="true">${esc(initials(label))}</span>
      <div class="min-w-0 grow">
        <h1 class="cust-name truncate">${esc(label)}</h1>
        <div class="cust-badges">
          ${state ? accessBadge(state) : ''}
          ${u.status !== 'active' ? `<span class="user-status"><i class="dot" style="--c:var(--${STATUS_COLOR[u.status]})"></i>${esc(STATUS_LABEL[u.status])}${u.lock_reason ? ` · ${esc(u.lock_reason)}` : ''}</span>` : ''}
          ${u.email ? (u.email_confirmed ? `<span class="text-3">${icon('check', 'ico-sm')} Email confirmed</span>` : '<span class="text-warning">Email not confirmed</span>') : '<span class="text-4">No email address</span>'}
          <button type="button" class="cust-id" data-p360="copy-id" data-tip="Copy the customer number">#${u.id}</button>
          <span class="text-3">Customer since ${esc(fmtDate(u.created_at, { year: true }))}</span>
        </div>
      </div>
      <div class="cust-actions">
        ${u.email ? `<button type="button" class="btn btn-secondary" data-p360="email">${icon('mail')}<span class="label">Email</span></button>` : ''}
        ${primaryAccessAction()}
        <button type="button" class="btn btn-secondary btn-icon" data-p360="menu" aria-haspopup="menu" aria-label="More actions">${icon('more-horizontal')}</button>
      </div>
    </header>
    <nav class="tabs cust-tabs" role="tablist" aria-label="About this customer">${P360_TABS.map(([k, l]) =>
      `<a role="tab" class="tab ${P360.tab === k ? 'active' : ''}" aria-selected="${P360.tab === k}" href="#customers/${u.id}/${k}">${esc(l)}${k === 'notes' && P360.data.notes.length ? ` <span class="pill">${P360.data.notes.length}</span>` : ''}</a>`).join('')}</nav>
    <div class="cust-grid">
      <div class="cust-main" id="p360-tab" role="tabpanel">${tabHtml(P360.tab)}</div>
      <aside class="cust-side" aria-label="Facts">${factsCard()}</aside>
    </div>
  </div>`;
}

function tagChips(tags) {
  return (tags || []).map((t) => `<span class="adm-tag" style="--c:var(--${esc(t.color)})">${esc(t.name)}</span>`).join('');
}

function tagsBlock() {
  return `<div class="cust-tags" id="p360-tags">${tagChips(P360.data.tags)}<button type="button" class="btn btn-ghost btn-xs" data-p360="tags">${icon('tag', 'ico-sm')}${P360.data.tags.length ? 'Edit' : 'Add tags'}</button></div>`;
}

function factsCard() {
  const { user: u, subscription: s, billing: b, attribution: at } = P360.data;
  const cur = (s.currency || 'usd').toUpperCase();
  const act = P360.data.activation;
  const state = personState();
  const facts = [
    ['Access', state ? accessBadge(state) : '—'],
    ['Plan', s.plan ? esc(s.plan === 'yearly' ? 'Yearly' : 'Monthly') : '—'],
    ['MRR', s.mrr_cents ? esc(fmtMoney(s.mrr_cents / 100, cur)) : '—'],
    ['Paid so far', s.lifetime_cents ? esc(fmtMoney(s.lifetime_cents / 100, cur)) : '—'],
    ...(s.trial_end && state === 'trialing' ? [['Trial ends', esc(fmtDateLong(s.trial_end))]] : []),
    ...(s.current_period_end ? [[s.cancel_at_period_end ? 'Ends on' : 'Renews on', esc(fmtDateLong(s.current_period_end))]] : []),
    ...(b && b.comped_until ? [['Free until', b.comped_until === 'forever' ? 'Forever' : esc(fmtDateLong(b.comped_until))]] : []),
    ['Last seen', u.last_seen_at ? `<span data-tip="${esc(fmtDateTime(u.last_seen_at))}">${esc(fmtRelative(u.last_seen_at))}</span>` : 'Never'],
    ['Activated', act.activated ? 'Yes' : (act.first_commit_at ? 'Later' : 'Not yet')],
    ['Came from', at ? esc(at.channel) : '—'],
    ...(u.email && u.username !== u.email ? [['Username', esc(u.username)]] : []),
  ];
  return `<section class="card cust-facts">
    <dl class="adm-dl">${facts.map(([k, v]) => `<div><dt>${esc(k)}</dt><dd>${v}</dd></div>`).join('')}</dl>
    <div class="section-label mt-4 mb-2">Tags</div>${tagsBlock()}
  </section>`;
}

function tabHtml(tab) {
  if (tab === 'billing') return billingTab();
  if (tab === 'activity') return activityTab();
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
    <section class="card card-pad">
    <div class="section-label mb-2">Getting started</div>
    <ol class="p360-steps">
      ${step(true, 'Signed up', u.created_at)}
      ${u.email ? step(!!a.confirmed_at || u.email_confirmed, 'Confirmed their email', a.confirmed_at) : ''}
      ${step(!!a.first_upload_at, 'Uploaded a statement', a.first_upload_at)}
      ${step(!!a.first_commit_at, a.activated ? 'Imported it within the first week' : 'Imported a statement', a.first_commit_at)}
    </ol>
    ${at ? `<div class="hint mt-2">Came from <b>${esc(at.channel)}</b>${at.utm_campaign ? ` · campaign ${esc(at.utm_campaign)}` : ''}${at.referrer_host ? ` · via ${esc(at.referrer_host)}` : ''}.</div>` : ''}
    </section>
    <section class="card card-pad mt-4">
    <div class="section-label mb-2">What they have</div>
    ${kpis(tiles.map(([l, v]) => [l, fmtNumber(v || 0)]), 'mb-4')}
    <div class="setting-row"><div><div class="title">Statements cover</div><div class="desc">Last import ${r.last_import_at ? esc(fmtRelative(r.last_import_at)) : 'never'}</div></div>
      <div class="text-1">${r.first_txn ? `${esc(fmtDate(r.first_txn, { year: true }))} – ${esc(fmtDate(r.last_txn, { year: true }))}` : '—'}</div></div>
    <div class="setting-row"><div><div class="title">Storage</div><div class="desc">${fmtNumber(st.unique_files || 0)} files</div></div>
      <div class="text-1">${esc(fmtBytes(st.disk_scan_ok ? st.disk_bytes || 0 : st.source_bytes || 0))}</div></div>
    ${P360.data.ai && P360.data.ai.calls ? `<div class="setting-row"><div><div class="title">AI</div>
      <div class="desc">${fmtNumber(P360.data.ai.calls)} calls${P360.data.ai.errors ? `, ${fmtNumber(P360.data.ai.errors)} failed` : ''}</div></div>
      <div class="text-1">${P360.data.ai.shared_cost_usd ? `${esc(fmtMoney(P360.data.ai.shared_cost_usd, 'USD'))} on the shared key` : 'Own key'}</div></div>` : ''}
    <div class="hint mt-4">${icon('eye-off', 'ico-sm')} Only counts are shown here — never their transactions, merchants, balances or files.</div>
    </section>
    <section class="card card-pad mt-4 cust-danger">
      <div class="row-between gap-4 wrap"><div class="min-w-0"><div class="title">Erase this customer</div>
        <div class="desc">Deletes their account, statements, transactions and files, cancels any subscription first, and keeps only anonymous bookkeeping. This cannot be undone.</div></div>
        <button type="button" class="btn btn-danger-solid" data-p360="erase">Erase…</button></div>
    </section>`;
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
  const state = personState();
  const billingEvents = (P360.data.timeline || []).filter((t) => BILLING_KINDS.has(t.kind));
  return `
    <section class="card card-pad">
    <dl class="adm-dl">${rows.map(([k, v]) => `<div><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`).join('')}</dl>
    <div class="row gap-2 wrap mt-4">
      <button type="button" class="btn btn-secondary btn-sm" data-p360="extend-trial" ${state === 'active' ? 'disabled data-tip="They are paying; a trial does not apply"' : ''}>${icon('clock', 'ico-sm')}Extend trial</button>
      <button type="button" class="btn btn-secondary btn-sm" data-p360="extend-grace" ${state === 'grace' || state === 'read_only' ? '' : 'disabled data-tip="Only after a payment fails or a trial runs out"'}>${icon('clock', 'ico-sm')}Extend grace</button>
      <button type="button" class="btn btn-secondary btn-sm" data-p360="comp">${icon('gift', 'ico-sm')}${b && b.comped_until ? 'Change free access' : 'Give free access'}</button>
      ${s.stripe_subscription_id ? `<button type="button" class="btn btn-secondary btn-sm" data-p360="sync">${icon('refresh', 'ico-sm')}Refresh from Stripe</button>
        <button type="button" class="btn btn-secondary btn-sm" data-p360="cancel">${icon('x', 'ico-sm')}Cancel subscription</button>` : ''}
      ${s.stripe_customer_url ? `<a class="btn btn-ghost btn-sm" href="${esc(s.stripe_customer_url)}" target="_blank" rel="noopener">${icon('external-link', 'ico-sm')}Open in Stripe</a>` : ''}
    </div>
    </section>
    <section class="card card-pad mt-4">
    <div class="section-label mb-2">Payments</div>
    ${payments.length ? `<div class="tbl-wrap"><table class="tbl tbl--list"><thead><tr><th>Date</th><th>Status</th><th class="right">Amount</th></tr></thead><tbody>
      ${payments.map((p) => `<tr><td>${esc(fmtDateLong(p.paid_at || p.failed_at || p.period_start))}</td>
        <td><span class="badge ${p.status === 'paid' ? 'badge-success' : p.status === 'failed' ? 'badge-danger' : 'badge-neutral'}">${esc(p.status.replace('_', ' '))}</span></td>
        <td class="right num">${esc(fmtMoney((p.amount_paid_cents - (p.amount_refunded_cents || 0)) / 100, (p.currency || 'usd').toUpperCase()))}</td></tr>`).join('')}
      </tbody></table></div>`
    : '<div class="hint">No payments yet.</div>'}
    </section>
    ${billingEvents.length ? `<section class="card card-pad mt-4"><div class="section-label mb-2">Subscription history</div>
      <ol class="timeline cust-feed">${billingEvents.map(feedItemHtml).join('')}</ol></section>` : ''}`;
}

/* ---------- activity: one feed ---------- */

const BILLING_KINDS = new Set(['payment', 'payment_failed', 'subscribed', 'canceled', 'plan_changed', 'mrr_changed',
  'status_changed', 'cancel_scheduled', 'cancel_unscheduled', 'trial_started', 'trial_extended', 'grace_extended',
  'comped', 'uncomped']);
const FEED_FILTERS = [['all', 'Everything'], ['account', 'Their account'], ['billing', 'Billing'], ['signin', 'Sign-ins'],
  ['email', 'Emails'], ['admin', 'By admins']];
const FEED_ICON = { account: 'user', billing: 'credit-card', signin: 'lock', email: 'mail', admin: 'shield' };

/* Their story, sign-ins, emails and what they and the admins did, newest first. */
function feedItems() {
  const { timeline, logins, emails, recent_activity: recent } = P360.data;
  const items = [];
  (timeline || []).forEach((t) => {
    const group = t.kind === 'admin' ? 'admin' : BILLING_KINDS.has(t.kind) ? 'billing' : 'account';
    items.push({ at: t.at, group, danger: t.kind === 'payment_failed',
      label: t.kind === 'admin' ? auditTitle(t.label) : t.label,
      sub: [t.amount_cents ? fmtMoney(t.amount_cents / 100, (t.currency || 'usd').toUpperCase()) : null,
        t.by ? `by ${t.by}` : null].filter(Boolean).join(' · ') });
  });
  (logins || []).forEach((l) => items.push({ at: l.created_at, group: 'signin', danger: !l.ok,
    label: l.ok ? (l.kind === 'password' ? 'Signed in' : cap(words(l.kind))) : `Sign-in failed · ${words(l.reason || '')}`,
    sub: [[l.browser, l.os].filter(Boolean).join(' on ') || l.device, l.ip, l.country].filter(Boolean).join(' · '),
    badge: l.new_network ? ['badge-warning', 'new network'] : null }));
  (emails || []).forEach((m) => items.push({ at: m.created_at, group: 'email', danger: m.status === 'failed',
    label: `Email: ${words(m.template)}`, sub: m.error || '',
    badge: [m.status === 'sent' ? 'badge-success' : m.status === 'failed' ? 'badge-danger' : 'badge-neutral', m.status] }));
  (recent || []).filter((a) => !String(a.action).startsWith('auth.login')).forEach((a) =>
    items.push({ at: a.created_at, group: 'account', label: auditTitle(a.action), sub: '' }));
  return items.filter((i) => i.at).sort((a, b) => (a.at < b.at ? 1 : -1));
}

function feedItemHtml(t) {
  const group = t.group || (BILLING_KINDS.has(t.kind) ? 'billing' : 'account');
  return `<li class="tl-item cust-feed-item"><span class="cust-feed-ico ${t.danger ? 'is-danger' : ''}" aria-hidden="true">${icon(FEED_ICON[group] || 'circle', 'ico-sm')}</span>
    <div class="grow min-w-0"><div>${esc(t.label)}${t.badge ? ` <span class="badge ${t.badge[0]}">${esc(t.badge[1])}</span>` : ''}${!t.group && t.amount_cents ? ` · ${esc(fmtMoney(t.amount_cents / 100, (t.currency || 'usd').toUpperCase()))}` : ''}</div>
      ${t.sub ? `<div class="text-3 fs-sm truncate">${esc(t.sub)}</div>` : ''}</div>
    <time class="text-3" datetime="${esc(t.at || '')}" data-tip="${esc(t.at ? fmtDateTime(t.at) : '')}">${t.at ? esc(fmtRelative(t.at)) : ''}</time></li>`;
}

function activityTab() {
  const all = feedItems();
  const shown = P360.feed === 'all' ? all : all.filter((i) => i.group === P360.feed);
  let day = null;
  const list = shown.map((t) => {
    const label = dayLabel(t.at);
    const head = label === day ? '' : `<li class="adm-day" role="presentation">${esc(label)}</li>`;
    day = label;
    return head + feedItemHtml(t);
  }).join('');
  const u = P360.data.user;
  return `<section class="card card-pad">
    <div class="seg cust-feed-filter" role="radiogroup" aria-label="Show">${FEED_FILTERS.map(([k, l]) => {
      const n = k === 'all' ? all.length : all.filter((i) => i.group === k).length;
      return `<button type="button" class="seg-btn ${P360.feed === k ? 'active' : ''}" role="radio" aria-checked="${P360.feed === k}" data-p360-feed="${k}" ${n ? '' : 'disabled'}>${esc(l)}</button>`;
    }).join('')}</div>
    ${shown.length ? `<ol class="timeline cust-feed mt-4">${list}</ol>` : '<div class="hint mt-4">Nothing here yet.</div>'}
    <a class="btn btn-secondary btn-sm mt-4" href="${esc(adminHref('activity', { params: { user: u.id, who: personLabel(u) } }))}">${icon('clock', 'ico-sm')}Open in the activity log</a>
  </section>`;
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
  return { ...listed, ...u,
    txn_count: P360.data.counts.transactions, statement_count: P360.data.counts.statements,
    storage_bytes: P360.data.storage.source_bytes };
}

function personMenu(anchor) {
  const u = personAsRow();
  const s = P360.data.subscription || {};
  const extra = [
    ...(u.email && !u.email_confirmed ? [
      { label: 'Resend the confirmation email', icon: 'mail', onClick: () => personPost('resend-confirmation', 'Confirmation email sent') },
      { label: 'Mark email as confirmed', icon: 'check', onClick: () => personPost('mark-confirmed', 'Email marked as confirmed') },
    ] : []),
    { label: u.email ? 'Change email address…' : 'Add an email address…', icon: 'pencil', onClick: changeEmail },
    ...(u.email ? [{ label: 'Send an invitation to set a password', icon: 'mail', onClick: () => personPost('invite', 'Invitation sent') }] : []),
    ...(s.stripe_subscription_id ? [{ label: 'Refresh from Stripe', icon: 'refresh', onClick: syncFromStripe }] : []),
    ...(s.stripe_customer_url ? [{ label: 'Open in Stripe', icon: 'external-link', onClick: () => window.open(s.stripe_customer_url, '_blank', 'noopener') }] : []),
    { divider: true },
    { label: 'Email them a copy of their data', icon: 'download', disabled: !u.email, onClick: () => personPost('export', 'Preparing their data — they will get an email with the link') },
    { divider: true },
  ];
  ui.menu(anchor, [...extra, ...userMenuItems(u).filter((item) => item.label !== 'Open'),
    { label: 'Erase permanently…', icon: 'trash', danger: true, onClick: erasePerson }]);
}

async function syncFromStripe() {
  await api(`/api/admin/billing/users/${P360.id}/sync`, { method: 'POST', body: {} });
  toast('Refreshed from Stripe', { type: 'success' });
  afterChange();
}

function erasePerson() {
  const u = P360.data.user;
  const m = ui.modal({
    title: `Erase ${personLabel(u)} permanently?`,
    html: `<p class="mb-4">Their account, statements, transactions, files and settings are deleted at once, any subscription is cancelled first,
        and their name is removed from the activity log. Only anonymous bookkeeping (invoices, the revenue history) is kept. <b>This cannot be undone.</b></p>
      <div class="field"><label for="er-reason">Reason (kept on the erasure record)</label><input id="er-reason" class="input" maxlength="500" placeholder="e.g. asked by email on 3 October"></div>
      <div class="field"><label for="er-confirm">Type <b>${esc(u.username)}</b> to confirm</label><input id="er-confirm" class="input" autocomplete="off" spellcheck="false"></div>`,
    actions: [{ label: 'Cancel' }, { label: 'Erase', danger: true, onClick: async () => {
      if ($('#er-confirm', m.el).value.trim() !== u.username) { ui.fieldError($('#er-confirm', m.el), 'That does not match'); return false; }
      await api(`/api/admin/users/${P360.id}/erase`, { method: 'POST', body: { confirm: u.username, reason: $('#er-reason', m.el).value.trim() } });
      toast(`${personLabel(u)} erased`, { type: 'success' });
      closeCustomer();
      location.hash = '#customers';
      return undefined;
    } }],
  });
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
      $('#p360-tags').outerHTML = tagsBlock();
      if (typeof AU !== 'undefined') AU.facets = null;
    },
  });
  if (!all.length) { ui.closeTop(); newTag().then((t) => { if (t) editTags(anchor); }); }
}

document.addEventListener('click', async (e) => {
  if (P360.open && e.target.closest('[data-act="reload-person"]')) { refreshPerson(); return; }
  if (!P360.open || !P360.data) return;
  const feed = e.target.closest('[data-p360-feed]');
  if (feed) {
    P360.feed = feed.dataset.p360Feed;
    $('#p360-tab').innerHTML = tabHtml(P360.tab);
    return;
  }
  const el = e.target.closest('[data-p360]');
  if (!el) return;
  const run = (fn) => ui.busy(el, fn);
  switch (el.dataset.p360) {
    case 'menu': personMenu(el); break;
    case 'email': openComposer({ audience: { ids: [P360.id] }, label: personLabel(P360.data.user) }); break;
    case 'erase': erasePerson(); break;
    case 'copy-id':
      try { await navigator.clipboard.writeText(String(P360.id)); toast('Customer number copied'); } catch { /* clipboard blocked */ }
      break;
    case 'tags': editTags(el); break;
    case 'extend-trial': daysDialog('Extend the trial', 'Counted from today, or from the current trial end if that is later.', 'Extend trial', 'extend-trial'); break;
    case 'extend-grace': daysDialog('Extend the grace period', 'More time to fix a failed payment before the account becomes read-only.', 'Extend grace', 'extend-grace'); break;
    case 'comp': compDialog(); break;
    case 'cancel': cancelSubscription(); break;
    case 'sync': run(syncFromStripe); break;
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
