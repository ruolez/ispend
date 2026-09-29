/* Admin › Settings: how this iSpend runs, one page per subject, at #settings/<page>. Each page is its
   own file and registers here:
     AdminSettings.register('billing', { label, icon, sub, load(host, ctx) })
   host is the page's own element (kept between visits); a page that edits settings takes a save bar
   from adminDirtyBar(`settings/${key}`, …), and the shell asks before its unsaved edits are left. */

const ASET = { pages: {}, hosts: {} };
const SETTINGS_ORDER = ['account', 'billing', 'signups', 'email', 'support', 'ai', 'landing', 'retention'];

window.AdminSettings = {
  register(key, meta) { ASET.pages[key] = { key, ...meta }; },
  keys: () => SETTINGS_ORDER.filter((k) => ASET.pages[k]).concat(Object.keys(ASET.pages).filter((k) => !SETTINGS_ORDER.includes(k))),
  meta: (key) => ASET.pages[key],
};

AdminPanels.register('settings', {
  label: 'Settings', icon: 'settings', group: 'footer',
  sub: 'How this iSpend runs: billing, sign-ups, email, AI and your own account',
  markup: `<div class="settings-layout adm-settings">
      <nav class="settings-nav" id="set-nav" aria-label="Settings pages"></nav>
      <div class="adm-settings-body" id="set-body"></div>
    </div>`,
  load: loadSettingsPage,
});

async function loadSettingsPage(host, ctx) {
  const keys = AdminSettings.keys();
  const key = ASET.pages[ctx.route.id] ? ctx.route.id : keys[0];
  $('#set-nav').innerHTML = keys.map((k) => {
    const m = ASET.pages[k];
    return `<a class="nav-item" href="#settings/${k}" ${k === key ? 'aria-current="page"' : ''}>${icon(m.icon)}<span class="label">${esc(m.label)}</span></a>`;
  }).join('');
  const body = $('#set-body');
  if (!ASET.hosts[key]) {
    const page = document.createElement('section');
    page.className = 'adm-settings-page';
    page.dataset.spage = key;
    page.setAttribute('aria-labelledby', `set-h-${key}`);
    page.innerHTML = `<header class="adm-settings-head"><h2 id="set-h-${key}">${esc(ASET.pages[key].label)}</h2>
      ${ASET.pages[key].sub ? `<p>${esc(ASET.pages[key].sub)}</p>` : ''}</header><div class="adm-settings-content"></div>`;
    body.appendChild(page);
    ASET.hosts[key] = page;
  }
  Object.entries(ASET.hosts).forEach(([k, el]) => { el.hidden = k !== key; });
  setPageTitle(`Settings · ${ASET.pages[key].label}`);
  return ASET.pages[key].load(ASET.hosts[key].querySelector('.adm-settings-content'), ctx);
}
