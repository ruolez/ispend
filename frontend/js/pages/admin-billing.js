/* Billing configuration and the instance summary, mounted into the Admin page. */

const ABL = { summary: null, config: null };

AdminPanels.register('billing', { label: 'Billing', icon: 'credit-card', load: loadAdminBilling });

const STATE_LABEL = { active: 'Paying', trialing: 'On trial', grace: 'Payment needed',
                      read_only: 'Read-only', admin_exempt: 'Admins (not billed)' };

async function loadAdminBilling(host) {
  ABL.host = host;
  host.innerHTML = ui.skeletonList(4);
  try {
    [ABL.summary, ABL.config] = await Promise.all([
      api('/api/admin/billing/summary'), api('/api/admin/billing/config')]);
  } catch (err) {
    host.innerHTML = ui.errorBox(err.message, { retry: 'reload-admin-billing' });
    return;
  }
  renderAdminBilling();
}

function renderAdminBilling() {
  const s = ABL.summary;
  const c = ABL.config;
  const counts = s.counts || {};
  ABL.host.innerHTML = `
    <div class="settings-section">
      <h2>Subscriptions</h2>
      <div class="sub">${s.enabled
        ? 'Stripe is configured. New accounts start a trial and are billed after it.'
        : 'Stripe is not configured, so every account has full access and nothing is billed.'}</div>
      <div class="adm-kpis mb-4">
        ${s.enabled
          // With Stripe off every account evaluates to "active", so a paying/trialing breakdown
          // would be a fiction. Show the one number that is true instead.
          ? Object.entries(STATE_LABEL).map(([k, label]) =>
              `<div><div class="l">${esc(label)}</div><div class="v">${fmtNumber(counts[k] || 0)}</div></div>`).join('')
          : `<div><div class="l">Accounts</div><div class="v">${fmtNumber(Object.values(counts).reduce((a, b) => a + b, 0))}</div></div>`}
      </div>
      <div class="adm-kpis">
        <div><div class="l">Trials ending (7d)</div><div class="v">${fmtNumber(s.trials_ending_7d || 0)}</div></div>
        <div><div class="l">Webhooks (24h)</div><div class="v">${fmtNumber((s.webhook || {}).events_24h || 0)}</div></div>
        <div><div class="l">Webhook failures</div><div class="v ${(s.webhook || {}).failed_24h ? 'text-danger' : ''}">${fmtNumber((s.webhook || {}).failed_24h || 0)}</div></div>
        <div><div class="l">Emails sent (24h)</div><div class="v">${fmtNumber((s.email || {}).sent_24h || 0)}${(s.email || {}).failed_24h ? ` <span class="text-danger">/ ${fmtNumber(s.email.failed_24h)} failed</span>` : ''}</div></div>
      </div>
      <div class="hint mt-3">Last webhook ${(s.webhook || {}).last_event_at ? esc(fmtRelative(s.webhook.last_event_at)) : 'never received'}.
        ${s.email && s.email.configured ? '' : 'SMTP is not configured, so no lifecycle emails are sent.'}</div>
    </div>

    <div class="settings-section">
      <h2>Plan settings</h2>
      <div class="setting-row"><div><div class="title">Accept new sign-ups</div>
        <div class="desc">When off, /signup.html is closed and only an admin can create accounts.</div></div>
        <label class="switch"><input type="checkbox" id="ab-signup" ${c.signup_enabled.value ? 'checked' : ''}>
          <span class="switch-track"></span></label></div>
      <div class="setting-row"><div><div class="title">Trial length</div>
        <div class="desc">Granted locally at sign-up; no Stripe object exists until someone subscribes.</div></div>
        <div class="row gap-2"><input id="ab-trial" class="input input-sm num-input" type="number" min="1" max="90"
          aria-label="Trial length in days" value="${c.billing_trial_days.value}"><span class="text-3">days</span></div></div>
      <div class="setting-row"><div><div class="title">Grace period</div>
        <div class="desc">After a trial ends or a payment fails, before the account becomes read-only.</div></div>
        <div class="row gap-2"><input id="ab-grace" class="input input-sm num-input" type="number" min="0" max="90"
          aria-label="Grace period in days" value="${c.billing_grace_days.value}"><span class="text-3">days</span></div></div>
    </div>

    <div class="settings-section">
      <h2>Stripe</h2>
      <div class="sub">Use hosted Checkout and the Billing Portal; card details never reach this server.
        Point a Stripe webhook at <span class="mono">/api/billing/webhook</span>.</div>
      ${['stripe_secret_key', 'stripe_webhook_secret', 'stripe_price_monthly', 'stripe_price_yearly']
        .map((k) => field(k, c[k])).join('')}
    </div>

    <div class="settings-section">
      <h2>Email</h2>
      <div class="sub">Used for the welcome, trial-ending, payment-failed and password-reset messages.
        Leave empty to send nothing; Stripe still emails receipts.</div>
      ${['smtp_host', 'smtp_port', 'smtp_security', 'smtp_user', 'smtp_password',
         'smtp_from_email', 'smtp_from_name'].map((k) => field(k, c[k])).join('')}
      <div class="row gap-2 mt-4">
        <input id="ab-test-to" class="input input-sm grow" type="email" placeholder="you@example.com" aria-label="Send a test email to">
        <button type="button" class="btn btn-secondary btn-sm" data-act="send-test-email">Send a test email</button>
      </div>
      <div id="ab-test-result" class="hint mt-2"></div>
    </div>

    <div class="row gap-2">
      <button type="button" class="btn btn-primary" data-act="save-admin-billing">Save</button>
      <button type="button" class="btn btn-secondary" data-act="sync-stale">Sync stale subscriptions</button>
    </div>`;
}

const LABELS = {
  stripe_secret_key: 'Secret key', stripe_webhook_secret: 'Webhook signing secret',
  stripe_price_monthly: 'Monthly price ID', stripe_price_yearly: 'Yearly price ID (optional)',
  smtp_host: 'Host', smtp_port: 'Port', smtp_security: 'Security', smtp_user: 'Username',
  smtp_password: 'Password', smtp_from_email: 'From address', smtp_from_name: 'From name',
};

function field(key, meta) {
  if (!meta) return '';
  const id = `ab-${key}`;
  const secret = key.includes('secret') || key.includes('password');
  if (key === 'smtp_security') {
    return `<div class="setting-row"><div><div class="title">${esc(LABELS[key])}</div></div>
      <select class="select select-sm" id="${id}" ${meta.locked ? 'disabled' : ''}>
        ${['starttls', 'ssl', 'none'].map((v) =>
          `<option value="${v}" ${meta.value === v ? 'selected' : ''}>${v}</option>`).join('')}
      </select></div>`;
  }
  return `<div class="setting-row"><div><div class="title">${esc(LABELS[key] || key)}</div>
      ${meta.locked ? '<div class="desc">Set in .env on the server.</div>' : ''}</div>
    <input class="input input-sm ab-field" id="${id}" type="${secret ? 'password' : 'text'}"
      autocomplete="off" spellcheck="false" ${meta.locked ? 'disabled' : ''}
      placeholder="${meta.set && !meta.value ? '••••••••' : ''}" value="${esc(meta.value || '')}"></div>`;
}

document.addEventListener('click', async (e) => {
  const el = e.target.closest('[data-act]');
  if (!el) return;
  switch (el.dataset.act) {
    case 'reload-admin-billing': return loadAdminBilling(ABL.host);
    case 'save-admin-billing':
      return ui.busy(el, async () => {
        const body = { signup_enabled: $('#ab-signup').checked,
                       billing_trial_days: Number($('#ab-trial').value),
                       billing_grace_days: Number($('#ab-grace').value) };
        Object.keys(LABELS).forEach((k) => {
          const input = document.getElementById(`ab-${k}`);
          // A masked placeholder means "unchanged": sending it would blank the stored secret.
          if (input && !input.disabled && input.value !== '••••••••') body[k] = input.value.trim();
        });
        await api('/api/admin/billing/config', { method: 'PUT', body });
        toast('Billing settings saved', { type: 'success' });
        loadAdminBilling(ABL.host);
      });
    case 'send-test-email':
      return ui.busy(el, async () => {
        const r = await api('/api/admin/billing/email/test', { method: 'POST', body: { to: $('#ab-test-to').value.trim() } });
        $('#ab-test-result').innerHTML = r.ok
          ? `<span class="text-success">Sent.</span>`
          : `<span class="text-danger">${esc(r.error || 'Failed')}</span>`;
      });
    case 'sync-stale':
      return ui.busy(el, async () => {
        const r = await api('/api/admin/billing/sync-stale', { method: 'POST', body: {} });
        toast(`${plural(r.queued, 'subscription')} queued for a Stripe refresh`, { type: 'success' });
      });
    default: return undefined;
  }
});
