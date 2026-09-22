/* Who may create an account, whether they must confirm their address, and the SMTP settings every
   email goes out through. Mounted into the Admin page; shares its config endpoint with Billing. */

const ASU = { config: null, bar: null };

AdminPanels.register('signups', {
  label: 'Sign-ups & email', icon: 'mail',
  sub: 'Who can create an account, and the email iSpend sends',
  load: loadAdminSignups,
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

async function loadAdminSignups(host) {
  ASU.host = host;
  if (!ASU.wired) {
    ASU.wired = true;
    ASU.bar = adminDirtyBar('signups', { saveAct: 'save-admin-signups', discardAct: 'discard-admin-signups',
                                         unsavedText: 'Sign-up settings were not saved' });
    const onEdit = (e) => { if (!e.target.closest('#as-test-to')) ASU.bar.set(true); };
    host.addEventListener('input', onEdit);
    host.addEventListener('change', onEdit);
  }
  host.innerHTML = ui.skeletonList(3);
  try {
    ASU.config = await api('/api/admin/billing/config');
  } catch (err) {
    host.innerHTML = ui.errorBox(err.message, { retry: 'reload-admin-signups' });
    return;
  }
  renderAdminSignups();
}

function renderAdminSignups() {
  const c = ASU.config;
  const requireOn = c.signup_require_verification.value;
  ASU.bar.set(false);
  ASU.host.innerHTML = `
    <section class="settings-section">
      ${secHead('Sign-ups', requireOn && (c.email_configured ? ['Confirmation enforced', 'badge-success'] : ['Waiting for email', 'badge-warning']))}
      <div class="setting-row"><div class="min-w-0"><div class="title">Accept new sign-ups</div>
        <div class="desc">When off, /signup.html is closed and only an admin can create accounts.</div></div>
        <label class="switch"><input type="checkbox" id="as-signup" ${c.signup_enabled.value ? 'checked' : ''}>
          <span class="switch-track"></span></label></div>
      <div class="setting-row"><div class="min-w-0"><div class="title">Require email confirmation</div>
        <div class="desc">New accounts must open the link we email them before they can sign in.
          Needs email set up below — until it is, the switch has no effect. Turning it off lets
          accounts still waiting on a link sign in straight away.</div></div>
        <label class="switch"><input type="checkbox" id="as-require-verify" ${requireOn ? 'checked' : ''}>
          <span class="switch-track"></span></label></div>
    </section>

    <section class="settings-section">
      ${secHead('Email', c.email_configured ? ['Configured', 'badge-success'] : ['Not configured', 'badge-neutral'])}
      <div class="sub">Used for the sign-up confirmation, welcome, trial-ending, payment-failed and
        password-reset messages. Leave empty to send nothing; Stripe still emails receipts.</div>
      ${SMTP_KEYS.map((k) => configField('as', k, c[k], SMTP_LABELS, SMTP_HINTS)).join('')}
      <div class="setting-row"><div class="min-w-0"><div class="title">Send a test email</div>
        <div class="desc">Uses the settings as they are saved on the server, not what is typed above.</div>
        <div id="as-test-result" class="hint mt-2"></div></div>
        <div class="row gap-2 adm-ctl">
          <input id="as-test-to" class="input input-sm grow" type="email" placeholder="you@example.com" aria-label="Send a test email to">
          <button type="button" class="btn btn-secondary btn-sm" data-act="send-test-email">Send</button>
        </div></div>
    </section>`;
}

document.addEventListener('click', async (e) => {
  const el = e.target.closest('[data-act]');
  if (!el) return;
  switch (el.dataset.act) {
    case 'reload-admin-signups': return loadAdminSignups(ASU.host);
    case 'discard-admin-signups': return loadAdminSignups(ASU.host);
    case 'save-admin-signups':
      return ui.busy(el, async () => {
        const body = { signup_enabled: $('#as-signup').checked,
                       signup_require_verification: $('#as-require-verify').checked,
                       ...collectConfigFields('as', SMTP_KEYS) };
        await api('/api/admin/billing/config', { method: 'PUT', body });
        ASU.bar.set(false);
        toast('Sign-up settings saved', { type: 'success' });
        loadAdminSignups(ASU.host);
      });
    case 'send-test-email':
      return ui.busy(el, async () => {
        const r = await api('/api/admin/billing/email/test', { method: 'POST', body: { to: $('#as-test-to').value.trim() } });
        $('#as-test-result').innerHTML = r.ok
          ? `<span class="chip chip-ok">${icon('check')}Sent</span>`
          : `<span class="chip chip-err">${icon('alert-circle')}${esc(r.error || 'Failed')}</span>`;
      });
    default: return undefined;
  }
});
