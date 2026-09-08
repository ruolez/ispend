/* Insights: recurring charges, anomalies and optional AI-written monthly insights. */
const state = { month: null, data: null, currency: 'USD', me: null, cats: new Map(), generating: false, seq: 0 };
const KIND = { unusual_amount: { label: 'Unusual amount', icon: 'trending-up' }, new_merchant: { label: 'New merchant', icon: 'sparkles' }, duplicate_charge: { label: 'Possible duplicate', icon: 'copy' } };

initNav('insights').then(async (me) => {
  state.me = me;
  state.currency = await store.displayCurrency();
  state.month = qs().month || periodMonth(periodGet()) || currentMonth();
  $('#month').value = state.month;
  $('[data-act="prev-month"]').innerHTML = icon('chevron-left'); $('[data-act="next-month"]').innerHTML = icon('chevron-right');
  $('.ai-mark').innerHTML = icon('sparkles');
  $('#month').addEventListener('change', (e) => { state.month = e.target.value || currentMonth(); sync(); load(); });
  document.body.addEventListener('click', onAction);
  await store.categoriesFlat().then((f) => { state.cats = new Map(f.map((c) => [c.id, c])); }).catch(() => {});
  load();
});

function currentMonth() { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`; }
function shiftMonth(ym, delta) { const [y, m] = ym.split('-').map(Number); const d = new Date(y, m - 1 + delta, 1); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`; }
function sync() { $('#month').value = state.month; periodSet({ preset: state.month === currentMonth() ? 'this-month' : `month:${state.month}` }); setQs({ month: state.month === currentMonth() ? null : state.month }, { replace: true }); }

async function load() {
  $('#ins-error').innerHTML = '';
  $$('#ins-stats .stat').forEach((s) => s.classList.add('is-loading'));
  $('#recurring').innerHTML = `<div class="card-body">${ui.skeletonList(5)}</div>`;
  $('#anomalies').innerHTML = ui.skeletonList(2);
  $('#ai-panel').innerHTML = `<div class="col gap-3">${ui.skeleton('90%', 14)}${ui.skeleton('100%', 14)}${ui.skeleton('70%', 14)}</div>`;
  const seq = ++state.seq;
  let data;
  try { data = await api(`/api/insights${toQuery({ month: state.month })}`); }
  catch (err) { if (seq === state.seq) $('#ins-error').innerHTML = ui.errorBox(err.message, { retry: 'reload' }); return; }
  if (seq !== state.seq) return;
  state.data = data;
  setPageTitle(fmtMonth(state.month, { long: true }));
  renderStats(); renderRecurring(); renderAnomalies(); renderAI();
}

function renderStats() {
  const d = state.data; const active = d.recurring.filter((r) => r.is_active);
  const monthly = active.reduce((s, r) => s + (r.monthly_equivalent || 0), 0);
  const stats = $$('#ins-stats .stat');
  stats[0].querySelector('.stat-value').textContent = fmtNumber(active.length);
  stats[0].querySelector('.stat-delta').innerHTML = `<span class="stat-delta-vs">${d.recurring.length - active.length ? `${d.recurring.length - active.length} inactive` : 'detected from your statements'}</span>`;
  stats[1].querySelector('.stat-value').textContent = fmtMoney(monthly, state.currency);
  stats[1].querySelector('.stat-delta').innerHTML = `<span class="stat-delta-vs">${fmtMoney(monthly * 12, state.currency, { compact: true })} per year</span>`;
  stats[2].querySelector('.stat-value').textContent = fmtNumber(d.anomalies.length);
  stats[2].querySelector('.stat-delta').innerHTML = `<span class="stat-delta-vs">in the last 45 days</span>`;
  stats.forEach((s) => s.classList.remove('is-loading'));
}

function daysLabel(n) { if (n == null) return ''; if (n < 0) return `${Math.abs(n)}d overdue`; if (n === 0) return 'Today'; if (n === 1) return 'Tomorrow'; return `in ${n} days`; }
function renderRecurring() {
  const host = $('#recurring'); const rows = state.data.recurring; const cur = state.currency;
  if (!rows.length) { host.innerHTML = `<div class="card-body">${ui.emptyState({ icon: 'repeat', title: 'No recurring charges yet', body: 'Subscriptions and bills show up here once the same merchant repeats on a regular cadence across a few statements.' })}</div>`; return; }
  host.innerHTML = `<div class="tbl-wrap tbl-wrap--flush"><table class="tbl rec-table"><thead><tr><th>Merchant</th><th>Category</th><th class="col-cadence">Cadence</th><th class="right">Amount</th><th class="col-last">Last</th><th class="right">Next</th><th class="col-actions"><span class="sr-only">Actions</span></th></tr></thead><tbody>
    ${rows.map((r) => { const c = state.cats.get(r.category_id); const n = r.days_until_next; return `<tr data-key="${esc(r.merchant_key)}" class="${r.is_active ? '' : 'text-3'}">
      <td><div class="rec-merchant"><a href="/transactions.html${toQuery({ q: r.merchant_name, range: 'all' })}">${esc(r.merchant_name)}</a><span class="sub">${plural(r.occurrences, 'charge')}${r.is_active ? '' : ' · inactive'}</span></div></td>
      <td>${c ? `<span class="catchip"><i class="dot" style="--c:var(--${esc(c.color || c.parent_color || 'c1')})"></i><span class="catchip-label">${esc(c.name)}</span></span>` : '<span class="text-4">—</span>'}</td>
      <td class="col-cadence"><span class="badge badge-neutral cadence" aria-label="Cadence: ${esc(r.cadence)}">${esc(r.cadence)}</span></td>
      <td class="right"><div class="rec-amt"><span class="num fw-500">${fmtMoney(r.median_amount, cur)}</span>${r.amount_kind === 'variable' ? '<span class="badge badge-warning" aria-label="Amount varies between charges">varies</span>' : ''}</div></td>
      <td class="col-last text-3">${fmtDate(r.last_date, { year: true })}</td>
      <td class="right"><div class="rec-next"><span class="when ${n != null && n < 0 ? 'is-over' : (n != null && n <= 3 ? 'is-soon' : '')}">${r.is_active ? esc(daysLabel(n)) : '—'}</span><span class="date">${fmtDate(r.next_expected)}</span></div></td>
      <td class="col-actions"><button type="button" class="btn btn-icon btn-ghost btn-xs rec-dismiss" data-act="dismiss-rec" data-key="${esc(r.merchant_key)}" data-name="${esc(r.merchant_name)}" aria-label="Not a subscription — hide">${icon('x')}</button></td></tr>`; }).join('')}</tbody></table></div>
    <div class="rec-foot"><span>${fmtNumber(rows.filter((r) => r.is_active).length)} active</span><span>${fmtMoney(rows.filter((r) => r.is_active).reduce((s, r) => s + r.monthly_equivalent, 0), cur)} / month</span></div>`;
}

function renderAnomalies() {
  const host = $('#anomalies'); const rows = state.data.anomalies; const cur = state.currency;
  if (!rows.length) { host.innerHTML = ui.emptyState({ icon: 'check-circle', title: 'Nothing unusual', body: 'No charges stood out in the last 45 days.' }); return; }
  host.innerHTML = `<div class="anom-list">${rows.map((a) => { const k = KIND[a.kind] || { label: a.kind, icon: 'alert-triangle' }; return `<div class="anom anom--${esc(a.kind)}" data-id="${a.transaction_id}">
    <span class="anom-ico">${icon(k.icon)}</span>
    <div><div class="anom-title">${esc(a.merchant_name)}<span class="badge badge-neutral" aria-label="Anomaly type: ${esc(k.label)}">${esc(k.label)}</span></div><div class="anom-text">${esc(a.text || describe(a))}</div><div class="anom-meta">${fmtDate(a.txn_date, { year: true })}${a.delta != null && a.kind === 'unusual_amount' ? ` · ${fmtMoney(Math.abs(a.delta), cur)} above usual` : ''}</div></div>
    <div class="anom-actions"><span class="anom-amt">${fmtMoney(Math.abs(a.amount), cur)}</span><div class="row"><a class="btn btn-ghost btn-xs" href="/transactions.html${toQuery({ open: a.transaction_id, range: 'all' })}">View</a><button type="button" class="btn btn-ghost btn-xs" data-act="dismiss-anom" data-id="${a.transaction_id}">Looks fine</button></div></div></div>`; }).join('')}</div>`;
}
function describe(a) {
  if (a.kind === 'unusual_amount') return 'This charge is much larger than what this merchant usually costs you.';
  if (a.kind === 'new_merchant') return 'First time you have paid this merchant, and it was a sizable amount.';
  if (a.kind === 'duplicate_charge') return 'Same merchant, same amount, same day. Could be a double charge.';
  return '';
}

/* ---------- AI panel ---------- */
function renderAI() {
  const ai = state.data.ai; const host = $('#ai-panel'); const actions = $('#ai-actions');
  actions.innerHTML = '';
  host.setAttribute('aria-busy', state.generating ? 'true' : 'false');
  if (ai.status === 'disabled') {
    const admin = state.me.role === 'admin';
    host.innerHTML = ui.emptyState({ icon: 'sparkles', title: 'AI insights are off', body: admin ? 'Add an OpenRouter key and turn on Monthly insights to get a written read on your spending each month.' : 'Add your own OpenRouter key (or use one shared by your admin) and turn on Monthly insights in Settings › AI.', action: { label: 'Open AI settings', href: '/settings.html#ai' } });
    return;
  }
  if (state.generating) {
    actions.innerHTML = `<button type="button" class="btn btn-ghost btn-xs" disabled aria-busy="true"><span class="spinner"></span>Generating…</button>`;
    host.innerHTML = `<div class="ai-thinking"><span class="hint"><span class="spinner"></span>Reading ${esc(fmtMonth(state.month, { long: true }))}…</span>${ui.skeleton('95%', 14)}${ui.skeleton('88%', 14)}${ui.skeleton('60%', 14)}<div class="mt-2"></div>${ui.skeleton('100%', 56)}${ui.skeleton('100%', 56)}</div>`;
    return;
  }
  if (ai.status !== 'ready' || !ai.content) {
    host.innerHTML = ui.emptyState({ icon: 'lightbulb', title: `No insights for ${fmtMonth(state.month, { long: true })} yet`, body: `Generate a short summary of what changed, what looks off and where you could save. Uses ${ai.model || 'your configured model'}.`, action: { label: 'Generate insights', act: 'generate' } });
    return;
  }
  let content = ai.content;
  if (typeof content === 'string') { try { content = JSON.parse(content); } catch { content = { summary: content, insights: [] }; } }
  const items = Array.isArray(content.insights) ? content.insights : [];
  const sevIcon = { good: 'check-circle', warn: 'alert-triangle', info: 'info' };
  host.innerHTML = `${content.summary ? `<p class="ai-summary">${esc(content.summary)}</p>` : ''}
    <div class="ai-list">${items.map((it) => { const sev = ['good', 'warn', 'info'].includes(it.severity) ? it.severity : 'info'; return `<div class="ai-item ai-item--${sev}" aria-label="${sev === 'warn' ? 'Warning' : sev === 'good' ? 'Good news' : 'Note'}: ${esc(it.title || '')}"><div class="t">${icon(sevIcon[sev])}${esc(it.title || '')}</div>${it.body ? `<div class="b">${esc(it.body)}</div>` : ''}${it.category ? `<div class="c">${esc(it.category)}</div>` : ''}</div>`; }).join('')}</div>
    <div class="ai-meta"><span>${ai.model ? esc(ai.model) : ''}</span><span>${ai.created_at ? `Generated ${fmtRelative(ai.created_at)}` : ''}</span></div>`;
  actions.innerHTML = `<button type="button" class="btn btn-ghost btn-xs" data-act="regenerate">${icon('refresh', 'ico-sm')}Regenerate</button>`;
}
async function generate(force) {
  state.generating = true; renderAI();
  try {
    const res = await api('/api/insights/generate', { method: 'POST', body: { month: state.month, force } });
    state.data.ai = res.ai;
    toast(res.ai.cached ? 'Showing saved insights' : 'Insights generated', { type: 'success' });
  } catch (err) { toast(err.message, { type: 'error', duration: 8000 }); }
  finally { state.generating = false; renderAI(); }
}

/* ---------- Actions ---------- */
async function onAction(e) {
  const b = e.target.closest('[data-act]'); if (!b) return;
  const act = b.dataset.act;
  if (act === 'reload') return load();
  if (act === 'prev-month' || act === 'next-month') { state.month = shiftMonth(state.month, act === 'next-month' ? 1 : -1); sync(); return load(); }
  if (act === 'generate') return generate(false);
  if (act === 'regenerate') { if (await ui.confirm({ title: 'Regenerate insights?', body: 'This makes a new AI request and replaces the saved insights for this month.', confirmText: 'Regenerate' })) generate(true); return; }
  if (act === 'dismiss-rec') {
    const key = b.dataset.key; const name = b.dataset.name;
    try {
      await api('/api/reports/recurring/dismiss', { method: 'POST', body: { merchant_key: key } });
      state.data.recurring = state.data.recurring.filter((r) => r.merchant_key !== key); renderRecurring(); renderStats();
      toast(`${name} hidden from recurring`, { type: 'success', action: { label: 'Undo', fn: async () => { await api(`/api/reports/recurring/dismiss/${encodeURIComponent(key)}`, { method: 'DELETE' }); load(); } } });
    } catch (err) { toast(err.message, { type: 'error' }); }
    return;
  }
  if (act === 'dismiss-anom') {
    const id = Number(b.dataset.id);
    try {
      await api(`/api/insights/anomalies/${id}/dismiss`, { method: 'POST' });
      state.data.anomalies = state.data.anomalies.filter((a) => a.transaction_id !== id); renderAnomalies(); renderStats();
      toast('Marked as fine', { type: 'success', action: { label: 'Undo', fn: async () => { await api(`/api/insights/anomalies/${id}/dismiss`, { method: 'DELETE' }); load(); } } });
    } catch (err) { toast(err.message, { type: 'error' }); }
  }
}
