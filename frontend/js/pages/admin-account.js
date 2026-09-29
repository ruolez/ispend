/* Admin › Settings › My account: the admin's own sign-in — email for alerts and resets, password,
   other sessions and recent sign-ins — and, once, the finance data left on it from before admin
   accounts stopped using the app. */

const AAC = {};

AdminSettings.register('account', {
  label: 'My account', icon: 'user',
  sub: 'Your sign-in, the address security alerts go to, and where you are signed in',
  load: loadAdminAccount,
});

async function loadAdminAccount(host) {
  AAC.host = host;
  host.innerHTML = ui.skeletonList(4);
  let me, logins, leftover;
  try {
    [me, logins, leftover] = await Promise.all([api('/api/auth/me'), api('/api/admin/me/logins'), api('/api/admin/me/leftover-data')]);
  } catch (err) {
    host.innerHTML = ui.errorBox(err.message, { retry: 'reload-admin-account' });
    return;
  }
  AAC.me = me;
  const login = (l) => `<tr><td>${esc(fmtDateTime(l.created_at))}</td>
    <td>${l.ok ? '<span class="text-success">Signed in</span>' : `<span class="text-danger">Failed · ${esc((l.reason || '').replace('_', ' '))}</span>`}${l.kind !== 'password' ? ` <span class="text-3">(${esc(l.kind.replace('_', ' '))})</span>` : ''}${l.new_network ? ' <span class="badge badge-warning">new network</span>' : ''}</td>
    <td class="text-3">${esc([l.browser, l.os].filter(Boolean).join(' on ') || l.device || '—')}</td>
    <td class="text-3 num">${esc(l.ip || '—')}${l.country ? ` · ${esc(l.country)}` : ''}</td></tr>`;
  host.innerHTML = `
    ${leftover.any ? leftoverCard(leftover.counts) : ''}
    <section class="settings-section card card-pad">
      <div class="setting-row"><div class="min-w-0"><div class="title">Username</div><div class="desc">What you sign in with.</div></div>
        <div class="text-1 fw-500">${esc(me.username)}</div></div>
      <div class="setting-row"><div class="min-w-0"><div class="title">Email</div>
        <div class="desc">${me.email ? `${esc(me.email)} · ${me.email_verified ? 'confirmed' : '<span class="text-warning">not confirmed yet</span>'}` : '<span class="text-warning">None.</span> Add one so you get security alerts and can reset a forgotten password.'}</div></div>
        <button type="button" class="btn btn-secondary btn-sm" data-act="acct-email">${me.email ? 'Change' : 'Add email'}</button></div>
      <div class="setting-row"><div class="min-w-0"><div class="title">Password</div><div class="desc">Changing it signs out every other browser.</div></div>
        <button type="button" class="btn btn-secondary btn-sm" data-act="acct-password">Change password</button></div>
      <div class="setting-row"><div class="min-w-0"><div class="title">Other sessions</div><div class="desc">Sign out every other browser and phone signed in as you. This one stays signed in.</div></div>
        <button type="button" class="btn btn-secondary btn-sm" data-act="acct-sign-out-others">Sign out everywhere else</button></div>
    </section>
    <section class="settings-section card">
      <header class="card-head"><h2>Recent sign-ins</h2></header>
      ${logins.length ? `<div class="tbl-wrap"><table class="tbl tbl--list"><thead><tr><th>When</th><th>Result</th><th>Device</th><th>Address</th></tr></thead>
        <tbody>${logins.map(login).join('')}</tbody></table></div>` : `<div class="card-body hint">No sign-ins recorded yet.</div>`}
    </section>`;
}

function leftoverCard(c) {
  const parts = [['transactions', 'transaction'], ['statements', 'statement'], ['accounts', 'account'], ['rules', 'rule'],
    ['budgets', 'budget'], ['categories', 'category']].filter(([k]) => c[k]).map(([k, w]) => plural(c[k], w, k === 'categories' ? 'categories' : undefined));
  return `<section class="settings-section card card-pad cust-danger" id="acct-leftover">
    <div class="row-between gap-4 wrap"><div class="min-w-0"><div class="title">Finance data on this admin account</div>
      <div class="desc">This account still holds ${esc(parts.join(', '))} from before admin accounts stopped using the app.
        Nobody can see or use them any more. <a href="#backup">Make a backup</a> first if you might want them.</div></div>
      <button type="button" class="btn btn-danger-solid" data-act="acct-wipe">Delete this data…</button></div>
  </section>`;
}

function changeOwnEmail() {
  const me = AAC.me;
  const m = ui.modal({
    title: me.email ? 'Change your email' : 'Add your email',
    html: `<div class="field"><label for="ae-email">Email</label><input id="ae-email" class="input" type="email" value="${esc(me.email || '')}" autofocus></div>
      <div class="field"><label for="ae-pw">Your password</label><input id="ae-pw" class="input" type="password" autocomplete="current-password">
      <div class="hint">We send a link to confirm the new address.</div></div>`,
    actions: [{ label: 'Cancel' }, { label: 'Save', primary: true, onClick: async () => {
      await api('/api/auth/me/email', { method: 'PUT', body: { email: $('#ae-email', m.el).value.trim(), current_password: $('#ae-pw', m.el).value } });
      toast('Email saved — check your inbox for the confirmation link', { type: 'success' });
      loadAdminAccount(AAC.host);
    } }],
  });
}

async function wipeLeftover() {
  const ok = await ui.confirmTyped({
    title: 'Delete the finance data on this admin account?',
    body: 'Every account, statement, transaction, rule, budget and category on this admin account is deleted, with the uploaded files. Your sign-in and the activity log stay. This cannot be undone.',
    phrase: 'delete', confirmText: 'Delete the data',
  });
  if (!ok) return;
  const r = await api('/api/admin/me/leftover-data/wipe', { method: 'POST', body: { confirm: 'delete' } });
  toast(`Deleted ${plural(r.removed.transactions || 0, 'transaction')} and everything with them`, { type: 'success' });
  loadAdminAccount(AAC.host);
}

document.addEventListener('click', async (e) => {
  const el = e.target.closest('[data-act^="acct-"], [data-act="reload-admin-account"]');
  if (!el) return;
  switch (el.dataset.act) {
    case 'reload-admin-account': loadAdminAccount(AAC.host); break;
    case 'acct-email': changeOwnEmail(); break;
    case 'acct-password': openChangePassword(); break;
    case 'acct-wipe': wipeLeftover(); break;
    case 'acct-sign-out-others':
      if (await ui.confirm({ title: 'Sign out everywhere else?', body: 'Every other browser and phone signed in as you has to sign in again.', confirmText: 'Sign out others' })) {
        await api('/api/admin/me/sign-out-others', { method: 'POST' });
        toast('Every other session was signed out', { type: 'success' });
      }
      break;
    default: break;
  }
});
