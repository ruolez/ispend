/* Landing page: icons, scroll choreography, and the pricing block, which is rendered entirely
   from GET /api/public/landing so an admin can change plan copy without a deploy.

   Deliberately does NOT use api() from api.js: its 401 handler redirects to /login.html, which is
   exactly the wrong thing to do to an anonymous visitor reading the marketing page.

   Motion is an enhancement on top of complete markup: every figure is printed in the HTML, every
   section is visible without JS, and this file only plays the count-ups and rises as things enter
   the viewport. */

const REDUCED = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

/* ---------- chrome ---------- */

function paintIcons() {
  $$('[data-icon]').forEach((el) => { el.innerHTML = icon(el.dataset.icon); });
}

function stickyHeader() {
  const head = $('#lp-head');
  const bar = $('#lp-sticky');
  const heroCta = $('#lp-hero-cta');
  const links = $$('#lp-nav a[href^="#"]');
  const sections = links.map((a) => document.getElementById(a.getAttribute('href').slice(1))).filter(Boolean);
  const onScroll = () => {
    head.classList.toggle('is-stuck', window.scrollY > 8);
    /* The link for the section under the top third of the viewport is the current one. */
    const line = window.innerHeight * 0.35;
    const current = sections.filter((s) => s.getBoundingClientRect().top <= line).pop();
    links.forEach((a) => {
      if (current && a.getAttribute('href') === '#' + current.id) a.setAttribute('aria-current', 'true');
      else a.removeAttribute('aria-current');
    });
    /* The phone bar appears once the hero's own button has scrolled away, so it never doubles it. */
    const gone = heroCta.getBoundingClientRect().bottom < 0;
    bar.classList.toggle('is-on', gone);
    bar.setAttribute('aria-hidden', String(!gone));
    bar.inert = !gone;
  };
  window.addEventListener('scroll', onScroll, { passive: true });
  onScroll();
}

function mobileMenu() {
  const burger = $('#lp-burger');
  const nav = $('#lp-nav');
  const glyph = burger.querySelector('.lp-ic');
  const close = () => { nav.classList.remove('is-open'); burger.setAttribute('aria-expanded', 'false'); glyph.innerHTML = icon('menu'); };
  burger.addEventListener('click', () => {
    const open = nav.classList.toggle('is-open');
    burger.setAttribute('aria-expanded', String(open));
    glyph.innerHTML = icon(open ? 'x' : 'menu');
  });
  nav.addEventListener('click', (e) => { if (e.target.closest('a')) close(); });
  window.addEventListener('keydown', (e) => { if (e.key === 'Escape') close(); });
}

/* ---------- motion ---------- */

function countUp(el) {
  if (el.dataset.done || REDUCED) return;
  el.dataset.done = '1';
  const to = Number(el.dataset.to || 0);
  const money = el.dataset.money === '1';
  const opts = {};
  if (el.dataset.plus === '1') opts.sign = 'always';
  if (el.dataset.whole === '1') opts.decimals = 0;
  const show = (v) => { el.textContent = money ? fmtMoney(v, 'USD', opts) : fmtNumber(Math.round(v)); };
  const start = performance.now();
  const tick = (now) => {
    const t = Math.min(1, (now - start) / 1100);
    show(to * (1 - Math.pow(1 - t, 3)));
    if (t < 1) requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
}

function reveal() {
  /* Whatever is already on screen when the page opens must not vanish and rise: on a tall window
     the first chapter is visible at load, and a rise there reads as a flicker. Only sections that
     scroll into view play it. */
  let opening = true;
  const enter = (el) => {
    el.classList.add('is-in');
    if (opening) el.classList.add('is-still');
    $$('.lp-num', el).forEach(countUp);
  };
  if (!('IntersectionObserver' in window)) return;
  const io = new IntersectionObserver((entries) => {
    entries.forEach((e) => { if (e.isIntersecting) { enter(e.target); io.unobserve(e.target); } });
    opening = false;
  }, { rootMargin: '0px 0px -10% 0px', threshold: 0.08 });
  $$('.lp-reveal').forEach((el) => io.observe(el));
}

function heroLive() {
  /* The statement translates itself once, on load: raw lines light up as their rows land, and
     the total is counted once its rule has drawn. */
  const ledger = $('#lp-ledger');
  if (!ledger) return;
  ledger.classList.add('is-live');
  window.setTimeout(() => { $$('.lp-num', ledger).forEach(countUp); }, REDUCED ? 0 : 1500);
}

/* ---------- pricing ---------- */

function planFeatures(list) {
  return (list || []).map((f) =>
    `<li><span class="lp-ic ok" data-icon="check"></span>${esc(f)}</li>`).join('');
}

function priceBlock(price, interval, yearlyNote) {
  if (!price) return '';
  const cur = price.currency || 'USD';
  const perMonth = interval === 'yearly' ? (price.amount_cents || 0) / 12 / 100 : (price.amount_cents || 0) / 100;
  const note = interval === 'yearly'
    ? `Billed annually at ${esc(fmtMoney((price.amount_cents || 0) / 100, cur))}${yearlyNote ? ` · ${esc(yearlyNote)}` : ''}`
    : 'Billed monthly';
  return `<div class="lp-price"><span class="lp-price-v">${esc(fmtMoney(perMonth, cur))}</span>
            <span class="lp-price-per">per month</span></div>
          <div class="lp-price-note">${note}</div>`;
}

function paidCard(cfg, interval) {
  const paid = cfg.landing.paid;
  const price = (cfg.prices || []).find((p) => p.plan === interval) || (cfg.prices || [])[0];
  const open = cfg.signup_enabled;
  const cta = open
    ? `<a class="btn btn-primary btn-lg btn-block" href="/signup.html">${esc(paid.cta_label)}</a>`
    : '<a class="btn btn-primary btn-lg btn-block" href="/login.html">Sign in</a>';
  const trial = open && cfg.trial_days
    ? `<div class="lp-price-note">${cfg.trial_days} days free, and we don’t ask for a card</div>` : '';
  return `<div class="lp-plan">
      ${paid.badge ? `<span class="lp-badge">${esc(paid.badge)}</span>` : ''}
      <div class="lp-plan-name">${esc(paid.name)}</div>
      <div class="lp-plan-tag">${esc(paid.tagline)}</div>
      ${price ? priceBlock(price, interval, cfg.landing.yearly_note)
              : '<div class="lp-price-note">We couldn’t fetch the price just now. Nothing is charged until your trial ends.</div>'}
      ${cta}${trial}
      <ul class="lp-plan-feats">${planFeatures(paid.features)}</ul>
    </div>`;
}

function openCard(cfg) {
  /* Nothing is for sale on this instance, so this is not a plan — it is the whole app, free. The
     copy is fixed rather than admin-editable: an operator running without Stripe is not writing
     marketing. The bullet list is still theirs. */
  const cta = cfg.signup_enabled
    ? '<a class="btn btn-primary btn-lg btn-block" href="/signup.html">Create an account</a>'
    : '<a class="btn btn-primary btn-lg btn-block" href="/login.html">Sign in</a>';
  return `<div class="lp-plan">
      <div class="lp-plan-name">Full access</div>
      <div class="lp-plan-tag">Every feature, with nothing to pay.</div>
      <div class="lp-price"><span class="lp-price-v">Free</span></div>
      <div class="lp-price-note">Nothing to pay here</div>
      ${cta}
      <ul class="lp-plan-feats">${planFeatures(cfg.landing.paid.features)}</ul>
    </div>`;
}

function savingsPct(prices) {
  const m = prices.find((p) => p.plan === 'monthly');
  const y = prices.find((p) => p.plan === 'yearly');
  if (!m || !y || !m.amount_cents || !y.amount_cents) return 0;
  return Math.round((1 - y.amount_cents / (m.amount_cents * 12)) * 100);
}

function pricingHead(heading, sub, toggle = '') {
  return `<p class="lp-kicker">Pricing</p><h2 class="lp-h2">${esc(heading)}</h2>
    ${sub ? `<p class="lp-sub">${esc(sub)}</p>` : ''}${toggle}`;
}

function renderPricing(cfg) {
  const head = $('#lp-pricing .lp-pricing-head');
  const body = $('#lp-pricing .lp-pricing-body');
  const L = cfg.landing;
  const prices = cfg.prices || [];
  /* "Is this instance selling?" and "did Stripe answer?" are different questions, and conflating
     them would advertise a paid product as free the moment Stripe hiccups. Only a server with no
     Stripe configured at all gets the free card; a priced server that cannot read its price still
     shows the plan, minus the number. */
  const sellable = cfg.billing_enabled;
  const hasYearly = sellable && prices.some((p) => p.plan === 'yearly');
  let interval = 'monthly';

  const draw = () => {
    /* With nothing to sell, the trial and cancellation wording around the plan is simply untrue. */
    const sub = sellable ? L.sub : 'Everyone here gets everything, free.';
    const note = sellable ? L.footnote : '';
    const save = savingsPct(prices);
    const toggle = hasYearly ? `<div class="lp-seg" role="radiogroup" aria-label="Billing period">
        <button type="button" role="radio" data-interval="monthly" aria-checked="${interval === 'monthly'}">Monthly</button>
        <button type="button" role="radio" data-interval="yearly" aria-checked="${interval === 'yearly'}">Yearly${save > 0 ? `<span class="lp-save">−${save}%</span>` : ''}</button>
      </div>` : '';
    head.innerHTML = pricingHead(L.heading, sub, toggle);
    body.innerHTML = `${sellable ? paidCard(cfg, interval) : openCard(cfg)}
      ${!cfg.signup_enabled ? '<p class="lp-foot-note">This site isn’t taking new accounts at the moment.</p>' : ''}
      ${note ? `<p class="lp-foot-note">${esc(note)}</p>` : ''}`;
    paintIcons();
  };

  head.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-interval]');
    if (!btn || btn.dataset.interval === interval) return;
    interval = btn.dataset.interval;
    draw();
  });
  draw();
}

/* ---------- page state ---------- */

function applyConfig(cfg) {
  const days = cfg.trial_days;
  if (days) $$('[data-trial-line]').forEach((el) => {
    el.textContent = el.textContent.replace(/^\d+-day/, `${days}-day`);
  });
  if (!cfg.signup_enabled) {
    /* Every call to action collapses to "Sign in", so the plain Sign in links beside them would
       read as the same button twice. Drop those, and the trial promises with them. */
    $$('[data-signin-alt]').forEach((el) => el.remove());
    $$('.lp-trust').forEach((el) => { el.hidden = true; });
    $$('[data-cta]').forEach((el) => {
      el.setAttribute('href', '/login.html');
      el.textContent = 'Sign in';
    });
  }
  renderPricing(cfg);
}

async function whoami() {
  try {
    const res = await fetch('/api/auth/me', { credentials: 'same-origin' });
    if (!res.ok) return;
  } catch { return; }
  $('#lp-head-cta').innerHTML =
    '<a class="btn btn-primary lp-head-go" href="/index.html">Open iSpend <span class="lp-ic" data-icon="arrow-right"></span></a>';
  paintIcons();
}

async function loadConfig() {
  try {
    const res = await fetch('/api/public/landing', { credentials: 'same-origin' });
    if (!res.ok) throw new Error(String(res.status));
    applyConfig(await res.json());
  } catch {
    /* The static markup stays on the page; only the pricing block needs a fallback. */
    $('#lp-pricing .lp-pricing-head').innerHTML = pricingHead('Simple pricing',
      'We couldn’t load the plan just now. Create an account to see it — every account starts with a free trial, and we don’t ask for a card.');
    $('#lp-pricing .lp-pricing-body').innerHTML = `<div class="lp-cta-row"><a class="btn btn-primary btn-lg" href="/signup.html">Create an account</a>
        <a class="btn btn-secondary btn-lg" href="/login.html">Sign in</a></div>`;
  }
}

paintIcons();
stickyHeader();
mobileMenu();
reveal();
heroLive();
$('#lp-year').textContent = String(new Date().getFullYear());
loadConfig();
whoami();
