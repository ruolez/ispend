/* Help: searchable answers, ways to reach the iSpend team, the customer's reports and each
   report's conversation (?report=<id>, which is also the link in every support email). */
const help = { topic: 'all', q: '', reports: null, thread: null, shots: null };
const KIND_LABEL = { bug: 'Problem', data: 'Numbers', question: 'Question', idea: 'Idea', billing: 'Billing' };

initNav('help').then(async () => {
  const reportsLoad = store.page('help:reports', '/api/support/reports', renderReports);
  reportsLoad.catch((err) => { if (!err.shown) $('#reports-list').innerHTML = `<div class="card-body">${ui.errorBox(err.message, { retry: 'reload-reports' })}</div>`; });
  apiShared('/api/support/meta').then(renderMeta).catch(() => {});
  $('#help-search-ico').innerHTML = icon('search');
  renderTopics();
  renderFaq();
  renderReleases();
  wire();
  const q = qs();
  if (q.report) openThread(Number(q.report), { replace: true });
  else if (q.new) { history.replaceState(null, '', location.pathname + location.hash); openReport({ kind: q.new }); }
  else revealAnswer(location.hash);
  window.addEventListener('ispend:resume', () => {
    if (store.pageStale('help:reports')) reloadReports();
    if (help.thread) loadThread(help.thread.id);
  });
});

function wire() {
  document.body.addEventListener('click', (e) => {
    const act = e.target.closest('[data-act]');
    if (!act) return;
    const a = act.dataset.act;
    if (a === 'report') openReport({ kind: act.dataset.kind || 'bug' });
    else if (a === 'topic') { help.topic = act.dataset.topic; renderTopics(); renderFaq(); }
    else if (a === 'open-report') { e.preventDefault(); openThread(Number(act.dataset.id)); }
    else if (a === 'back') { e.preventDefault(); closeThread(); }
    else if (a === 'resolve') resolveThread(act);
    else if (a === 'follow-up') openReport({ kind: help.thread.kind, subject: `Follow-up to ${help.thread.ref}` });
    else if (a === 'reload-reports') reloadReports();
    else if (a === 'clear-search') { $('#help-q').value = ''; help.q = ''; renderFaq(); $('#help-q').focus(); }
  });
  $('#help-q').addEventListener('input', debounce((e) => { help.q = e.target.value.trim(); renderFaq(); }, 120));
  window.addEventListener('ispend:report-sent', () => reloadReports());
  window.addEventListener('ispend:open-report', (e) => openThread(e.detail.id));
  window.addEventListener('popstate', () => {
    const id = Number(qs().report || 0);
    if (id) openThread(id, { fromHistory: true }); else closeThread({ fromHistory: true });
  });
  window.addEventListener('hashchange', () => revealAnswer(location.hash));
}

/* ---------- answers ---------- */
function renderTopics() {
  const chips = [{ id: 'all', label: 'All topics' }, ...HELP_TOPICS];
  $('#help-topics').innerHTML = chips.map((t) => `<button type="button" class="chip${help.topic === t.id ? ' active' : ''}" data-act="topic" data-topic="${t.id}" aria-pressed="${help.topic === t.id}">${t.icon ? icon(t.icon, 'ico-sm') : ''}${esc(t.label)}</button>`).join('');
}

function faqMatchesQuery(f, words) {
  const hay = `${f.q} ${f.a.replace(/<[^>]+>/g, '')} ${f.k}`.toLowerCase();
  return words.every((w) => hay.includes(w));
}

function renderFaq() {
  const words = help.q.toLowerCase().split(/\s+/).filter(Boolean);
  const items = HELP_FAQ.filter((f) => (help.topic === 'all' || f.topic === help.topic) && (!words.length || faqMatchesQuery(f, words)));
  const host = $('#faq-list');
  $('#faq-live').textContent = words.length ? `${plural(items.length, 'answer')} found` : '';
  if (!items.length) {
    host.innerHTML = `<div class="card"><div class="empty">
      <div class="empty-icon">${icon('search')}</div>
      <div class="empty-title">No answers match “${esc(help.q)}”</div>
      <div class="empty-body">Try other words, or ask us directly — a person reads every report.</div>
      <div class="row gap-2 mt-2"><button type="button" class="btn btn-secondary" data-act="clear-search">Clear search</button>
      <button type="button" class="btn btn-primary" data-act="report" data-kind="question">Ask a question</button></div></div></div>`;
    return;
  }
  const groups = help.topic === 'all' && !words.length
    ? HELP_TOPICS.map((t) => ({ t, list: items.filter((f) => f.topic === t.id) })).filter((g) => g.list.length)
    : [{ t: null, list: items }];
  host.innerHTML = groups.map(({ t, list }) => `
    ${t ? `<h3 class="faq-topic" id="topic-${t.id}">${icon(t.icon, 'ico-sm')}${esc(t.label)}</h3>` : ''}
    <div class="card faq-card">${list.map((f) => `<details class="faq-item" id="faq-${f.id}" ${words.length && items.length <= 3 ? 'open' : ''}>
      <summary><span class="grow">${esc(f.q)}</span>${icon('chevron-down', 'ico-sm faq-chev')}</summary>
      <div class="faq-a">${f.a}</div></details>`).join('')}</div>`).join('');
}

function revealAnswer(hash) {
  const m = /^#faq-([\w-]+)$/.exec(hash || '');
  if (!m) return;
  const el = document.getElementById(`faq-${m[1]}`);
  if (!el) return;
  el.open = true;
  requestAnimationFrame(() => el.scrollIntoView({ block: 'center' }));
}

function renderReleases() {
  $('#help-releases').innerHTML = HELP_RELEASES.slice(0, 4).map((r) => `<li class="help-release">
    <time datetime="${esc(r.date)}" class="text-3 fs-sm">${esc(fmtDate(r.date, { year: true }))}</time>
    <div class="fw-600">${esc(r.title)}</div><div class="text-2 fs-sm">${esc(r.body)}</div></li>`).join('');
}

function renderMeta(meta) {
  if (!meta) return;
  if (meta.support_email) {
    const el = $('#help-email');
    el.innerHTML = `Prefer email? Write to <a href="mailto:${esc(meta.support_email)}">${esc(meta.support_email)}</a>.`;
    el.hidden = false;
  }
  $('#help-version').textContent = `iSpend ${meta.version}`;
}

/* ---------- the customer's reports ---------- */
async function reloadReports() {
  try { await store.page('help:reports', '/api/support/reports', renderReports); } catch (err) { toast(err.message, { type: 'error' }); }
}

function renderReports(list) {
  help.reports = list;
  const host = $('#reports-list');
  const unread = list.filter((r) => r.unread).length;
  $('#reports-count').textContent = unread ? `${plural(unread, 'new reply', 'new replies')}` : (list.length ? plural(list.length, 'report') : '');
  if (!list.length) {
    host.innerHTML = `<div class="help-reports-empty text-3">Nothing sent yet. When you report something, you can follow it here.</div>`;
    return;
  }
  host.innerHTML = `<ul class="help-report-list">${list.slice(0, 8).map((r) => `<li>
    <a class="help-report${r.unread ? ' is-unread' : ''}" href="/help.html?report=${r.id}" data-act="open-report" data-id="${r.id}">
      <span class="help-report-main"><span class="help-report-subject truncate">${r.unread ? '<span class="dot help-unread-dot" aria-hidden="true"></span>' : ''}${esc(r.subject)}</span>
      <span class="help-report-meta text-3 fs-sm"><span class="mono">${esc(r.ref)}</span> · ${esc(fmtRelative(r.updated_at))}${r.unread ? ' · <b class="text-accent">New reply</b>' : ''}</span></span>
      ${ReportForm.statusBadge(r.status)}</a></li>`).join('')}</ul>
    ${list.length > 8 ? `<div class="card-foot text-3 fs-sm">Showing the 8 most recent of ${list.length}.</div>` : ''}`;
}

/* ---------- one report's conversation ---------- */
function openThread(id, { replace = false, fromHistory = false } = {}) {
  if (!id) return;
  if (!fromHistory) {
    const url = `${location.pathname}?report=${id}`;
    if (replace) history.replaceState({ report: id }, '', url); else history.pushState({ report: id }, '', url);
  }
  $('#help-home').hidden = true;
  const host = $('#help-thread');
  host.hidden = false;
  host.innerHTML = `<div class="page-head"><div><a class="help-back" href="/help.html" data-act="back">${icon('chevron-left', 'ico-sm')}Help</a>
    <h1>${ui.skeleton(260, 24)}</h1></div></div><div class="card"><div class="card-body">${ui.skeletonList(3)}</div></div>`;
  window.scrollTo(0, 0);
  loadThread(id);
}

function closeThread({ fromHistory = false } = {}) {
  if (!fromHistory && help.thread) history.pushState(null, '', location.pathname);
  help.thread = null;
  help.shots = null;
  $('#help-thread').hidden = true;
  $('#help-thread').innerHTML = '';
  $('#help-home').hidden = false;
  setPageTitle('');
  reloadReports();
}

async function loadThread(id) {
  try {
    const report = await api(`/api/support/reports/${id}`);
    renderThread(report);
    refreshSupportPill();
  } catch (err) {
    $('#help-thread').innerHTML = `<div class="page-head"><div><a class="help-back" href="/help.html" data-act="back">${icon('chevron-left', 'ico-sm')}Help</a><h1>Report</h1></div></div>
      ${err.status === 404 ? ui.emptyState({ icon: 'help', title: 'We can’t find that report', body: 'It may belong to a different account.' }) : ui.errorBox(err.message)}`;
  }
}

function statusNotice(r) {
  if (r.status === 'waiting') return `<div class="notice notice-info">${icon('info')}<div>The iSpend team answered and is waiting to hear back from you.</div></div>`;
  if (r.status === 'resolved' && r.can_reopen) return `<div class="notice">${icon('check-circle')}<div>This report is resolved. If something still isn’t right, reply below and it reopens.</div></div>`;
  if (r.status === 'resolved') return `<div class="notice">${icon('check-circle')}<div>This report was resolved a while ago. Start a new one and we’ll link them up.</div><button type="button" class="btn btn-secondary btn-sm" data-act="follow-up">New report</button></div>`;
  return '';
}

function messageHtml(m) {
  const mine = m.author_role === 'user';
  return `<li class="msg ${mine ? 'msg--me' : 'msg--team'}">
    <div class="msg-head"><span class="msg-who">${mine ? 'You' : `<span class="msg-mark">${brandMark()}</span><span>iSpend team</span>`}</span>
      <time class="text-3 fs-sm" datetime="${esc(m.created_at)}">${esc(fmtDateTime(m.created_at))}</time></div>
    ${m.body === '(screenshot)' && m.attachments.length ? '' : `<div class="msg-body">${esc(m.body)}</div>`}
    ${m.attachments.length ? `<div class="msg-shots">${m.attachments.map((a, i) => `<a class="msg-shot" href="${esc(a.url)}" target="_blank" rel="noopener">
      <img src="${esc(a.url)}" alt="Screenshot ${i + 1}" loading="lazy" width="${a.width}" height="${a.height}"></a>`).join('')}</div>` : ''}
  </li>`;
}

function renderThread(r) {
  help.thread = r;
  setPageTitle(r.ref);
  const canReply = r.status !== 'resolved' || r.can_reopen;
  const host = $('#help-thread');
  host.innerHTML = `
    <div class="page-head">
      <div><a class="help-back" href="/help.html" data-act="back">${icon('chevron-left', 'ico-sm')}Help</a>
        <h1 class="help-thread-title">${esc(r.subject)}</h1>
        <div class="page-sub help-thread-sub">${ReportForm.statusBadge(r.status)}<span class="help-thread-facts"><span class="mono">${esc(r.ref)}</span> · ${esc(KIND_LABEL[r.kind] || r.kind)} · Opened ${esc(fmtDate(r.created_at, { year: true }))}</span></div></div>
      <div class="page-actions">${r.status !== 'resolved' ? '<button type="button" class="btn btn-secondary" data-act="resolve">Mark as resolved</button>' : ''}</div>
    </div>
    ${statusNotice(r)}
    <ol class="msgs" aria-label="Conversation">${r.messages.map(messageHtml).join('')}</ol>
    ${canReply ? `<form class="card reply" id="reply-form" novalidate>
      <div class="card-body">
        <div class="field"><label for="reply-body">${r.status === 'resolved' ? 'Reply to reopen this report' : 'Add a reply'}</label>
          <textarea id="reply-body" class="textarea" rows="3" maxlength="8000" placeholder="${r.status === 'waiting' ? 'Answer the team’s question, or add anything new' : 'Add details, or tell us it’s sorted'}"></textarea></div>
        <div class="reply-shots">${ReportForm.shotsHtml()}</div>
        <div class="reply-foot"><span class="hint">Please don’t include passwords or full account numbers.</span>
          <button type="submit" class="btn btn-primary" id="reply-send">${r.status === 'resolved' ? 'Send and reopen' : 'Send reply'}</button></div>
      </div></form>` : ''}`;
  if (canReply) {
    const form = $('#reply-form');
    help.shots = ReportForm.mountShots($('.reply-shots', form), form);
    form.addEventListener('submit', (e) => { e.preventDefault(); sendReply(); });
    $('#reply-body').addEventListener('keydown', (e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); sendReply(); } });
  }
}

async function sendReply() {
  const input = $('#reply-body');
  const text = input.value.trim();
  if (!text && !help.shots.count()) { ui.fieldError(input, 'Write a message or add a screenshot'); input.focus(); return; }
  ui.fieldError(input, null);
  await ui.busy($('#reply-send'), async () => {
    const fd = new FormData();
    fd.append('body', text);
    help.shots.files().forEach((f) => fd.append('files', f, f.name || 'screenshot.png'));
    const r = await apiUpload(`/api/support/reports/${help.thread.id}/messages`, fd);
    store.afterWrite(`/api/support/reports/${help.thread.id}/messages`);
    renderThread(r);
    toast('Reply sent', { type: 'success' });
    const last = $$('.msgs .msg').pop();
    if (last) last.scrollIntoView({ block: 'nearest' });
  });
}

async function resolveThread(btn) {
  await ui.busy(btn, async () => {
    const r = await api(`/api/support/reports/${help.thread.id}/resolve`, { method: 'POST' });
    renderThread(r);
    toast('Marked as resolved — thanks for letting us know', { type: 'success' });
  });
}
