/* Admin › Settings › Sign-ups (who may create an account, and whether they confirm their address)
   and › Email delivery (the SMTP server every email goes out through). Both read and write
   /api/admin/billing/config, each sending only its own keys. */

const ASU = { config: null, bar: null };
const AED = { config: null, bar: null };

AdminSettings.register('signups', {
  label: 'Sign-ups', icon: 'user',
  sub: 'Who can create an account, and whether they must confirm their email first',
  load: loadAdminSignups,
});

AdminSettings.register('email', {
  label: 'Email delivery', icon: 'mail',
  sub: 'The mail server iSpend sends confirmations, receipts and reminders through',
  load: loadAdminEmail,
});

const SMTP_KEYS = ['smtp_host', 'smtp_port', 'smtp_security', 'smtp_user', 'smtp_password',
                   'smtp_from_email', 'smtp_from_name'];

const SMTP_LABELS = {
  smtp_host: 'Host', smtp_port: 'Port', smtp_security: 'Security', smtp_user: 'Username',
  smtp_password: 'Password', smtp_from_email: 'From address', smtp_from_name: 'From name',
};

const SMTP_HINTS = {
  smtp_host: 'Your provider’s SMTP hostname.',
  smtp_port: '587 for STARTTLS, 465 for SSL.',
  smtp_from_email: 'Must be an address your provider lets you send from.',
};

async function loadConfigInto(state, host, retry) {
  host.innerHTML = ui.skeletonList(3);
  try {
    state.config = await api('/api/admin/billing/config');
    return true;
  } catch (err) {
    host.innerHTML = ui.errorBox(err.message, { retry });
    return false;
  }
}

/* ---------- Sign-ups ---------- */

async function loadAdminSignups(host) {
  ASU.host = host;
  if (!ASU.bar) {
    ASU.bar = adminDirtyBar('settings/signups', { save: saveAdminSignups, discard: () => loadAdminSignups(ASU.host) });
    host.addEventListener('change', () => ASU.bar.set(true));
  }
  if (ASU.bar.dirty || !(await loadConfigInto(ASU, host, 'reload-admin-signups'))) return;
  const c = ASU.config;
  const requireOn = c.signup_require_verification.value;
  ASU.bar.set(false);
  host.innerHTML = `
    <section class="settings-section card card-pad">
      <div class="setting-row"><div class="min-w-0"><div class="title">Accept new sign-ups</div>
        <div class="desc">When off, the sign-up page is closed and only you can add customers.</div></div>
        <label class="switch"><input type="checkbox" id="as-signup" ${c.signup_enabled.value ? 'checked' : ''}>
          <span class="switch-track"></span></label></div>
      <div class="setting-row"><div class="min-w-0"><div class="title">Require email confirmation</div>
        <div class="desc">New customers must open the link we email them before they can sign in.
          ${c.email_configured ? '' : '<b>Email delivery is not set up yet, so this has no effect.</b>'}
          Turning it off lets anyone still waiting on a link sign in straight away.</div></div>
        <label class="switch"><input type="checkbox" id="as-require-verify" ${requireOn ? 'checked' : ''}>
          <span class="switch-track"></span></label></div>
    </section>
    ${c.email_configured ? '' : `<div class="notice notice-info">${icon('info')}<div class="grow">Set up email delivery so confirmation links and password resets reach people.</div>
      <a class="btn btn-secondary btn-sm" href="#settings/email">Email delivery</a></div>`}`;
}

async function saveAdminSignups() {
  await api('/api/admin/billing/config', { method: 'PUT', body: {
    signup_enabled: $('#as-signup').checked, signup_require_verification: $('#as-require-verify').checked } });
  toast('Sign-up settings saved', { type: 'success' });
  ASU.bar.set(false);
  return loadAdminSignups(ASU.host);
}

/* ---------- Email delivery ---------- */

async function loadAdminEmail(host) {
  AED.host = host;
  if (!AED.bar) {
    AED.bar = adminDirtyBar('settings/email', { save: saveAdminEmail, discard: () => loadAdminEmail(AED.host) });
    const onEdit = (e) => { if (!e.target.closest('#as-test-to')) AED.bar.set(true); };
    host.addEventListener('input', onEdit);
    host.addEventListener('change', onEdit);
  }
  if (AED.bar.dirty || !(await loadConfigInto(AED, host, 'reload-admin-email'))) return;
  const c = AED.config;
  AED.bar.set(false);
  host.innerHTML = `
    <section class="settings-section card card-pad">
      ${secHead('Mail server', c.email_configured ? ['Working', 'badge-success'] : ['Not set up', 'badge-neutral'])}
      <div class="sub">Used for sign-up confirmations, welcome, trial-ending, payment-failed and password-reset
        messages, and anything you send from Messages. Leave empty to send nothing; Stripe still emails receipts.</div>
      ${SMTP_KEYS.map((k) => configField('as', k, c[k], SMTP_LABELS, SMTP_HINTS)).join('')}
    </section>
    <section class="settings-section card card-pad">
      ${secHead('Send a test email')}
      <div class="sub">Uses the settings as they are saved, not what is typed above.</div>
      <div class="row gap-2">
        <input id="as-test-to" class="input input-sm grow" type="email" placeholder="you@example.com" aria-label="Send a test email to" value="${esc((ADMIN.me || {}).email || '')}">
        <button type="button" class="btn btn-secondary btn-sm" data-act="send-test-email">Send</button>
      </div>
      <div id="as-test-result" class="hint mt-2"></div>
    </section>`;
}

async function saveAdminEmail() {
  await api('/api/admin/billing/config', { method: 'PUT', body: collectConfigFields('as', SMTP_KEYS) });
  toast('Email settings saved', { type: 'success' });
  AED.bar.set(false);
  return loadAdminEmail(AED.host);
}

document.addEventListener('click', async (e) => {
  const el = e.target.closest('[data-act]');
  if (!el) return;
  switch (el.dataset.act) {
    case 'reload-admin-signups': loadAdminSignups(ASU.host); break;
    case 'reload-admin-email': loadAdminEmail(AED.host); break;
    case 'send-test-email':
      await ui.busy(el, async () => {
        const r = await api('/api/admin/billing/email/test', { method: 'POST', body: { to: $('#as-test-to').value.trim() } });
        $('#as-test-result').innerHTML = r.ok
          ? `<span class="chip chip-ok">${icon('check')}Sent</span>`
          : `<span class="chip chip-err">${icon('alert-circle')}${esc(r.error || 'Failed')}</span>`;
      });
      break;
    default: break;
  }
});
