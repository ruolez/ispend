/* Admin › Messages: write to one person, a selection, or everyone matching a filter. The composer is
   shared: the people list's bulk bar, the person view and this section all open it. */

const AEM = { saved: null };
const AUDIENCES = [
  ['?status=active', 'Everyone with an active account'],
  ['?state=active', 'Paying customers'],
  ['?state=trialing', 'Everyone on a trial'],
  ['?state=trialing&trial_ending=7', 'Trials ending in the next 7 days'],
  ['?state=grace', 'People whose payment failed'],
  ['?activated=0', 'Signed up but never imported'],
];

AdminPanels.register('messages', {
  label: 'Messages', icon: 'send', group: 'customers',
  sub: 'Email one person, a group, or everyone — with a record of every message sent',
  actions: '<button type="button" class="btn btn-primary" data-act="compose"></button>',
  markup: '<div id="msg-list"></div>',
  load: loadMessages,
});

async function loadMessages() {
  $('[data-act="compose"]').innerHTML = `${icon('mail')}<span class="label">New message</span>`;
  const host = $('#msg-list');
  host.innerHTML = ui.skeletonList(4);
  let rows;
  try { rows = await api('/api/admin/email/campaigns'); } catch (err) { host.innerHTML = ui.errorBox(err.message); return; }
  if (!rows.length) {
    host.innerHTML = `<div class="card">${ui.emptyState({ icon: 'mail', title: 'No messages sent yet', body: 'Write to everyone on a trial, to paying customers, or to anyone you select in Users.', action: { label: 'New message', act: 'compose' } })}</div>`;
    return;
  }
  host.innerHTML = `<div class="tbl-wrap"><table class="tbl tbl--list"><thead><tr><th>Subject</th><th>Kind</th><th>Sent</th>
    <th class="right">To</th><th class="right">Delivered</th><th class="right">Failed</th><th></th></tr></thead><tbody>
    ${rows.map((c) => `<tr><td class="fw-500">${esc(c.subject)}<div class="sub text-3">by ${esc(c.author || 'someone')}</div></td>
      <td><span class="badge ${c.category === 'marketing' ? 'badge-info' : 'badge-neutral'}">${c.category === 'marketing' ? 'News' : 'Account'}</span></td>
      <td class="text-3">${esc(fmtDateTime(c.created_at))}</td>
      <td class="right num">${fmtNumber(c.recipients)}</td><td class="right num">${fmtNumber(c.sent)}</td>
      <td class="right num ${c.failed ? 'text-danger' : ''}">${fmtNumber(c.failed)}</td>
      <td class="right">${c.status === 'sending' ? `<span class="text-3">${fmtNumber(c.pending)} to go</span>
        <button type="button" class="btn btn-ghost btn-xs" data-act="cancel-campaign" data-id="${c.id}">Stop</button>`
        : `<span class="badge ${c.status === 'done' ? 'badge-success' : 'badge-neutral'}">${esc(c.status === 'done' ? 'sent' : c.status)}</span>`}</td></tr>`).join('')}
    </tbody></table></div>`;
  if (rows.some((c) => c.status === 'sending')) setTimeout(() => { if (AdminPanels.current() === 'messages') loadMessages(); }, 4000);
}

/* audience: {ids:[…]} or {query:'?…'}; label says who that is in words. */
function openComposer({ audience = null, label = '' } = {}) {
  const free = !audience;
  const m = ui.modal({
    title: 'New message',
    size: 'lg',
    html: `<form id="cmp-form" novalidate>
      <div class="field"><label for="cmp-to">To</label>${free
        ? `<select id="cmp-to" class="select">${AUDIENCES.map(([q, l]) => `<option value="${esc(q)}">${esc(l)}</option>`).join('')}</select>`
        : `<div class="input cmp-to" id="cmp-to-fixed">${esc(label)}</div>`}</div>
      <div class="field"><span class="label">What is it about?</span>
        <div class="seg cmp-kind" role="radiogroup" aria-label="Kind of message">
          <button type="button" class="seg-btn active" role="radio" aria-checked="true" data-kind="service">Their account</button>
          <button type="button" class="seg-btn" role="radio" aria-checked="false" data-kind="marketing">News or offers</button></div>
        <div class="hint" id="cmp-kind-hint"></div></div>
      <div class="field"><label for="cmp-subject">Subject</label><input id="cmp-subject" class="input" maxlength="200"></div>
      <div class="field"><label for="cmp-body">Message</label><textarea id="cmp-body" class="input cmp-body" rows="9" maxlength="20000"></textarea>
        <div class="hint">Plain text. <code>{name}</code>, <code>{trial_end}</code> and <code>{app_link}</code> are filled in for each person.</div></div>
      <div class="cmp-preview" id="cmp-preview" aria-live="polite"></div>
      <div class="row gap-2 mt-2"><button type="button" class="btn btn-ghost btn-sm" data-cmp="saved">${icon('star', 'ico-sm')}Saved messages</button>
        <button type="button" class="btn btn-ghost btn-sm" data-cmp="save">Save this message</button></div>
    </form>`,
    actions: [{ label: 'Cancel' },
      { label: 'Send a test to me', onClick: async () => { await sendMessage(true); return false; } },
      { label: 'Send', primary: true, onClick: async () => sendMessage(false) }],
  });
  let kind = 'service';
  const who = () => (free ? { query: $('#cmp-to', m.el).value } : audience);
  const payload = () => ({ subject: $('#cmp-subject', m.el).value.trim(), body: $('#cmp-body', m.el).value,
    category: kind, audience: who() });
  const paintKind = () => {
    $('#cmp-kind-hint', m.el).textContent = kind === 'marketing'
      ? 'Sent only to confirmed addresses that have not unsubscribed, with a one-click unsubscribe link.'
      : 'For things they need to know about their account or the service. Sent to everyone addressed.';
  };
  paintKind();
  ui.segmented($('.cmp-kind', m.el), { onChange: (b) => { kind = b.dataset.kind; paintKind(); preview(); } });

  let seq = 0;
  async function preview() {
    const my = ++seq;
    const p = payload();
    const box = $('#cmp-preview', m.el);
    if (!p.subject || !p.body.trim()) { box.innerHTML = '<div class="hint">A preview of the first message appears here.</div>'; return; }
    try {
      const r = await api('/api/admin/email/preview', { method: 'POST', body: p });
      if (my !== seq) return;
      const skipped = Object.entries(r.skipped || {}).map(([k, n]) => `${fmtNumber(n)} ${SKIP_WORDS[k] || k}`).join(', ');
      box.innerHTML = `
        ${r.configured ? '' : `<div class="notice notice-warning mb-2">${icon('alert-triangle')}<div class="grow">Email is not set up, so nothing would actually be delivered.</div></div>`}
        <div class="row-between mb-2"><b>${esc(people(r.recipients))} will get this</b><span class="text-3">${skipped ? `left out: ${esc(skipped)}` : ''}</span></div>
        ${r.big ? `<div class="notice notice-warning mb-2">${icon('alert-triangle')}<div class="grow">That is a lot of email at once. Make sure it is worth their attention — spam complaints hurt delivery for everyone.</div></div>` : ''}
        ${r.sample ? `<div class="cmp-sample"><div class="text-3">To ${esc(r.sample.to)}</div><div class="fw-500 mb-2">${esc(r.sample.subject)}</div><pre>${esc(r.sample.text)}</pre></div>` : ''}`;
    } catch (err) {
      if (my === seq) box.innerHTML = `<div class="hint text-danger">${esc(err.message)}</div>`;
    }
  }
  const debounced = debounce(preview, 400);
  m.el.addEventListener('input', debounced);
  m.el.addEventListener('change', debounced);
  preview();

  async function sendMessage(test) {
    const p = payload();
    if (!p.subject) { ui.fieldError($('#cmp-subject', m.el), 'Add a subject'); return false; }
    if (!p.body.trim()) { ui.fieldError($('#cmp-body', m.el), 'Write a message'); return false; }
    if (test) {
      const r = await api('/api/admin/email/send', { method: 'POST', body: { ...p, test: true } });
      toast(r.ok ? `Test sent to ${r.to}` : `The test did not go: ${r.error}`, { type: r.ok ? 'success' : 'error' });
      return false;
    }
    const single = !free && audience.ids && audience.ids.length === 1;
    const r = single
      ? await api(`/api/admin/users/${audience.ids[0]}/email`, { method: 'POST', body: p })
      : await api('/api/admin/email/send', { method: 'POST', body: p });
    toast(single ? 'Message on its way' : `Sending to ${people(r.recipients)}`, { type: 'success' });
    if (AdminPanels.current() === 'messages') loadMessages();
    return undefined;
  }

  m.el.addEventListener('click', async (e) => {
    const b = e.target.closest('[data-cmp]');
    if (!b) return;
    if (!AEM.saved) AEM.saved = await api('/api/admin/email/templates').catch(() => []);
    if (b.dataset.cmp === 'save') {
      const p = payload();
      const name = p.subject || 'Untitled';
      AEM.saved = await api('/api/admin/email/templates', { method: 'PUT', body: { templates: [...AEM.saved.filter((t) => t.name !== name), { name, ...p }].slice(-20) } });
      toast('Saved for next time', { type: 'success' });
      return;
    }
    ui.menu(b, AEM.saved.length ? AEM.saved.map((t) => ({ label: t.name, onClick: () => {
      $('#cmp-subject', m.el).value = t.subject;
      $('#cmp-body', m.el).value = t.body;
      const btn = m.el.querySelector(`[data-kind="${t.category}"]`);
      if (btn) btn.click();
      preview();
    } })) : [{ label: 'Nothing saved yet', disabled: true }]);
  });
}

const SKIP_WORDS = { no_email: 'without an email address', not_active: 'locked or deleted', unsubscribed: 'unsubscribed',
  unconfirmed: 'not confirmed' };

document.addEventListener('click', async (e) => {
  const el = e.target.closest('[data-act="compose"], [data-act="cancel-campaign"]');
  if (!el) return;
  if (el.dataset.act === 'compose') { openComposer(); return; }
  if (await ui.confirm({ title: 'Stop sending?', body: 'People who have not received it yet will not. Those who have, have.', confirmText: 'Stop' })) {
    await api(`/api/admin/email/campaigns/${el.dataset.id}/cancel`, { method: 'POST' });
    loadMessages();
  }
});
