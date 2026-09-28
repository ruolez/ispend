/* Admin › Settings › Billing: the trial and grace periods and the Stripe keys. How revenue is doing
   lives under Revenue; whether webhooks and email arrive lives under System. */

const ABL = { config: null, bar: null };

AdminSettings.register('billing', {
  label: 'Billing & plans', icon: 'credit-card',
  sub: 'Trial and grace periods, and the Stripe account customers pay through',
  load: loadAdminBilling,
});

const STRIPE_KEYS = ['stripe_secret_key', 'stripe_webhook_secret', 'stripe_price_monthly', 'stripe_price_yearly'];

const STRIPE_LABELS = {
  stripe_secret_key: 'Secret key', stripe_webhook_secret: 'Webhook signing secret',
  stripe_price_monthly: 'Monthly price ID', stripe_price_yearly: 'Yearly price ID (optional)',
};

const STRIPE_HINTS = {
  stripe_secret_key: 'Starts with sk_live_ or sk_test_. A test key puts the console in test mode.',
  stripe_webhook_secret: 'The whsec_… Stripe shows when you add the endpoint.',
  stripe_price_monthly: 'The price_… of the recurring monthly price.',
  stripe_price_yearly: 'Adds a yearly option at checkout when set.',
};

async function loadAdminBilling(host) {
  ABL.host = host;
  if (!ABL.bar) {
    ABL.bar = adminDirtyBar('settings/billing', { save: saveAdminBilling, discard: () => loadAdminBilling(ABL.host) });
    host.addEventListener('input', () => ABL.bar.set(true));
    host.addEventListener('change', () => ABL.bar.set(true));
  }
  if (ABL.bar.dirty) return;
  host.innerHTML = ui.skeletonList(4);
  try {
    ABL.config = await api('/api/admin/billing/config');
  } catch (err) {
    host.innerHTML = ui.errorBox(err.message, { retry: 'reload-admin-billing' });
    return;
  }
  renderAdminBilling();
}

function renderAdminBilling() {
  const c = ABL.config;
  const stripeSet = c.stripe_secret_key.set && c.stripe_price_monthly.set;
  ABL.bar.set(false);
  ABL.host.innerHTML = `
    <section class="settings-section card card-pad">
      ${secHead('Plan')}
      <div class="setting-row"><div class="min-w-0"><div class="title">Trial length</div>
        <div class="desc">Every new customer starts with this many days of full access. No card is asked for.</div></div>
        <div class="row gap-2 adm-ctl adm-ctl--sm"><input id="ab-trial" class="input input-sm num-input" type="number" min="1" max="90"
          aria-label="Trial length in days" value="${c.billing_trial_days.value}"><span class="text-3">days</span></div></div>
      <div class="setting-row"><div class="min-w-0"><div class="title">Grace period</div>
        <div class="desc">After a trial ends or a payment fails, before the account becomes read-only.</div></div>
        <div class="row gap-2 adm-ctl adm-ctl--sm"><input id="ab-grace" class="input input-sm num-input" type="number" min="0" max="90"
          aria-label="Grace period in days" value="${c.billing_grace_days.value}"><span class="text-3">days</span></div></div>
    </section>

    <section class="settings-section card card-pad">
      ${secHead('Stripe', stripeSet ? ['Connected', 'badge-success'] : ['Not connected', 'badge-neutral'])}
      <div class="sub">Customers pay on Stripe's own checkout and billing pages; card details never reach this server.
        Point a Stripe webhook at <span class="mono">/api/billing/webhook</span>. Without Stripe every customer has full access for free.</div>
      ${STRIPE_KEYS.map((k) => configField('ab', k, c[k], STRIPE_LABELS, STRIPE_HINTS)).join('')}
    </section>`;
}

async function saveAdminBilling() {
  const body = { billing_trial_days: Number($('#ab-trial').value), billing_grace_days: Number($('#ab-grace').value),
                 ...collectConfigFields('ab', STRIPE_KEYS) };
  await api('/api/admin/billing/config', { method: 'PUT', body });
  toast('Billing settings saved', { type: 'success' });
  ABL.bar.set(false);
  api('/api/admin/shell').then((sh) => { ADMIN.stripeMode = sh.stripe_mode; paintEnvBadge(sh.stripe_mode); }).catch(() => {});
  return loadAdminBilling(ABL.host);
}

document.addEventListener('click', (e) => {
  if (e.target.closest('[data-act="reload-admin-billing"]')) loadAdminBilling(ABL.host);
});
