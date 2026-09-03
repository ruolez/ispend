/* Pickers built on ui.popover: categoryPicker (ARIA combobox) and dateRangePicker. */

const RECENT_CATS_KEY = 'ispend.recentCats';
function recentCategoryIds() { try { return JSON.parse(localStorage.getItem(RECENT_CATS_KEY) || '[]'); } catch { return []; } }
function rememberCategory(id) {
  const ids = [Number(id), ...recentCategoryIds().filter((x) => x !== Number(id))].slice(0, 5);
  try { localStorage.setItem(RECENT_CATS_KEY, JSON.stringify(ids)); } catch { /* ignore */ }
}

/* categoryPicker({anchor, value, onPick(category|null), allowCreate, suggestedId, allowNone})
   -> popover handle. Ranks prefix > word-start > contains, on name and parent name. */
async function categoryPicker({ anchor, value = null, onPick, allowCreate = true, suggestedId = null, allowNone = false, placeholder = 'Search categories…' } = {}) {
  const flat = await store.categoriesFlat();
  const byId = new Map(flat.map((c) => [c.id, c]));
  const el = document.createElement('div');
  el.className = 'cat-picker';
  el.style.width = '300px';
  const listId = `cp-${uid()}`;
  el.innerHTML = `
    <div class="cat-picker-search input-group" style="padding:8px 8px 4px">${icon('search')}
      <input class="input input-sm" role="combobox" aria-expanded="true" aria-autocomplete="list" aria-controls="${listId}" placeholder="${esc(placeholder)}" autocomplete="off" spellcheck="false" style="padding-left:34px"></div>
    <ul class="cat-picker-list menu" id="${listId}" role="listbox" style="max-height:340px;overflow-y:auto"></ul>`;
  const input = el.querySelector('input');
  const list = el.querySelector('ul');
  let active = 0;
  let rows = [];

  function score(c, q) {
    const n = c.name.toLowerCase(), p = (c.parent_name || '').toLowerCase();
    if (!q) return 1;
    if (n.startsWith(q)) return 100;
    if (n.split(/\s+/).some((w) => w.startsWith(q))) return 80;
    if (n.includes(q)) return 60;
    if (p.startsWith(q)) return 40;
    if (p.includes(q)) return 20;
    return 0;
  }
  function optionHtml(c, opts = {}) {
    const color = c.color || c.parent_color || 'c1';
    return `<li class="menu-item cp-opt" role="option" id="${listId}-${opts.key || c.id}" data-id="${c.id}" aria-selected="false">
      <i class="dot" style="--c:var(--${esc(color)})"></i><span class="grow truncate">${esc(c.name)}</span>
      ${c.parent_name ? `<span class="text-4 fs-xs truncate" style="max-width:110px">${esc(c.parent_name)}</span>` : ''}
      ${Number(value) === c.id ? `<span class="menu-check">${icon('check')}</span>` : ''}</li>`;
  }
  function build(q) {
    q = q.trim().toLowerCase();
    rows = [];
    let html = '';
    if (!q) {
      if (suggestedId && byId.has(Number(suggestedId))) {
        html += '<div class="menu-label">Suggested</div>' + optionHtml(byId.get(Number(suggestedId)), { key: 's' });
        rows.push({ id: Number(suggestedId) });
      }
      const recent = recentCategoryIds().map((id) => byId.get(id)).filter(Boolean);
      if (recent.length) {
        html += '<div class="menu-label">Recent</div>' + recent.map((c) => { rows.push({ id: c.id }); return optionHtml(c, { key: 'r' + c.id }); }).join('');
      }
      flat.filter((c) => c.depth === 0).forEach((p) => {
        html += `<div class="menu-label">${esc(p.name)}</div>`;
        rows.push({ id: p.id }); html += optionHtml({ ...p, name: p.name }, { key: 'p' + p.id });
        flat.filter((c) => c.parent_id === p.id).forEach((c) => { rows.push({ id: c.id }); html += optionHtml({ ...c, parent_name: null }, { key: 'c' + c.id }); });
      });
    } else {
      const ranked = flat.map((c) => ({ c, s: score(c, q) })).filter((x) => x.s > 0).sort((a, b) => b.s - a.s || (a.c.path || "").localeCompare(b.c.path || ""));
      ranked.forEach(({ c }) => { rows.push({ id: c.id }); html += optionHtml(c); });
      const exact = flat.some((c) => c.name.toLowerCase() === q);
      if (allowCreate && !exact) {
        rows.push({ create: input.value.trim() });
        html += `<li class="menu-item cp-opt cp-create" role="option" id="${listId}-create" data-create="1" aria-selected="false">${icon('plus')}<span class="grow">Create “${esc(input.value.trim())}”</span></li>`;
      }
      if (!rows.length) html = '<div class="palette-empty">No categories match</div>';
    }
    if (allowNone && !q) {
      rows.unshift({ id: null });
      html = `<li class="menu-item cp-opt" role="option" id="${listId}-none" data-id="" aria-selected="false"><i class="dot" style="--c:var(--warning)"></i><span class="grow">Uncategorized</span></li>` + html;
    }
    list.innerHTML = html;
    active = 0;
    highlight();
  }
  function highlight() {
    const opts = $$('.cp-opt', list);
    opts.forEach((o, i) => { o.classList.toggle('is-active', i === active); o.setAttribute('aria-selected', i === active); });
    const a = opts[active];
    if (a) { input.setAttribute('aria-activedescendant', a.id); a.scrollIntoView({ block: 'nearest' }); }
  }
  async function pickRow(r) {
    if (!r) return;
    if (r.create != null) {
      try {
        const cat = await api('/api/categories', { method: 'POST', body: { name: r.create } });
        store.invalidate('categories');
        await store.categories({ force: true });
        rememberCategory(cat.id);
        pop.close('pick');
        onPick && onPick({ ...cat, path: cat.name });
      } catch (err) { toast(err.message, { type: 'error' }); }
      return;
    }
    if (r.id == null) { pop.close('pick'); onPick && onPick(null); return; }
    rememberCategory(r.id);
    pop.close('pick');
    onPick && onPick(byId.get(r.id));
  }
  const pop = ui.popover(anchor, el, { onClose: () => { if (anchor && anchor.focus) anchor.focus(); } });
  build('');
  pop.position();
  input.addEventListener('input', () => build(input.value));
  input.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); active = Math.min(active + 1, rows.length - 1); highlight(); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); active = Math.max(active - 1, 0); highlight(); }
    else if (e.key === 'Enter') { e.preventDefault(); pickRow(rows[active]); }
    else if (e.key === 'Escape') { e.preventDefault(); pop.close('esc'); }
  });
  list.addEventListener('click', (e) => {
    const li = e.target.closest('.cp-opt'); if (!li) return;
    const i = $$('.cp-opt', list).indexOf(li);
    pickRow(rows[i]);
  });
  list.addEventListener('mousemove', (e) => { const li = e.target.closest('.cp-opt'); if (!li) return; const i = $$('.cp-opt', list).indexOf(li); if (i !== active) { active = i; highlight(); } });
  requestAnimationFrame(() => input.focus());
  return pop;
}

/* ---------- Date ranges ---------- */
const RANGE_PRESETS = [
  { key: 'this-month', label: 'This month' },
  { key: 'last-month', label: 'Last month' },
  { key: 'last-30', label: 'Last 30 days' },
  { key: 'last-90', label: 'Last 90 days' },
  { key: 'this-year', label: 'This year' },
  { key: 'last-year', label: 'Last year' },
  { key: 'all', label: 'All time' },
];
function rangeDates(value) {
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const y = today.getFullYear(), m = today.getMonth();
  const iso = (d) => toISODate(d);
  if (!value || value.preset === 'all' || (!value.preset && !value.from && !value.to)) return { from: null, to: null };
  if (value.from || value.to) return { from: value.from || null, to: value.to || null };
  switch (value.preset) {
    case 'this-month': return { from: iso(new Date(y, m, 1)), to: iso(new Date(y, m + 1, 0)) };
    case 'last-month': return { from: iso(new Date(y, m - 1, 1)), to: iso(new Date(y, m, 0)) };
    case 'last-30': return { from: iso(new Date(y, m, today.getDate() - 29)), to: iso(today) };
    case 'last-90': return { from: iso(new Date(y, m, today.getDate() - 89)), to: iso(today) };
    case 'this-year': return { from: iso(new Date(y, 0, 1)), to: iso(new Date(y, 11, 31)) };
    case 'last-year': return { from: iso(new Date(y - 1, 0, 1)), to: iso(new Date(y - 1, 11, 31)) };
    default: return { from: null, to: null };
  }
}
function rangeLabel(value) {
  if (!value || (!value.preset && !value.from && !value.to)) return 'All time';
  if (value.preset) { const p = RANGE_PRESETS.find((x) => x.key === value.preset); if (p) return p.label; }
  const { from, to } = value;
  if (from && to) return `${fmtDate(from)} – ${fmtDate(to)}`;
  if (from) return `From ${fmtDate(from)}`;
  return `Until ${fmtDate(to)}`;
}
/* -> {range:'this-month'} or {from,to} for URL/query usage. */
function rangeToQuery(value) {
  if (!value) return { range: null, from: null, to: null };
  if (value.preset) return { range: value.preset, from: null, to: null };
  return { range: null, from: value.from || null, to: value.to || null };
}
function rangeFromQuery(q, fallback = { preset: 'this-month' }) {
  if (q.range) return { preset: q.range };
  if (q.from || q.to) return { from: q.from || null, to: q.to || null };
  return fallback;
}

function dateRangePicker({ anchor, value = { preset: 'this-month' }, onChange, allowAll = true } = {}) {
  const el = document.createElement('div');
  el.className = 'menu';
  el.style.width = '360px';
  const cur = rangeDates(value);
  el.innerHTML = `
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px">
      <div>${RANGE_PRESETS.filter((p) => allowAll || p.key !== 'all').map((p) => `<button type="button" class="menu-item" data-preset="${p.key}" role="menuitemradio" aria-checked="${value.preset === p.key}"><span class="grow">${esc(p.label)}</span>${value.preset === p.key ? `<span class="menu-check">${icon('check')}</span>` : ''}</button>`).join('')}</div>
      <div style="border-left:1px solid var(--border);padding-left:10px">
        <div class="menu-label" style="padding-left:0">Custom</div>
        <div class="field" style="margin-bottom:8px"><label>From</label><input type="date" class="input input-sm" data-f="from" value="${esc(cur.from || '')}"></div>
        <div class="field" style="margin-bottom:8px"><label>To</label><input type="date" class="input input-sm" data-f="to" value="${esc(cur.to || '')}"></div>
        <button type="button" class="btn btn-primary btn-sm btn-block" data-act="apply">Apply</button>
      </div>
    </div>`;
  const pop = ui.popover(anchor, el, { onClose: () => anchor.focus && anchor.focus() });
  el.addEventListener('click', (e) => {
    const p = e.target.closest('[data-preset]');
    if (p) { pop.close('pick'); onChange && onChange({ preset: p.dataset.preset }); return; }
    if (e.target.closest('[data-act="apply"]')) {
      const from = el.querySelector('[data-f="from"]').value || null;
      const to = el.querySelector('[data-f="to"]').value || null;
      if (!from && !to) { onChange && onChange({ preset: 'all' }); pop.close('pick'); return; }
      pop.close('pick'); onChange && onChange({ from, to });
    }
  });
  requestAnimationFrame(() => { const f = el.querySelector('[data-preset][aria-checked="true"]') || el.querySelector('[data-preset]'); f && f.focus(); });
  return pop;
}

/* Small helper to render a range trigger button and wire the picker. */
function mountRangeButton(btn, value, onChange) {
  btn.innerHTML = `${icon('calendar', 'ico-sm')}<span>${esc(rangeLabel(value))}</span>${icon('chevron-down', 'ico-sm')}`;
  btn.onclick = () => dateRangePicker({ anchor: btn, value, onChange: (v) => { mountRangeButton(btn, v, onChange); onChange(v); } });
}
