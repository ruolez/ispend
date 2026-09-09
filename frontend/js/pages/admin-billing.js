/* Billing configuration and the instance summary, mounted into the Admin page. */

const ABL = { summary: null, config: null, dirty: false, bar: null };

AdminPanels.register('billing', {
  label: 'Billing', icon: 'credit-card',
  sub: 'Subscriptions, Stripe keys and the lifecycle email',
  load: loadAdminBilling,
});

const STATE_LABEL = { active: 'Paying', trialing: 'On trial', grace: 'Payment needed',
                      read_only: 'Read-only', admin_exempt: 'Admins (not billed)' };

async function loadAdminBilling(host) {
  ABL.host = host;
  if (!ABL.wired) {
    ABL.wired = true;
    host.addEventListener('input', onBillingEdit);
    host.addEventListener('change', onBillingEdit);
    // Leaving the tab must take the save bar with it; it is fixed to the viewport, not the panel.
    window.addEventListener('ispend:admin-tab', (e) => {
      if (e.detail === 'billing') return;
      if (ABL.dirty) toast('Billing changes were not saved', { type: 'info' });
      setBillingDirty(false);
    });
  }
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

function secHead(title, badge) {
  return `<div class="adm-sec-head"><h2>${esc(title)}</h2>${badge
    ? `<span class="badge ${badge[1]}">${esc(badge[0])}</span>` : ''}</div>`;
}

function renderAdminBilling() {
  const s = ABL.summary;
  const c = ABL.config;
  const counts = s.counts || {};
  const email = s.email || {};
  const hook = s.webhook || {};
  const stripeSet = c.stripe_secret_key.set && c.stripe_price_monthly.set;
  setBillingDirty(false);
  ABL.host.innerHTML = `
    <section class="settings-section">
      ${secHead('Subscriptions', s.enabled ? ['Billing on', 'badge-success'] : ['Billing off', 'badge-neutral'])}
      <div class="sub">${s.enabled
        ? 'Stripe is configured. New accounts start a trial and are billed after it.'
        : 'Stripe is not configured, so every account has full access and nothing is billed.'}</div>
      ${s.enabled
        // With Stripe off every account evaluates to "active", so a paying/trialing breakdown
        // would be a fiction. Show the one number that is true instead.
        ? kpis(Object.entries(STATE_LABEL).map(([k, label]) => [label, fmtNumber(counts[k] || 0)]))
        : kpis([['Accounts', fmtNumber(Object.values(counts).reduce((a, b) => a + b, 0))]])}
      ${s.enabled ? `<div class="section-label mt-6 mb-2">Last 24 hours</div>` : `<div class="section-label mt-6 mb-2">Email</div>`}
      ${kpis([
        ...(s.enabled ? [
          ['Trials ending (7d)', fmtNumber(s.trials_ending_7d || 0)],
          ['Webhooks', fmtNumber(hook.events_24h || 0)],
          ['Webhook failures', fmtNumber(hook.failed_24h || 0), hook.failed_24h ? 'text-danger' : ''],
        ] : []),
        ['Emails sent', fmtNumber(email.sent_24h || 0)],
        ['Email failures', fmtNumber(email.failed_24h || 0), email.failed_24h ? 'text-danger' : ''],
      ])}
      <div class="hint mt-3">${s.enabled
        ? `Last webhook ${hook.last_event_at ? esc(fmtRelative(hook.last_event_at)) : 'never received'}.`
        : 'Add a Stripe secret key and a monthly price below to start billing new accounts.'}
        ${email.configured ? '' : 'SMTP is not configured, so no lifecycle emails are sent.'}</div>
      ${s.enabled ? `<button type="button" class="btn btn-secondary btn-sm mt-4" data-act="sync-stale">Sync stale subscriptions</button>` : ''}
    </section>

    <section class="settings-section">
      ${secHead('Plan settings')}
      <div class="setting-row"><div class="min-w-0"><div class="title">Accept new sign-ups</div>
        <div class="desc">When off, /signup.html is closed and only an admin can create accounts.</div></div>
        <label class="switch"><input type="checkbox" id="ab-signup" ${c.signup_enabled.value ? 'checked' : ''}>
          <span class="switch-track"></span></label></div>
      <div class="setting-row"><div class="min-w-0"><div class="title">Trial length</div>
        <div class="desc">Granted locally at sign-up; no Stripe object exists until someone subscribes.</div></div>
        <div class="row gap-2 adm-ctl adm-ctl--sm"><input id="ab-trial" class="input input-sm num-input" type="number" min="1" max="90"
          aria-label="Trial length in days" value="${c.billing_trial_days.value}"><span class="text-3">days</span></div></div>
      <div class="setting-row"><div class="min-w-0"><div class="title">Grace period</div>
        <div class="desc">After a trial ends or a payment fails, before the account becomes read-only.</div></div>
        <div class="row gap-2 adm-ctl adm-ctl--sm"><input id="ab-grace" class="input input-sm num-input" type="number" min="0" max="90"
          aria-label="Grace period in days" value="${c.billing_grace_days.value}"><span class="text-3">days</span></div></div>
    </section>

    <section class="settings-section">
      ${secHead('Stripe', stripeSet ? ['Configured', 'badge-success'] : ['Not configured', 'badge-neutral'])}
      <div class="sub">Use hosted Checkout and the Billing Portal; card details never reach this server.
        Point a Stripe webhook at <span class="mono">/api/billing/webhook</span>.</div>
      ${['stripe_secret_key', 'stripe_webhook_secret', 'stripe_price_monthly', 'stripe_price_yearly']
        .map((k) => field(k, c[k])).join('')}
    </section>

    <section class="settings-section">
      ${secHead('Email', email.configured ? ['Configured', 'badge-success'] : ['Not configured', 'badge-neutral'])}
      <div class="sub">Used for the welcome, trial-ending, payment-failed and password-reset messages.
        Leave empty to send nothing; Stripe still emails receipts.</div>
      ${['smtp_host', 'smtp_port', 'smtp_security', 'smtp_user', 'smtp_password',
         'smtp_from_email', 'smtp_from_name'].map((k) => field(k, c[k])).join('')}
      <div class="setting-row"><div class="min-w-0"><div class="title">Send a test email</div>
        <div class="desc">Uses the settings as they are saved on the server, not what is typed above.</div>
        <div id="ab-test-result" class="hint mt-2"></div></div>
        <div class="row gap-2 adm-ctl">
          <input id="ab-test-to" class="input input-sm grow" type="email" placeholder="you@example.com" aria-label="Send a test email to">
          <button type="button" class="btn btn-secondary btn-sm" data-act="send-test-email">Send</button>
        </div></div>
    </section>`;
}

const LABELS = {
  stripe_secret_key: 'Secret key', stripe_webhook_secret: 'Webhook signing secret',
  stripe_price_monthly: 'Monthly price ID', stripe_price_yearly: 'Yearly price ID (optional)',
  smtp_host: 'Host', smtp_port: 'Port', smtp_security: 'Security', smtp_user: 'Username',
  smtp_password: 'Password', smtp_from_email: 'From address', smtp_from_name: 'From name',
};

const HINTS = {
  stripe_secret_key: 'Starts with sk_live_ or sk_test_.',
  stripe_webhook_secret: 'The whsec_… Stripe shows when you add the endpoint.',
  stripe_price_monthly: 'The price_… of the recurring monthly price.',
  stripe_price_yearly: 'Adds a yearly option at checkout when set.',
  smtp_host: 'Your provider’s SMTP hostname.',
  smtp_port: '587 for STARTTLS, 465 for SSL.',
  smtp_from_email: 'Must be an address your provider lets you send from.',
};

function field(key, meta) {
  if (!meta) return '';
  const id = `ab-${key}`;
  const secret = key.includes('secret') || key.includes('password');
  const desc = meta.locked ? 'Set in .env on the server.' : (HINTS[key] || '');
  const head = `<div class="min-w-0"><div class="title">${esc(LABELS[key] || key)}</div>
    ${desc ? `<div class="desc">${esc(desc)}</div>` : ''}</div>`;
  if (key === 'smtp_security') {
    return `<div class="setting-row">${head}
      <div class="adm-ctl"><select class="select select-sm" id="${id}" ${meta.locked ? 'disabled' : ''}>
        ${['starttls', 'ssl', 'none'].map((v) =>
          `<option value="${v}" ${meta.value === v ? 'selected' : ''}>${v}</option>`).join('')}
      </select></div></div>`;
  }
  const input = `<input class="input input-sm ab-field mono ${secret ? 'has-trailing' : ''}" id="${id}"
      type="${secret ? 'password' : 'text'}" autocomplete="off" spellcheck="false" ${meta.locked ? 'disabled' : ''}
      value="${esc(meta.value || '')}">`;
  return `<div class="setting-row">${head}
    <div class="adm-ctl">${secret
      ? `<div class="input-group">${input}<button type="button" class="btn btn-icon btn-ghost btn-sm trailing"
           data-act="peek-secret" data-for="${id}" aria-label="Show ${esc(LABELS[key] || key)}">${icon('eye')}</button></div>`
      : input}</div></div>`;
}

/* A save bar rather than a button at the bottom: the form is long enough that the button would be
   off screen while typing in the Stripe section. Same pattern as the AI settings page. */
function onBillingEdit(e) {
  if (e.target.closest('#ab-test-to')) return;
  setBillingDirty(true);
}

function setBillingDirty(v) {
  ABL.dirty = v;
  if (v && !ABL.bar) {
    ABL.bar = document.createElement('div');
    ABL.bar.className = 'floatbar';
    ABL.bar.setAttribute('role', 'status');
    ABL.bar.innerHTML = `<span>Unsaved changes</span><span class="sep"></span>
      <button type="button" class="btn btn-ghost btn-sm" data-act="discard-admin-billing">Discard</button>
      <button type="button" class="btn btn-primary btn-sm" data-act="save-admin-billing">Save</button>`;
    document.body.appendChild(ABL.bar);
  } else if (!v && ABL.bar) {
    ABL.bar.remove();
    ABL.bar = null;
  }
}

document.addEventListener('click', async (e) => {
  const el = e.target.closest('[data-act]');
  if (!el) return;
  switch (el.dataset.act) {
    case 'reload-admin-billing': return loadAdminBilling(ABL.host);
    case 'discard-admin-billing': return loadAdminBilling(ABL.host);
    case 'peek-secret': {
      const input = document.getElementById(el.dataset.for);
      const show = input.type === 'password';
      input.type = show ? 'text' : 'password';
      el.innerHTML = icon(show ? 'eye-off' : 'eye');
      return undefined;
    }
    case 'save-admin-billing':
      return ui.busy(el, async () => {
        const body = { signup_enabled: $('#ab-signup').checked,
                       billing_trial_days: Number($('#ab-trial').value),
                       billing_grace_days: Number($('#ab-grace').value) };
        Object.keys(LABELS).forEach((k) => {
          const input = document.getElementById(`ab-${k}`);
          // A masked value means "unchanged": sending it would blank the stored secret.
          if (input && !input.disabled && input.value !== '••••••••') body[k] = input.value.trim();
        });
        await api('/api/admin/billing/config', { method: 'PUT', body });
        setBillingDirty(false);
        toast('Billing settings saved', { type: 'success' });
        loadAdminBilling(ABL.host);
      });
    case 'send-test-email':
      return ui.busy(el, async () => {
        const r = await api('/api/admin/billing/email/test', { method: 'POST', body: { to: $('#ab-test-to').value.trim() } });
        $('#ab-test-result').innerHTML = r.ok
          ? `<span class="chip chip-ok">${icon('check')}Sent</span>`
          : `<span class="chip chip-err">${icon('alert-circle')}${esc(r.error || 'Failed')}</span>`;
      });
    case 'sync-stale':
      return ui.busy(el, async () => {
        const r = await api('/api/admin/billing/sync-stale', { method: 'POST', body: {} });
        toast(`${plural(r.queued, 'subscription')} queued for a Stripe refresh`, { type: 'success' });
      });
    default: return undefined;
  }
});
