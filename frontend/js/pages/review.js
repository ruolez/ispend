/* Review queue: merchant groups (default) or one-by-one; keyboard first. */
const SKIP_KEY = 'ispend.review.skipped';
const rv = {
  mode: 'merchant', groups: [], remaining: 0, remainingItems: 0, done: 0, focus: -1,
  cats: new Map(), catsFlat: [], settings: null, skipped: new Set(), loading: false,
};
function loadSkipped() { try { return new Set(JSON.parse(sessionStorage.getItem(SKIP_KEY) || '[]')); } catch { return new Set(); } }
function saveSkipped() { try { sessionStorage.setItem(SKIP_KEY, JSON.stringify(Array.from(rv.skipped))); } catch { /* ignore */ } }

initNav('review').then(async () => {
  rv.skipped = loadSkipped();
  const q = qs();
  if (q.mode === 'single') rv.mode = 'single';
  await loadRefs();
  rv.settings = await store.settings().catch(() => ({}));
  const aiBtn = $('#btn-ai');
  if (rv.settings && rv.settings.ai_categorize_enabled) { aiBtn.hidden = false; aiBtn.innerHTML = `${icon('sparkles', 'ico-sm')}<span class="label">Ask AI</span>`; aiBtn.addEventListener('click', askAI); }
  $('#rv-mode').addEventListener('click', (e) => { const b = e.target.closest('[data-mode]'); if (!b || b.dataset.mode === rv.mode) return; rv.mode = b.dataset.mode; setQs({ mode: rv.mode === 'single' ? 'single' : null }); paintMode(); load(); });
  $('#rv-list').addEventListener('click', onCardClick);
  $('#rv-list').addEventListener('change', onCardChange);
  $('#rv-list').addEventListener('focusin', (e) => { const card = e.target.closest('.rv-card'); if (card) setFocus(Number(card.dataset.idx), { scroll: false }); });
  document.body.addEventListener('click', (e) => { if (e.target.closest('[data-act="reload"]')) load(); if (e.target.closest('[data-act="unskip"]')) { rv.skipped.clear(); saveSkipped(); load(); } });
  store.on('categories-changed', async () => { await loadRefs(); render(); });
  registerShortcuts();
  paintMode();
  await load();
});

async function loadRefs() {
  rv.catsFlat = await store.categoriesFlat();
  rv.cats = new Map(rv.catsFlat.map((c) => [c.id, c]));
}
function catOf(id) { return rv.cats.get(Number(id)) || null; }
function paintMode() { $$('#rv-mode .seg-btn').forEach((b) => { const on = b.dataset.mode === rv.mode; b.classList.toggle('active', on); b.setAttribute('aria-pressed', String(on)); }); }
function announce(t) { const l = $('#rv-live'); l.textContent = ''; setTimeout(() => { l.textContent = t; }, 30); }

/* ---------- data ---------- */
async function load() {
  rv.loading = true;
  $('#rv-list').innerHTML = `<div class="rv-card">${ui.skeleton('40%', 18)}<div class="mt-2">${ui.skeleton('60%', 12)}</div><div class="mt-3">${ui.skeleton('100%', 32)}</div></div><div class="rv-card">${ui.skeleton('35%', 18)}<div class="mt-2">${ui.skeleton('55%', 12)}</div></div>`;
  try {
    const r = await api(`/api/review?mode=${rv.mode}&limit=100`);
    if (rv.mode === 'merchant') {
      rv.groups = r.groups.map((g) => ({ ...g, excluded: new Set(), expanded: false, always: g.count >= 1, pattern: g.key, patternOpen: false, rows: null }));
      rv.remaining = r.remaining; rv.remainingItems = r.remaining_items != null ? r.remaining_items : r.groups.reduce((a, g) => a + g.count, 0);
    } else {
      rv.groups = r.items.map((t) => ({ key: t.merchant_key, display: t.merchant_name, count: 1, total: t.amount, first: t.txn_date, last: t.txn_date, ids: [t.id], item: t, suggestion: t.category_status === 'suggested' && t.category_id ? { category_id: t.category_id, confidence: t.category_confidence, source: t.category_source } : null, sample_description: t.description_raw, excluded: new Set(), always: false, pattern: t.merchant_key, patternOpen: false, currency: t.currency }));
      rv.remaining = r.remaining; rv.remainingItems = r.remaining;
    }
  } catch (err) { $('#rv-list').innerHTML = ui.errorBox(err.message, { retry: 'reload' }); rv.loading = false; return; }
  rv.loading = false;
  rv.focus = -1;
  render();
  const first = visibleGroups()[0];
  if (first) setFocus(0, { scroll: false });
}
function visibleGroups() { return rv.groups.filter((g) => !rv.skipped.has(g.key) || rv.mode === 'single' && !rv.skipped.has(`t${g.ids[0]}`)); }
function groupKey(g) { return rv.mode === 'single' ? `t${g.ids[0]}` : g.key; }

/* ---------- render ---------- */
function render() {
  const groups = rv.groups.filter((g) => !rv.skipped.has(groupKey(g)));
  const skippedN = rv.groups.length - groups.length;
  const total = rv.done + rv.remainingItems;
  $('#rv-progress').innerHTML = `<span class="rp-text"><b>${fmtNumber(rv.remainingItems)}</b> charge${rv.remainingItems === 1 ? '' : 's'} left${rv.mode === 'merchant' ? ` in <b>${fmtNumber(rv.remaining)}</b> merchant${rv.remaining === 1 ? '' : 's'}` : ''}</span><div class="progress"><span style="width:${total ? Math.round((rv.done / total) * 100) : 0}%"></span></div><span class="rp-text text-3">${fmtNumber(rv.done)} done this session${skippedN ? ` · <button type="button" class="btn btn-ghost btn-xs" data-act="unskip">${fmtNumber(skippedN)} skipped</button>` : ''}</span>`;
  const host = $('#rv-list');
  if (!groups.length) {
    host.innerHTML = `<div class="card">${ui.emptyState({ icon: 'inbox-check', title: rv.remainingItems ? 'Everything visible is skipped' : 'All caught up', body: rv.remainingItems ? 'You skipped the remaining merchants for this session.' : 'Every charge has a category. New imports will show up here when they need a decision.', action: rv.remainingItems ? { label: 'Show skipped', act: 'unskip' } : { label: 'Import a statement', href: '/import.html' } })}</div>`;
    host.classList.remove('has-focus');
    return;
  }
  host.innerHTML = groups.map((g, i) => cardHtml(g, i)).join('');
  host.classList.toggle('has-focus', rv.focus >= 0);
}
function cardHtml(g, idx) {
  const cur = g.currency || 'USD';
  const sug = g.suggestion && catOf(g.suggestion.category_id) ? { ...g.suggestion, cat: catOf(g.suggestion.category_id) } : null;
  const n = g.count - g.excluded.size;
  const single = rv.mode === 'single';
  return `<article class="rv-card ${single ? 'rv-single' : ''} ${idx === rv.focus ? 'is-focused' : ''}" data-idx="${idx}" data-key="${esc(groupKey(g))}" tabindex="0" aria-label="${esc(g.display)}">
    <div class="rv-head">
      <div style="min-width:0">
        <div class="rv-name">${esc(g.display)}</div>
        <div class="rv-meta">${single ? `<span>${esc(fmtDateLong(g.first))}</span>` : `<span>${plural(g.count, 'charge')}</span><span>·</span>`}<span class="amt">${fmtMoney(g.total, cur, { sign: 'always' })}</span>${!single && g.first !== g.last ? `<span>·</span><span>${fmtDate(g.first)} – ${fmtDate(g.last)}</span>` : !single ? `<span>·</span><span>${fmtDate(g.first)}</span>` : ''}</div>
        ${g.sample_description ? `<div class="rv-raw" title="${esc(g.sample_description)}">${esc(g.sample_description)}</div>` : ''}
      </div>
      ${!single ? `<button type="button" class="btn btn-ghost btn-xs" data-cact="expand" aria-expanded="${g.expanded}">${icon(g.expanded ? 'chevron-up' : 'chevron-down', 'ico-sm')}${g.expanded ? 'Hide' : 'Show'} charges</button>` : ''}
    </div>
    ${sug ? `<div class="rv-suggest">${icon('sparkles')}<span>Suggested:</span><button type="button" class="catchip" data-cact="pick"><i class="dot" style="--c:var(--${esc(sug.cat.color || sug.cat.parent_color || 'c1')})"></i><span class="catchip-label">${esc(sug.cat.path)}</span></button><span class="rv-conf">${sug.confidence != null ? `${Math.round(Number(sug.confidence) * 100)}% · ` : ''}${sug.source === 'ai' ? 'AI' : sug.source === 'merchant' ? 'similar merchant' : 'built-in hints'}</span></div>` : ''}
    <div class="rv-actions">
      ${sug ? `<button type="button" class="btn btn-primary" data-cact="accept">Accept<kbd>↵</kbd></button><button type="button" class="btn btn-secondary" data-cact="pick">Choose<kbd>C</kbd></button>` : `<button type="button" class="btn btn-primary" data-cact="pick">Choose category<kbd>C</kbd></button>`}
      <button type="button" class="btn btn-ghost" data-cact="transfer" title="Transfer between your own accounts">Transfer<kbd>T</kbd></button>
      <button type="button" class="btn btn-ghost" data-cact="skip">Skip<kbd>S</kbd></button>
      <span class="grow-sep"></span>
      <span class="rv-always"><label class="switch switch-sm" title="Create a rule so future charges from this merchant are categorized automatically"><input type="checkbox" data-cfield="always" ${g.always ? 'checked' : ''}><span class="switch-track"></span>Always do this</label><button type="button" class="btn btn-icon btn-ghost btn-xs" data-cact="pattern" title="Edit the rule pattern" aria-label="Edit rule pattern">${icon('pencil', 'ico-sm')}</button></span>
    </div>
    <div class="rv-pattern ${g.patternOpen ? 'is-open' : ''}"><span>Rule: merchant</span><select class="select input-sm" data-cfield="ptype"><option value="equals" ${g.ptype !== 'contains' ? 'selected' : ''}>equals</option><option value="contains" ${g.ptype === 'contains' ? 'selected' : ''}>description contains</option></select><input class="input input-sm" data-cfield="pattern" value="${esc(g.pattern)}" aria-label="Rule pattern"></div>
    ${g.expanded && g.rows ? `<div class="rv-rows">${g.rows.map((r) => `<div class="rv-row ${g.excluded.has(r.id) ? 'is-excluded' : ''}"><input type="checkbox" class="check" data-crow="${r.id}" ${g.excluded.has(r.id) ? '' : 'checked'} aria-label="Include"><span class="text-3 num">${fmtDate(r.txn_date)}</span><span class="desc">${esc(r.description_raw)}<small>${esc(r.merchant_name !== g.display ? r.merchant_name : '')}</small></span><span class="amt ${r.amount > 0 ? 'amt--income' : ''}">${fmtMoney(r.amount, r.currency || cur)}</span></div>`).join('')}${n < g.count ? `<div class="text-3 fs-sm mt-1">${g.count - n} unchecked charge${g.count - n === 1 ? '' : 's'} will stay in the queue.</div>` : ''}</div>` : g.expanded ? `<div class="rv-rows">${ui.skeletonList(Math.min(g.count, 4))}</div>` : ''}
  </article>`;
}
function rerenderCard(g) {
  const idx = rv.groups.filter((x) => !rv.skipped.has(groupKey(x))).indexOf(g);
  const el = $(`.rv-card[data-key="${CSS.escape(groupKey(g))}"]`);
  if (!el) return;
  const tmp = document.createElement('div'); tmp.innerHTML = cardHtml(g, idx);
  el.replaceWith(tmp.firstElementChild);
}

/* ---------- focus ---------- */
function setFocus(idx, { scroll = true } = {}) {
  const cards = $$('.rv-card');
  rv.focus = Math.max(-1, Math.min(idx, cards.length - 1));
  cards.forEach((c, i) => c.classList.toggle('is-focused', i === rv.focus));
  $('#rv-list').classList.toggle('has-focus', rv.focus >= 0);
  const card = cards[rv.focus];
  if (card) {
    if (scroll) card.scrollIntoView({ block: 'center', behavior: 'smooth' });
    if (document.activeElement && !card.contains(document.activeElement)) card.focus({ preventScroll: true });
    const g = focusedGroup();
    if (g) announce(`${g.display}, ${plural(g.count, 'charge')}, ${fmtMoney(g.total, g.currency || 'USD')}${g.suggestion && catOf(g.suggestion.category_id) ? `, suggested ${catOf(g.suggestion.category_id).name}` : ''}`);
  }
}
function focusedGroup() {
  const card = $$('.rv-card')[rv.focus];
  if (!card) return null;
  return rv.groups.find((g) => groupKey(g) === card.dataset.key) || null;
}
function registerShortcuts() {
  const ok = () => !ui.layers.length && !rv.loading;
  ui.shortcuts.register('j', () => setFocus(rv.focus + 1), { when: ok, description: 'Next merchant' });
  ui.shortcuts.register('k', () => setFocus(Math.max(0, rv.focus - 1)), { when: ok, description: 'Previous merchant' });
  ui.shortcuts.register('ArrowDown', () => setFocus(rv.focus + 1), { when: ok });
  ui.shortcuts.register('ArrowUp', () => setFocus(Math.max(0, rv.focus - 1)), { when: ok });
  ui.shortcuts.register('Enter', () => { const g = focusedGroup(); if (!g) return; if (g.suggestion) accept(g); else pick(g); }, { when: ok, description: 'Accept suggestion' });
  ui.shortcuts.register('c', () => { const g = focusedGroup(); if (g) pick(g); }, { when: ok, description: 'Choose category' });
  ui.shortcuts.register('t', () => { const g = focusedGroup(); if (g) transfer(g); }, { when: ok, description: 'Mark as transfer' });
  ui.shortcuts.register('s', () => { const g = focusedGroup(); if (g) skip(g); }, { when: ok, description: 'Skip' });
  ui.shortcuts.register('e', () => { const g = focusedGroup(); if (g && rv.mode === 'merchant') toggleExpand(g); }, { when: ok, description: 'Show charges' });
  window.PAGE_SHORTCUTS = [{ title: 'Review', items: [['j / k', 'Move between merchants'], ['↵', 'Accept suggestion'], ['c', 'Choose category'], ['t', 'Mark as transfer'], ['s', 'Skip for now'], ['e', 'Show charges']] }];
}

/* ---------- interactions ---------- */
function groupFromEvent(e) { const card = e.target.closest('.rv-card'); return card ? rv.groups.find((g) => groupKey(g) === card.dataset.key) : null; }
function onCardClick(e) {
  const g = groupFromEvent(e);
  if (!g) return;
  const b = e.target.closest('[data-cact]');
  if (!b) return;
  const idx = $$('.rv-card').indexOf(e.target.closest('.rv-card'));
  if (idx !== rv.focus) setFocus(idx, { scroll: false });
  switch (b.dataset.cact) {
    case 'accept': return accept(g);
    case 'pick': return pick(g, b);
    case 'transfer': return transfer(g);
    case 'skip': return skip(g);
    case 'expand': return toggleExpand(g);
    case 'pattern': { g.patternOpen = !g.patternOpen; rerenderCard(g); if (g.patternOpen) { const inp = $(`.rv-card[data-key="${CSS.escape(groupKey(g))}"] [data-cfield="pattern"]`); if (inp) inp.focus(); } return; }
    default:
  }
}
function onCardChange(e) {
  const g = groupFromEvent(e);
  if (!g) return;
  const t = e.target;
  if (t.matches('[data-cfield="always"]')) g.always = t.checked;
  else if (t.matches('[data-cfield="pattern"]')) g.pattern = t.value.trim();
  else if (t.matches('[data-cfield="ptype"]')) g.ptype = t.value;
  else if (t.matches('[data-crow]')) { const id = Number(t.dataset.crow); if (t.checked) g.excluded.delete(id); else g.excluded.add(id); t.closest('.rv-row').classList.toggle('is-excluded', !t.checked); }
}
async function toggleExpand(g) {
  g.expanded = !g.expanded;
  rerenderCard(g);
  if (g.expanded && !g.rows) {
    try {
      const rows = await api(`/api/transactions/merchant/${encodeURIComponent(g.key)}?limit=200`);
      g.rows = rows.filter((r) => g.ids.includes(r.id));
    } catch (err) { g.rows = []; toast(err.message, { type: 'error' }); }
    rerenderCard(g);
  }
}
function includedIds(g) { return g.ids.filter((id) => !g.excluded.has(id)); }
function ruleFor(g, categoryId) {
  if (!g.always || !g.pattern) return null;
  if (g.ptype === 'contains') return { pattern: g.pattern, match_type: 'contains', match_field: 'description_clean', name: `${g.display} → ${(catOf(categoryId) || {}).name || 'transfer'}` };
  return { pattern: g.pattern, match_type: 'equals', match_field: 'merchant_key', name: `${g.display} → ${(catOf(categoryId) || {}).name || 'transfer'}` };
}
async function resolve(g, body, label) {
  const ids = includedIds(g);
  if (!ids.length) { toast('Every charge in this group is unchecked', { type: 'error' }); return; }
  try {
    const r = await api('/api/review/resolve', { method: 'POST', body: { ids, ...body } });
    rv.done += r.updated || ids.length;
    rv.remainingItems = Math.max(0, rv.remainingItems - (r.updated || ids.length));
    if (ids.length === g.ids.length) { rv.remaining = Math.max(0, rv.remaining - 1); leave(g); }
    else { g.ids = g.ids.filter((id) => g.excluded.has(id)); g.count = g.ids.length; g.excluded = new Set(); g.rows = null; g.expanded = false; rerenderCard(g); }
    toast(`${label}${r.rule_id ? ' · rule created' : ''}`, { type: 'success' });
    window.dispatchEvent(new Event('ispend:transactions-changed'));
    render();
  } catch (err) { toast(err.message, { type: 'error' }); }
}
function leave(g) {
  const el = $(`.rv-card[data-key="${CSS.escape(groupKey(g))}"]`);
  rv.groups = rv.groups.filter((x) => x !== g);
  if (!el) return render();
  el.style.maxHeight = `${el.offsetHeight}px`;
  requestAnimationFrame(() => el.classList.add('is-leaving'));
  setTimeout(() => { render(); setFocus(Math.min(rv.focus, $$('.rv-card').length - 1), { scroll: true }); }, 260);
}
function accept(g) {
  if (!g.suggestion) return pick(g);
  const c = catOf(g.suggestion.category_id);
  resolve(g, { category_id: g.suggestion.category_id, create_rule: ruleFor(g, g.suggestion.category_id) }, `${g.display} → ${c ? c.name : 'category'}`);
}
function pick(g, anchor) {
  anchor = anchor || $(`.rv-card[data-key="${CSS.escape(groupKey(g))}"] [data-cact="pick"]`);
  if (!anchor) return;
  categoryPicker({ anchor, value: g.suggestion ? g.suggestion.category_id : null, suggestedId: g.suggestion ? g.suggestion.category_id : null, onPick: (cat) => { if (!cat) return; resolve(g, { category_id: cat.id, create_rule: ruleFor(g, cat.id) }, `${g.display} → ${cat.name}`); } });
}
function transfer(g) { resolve(g, { category_id: null, mark_transfer: true, create_rule: ruleFor(g, null) }, `${g.display} marked as transfer`); }
function skip(g) {
  rv.skipped.add(groupKey(g)); saveSkipped();
  const el = $(`.rv-card[data-key="${CSS.escape(groupKey(g))}"]`);
  if (el) { el.style.maxHeight = `${el.offsetHeight}px`; requestAnimationFrame(() => el.classList.add('is-leaving')); }
  setTimeout(() => { render(); setFocus(Math.min(rv.focus, $$('.rv-card').length - 1)); }, 260);
}
async function askAI() {
  const btn = $('#btn-ai');
  const ids = rv.groups.filter((g) => !rv.skipped.has(groupKey(g)) && !g.suggestion).flatMap((g) => g.ids).slice(0, 40);
  if (!ids.length) { toast('Nothing left to suggest', { type: 'info' }); return; }
  btn.classList.add('is-loading');
  try {
    const r = await api('/api/review/suggest', { method: 'POST', body: { ids } });
    toast(`AI suggested categories for ${fmtNumber((r.items || []).length || r.suggested || 0)} charges`, { type: 'success' });
    await load();
  } catch (err) { toast(err.message, { type: 'error' }); }
  finally { btn.classList.remove('is-loading'); }
}
