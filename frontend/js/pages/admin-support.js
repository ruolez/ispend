/* Admin › Support: customers' problem reports, questions and ideas (#support), one conversation at
   #support/<id>, and Settings › Support. The inbox is sorted by whose move it is; a thread shows
   internal notes next to the conversation, the customer, the diagnostics they chose to send and the
   server errors behind their request ids. Data: /api/admin/support/*. */

const SUP = { view: 'needs_reply', data: null, thread: null, shots: null, mode: 'reply', seq: 0, wired: false, saved: [] };
const SUP_VIEWS = [['needs_reply', 'Needs reply'], ['waiting', 'Waiting on customer'], ['resolved', 'Resolved'], ['all', 'All']];
const SUP_KINDS = { bug: 'Problem', data: 'Numbers look wrong', question: 'Question', idea: 'Idea', billing: 'Billing & account' };
const SUP_IMPACT = { blocking: ['Blocking', 'badge-danger'], annoying: ['Annoying', 'badge-warning'], minor: ['Minor', 'badge-neutral'] };
const SUP_PRIORITY = { 1: ['Urgent', 'badge-danger'], 2: ['High', 'badge-warning'], 3: ['Normal', 'badge-neutral'] };
const SUP_STATUS_LABEL = { open: 'Open', in_progress: 'In progress', waiting: 'Waiting on customer', resolved: 'Resolved' };

AdminPanels.register('support', {
  label: 'Support', icon: 'help', group: 'customers', pill: 'admin-support',
  params: ['view', 'kind', 'priority', 'q', 'page'],
  sub: 'Problem reports, questions and ideas from customers',
  markup: `
    <div id="sup-list">
      <div class="sup-toolbar">
        <nav class="tabs sup-views" id="sup-views" role="tablist" aria-label="Reports"></nav>
        <div class="sup-filters">
          <div class="input-group sup-search"><span class="ico-wrap" id="sup-search-ico"></span>
            <input id="sup-q" class="input input-sm" type="search" autocomplete="off" placeholder="Subject, customer or R-1042" aria-label="Search reports"></div>
          <select id="sup-kind" class="select input-sm" aria-label="Type"><option value="">Every type</option>
            ${Object.entries(SUP_KINDS).map(([k, l]) => `<option value="${k}">${esc(l)}</option>`).join('')}</select>
          <select id="sup-priority" class="select input-sm" aria-label="Priority"><option value="">Any priority</option>
            <option value="1">Urgent</option><option value="2">High</option><option value="3">Normal</option><option value="none">Not set</option></select>
        </div>
      </div>
      <section class="card"><div id="sup-rows"></div></section>
      <div class="row-between mt-3"><span class="hint" id="sup-hint"></span><div id="sup-pager" class="row gap-2"></div></div>
    </div>
    <div id="sup-thread" hidden></div>`,
  load: supLoad,
});

async function supRefreshPill() {
  try { setNavPill('admin-support', (await api('/api/admin/shell')).support_needs_reply); } catch { /* the badge is a nicety */ }
}

function supAge(iso) {
  const mins = Math.max(0, (Date.now() - Date.parse(iso)) / 60000);
  const text = mins < 60 ? `${Math.max(1, Math.round(mins))}m` : mins < 60 * 24 ? `${Math.round(mins / 60)}h` : `${Math.round(mins / 1440)}d`;
  const tone = mins >= 72 * 60 ? 'sup-age--late' : mins >= 24 * 60 ? 'sup-age--due' : '';
  return { text, tone };
}

function supBadge(map, key) {
  const v = map[key];
  return v ? `<span class="badge ${v[1]}">${esc(v[0])}</span>` : '';
}

function supStatusBadge(status) {
  const cls = { open: 'badge-info', in_progress: 'badge-accent', waiting: 'badge-warning', resolved: 'badge-success' }[status] || 'badge-neutral';
  return `<span class="badge ${cls}">${esc(SUP_STATUS_LABEL[status] || status)}</span>`;
}

function supWire(host) {
  if (SUP.wired) return;
  SUP.wired = true;
  $('#sup-search-ico').innerHTML = icon('search');
  $('#sup-q').addEventListener('input', debounce((e) => { setQs({ q: e.target.value.trim() || undefined, page: undefined }, { merge: true }); supLoadList(); }, 250));
  $('#sup-kind').addEventListener('change', (e) => { setQs({ kind: e.target.value || undefined, page: undefined }, { merge: true }); supLoadList(); });
  $('#sup-priority').addEventListener('change', (e) => { setQs({ priority: e.target.value || undefined, page: undefined }, { merge: true }); supLoadList(); });
  host.addEventListener('click', (e) => {
    const view = e.target.closest('[data-sup-view]');
    if (view) { e.preventDefault(); setQs({ view: view.dataset.supView === 'needs_reply' ? undefined : view.dataset.supView, page: undefined }, { merge: true }); supLoadList(); return; }
    const page = e.target.closest('[data-sup-page]');
    if (page) { setQs({ page: Number(page.dataset.supPage) > 1 ? page.dataset.supPage : undefined }, { merge: true }); supLoadList(); return; }
    const act = e.target.closest('[data-sup]');
    if (act) supAction(act);
  });
  host.addEventListener('change', (e) => {
    if (e.target.id === 'sup-status') supPatch({ status: e.target.value });
    if (e.target.id === 'sup-prio') supPatch({ priority: e.target.value ? Number(e.target.value) : null });
  });
}

async function supLoad(host, ctx) {
  supWire(host);
  const id = Number(ctx.route.id);
  $('#sup-list').hidden = !!id;
  $('#sup-thread').hidden = !id;
  if (id) return supOpenThread(id);
  SUP.thread = null;
  SUP.shots = null;
  const q = qs();
  $('#sup-q').value = q.q || '';
  $('#sup-kind').value = q.kind || '';
  $('#sup-priority').value = q.priority || '';
  return supLoadList();
}

async function supLoadList() {
  const q = qs();
  SUP.view = SUP_VIEWS.some(([k]) => k === q.view) ? q.view : 'needs_reply';
  const seq = ++SUP.seq;
  if (!SUP.data) $('#sup-rows').innerHTML = `<div class="card-body">${ui.skeletonList(5)}</div>`;
  let d;
  try {
    d = await api(`/api/admin/support/reports${toQuery({ view: SUP.view, kind: q.kind, priority: q.priority, q: q.q, page: q.page })}`);
  } catch (err) {
    if (seq === SUP.seq) $('#sup-rows').innerHTML = `<div class="card-body">${ui.errorBox(err.message, { retry: 'sup-reload' })}</div>`;
    return;
  }
  if (seq !== SUP.seq) return;
  SUP.data = d;
  setNavPill('admin-support', q.kind || q.priority || q.q ? NAV_PILLS['admin-support'] : d.counts.needs_reply);
  $('#sup-views').innerHTML = SUP_VIEWS.map(([k, l]) => `<a role="tab" class="tab ${SUP.view === k ? 'active' : ''}" aria-selected="${SUP.view === k}" href="#support" data-sup-view="${k}">${esc(l)}${d.counts[k] ? ` <span class="pill ${k === 'needs_reply' ? '' : 'pill-soft'}">${esc(fmtNumber(d.counts[k]))}</span>` : ''}</a>`).join('');
  supRenderRows(d);
}

function supRenderRows(d) {
  const host = $('#sup-rows');
  if (!d.reports.length) {
    const filteredView = qs().q || qs().kind || qs().priority;
    host.innerHTML = `<div class="card-body">${ui.emptyState(filteredView
      ? { icon: 'search', title: 'No reports match', body: 'Try another search or clear the filters.' }
      : SUP.view === 'needs_reply'
        ? { icon: 'check-circle', title: 'All caught up', body: 'Nothing is waiting for your reply. New reports show up here and on Home.' }
        : { icon: 'inbox', title: 'Nothing here', body: 'Reports move here as their status changes.' })}</div>`;
    $('#sup-hint').textContent = '';
    $('#sup-pager').innerHTML = '';
    return;
  }
  host.innerHTML = `<ul class="sup-rows">${d.reports.map((r) => {
    const age = supAge(r.status === 'waiting' ? (r.last_admin_at || r.updated_at) : r.last_user_at);
    const who = r.user.email || r.user.username;
    return `<li><a class="sup-row${r.unread ? ' is-unread' : ''}" href="#support/${r.id}">
      <span class="sup-row-dot" aria-hidden="true">${r.unread ? '<i class="dot"></i>' : ''}</span>
      <span class="sup-row-main">
        <span class="sup-row-subject truncate">${r.unread ? '<span class="sr-only">Unread: </span>' : ''}${esc(r.subject)}</span>
        <span class="sup-row-preview truncate text-3">${esc(r.preview || '')}</span>
        <span class="sup-row-meta text-3"><span class="mono">${esc(r.ref)}</span> · ${esc(who)} · ${esc(SUP_KINDS[r.kind] || r.kind)}${r.attachment_count ? ` · ${icon('paperclip', 'ico-sm')}${r.attachment_count}` : ''}</span>
      </span>
      <span class="sup-row-tags">${supBadge(SUP_PRIORITY, r.priority)}${r.impact === 'blocking' ? supBadge(SUP_IMPACT, 'blocking') : ''}${supStatusBadge(r.status)}</span>
      <span class="sup-age ${r.status === 'resolved' ? '' : age.tone}" data-tip="${esc(r.status === 'waiting' ? 'Since your last reply' : 'Since the customer last wrote')}">${esc(r.status === 'resolved' ? fmtDate(r.resolved_at || r.updated_at) : age.text)}</span>
    </a></li>`;
  }).join('')}</ul>`;
  const page = d.page;
  $('#sup-hint').textContent = SUP.view === 'needs_reply' ? 'Oldest first. Amber after a day without an answer, red after three.' : '';
  $('#sup-pager').innerHTML = page > 1 || d.has_more
    ? `<button type="button" class="btn btn-secondary btn-sm" data-sup-page="${page - 1}" ${page > 1 ? '' : 'disabled'}>Previous</button>
       <button type="button" class="btn btn-secondary btn-sm" data-sup-page="${page + 1}" ${d.has_more ? '' : 'disabled'}>Next</button>` : '';
}

/* ---------- one report ---------- */

async function supOpenThread(id) {
  adminSetHead(null);
  if (!SUP.thread || SUP.thread.id !== id) {
    $('#sup-thread').innerHTML = `<div class="sup-thread">${supCrumb()}${ui.skeleton(320, 26)}<div class="mt-4">${ui.skeletonList(4)}</div></div>`;
    SUP.mode = 'reply';
  }
  try {
    supRenderThread(await api(`/api/admin/support/reports/${id}`));
    supRefreshPill();
  } catch (err) {
    $('#sup-thread').innerHTML = `<div class="sup-thread">${supCrumb()}${err.status === 404
      ? ui.emptyState({ icon: 'help', title: 'No such report', body: 'The customer may have been erased, or the link is wrong.', action: { label: 'All reports', href: '#support' } })
      : ui.errorBox(err.message, { retry: 'sup-reload-thread' })}</div>`;
  }
}

function supCrumb() {
  return `<nav class="adm-crumb" aria-label="Breadcrumb"><a href="#support">${icon('chevron-left', 'ico-sm')}Support</a></nav>`;
}

function supMessageHtml(m, customer) {
  const who = m.author_role === 'user' ? (customer.email || customer.username) : (m.author || 'Admin');
  return `<li class="sup-msg ${m.internal ? 'sup-msg--note' : m.author_role === 'admin' ? 'sup-msg--team' : ''}">
    <div class="sup-msg-head"><span class="fw-600">${esc(who)}</span>
      ${m.internal ? `<span class="badge badge-warning">${icon('lock', 'ico-sm')}Internal note</span>` : m.author_role === 'admin' ? '<span class="badge badge-accent">Reply</span>' : ''}
      <time class="text-3 fs-sm" datetime="${esc(m.created_at)}" data-tip="${esc(fmtDateTime(m.created_at))}">${esc(fmtRelative(m.created_at))}</time></div>
    ${m.body === '(screenshot)' && m.attachments.length ? '' : `<div class="sup-msg-body">${esc(m.body)}</div>`}
    ${m.attachments.length ? `<div class="sup-shots">${m.attachments.map((a, i) => `<button type="button" class="sup-shot" data-sup="shot" data-url="${esc(a.url)}" data-w="${a.width}" data-h="${a.height}" aria-label="Open screenshot ${i + 1} (${a.width}×${a.height})">
      <img src="${esc(a.url)}" alt="" loading="lazy"></button>`).join('')}</div>` : ''}
  </li>`;
}

function supContextHtml(c) {
  const rows = [
    ['Page', c.page ? `<span class="mono">${esc(c.page)}</span>` : null],
    ['Browser', [c.browser, c.os, c.device].filter(Boolean).map(esc).join(' · ') || null],
    ['Screen', c.viewport ? esc(c.viewport) : null],
    ['Theme', c.theme ? esc(c.theme) : null],
    ['Installed app', c.standalone == null ? null : (c.standalone ? 'Yes' : 'No')],
    ['Online', c.online == null ? null : (c.online ? 'Yes' : 'No')],
    ['Language', [c.language, c.timezone].filter(Boolean).map(esc).join(' · ') || null],
    ['Version', [c.app_version, c.build ? `build ${c.build}` : null].filter(Boolean).map(esc).join(' · ') || null],
    ['Request id', c.request_id ? `<span class="mono">${esc(c.request_id)}</span>` : null],
  ].filter(([, v]) => v);
  if (!rows.length && !(c.errors || []).length && !(c.requests || []).length) return '<p class="hint m-0">The customer chose not to send technical details.</p>';
  return `<dl class="adm-dl">${rows.map(([k, v]) => `<div><dt>${esc(k)}</dt><dd>${v}</dd></div>`).join('')}</dl>
    ${(c.requests || []).length ? `<div class="section-label mt-3 mb-1">Failed requests</div><ul class="sup-log">${c.requests.map((r) => `<li>
      <span class="mono">${esc(r.method || '')} ${esc(r.path || '')}</span> <span class="badge ${r.status >= 500 || !r.status ? 'badge-danger' : 'badge-neutral'}">${esc(r.status ? String(r.status) : 'network')}</span>
      ${r.message ? `<div class="text-2">${esc(r.message)}</div>` : ''}<div class="text-3">${r.request_id ? `<span class="mono">${esc(r.request_id)}</span> · ` : ''}${r.at ? esc(fmtDateTime(r.at)) : ''}</div></li>`).join('')}</ul>` : ''}
    ${(c.errors || []).length ? `<div class="section-label mt-3 mb-1">Script errors</div><ul class="sup-log">${c.errors.map((x) => `<li>
      <div class="text-2">${esc(x.message || '')}</div><div class="text-3">${x.source ? `<span class="mono">${esc(x.source)}</span> · ` : ''}${x.at ? esc(fmtDateTime(x.at)) : ''}</div></li>`).join('')}</ul>` : ''}`;
}

function supErrorsHtml(list) {
  if (!list.length) return '<p class="hint m-0">No server errors for this customer around the time of the report.</p>';
  return `<ul class="sup-log">${list.map((e) => `<li class="${e.matches_report ? 'is-match' : ''}">
    <div><span class="fw-600">${esc(e.error_type)}</span>${e.matches_report ? ' <span class="badge badge-danger">Their request</span>' : ''}</div>
    <div class="text-2">${esc(e.message || '')}</div>
    <div class="text-3"><span class="mono">${esc(e.location || '')}</span> · ${esc(fmtDateTime(e.created_at))}${e.request_id ? ` · <span class="mono">${esc(e.request_id)}</span>` : ''}</div></li>`).join('')}</ul>
    <a class="btn btn-ghost btn-sm mt-2" href="#system">${icon('activity', 'ico-sm')}Open System</a>`;
}

function supRenderThread(r) {
  const keepDraft = SUP.thread && SUP.thread.id === r.id ? ($('#sup-reply') || {}).value || '' : '';
  SUP.thread = r;
  const c = r.customer;
  const label = c.email || c.username;
  $('#tb-title').textContent = r.ref;
  setPageTitle(`${r.ref} · ${r.subject}`);
  const noEmail = !c.email;
  $('#sup-thread').innerHTML = `<div class="sup-thread">
    ${supCrumb()}
    <header class="sup-head">
      <div class="min-w-0 grow">
        <h1 class="sup-title">${esc(r.subject)}</h1>
        <div class="cust-badges">${supStatusBadge(r.status)}<span class="mono text-3">${esc(r.ref)}</span>
          <span class="text-3">${esc(SUP_KINDS[r.kind] || r.kind)}</span>${r.impact ? supBadge(SUP_IMPACT, r.impact) : ''}
          <span class="text-3" data-tip="${esc(fmtDateTime(r.created_at))}">Opened ${esc(fmtRelative(r.created_at))}</span></div>
      </div>
      <div class="sup-controls">
        <label class="sr-only" for="sup-status">Status</label>
        <select id="sup-status" class="select input-sm">${Object.entries(SUP_STATUS_LABEL).map(([k, l]) => `<option value="${k}" ${r.status === k ? 'selected' : ''}>${esc(l)}</option>`).join('')}</select>
        <label class="sr-only" for="sup-prio">Priority</label>
        <select id="sup-prio" class="select input-sm"><option value="">No priority</option>${[1, 2, 3].map((p) => `<option value="${p}" ${r.priority === p ? 'selected' : ''}>${esc(SUP_PRIORITY[p][0])}</option>`).join('')}</select>
      </div>
    </header>
    <div class="cust-grid">
      <div class="cust-main">
        <ol class="sup-msgs" aria-label="Conversation">${r.messages.map((m) => supMessageHtml(m, c)).join('')}</ol>
        <form class="card sup-compose ${SUP.mode === 'note' ? 'is-note' : ''}" id="sup-compose" novalidate>
          <div class="card-body">
            <div class="row-between gap-2 mb-2 sup-compose-head">
              <div class="seg" id="sup-mode" aria-label="Message type"><button type="button" class="seg-btn ${SUP.mode === 'reply' ? 'active' : ''}" data-mode="reply">Reply to customer</button><button type="button" class="seg-btn ${SUP.mode === 'note' ? 'active' : ''}" data-mode="note">Internal note</button></div>
              <button type="button" class="btn btn-ghost btn-sm" data-sup="saved" aria-haspopup="menu">${icon('file-text', 'ico-sm')}Saved replies</button>
            </div>
            <label class="sr-only" for="sup-reply">Message</label>
            <textarea id="sup-reply" class="textarea" rows="5" maxlength="8000"></textarea>
            <div class="sup-compose-shots mt-2">${ReportForm.shotsHtml()}</div>
            <div class="sup-compose-foot">
              <span class="hint" id="sup-compose-hint"></span>
              <div class="row gap-2">
                <button type="button" class="btn btn-secondary" data-sup="send-resolve" id="sup-send-resolve">Send and resolve</button>
                <button type="submit" class="btn btn-primary" id="sup-send">Send reply</button>
              </div>
            </div>
          </div>
        </form>
      </div>
      <aside class="cust-side sup-side" aria-label="About this report">
        <section class="card card-pad">
          <div class="section-label mb-2">Customer</div>
          <a class="sup-customer" href="${esc(adminHref('customers', { id: c.id }))}">
            <span class="avatar" aria-hidden="true">${esc(initials(label))}</span>
            <span class="min-w-0"><span class="fw-600 truncate sup-customer-name">${esc(label)}</span>
              <span class="text-3 fs-sm">#${c.id} · since ${esc(fmtDate(c.created_at, { year: true }))}</span></span></a>
          <div class="cust-badges mt-2">${c.billing && c.billing.state ? accessBadge(c.billing.state) : ''}${c.status !== 'active' ? `<span class="badge badge-danger">${esc(c.status)}</span>` : ''}${noEmail ? '<span class="text-warning fs-sm">No email address — replies show in the app only</span>' : ''}</div>
          ${r.other_reports.length ? `<div class="section-label mt-4 mb-1">Their other reports</div><ul class="sup-others">${r.other_reports.map((o) => `<li><a href="#support/${o.id}"><span class="truncate">${esc(o.subject)}</span>${supStatusBadge(o.status)}</a></li>`).join('')}</ul>` : ''}
        </section>
        <section class="card card-pad mt-4"><div class="section-label mb-2">Technical details</div>${supContextHtml(r.context || {})}</section>
        <section class="card card-pad mt-4"><div class="section-label mb-2">Server errors</div>${supErrorsHtml(r.server_errors || [])}</section>
      </aside>
    </div>
  </div>`;
  const form = $('#sup-compose');
  SUP.shots = ReportForm.mountShots($('.sup-compose-shots', form), form);
  $('#sup-reply').value = keepDraft;
  supPaintMode();
  ui.segmented($('#sup-mode'), { onChange: (btn) => { SUP.mode = btn.dataset.mode; supPaintMode(); } });
  form.addEventListener('submit', (e) => { e.preventDefault(); supSend(false); });
  $('#sup-reply').addEventListener('keydown', (e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); supSend(false); } });
}

function supPaintMode() {
  const note = SUP.mode === 'note';
  const r = SUP.thread;
  $('#sup-compose').classList.toggle('is-note', note);
  $('#sup-send').textContent = note ? 'Add note' : 'Send reply';
  $('#sup-send-resolve').hidden = note || r.status === 'resolved';
  $('#sup-reply').placeholder = note ? 'Only administrators see notes. The customer is not told.' : `Write to ${r.customer.username}…`;
  $('#sup-compose-hint').textContent = note ? 'Notes don’t change the status.'
    : r.customer.email ? `Emailed to ${r.customer.email} and shown in their Help page. Sets the report to “Waiting on customer”.` : 'Shown in their Help page. Sets the report to “Waiting on customer”.';
}

async function supSend(resolve) {
  const input = $('#sup-reply');
  const text = input.value.trim();
  if (!text && !SUP.shots.count()) { ui.fieldError(input, 'Write a message first'); input.focus(); return; }
  ui.fieldError(input, null);
  const btn = resolve ? $('#sup-send-resolve') : $('#sup-send');
  await ui.busy(btn, async () => {
    const fd = new FormData();
    fd.append('body', text);
    if (SUP.mode === 'note') fd.append('internal', '1');
    if (resolve) fd.append('resolve', '1');
    SUP.shots.files().forEach((f) => fd.append('files', f, f.name || 'screenshot.png'));
    const r = await apiUpload(`/api/admin/support/reports/${SUP.thread.id}/messages`, fd);
    input.value = '';
    supRenderThread(r);
    supRefreshPill();
    toast(SUP.mode === 'note' ? 'Note added' : resolve ? 'Reply sent and report resolved' : 'Reply sent', { type: 'success' });
  });
}

async function supPatch(body) {
  try {
    const r = await api(`/api/admin/support/reports/${SUP.thread.id}`, { method: 'PATCH', body });
    supRenderThread(r);
    supRefreshPill();
    toast(body.status ? `Marked as ${SUP_STATUS_LABEL[body.status].toLowerCase()}` : 'Priority updated', { type: 'success' });
  } catch (err) {
    ui.errorToast(err);
    supRenderThread(SUP.thread);
  }
}

function supShowShot(btn) {
  const w = Number(btn.dataset.w) || 0, h = Number(btn.dataset.h) || 0;
  ui.modal({
    title: 'Screenshot', size: 'xl', className: 'sup-lightbox', sheet: false,
    html: `<img class="sup-lightbox-img" src="${esc(btn.dataset.url)}" alt="Screenshot from the customer" ${w && h ? `width="${w}" height="${h}"` : ''}>
      <div class="row-between mt-2"><span class="hint">${w && h ? `${w} × ${h}` : ''}</span><a class="btn btn-secondary btn-sm" href="${esc(btn.dataset.url)}" target="_blank" rel="noopener">${icon('external-link', 'ico-sm')}Open full size</a></div>`,
  });
}

async function supSavedMenu(anchor) {
  if (!SUP.savedLoaded) {
    try { SUP.saved = (await api('/api/admin/support/settings')).saved_replies; SUP.savedLoaded = true; } catch { SUP.saved = []; }
  }
  const input = $('#sup-reply');
  const items = SUP.saved.map((s) => ({ label: s.title, onClick: () => {
    const name = (SUP.thread.customer.username || '').split(/[\s@._]/)[0];
    const text = s.body.replace(/\{name\}/g, name).replace(/\{ref\}/g, SUP.thread.ref);
    input.value = input.value.trim() ? `${input.value.trimEnd()}\n\n${text}` : text;
    input.focus();
  } }));
  ui.menu(anchor, [...(items.length ? items : [{ label: 'No saved replies yet', disabled: true }]), { divider: true },
    { label: 'Manage saved replies', icon: 'settings', href: '#settings/support' }]);
}

function supAction(el) {
  const a = el.dataset.sup;
  if (a === 'send-resolve') supSend(true);
  else if (a === 'shot') supShowShot(el);
  else if (a === 'saved') supSavedMenu(el);
}
document.addEventListener('click', (e) => {
  if (e.target.closest('[data-act="sup-reload"]')) supLoadList();
  if (e.target.closest('[data-act="sup-reload-thread"]') && SUP.thread) supOpenThread(SUP.thread.id);
});

/* ---------- Customer › Support tab ---------- */

function supCustomerTab(reports) {
  if (!reports.length) return `<section class="card card-pad">${ui.emptyState({ icon: 'help', title: 'No reports', body: 'Problem reports and questions this customer sends show up here.' })}</section>`;
  return `<section class="card"><ul class="sup-rows">${reports.map((r) => `<li><a class="sup-row" href="#support/${r.id}">
    <span class="sup-row-dot"></span>
    <span class="sup-row-main"><span class="sup-row-subject truncate">${esc(r.subject)}</span>
      <span class="sup-row-meta text-3"><span class="mono">${esc(r.ref)}</span> · ${esc(SUP_KINDS[r.kind] || r.kind)} · updated ${esc(fmtRelative(r.updated_at))}</span></span>
    <span class="sup-row-tags">${supBadge(SUP_PRIORITY, r.priority)}${supStatusBadge(r.status)}</span><span></span></a></li>`).join('')}</ul></section>`;
}

/* ---------- Settings › Support ---------- */

const ASP = { host: null, bar: null, data: null };

AdminSettings.register('support', {
  label: 'Support', icon: 'help',
  sub: 'The contact address customers see in Help, who is told about new reports, and your saved replies',
  load: supLoadSettings,
});

async function supLoadSettings(host) {
  ASP.host = host;
  if (!ASP.bar) {
    ASP.bar = adminDirtyBar('settings/support', { save: supSaveSettings, discard: () => supLoadSettings(ASP.host) });
    host.addEventListener('input', () => ASP.bar.set(true));
    host.addEventListener('change', () => ASP.bar.set(true));
    host.addEventListener('click', (e) => {
      const b = e.target.closest('[data-asp]');
      if (!b) return;
      if (b.dataset.asp === 'add') { ASP.data.saved_replies = supReadSaved().concat([{ title: '', body: '' }]); supPaintSaved(true); ASP.bar.set(true); }
      if (b.dataset.asp === 'remove') { const list = supReadSaved(); list.splice(Number(b.dataset.i), 1); ASP.data.saved_replies = list; supPaintSaved(); ASP.bar.set(true); }
    });
  }
  if (ASP.bar.dirty) return;
  host.innerHTML = ui.skeletonList(3);
  try { ASP.data = await api('/api/admin/support/settings'); } catch (err) { host.innerHTML = ui.errorBox(err.message, { retry: 'reload-admin-support' }); return; }
  const d = ASP.data;
  ASP.bar.set(false);
  host.innerHTML = `
    <section class="settings-section card card-pad">
      <div class="setting-row"><div class="min-w-0"><div class="title">Contact email</div>
        <div class="desc">Shown on customers’ Help page as another way to reach you. Leave empty to offer the report form only.</div></div>
        <input id="asp-email" class="input input-sm asp-email" type="email" autocomplete="off" placeholder="help@example.com" value="${esc(d.support_email)}" aria-label="Contact email"></div>
      <div class="setting-row"><div class="min-w-0"><div class="title">Email me about new reports and replies</div>
        <div class="desc">${d.notify_to.length ? `Sent to ${d.notify_to.map(esc).join(', ')}.` : 'Your admin account has no email address yet, so nothing can be sent. Add one in <a href="#settings/account">My account</a>.'}
          Reports always appear here and on Home either way.</div></div>
        <label class="switch"><input type="checkbox" id="asp-notify" ${d.notify ? 'checked' : ''}><span class="switch-track"></span></label></div>
    </section>
    <section class="settings-section card card-pad">
      ${secHead('Saved replies')}
      <div class="sub">Answers you give often, one click away in every conversation. <span class="mono">{name}</span> becomes the customer’s first name and <span class="mono">{ref}</span> the report number.</div>
      <div id="asp-saved"></div>
      <button type="button" class="btn btn-secondary btn-sm mt-3" data-asp="add">${icon('plus', 'ico-sm')}Add a saved reply</button>
    </section>`;
  supPaintSaved();
}

function supReadSaved() {
  return $$('#asp-saved .asp-saved').map((row) => ({ title: $('.asp-title', row).value, body: $('.asp-body', row).value }));
}

function supPaintSaved(focusLast = false) {
  const list = ASP.data.saved_replies;
  $('#asp-saved').innerHTML = list.length ? list.map((s, i) => `<div class="asp-saved">
    <div class="row gap-2"><label class="sr-only" for="asp-t-${i}">Name</label><input id="asp-t-${i}" class="input input-sm asp-title grow" maxlength="60" placeholder="Name, e.g. Fixed in the latest version" value="${esc(s.title)}">
      <button type="button" class="btn btn-ghost btn-sm btn-icon" data-asp="remove" data-i="${i}" aria-label="Remove saved reply ${i + 1}">${icon('trash', 'ico-sm')}</button></div>
    <label class="sr-only" for="asp-b-${i}">Message</label><textarea id="asp-b-${i}" class="textarea asp-body mt-2" rows="3" maxlength="4000" placeholder="Hi {name}, …">${esc(s.body)}</textarea></div>`).join('')
    : '<p class="hint">No saved replies yet.</p>';
  if (focusLast) { const t = $$('#asp-saved .asp-title').pop(); if (t) t.focus(); }
}

async function supSaveSettings() {
  const d = await api('/api/admin/support/settings', { method: 'PUT', body: {
    support_email: $('#asp-email').value.trim(), notify: $('#asp-notify').checked,
    saved_replies: supReadSaved().filter((s) => s.title.trim() || s.body.trim()) } });
  ASP.data = d;
  SUP.savedLoaded = false;
  toast('Support settings saved', { type: 'success' });
  ASP.bar.set(false);
  return supLoadSettings(ASP.host);
}
document.addEventListener('click', (e) => { if (e.target.closest('[data-act="reload-admin-support"]') && ASP.host) supLoadSettings(ASP.host); });
