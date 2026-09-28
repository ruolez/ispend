/* Admin › Settings › Landing page: the wording of the pricing block on the public landing page.
   Amounts, currency and the billing period always come live from Stripe. */

const ALP = { config: null, bar: null };

AdminSettings.register('landing', {
  label: 'Landing page', icon: 'globe',
  sub: 'The pricing block visitors see on the public landing page',
  load: loadAdminLanding,
});

async function loadAdminLanding(host) {
  ALP.host = host;
  if (!ALP.bar) {
    ALP.bar = adminDirtyBar('settings/landing', { save: saveAdminLanding, discard: () => loadAdminLanding(ALP.host) });
    host.addEventListener('input', () => ALP.bar.set(true));
  }
  if (ALP.bar.dirty) return;
  host.innerHTML = ui.skeletonList(5);
  try {
    ALP.config = await api('/api/admin/billing/config');
  } catch (err) {
    host.innerHTML = ui.errorBox(err.message, { retry: 'reload-admin-landing' });
    return;
  }
  const L = ALP.config.landing;
  ALP.bar.set(false);
  host.innerHTML = `
    <section class="settings-section card card-pad">
      <div class="sub">Shown on <a href="/" target="_blank" rel="noopener">the landing page</a>. This is the wording
        around the prices; with no Stripe set up the section shows a free-access card instead.</div>
      ${secHead('Section')}
      ${lpText('heading', 'Heading', '', L.heading, 60)}
      ${lpText('sub', 'Sub-heading', 'Leave empty to hide it.', L.sub, 160)}
    </section>
    <section class="settings-section card card-pad">
      ${secHead('The plan')}
      ${lpText('paid-name', 'Plan name', '', L.paid.name, 40)}
      ${lpText('paid-tagline', 'Tagline', 'One line under the plan name.', L.paid.tagline, 120)}
      ${lpText('paid-badge', 'Ribbon', 'Shown across the top of the card, e.g. “Best value”. Empty hides it.', L.paid.badge, 24)}
      ${lpText('paid-cta', 'Button label', 'Replaced by “Sign in” when sign-ups are closed.', L.paid.cta_label, 32)}
      ${lpList('paid-features', 'Bullet points', L.paid.features)}
    </section>
    <section class="settings-section card card-pad">
      ${secHead('Small print')}
      ${lpText('yearly-note', 'Yearly note', 'Appended to the yearly price, e.g. “2 months free”.', L.yearly_note, 32)}
      ${lpText('footnote', 'Footnote', 'Under the plans. Empty hides it.', L.footnote, 200)}
    </section>`;
}

function lpText(key, label, desc, value, max) {
  return `<div class="setting-row"><div class="min-w-0"><div class="title">${esc(label)}</div>
    ${desc ? `<div class="desc">${esc(desc)}</div>` : ''}</div>
    <div class="adm-ctl"><input class="input input-sm" id="lp-${key}" type="text" maxlength="${max}"
      autocomplete="off" aria-label="${esc(label)}" value="${esc(value || '')}"></div></div>`;
}

function lpList(key, label, lines) {
  return `<div class="setting-row"><div class="min-w-0"><div class="title">${esc(label)}</div>
    <div class="desc">One per line, up to 12. Blank lines are dropped.</div></div>
    <div class="adm-ctl"><textarea class="textarea" id="lp-${key}" rows="7" spellcheck="false"
      aria-label="${esc(label)}">${esc((lines || []).join('\n'))}</textarea></div></div>`;
}

function lpValue(key) {
  const el = document.getElementById(`lp-${key}`);
  return el ? el.value.trim() : '';
}

async function saveAdminLanding() {
  const lines = (key) => lpValue(key).split('\n').map((x) => x.trim()).filter(Boolean);
  const landing = {
    heading: lpValue('heading'), sub: lpValue('sub'),
    paid: { name: lpValue('paid-name'), tagline: lpValue('paid-tagline'), badge: lpValue('paid-badge'),
            cta_label: lpValue('paid-cta'), features: lines('paid-features') },
    yearly_note: lpValue('yearly-note'), footnote: lpValue('footnote'),
  };
  await api('/api/admin/billing/config', { method: 'PUT', body: { landing } });
  toast('Landing page saved', { type: 'success' });
  ALP.bar.set(false);
  return loadAdminLanding(ALP.host);
}

document.addEventListener('click', (e) => {
  if (e.target.closest('[data-act="reload-admin-landing"]')) loadAdminLanding(ALP.host);
});
