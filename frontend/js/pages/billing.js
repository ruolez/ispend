/* Plan status, Checkout and the Billing Portal. A page rather than a Settings tab: a read-only
   user needs somewhere to land from a toast, a banner or an email, and Stripe's success_url
   needs a stable path. */

const BILL = { data: null };

const STATE_BADGE = {
  active: ['badge-success', 'Active'], trialing: ['badge-info', 'Trial'],
  grace: ['badge-warning', 'Payment needed'], read_only: ['badge-danger', 'Read-only'],
  admin_exempt: ['badge-neutral', 'Administrator'],
};

initNav('billing').then(async () => {
  document.body.addEventListener('click', onAction);
  const params = qs();
  if (params.checkout) {
    // The success_url is never proof of payment: this re-reads the subscription from Stripe.
    if (params.checkout === 'success') {
      try { await api('/api/billing/refresh', { method: 'POST', body: {} }); } catch { /* shown below */ }
      toast('Subscription active — thank you!', { type: 'success' });
    } else {
      toast('Checkout cancelled', { type: 'info' });
    }
    history.replaceState(null, '', '/billing.html');
  }
  return load();
});

async function load() {
  const host = $('#bill-body');
  host.innerHTML = ui.skeletonList(3);
  try {
    BILL.data = await api('/api/billing/status');
  } catch (err) {
    host.innerHTML = ui.errorBox(err.message, { retry: 'reload' });
    return;
  }
  render();
}

function render() {
  const d = BILL.data;
  const host = $('#bill-body');
  if (!d.billing_enabled) {
    $('#bill-sub').textContent = '';
    host.innerHTML = ui.emptyState({ icon: 'credit-card', title: 'Billing is not configured',
      body: 'This iSpend server runs without subscriptions — every account has full access.' });
    return;
  }
  const [cls, label] = STATE_BADGE[d.state] || ['badge-neutral', d.state];
  $('#bill-sub').textContent = summaryLine(d);
  const canSubscribe = d.state !== 'admin_exempt' && !(d.state === 'active' && d.has_subscription);
  host.innerHTML = `
    <section class="card mb-4"><div class="card-body">
      <div class="row-between mb-3">
        <div><div class="fw-500">Your plan</div>
          <div class="sub">${esc(planLine(d))}</div></div>
        <span class="badge ${cls}">${esc(label)}</span>
      </div>
      ${d.cancel_at_period_end && d.current_period_end
        ? `<div class="notice notice-info mb-3">${icon('info')}<div>Your plan ends on
           ${esc(fmtDateLong(d.current_period_end))}. You keep full access until then.</div></div>` : ''}
      ${d.state === 'read_only'
        ? `<div class="notice notice-warning mb-3">${icon('alert-triangle')}<div>iSpend is read-only.
           All of your data is intact and you can still view and export everything.</div></div>` : ''}
      ${!d.email_verified && canSubscribe
        ? `<div class="notice notice-info mb-3">${icon('mail')}<div class="grow">Confirm your email
           address before subscribing.</div><button type="button" class="btn btn-secondary btn-sm"
           data-act="resend">Resend</button></div>` : ''}
      <div class="row gap-2 wrap">
        ${canSubscribe ? (d.prices || []).map((p) => `
          <button type="button" class="btn btn-primary" data-act="subscribe" data-plan="${esc(p.plan)}">
            ${esc(priceLabel(p))}</button>`).join('') : ''}
        ${d.has_subscription
          ? `<button type="button" class="btn btn-secondary" data-act="portal">${icon('external-link')}<span class="label">Manage subscription</span></button>`
          : ''}
      </div>
    </div></section>
    ${d.has_subscription ? `<section class="card"><div class="card-body">
      <div class="fw-500">Invoices and payment details</div>
      <div class="sub mb-3">Stripe hosts your receipts and card details.</div>
      <button type="button" class="btn btn-secondary btn-sm" data-act="portal">Open the billing portal</button>
    </div></section>` : ''}`;
}

function summaryLine(d) {
  if (d.state === 'admin_exempt') return 'Administrator accounts are not billed.';
  if (d.state === 'trialing') return `${plural(d.days_left || 0, 'day')} left in your trial.`;
  if (d.state === 'grace') return `Payment needed — ${plural(d.days_left || 0, 'day')} before iSpend becomes read-only.`;
  if (d.state === 'read_only') return 'Read-only until you subscribe.';
  if (d.comped) return 'Complimentary access.';
  return d.current_period_end ? `Renews on ${fmtDateLong(d.current_period_end)}.` : 'Active.';
}

function planLine(d) {
  if (d.plan) return `${d.plan === 'yearly' ? 'Yearly' : 'Monthly'} plan`;
  if (d.state === 'trialing') return d.trial_end ? `Trial until ${fmtDateLong(d.trial_end)}` : 'Free trial';
  return 'No subscription';
}

function priceLabel(p) {
  const amount = p.amount_cents != null
    ? fmtMoney(p.amount_cents / 100, p.currency) : '';
  const suffix = p.plan === 'yearly' ? ' / year' : ' / month';
  return `Subscribe — ${amount}${suffix}`;
}

async function onAction(e) {
  const el = e.target.closest('[data-act]');
  if (!el) return;
  switch (el.dataset.act) {
    case 'reload': return load();
    case 'resend':
      await ui.busy(el, async () => { await api('/api/auth/email/resend', { method: 'POST', body: {} }); });
      toast('Confirmation email sent', { type: 'success' });
      return undefined;
    case 'subscribe':
      return ui.busy(el, async () => {
        const r = await api('/api/billing/checkout', { method: 'POST', body: { plan: el.dataset.plan } });
        location.href = r.url;   // a full-page redirect, so no CSP change is needed
      });
    case 'portal':
      return ui.busy(el, async () => {
        const r = await api('/api/billing/portal', { method: 'POST', body: {} });
        location.href = r.url;
      });
    default: return undefined;
  }
}
