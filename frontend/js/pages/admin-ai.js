/* Admin › Settings › AI: the OpenRouter key and model this service provides to every customer who
   has not added their own. What it costs is under Imports & AI. */

const AAI = { config: null, bar: null, models: null };
const AI_MASK = '••••••••';

AdminSettings.register('ai', {
  label: 'AI', icon: 'sparkles',
  sub: 'The AI key customers use when they have not added their own',
  load: loadAdminAi,
});

async function loadAdminAi(host) {
  AAI.host = host;
  if (!AAI.bar) {
    AAI.bar = adminDirtyBar('settings/ai', { save: saveAdminAi, discard: () => loadAdminAi(AAI.host) });
    host.addEventListener('input', () => AAI.bar.set(true));
  }
  if (AAI.bar.dirty) return;
  host.innerHTML = ui.skeletonList(3);
  try {
    AAI.config = await api('/api/admin/ai-config');
  } catch (err) {
    host.innerHTML = ui.errorBox(err.message, { retry: 'reload-admin-ai' });
    return;
  }
  const c = AAI.config;
  const on = !!(c.api_key && c.model);
  AAI.bar.set(false);
  host.innerHTML = `
    <section class="settings-section card card-pad">
      ${secHead('OpenRouter', on ? ['Offered to customers', 'badge-success'] : ['Off', 'badge-neutral'])}
      <div class="sub">With a key and a model here, every customer can turn on category suggestions and monthly insights
        without a key of their own; each still decides whether to use it. Leave both empty to turn it off.
        What it costs is under <a href="#imports">Imports &amp; AI</a>.</div>
      <div class="setting-row"><div class="min-w-0"><div class="title">API key</div>
        <div class="desc">From openrouter.ai/keys. Stored on the server and never shown again.</div></div>
        <div class="adm-ctl"><div class="input-group"><input class="input input-sm mono has-trailing" id="ai-key" type="password"
          autocomplete="off" spellcheck="false" placeholder="sk-or-v1-…" value="${esc(c.api_key)}">
          <button type="button" class="btn btn-icon btn-ghost btn-sm trailing" data-act="peek-secret" data-for="ai-key" aria-label="Show key">${icon('eye')}</button></div></div></div>
      <div class="setting-row"><div class="min-w-0"><div class="title">Model</div>
        <div class="desc">A model that returns structured JSON works best.</div></div>
        <div class="adm-ctl"><input class="input input-sm mono" id="ai-model" list="ai-models" autocomplete="off" spellcheck="false"
          placeholder="e.g. anthropic/claude-sonnet-5" value="${esc(c.model)}"><datalist id="ai-models"></datalist></div></div>
      <div class="row gap-2 mt-4 wrap"><button type="button" class="btn btn-secondary btn-sm" data-act="test-admin-ai">${icon('play', 'ico-sm')}Test</button>
        <span class="hint" id="ai-test-result">Tests what is typed above, before saving.</span></div>
    </section>`;
  loadAiModels();
}

async function loadAiModels() {
  try {
    if (!AAI.models) AAI.models = await api('/api/admin/ai-config/models');
    const list = Array.isArray(AAI.models) ? AAI.models : (AAI.models.models || AAI.models.data || []);
    const dl = $('#ai-models');
    if (dl) dl.innerHTML = list.slice(0, 400).map((m) => `<option value="${esc(m.id || m)}">${esc(m.name || '')}</option>`).join('');
  } catch { /* the catalogue is a convenience; typing an id still works */ }
}

async function saveAdminAi() {
  const key = $('#ai-key').value.trim();
  await api('/api/admin/ai-config', { method: 'PUT', body: { api_key: key, model: $('#ai-model').value.trim() } });
  toast('AI settings saved', { type: 'success' });
  AAI.bar.set(false);
  return loadAdminAi(AAI.host);
}

document.addEventListener('click', async (e) => {
  const el = e.target.closest('[data-act="test-admin-ai"], [data-act="reload-admin-ai"]');
  if (!el) return;
  if (el.dataset.act === 'reload-admin-ai') { loadAdminAi(AAI.host); return; }
  const out = $('#ai-test-result');
  await ui.busy(el, async () => {
    try {
      const r = await api('/api/admin/ai-config/test', { method: 'POST', body: { api_key: $('#ai-key').value.trim(), model: $('#ai-model').value.trim() } });
      out.innerHTML = `<span class="chip chip-ok">${icon('check')}Works · ${esc(r.model || '')} · ${fmtNumber(r.latency_ms || 0)} ms</span>`;
    } catch (err) {
      out.innerHTML = `<span class="chip chip-err">${icon('alert-circle')}${esc(err.message)}</span>`;
    }
  }, { silent: true });
});
