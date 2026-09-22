/* Shared pieces for the admin tabs that edit server settings (Billing, Sign-ups & email).
   Every tab's panel sits in the DOM at once, so each keeps its own id prefix, its own label/hint
   maps and its own save bar; nothing here reads a global. */

function secHead(title, badge) {
  return `<div class="adm-sec-head"><h2>${esc(title)}</h2>${badge
    ? `<span class="badge ${badge[1]}">${esc(badge[0])}</span>` : ''}</div>`;
}

/* One settings row for a flat key from /api/admin/billing/config: {value, set, locked}. */
function configField(prefix, key, meta, labels, hints) {
  if (!meta) return '';
  const id = `${prefix}-${key}`;
  const label = labels[key] || key;
  const secret = key.includes('secret') || key.includes('password');
  const desc = meta.locked ? 'Set in .env on the server.' : (hints[key] || '');
  const head = `<div class="min-w-0"><div class="title">${esc(label)}</div>
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
           data-act="peek-secret" data-for="${id}" aria-label="Show ${esc(label)}">${icon('eye')}</button></div>`
      : input}</div></div>`;
}

/* Collects the flat keys a tab owns, in the shape PUT /api/admin/billing/config expects. A masked
   value means "unchanged": sending it would blank the stored secret. */
function collectConfigFields(prefix, keys) {
  const body = {};
  keys.forEach((k) => {
    const input = document.getElementById(`${prefix}-${k}`);
    if (input && !input.disabled && input.value !== '••••••••') body[k] = input.value.trim();
  });
  return body;
}

/* A save bar rather than a button at the bottom: these forms are long enough that the button would
   be off screen while typing. It is fixed to the viewport, not the panel, so leaving the tab must
   take it along. Same pattern as the AI settings page. */
function adminDirtyBar(tab, { saveAct, discardAct, unsavedText }) {
  let el = null;
  let dirty = false;
  const set = (v) => {
    dirty = v;
    if (v && !el) {
      el = document.createElement('div');
      el.className = 'floatbar';
      el.setAttribute('role', 'status');
      el.innerHTML = `<span>Unsaved changes</span><span class="sep"></span>
        <button type="button" class="btn btn-ghost btn-sm" data-act="${discardAct}">Discard</button>
        <button type="button" class="btn btn-primary btn-sm" data-act="${saveAct}">Save</button>`;
      document.body.appendChild(el);
    } else if (!v && el) {
      el.remove();
      el = null;
    }
  };
  window.addEventListener('ispend:admin-tab', (e) => {
    if (e.detail === tab) return;
    if (dirty) toast(unsavedText, { type: 'info' });
    set(false);
  });
  return { set, get dirty() { return dirty; } };
}

document.addEventListener('click', (e) => {
  const el = e.target.closest('[data-act="peek-secret"]');
  if (!el) return;
  const input = document.getElementById(el.dataset.for);
  const show = input.type === 'password';
  input.type = show ? 'text' : 'password';
  el.innerHTML = icon(show ? 'eye-off' : 'eye');
});
