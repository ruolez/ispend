/* Billing configuration and the instance summary, mounted into the Admin page. */

const ABL = { summary: null, config: null, bar: null };

AdminPanels.register('billing', {
  label: 'Billing', icon: 'credit-card',
  sub: 'Subscriptions, trial and grace periods, Stripe keys and the landing page',
  load: loadAdminBilling,
});

const STATE_LABEL = { active: 'Paying', trialing: 'On trial', grace: 'Payment needed',
                      read_only: 'Read-only', admin_exempt: 'Admins (not billed)' };

async function loadAdminBilling(host) {
  ABL.host = host;
  if (!ABL.wired) {
    ABL.wired = true;
    ABL.bar = adminDirtyBar('billing', { saveAct: 'save-admin-billing', discardAct: 'discard-admin-billing',
                                         unsavedText: 'Billing changes were not saved' });
    host.addEventListener('input', () => ABL.bar.set(true));
    host.addEventListener('change', () => ABL.bar.set(true));
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

function renderAdminBilling() {
  const s = ABL.summary;
  const c = ABL.config;
  const counts = s.counts || {};
  const email = s.email || {};
  const hook = s.webhook || {};
  const stripeSet = c.stripe_secret_key.set && c.stripe_price_monthly.set;
  const L = c.landing;
  ABL.bar.set(false);
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
        ${email.configured ? '' : 'Email is not set up, so nothing is sent — see Sign-ups &amp; email.'}</div>
      ${s.enabled ? `<button type="button" class="btn btn-secondary btn-sm mt-4" data-act="sync-stale">Sync stale subscriptions</button>` : ''}
    </section>

    <section class="settings-section">
      ${secHead('Plan settings')}
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
      ${secHead('Landing page')}
      <div class="sub">The pricing block on <a href="/" target="_blank" rel="noopener">the public landing page</a>.
        Amounts, currency and the billing period always come live from Stripe — this is the wording
        around them. With no Stripe configured the section shows a free-access card instead.</div>
      ${lpText('heading', 'Section heading', '', L.heading, 60)}
      ${lpText('sub', 'Section sub-heading', 'Leave empty to hide it.', L.sub, 160)}
      <div class="section-label mt-6 mb-2">The plan</div>
      ${lpText('paid-name', 'Plan name', '', L.paid.name, 40)}
      ${lpText('paid-tagline', 'Tagline', 'One line under the plan name.', L.paid.tagline, 120)}
      ${lpText('paid-badge', 'Ribbon', 'Shown across the top of the card, e.g. “Best value”. Empty hides it.', L.paid.badge, 24)}
      ${lpText('paid-cta', 'Button label', 'Replaced by “Sign in” when sign-ups are closed.', L.paid.cta_label, 32)}
      ${lpList('paid-features', 'Bullet points', L.paid.features)}
      <div class="section-label mt-6 mb-2">Small print</div>
      ${lpText('yearly-note', 'Yearly note', 'Appended to the yearly price, e.g. “2 months free”.', L.yearly_note, 32)}
      ${lpText('footnote', 'Footnote', 'Under the plans. Empty hides it.', L.footnote, 200)}
    </section>

    <section class="settings-section">
      ${secHead('Stripe', stripeSet ? ['Configured', 'badge-success'] : ['Not configured', 'badge-neutral'])}
      <div class="sub">Use hosted Checkout and the Billing Portal; card details never reach this server.
        Point a Stripe webhook at <span class="mono">/api/billing/webhook</span>.</div>
      ${STRIPE_KEYS.map((k) => configField('ab', k, c[k], STRIPE_LABELS, STRIPE_HINTS)).join('')}
    </section>`;
}

const STRIPE_KEYS = ['stripe_secret_key', 'stripe_webhook_secret', 'stripe_price_monthly', 'stripe_price_yearly'];

const STRIPE_LABELS = {
  stripe_secret_key: 'Secret key', stripe_webhook_secret: 'Webhook signing secret',
  stripe_price_monthly: 'Monthly price ID', stripe_price_yearly: 'Yearly price ID (optional)',
};

const STRIPE_HINTS = {
  stripe_secret_key: 'Starts with sk_live_ or sk_test_.',
  stripe_webhook_secret: 'The whsec_… Stripe shows when you add the endpoint.',
  stripe_price_monthly: 'The price_… of the recurring monthly price.',
  stripe_price_yearly: 'Adds a yearly option at checkout when set.',
};

/* The landing-page copy is a nested JSON blob rather than flat settings keys, so it gets its own
   two controls instead of going through field()/LABELS. */
function lpText(key, label, desc, value, max) {
  return `<div class="setting-row"><div class="min-w-0"><div class="title">${esc(label)}</div>
    ${desc ? `<div class="desc">${esc(desc)}</div>` : ''}</div>
    <div class="adm-ctl"><input class="input input-sm" id="ab-lp-${key}" type="text" maxlength="${max}"
      autocomplete="off" aria-label="${esc(label)}" value="${esc(value || '')}"></div></div>`;
}

function lpList(key, label, lines) {
  return `<div class="setting-row"><div class="min-w-0"><div class="title">${esc(label)}</div>
    <div class="desc">One per line, up to 12. Blank lines are dropped.</div></div>
    <div class="adm-ctl"><textarea class="textarea" id="ab-lp-${key}" rows="7" spellcheck="false"
      aria-label="${esc(label)}">${esc((lines || []).join('\n'))}</textarea></div></div>`;
}

function lpValue(key) {
  const el = document.getElementById(`ab-lp-${key}`);
  return el ? el.value.trim() : '';
}

function lpLines(key) {
  return lpValue(key).split('\n').map((s) => s.trim()).filter(Boolean);
}

function collectLanding() {
  return {
    heading: lpValue('heading'), sub: lpValue('sub'),
    paid: { name: lpValue('paid-name'), tagline: lpValue('paid-tagline'), badge: lpValue('paid-badge'),
            cta_label: lpValue('paid-cta'), features: lpLines('paid-features') },
    yearly_note: lpValue('yearly-note'), footnote: lpValue('footnote'),
  };
}

document.addEventListener('click', async (e) => {
  const el = e.target.closest('[data-act]');
  if (!el) return;
  switch (el.dataset.act) {
    case 'reload-admin-billing': return loadAdminBilling(ABL.host);
    case 'discard-admin-billing': return loadAdminBilling(ABL.host);
    case 'save-admin-billing':
      return ui.busy(el, async () => {
        const body = { billing_trial_days: Number($('#ab-trial').value),
                       billing_grace_days: Number($('#ab-grace').value),
                       landing: collectLanding(),
                       ...collectConfigFields('ab', STRIPE_KEYS) };
        await api('/api/admin/billing/config', { method: 'PUT', body });
        ABL.bar.set(false);
        toast('Billing settings saved', { type: 'success' });
        loadAdminBilling(ABL.host);
      });
    case 'sync-stale':
      return ui.busy(el, async () => {
        const r = await api('/api/admin/billing/sync-stale', { method: 'POST', body: {} });
        toast(`${plural(r.queued, 'subscription')} queued for a Stripe refresh`, { type: 'success' });
      });
    default: return undefined;
  }
});
