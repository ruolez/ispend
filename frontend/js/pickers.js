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
  const pop = ui.popover(anchor, el, { onClose: () => { const a = anchor && anchor.isConnected ? anchor : (anchor && anchor.dataset && anchor.dataset.catPick ? document.querySelector(`[data-cat-pick="${CSS.escape(anchor.dataset.catPick)}"]`) : null); if (a && a.focus) a.focus(); } });
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
  { key: 'this-week', label: 'This week' },
  { key: 'last-week', label: 'Last week' },
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
  if (value.preset && value.preset.startsWith('month:')) {
    const [my, mm] = value.preset.slice(6).split('-').map(Number);
    return { from: iso(new Date(my, mm - 1, 1)), to: iso(new Date(my, mm, 0)) };
  }
  const ws = Number((((window.currentUser || {}).preferences) || {}).week_start) || 0; // 0 = Sunday, like Date#getDay
  const weekStart = new Date(y, m, today.getDate() - ((today.getDay() - ws + 7) % 7));
  switch (value.preset) {
    case 'this-week': return { from: iso(weekStart), to: iso(new Date(weekStart.getFullYear(), weekStart.getMonth(), weekStart.getDate() + 6)) };
    case 'last-week': return { from: iso(new Date(weekStart.getFullYear(), weekStart.getMonth(), weekStart.getDate() - 7)), to: iso(new Date(weekStart.getFullYear(), weekStart.getMonth(), weekStart.getDate() - 1)) };
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
  if (value.preset && value.preset.startsWith('month:')) return fmtMonth(value.preset.slice(6), { long: true });
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
const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;
function rangeFromQuery(q, fallback = { preset: 'this-month' }) {
  if (q.range && (RANGE_PRESETS.some((p) => p.key === q.range) || /^month:\d{4}-\d{2}$/.test(q.range))) return { preset: q.range };
  const from = ISO_DATE.test(q.from || '') ? q.from : null;
  const to = ISO_DATE.test(q.to || '') ? q.to : null;
  if (from || to) return { from, to };
  return fallback;
}

function dateRangePicker({ anchor, value = { preset: 'this-month' }, onChange, allowAll = true } = {}) {
  const el = document.createElement('div');
  el.className = 'menu';
  el.style.width = '360px';
  const cur = rangeDates(value);
  el.innerHTML = `
    <div class="drp-grid">
      <div role="radiogroup" aria-label="Preset ranges">${RANGE_PRESETS.filter((p) => allowAll || p.key !== 'all').map((p) => `<button type="button" class="menu-item" data-preset="${p.key}" role="radio" aria-checked="${value.preset === p.key}"><span class="grow">${esc(p.label)}</span>${value.preset === p.key ? `<span class="menu-check">${icon('check')}</span>` : ''}</button>`).join('')}</div>
      <div class="drp-custom">
        <div class="menu-label" style="padding-left:0">Custom</div>
        <div class="field" style="margin-bottom:8px"><label for="drp-from">From</label><input type="date" id="drp-from" class="input input-sm" data-f="from" value="${esc(cur.from || '')}"></div>
        <div class="field" style="margin-bottom:8px"><label for="drp-to">To</label><input type="date" id="drp-to" class="input input-sm" data-f="to" value="${esc(cur.to || '')}"></div>
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

/* Tag picker: multi-select with inline create. onChange(selectedSet) fires on every toggle. */
async function tagPicker({ anchor, selected = new Set(), onChange, allowCreate = true, title = 'Tags' } = {}) {
  let tags = await store.tags().catch(() => []);
  const el = document.createElement('div');
  el.className = 'menu tag-picker';
  el.style.width = '260px';
  const render = (q = '') => {
    const ql = q.trim().toLowerCase();
    const rows = tags.filter((t) => !ql || t.name.toLowerCase().includes(ql));
    const exact = tags.some((t) => t.name.toLowerCase() === ql);
    return `<div class="menu-search"><input class="input input-sm" placeholder="${tags.length ? 'Search or create…' : 'New tag…'}" value="${esc(q)}" aria-label="${esc(title)}" autocomplete="off"></div>
      <div class="menu-opts" role="group" aria-label="${esc(title)}">${rows.map((t) => `<button type="button" class="menu-item" role="checkbox" aria-checked="${selected.has(t.id)}" data-tag="${t.id}"><span class="check-fake" aria-hidden="true"></span><i class="dot" style="--c:var(--${esc(t.color)})"></i><span class="grow truncate">${esc(t.name)}</span></button>`).join('')}${!rows.length && !ql ? '<div class="palette-empty">No tags yet · type a name to create one</div>' : ''}${allowCreate && ql && !exact ? `<button type="button" class="menu-item" data-create="1">${icon('plus')}<span class="grow">Create “${esc(q.trim())}”</span></button>` : ''}</div>`;
  };
  el.innerHTML = render();
  const pop = ui.popover(anchor, el, { onClose: () => { if (anchor && anchor.isConnected && anchor.focus) anchor.focus(); } });
  const refocus = (v) => { const i = el.querySelector('input'); i.focus(); i.setSelectionRange(v.length, v.length); };
  el.addEventListener('input', (e) => { if (e.target.matches('input')) { const v = e.target.value; el.innerHTML = render(v); refocus(v); } });
  el.addEventListener('click', async (e) => {
    const b = e.target.closest('[data-tag]'); const c = e.target.closest('[data-create]');
    if (b) { const id = Number(b.dataset.tag); if (selected.has(id)) selected.delete(id); else selected.add(id); b.setAttribute('aria-checked', String(selected.has(id))); onChange && onChange(selected); return; }
    if (c) {
      const name = el.querySelector('input').value.trim();
      try {
        const t = await api('/api/tags', { method: 'POST', body: { name } });
        store.invalidate('tags'); tags = await store.tags({ force: true });
        selected.add(t.id); el.innerHTML = render(''); refocus(''); onChange && onChange(selected);
      } catch (err) { toast(err.message, { type: 'error' }); }
    }
  });
  el.addEventListener('keydown', (e) => {
    const opts = $$('[data-tag],[data-create]', el);
    const input = el.querySelector('input');
    if (document.activeElement === input) {
      if (e.key === 'ArrowDown' && opts.length) { e.preventDefault(); opts[0].focus(); }
      if (e.key === 'Enter') { e.preventDefault(); const c = el.querySelector('[data-create]'); if (c) c.click(); else if (opts.length === 1) opts[0].click(); }
      return;
    }
    const i = opts.indexOf(document.activeElement);
    if (e.key === 'ArrowDown') { e.preventDefault(); (opts[i + 1] || opts[0]).focus(); }
    if (e.key === 'ArrowUp') { e.preventDefault(); if (i <= 0) input.focus(); else opts[i - 1].focus(); }
  });
  requestAnimationFrame(() => el.querySelector('input').focus());
  return pop;
}

/* Small helper to render a range trigger button and wire the picker. */
function mountRangeButton(btn, value, onChange) {
  btn.innerHTML = `${icon('calendar', 'ico-sm')}<span>${esc(rangeLabel(value))}</span>${icon('chevron-down', 'ico-sm')}`;
  btn.onclick = () => dateRangePicker({ anchor: btn, value, onChange: (v) => { mountRangeButton(btn, v, onChange); onChange(v); } });
}

/* ---------- period stepping (‹ label ›) ----------
   The window before or after the current one, as a picker value; null when there is nothing to step
   to (all time, an open-ended range, or a step that would start in the future). Whole months and
   whole years step by month and year so February keeps its length; everything else shifts by its
   own span. The result is normalised back to a preset when it matches one, so the label stays
   "Last month" rather than a pair of dates. */
const ymd = (iso) => { const [y, m, d] = iso.split('-').map(Number); return new Date(y, m - 1, d); };
function shiftRange(value, dir) {
  const { from, to } = rangeDates(value);
  if (!from || !to) return null;
  const [f, t] = [ymd(from), ymd(to)];
  const firstOfMonth = f.getDate() === 1;
  const lastOfMonth = t.getDate() === new Date(t.getFullYear(), t.getMonth() + 1, 0).getDate();
  let nf, nt;
  if (firstOfMonth && lastOfMonth && f.getMonth() === 0 && t.getMonth() === 11 && f.getFullYear() === t.getFullYear()) {
    nf = new Date(f.getFullYear() + dir, 0, 1); nt = new Date(t.getFullYear() + dir, 11, 31);
  } else if (firstOfMonth && lastOfMonth) {
    const months = (t.getFullYear() - f.getFullYear()) * 12 + (t.getMonth() - f.getMonth()) + 1;
    nf = new Date(f.getFullYear(), f.getMonth() + dir * months, 1);
    nt = new Date(nf.getFullYear(), nf.getMonth() + months, 0);
  } else {
    const span = Math.round((t - f) / 86400000) + 1;
    nf = new Date(f.getFullYear(), f.getMonth(), f.getDate() + dir * span);
    nt = new Date(t.getFullYear(), t.getMonth(), t.getDate() + dir * span);
  }
  const today = new Date(); today.setHours(0, 0, 0, 0);
  if (dir > 0 && nf > today) return null;
  return normalizeRange(toISODate(nf), toISODate(nt));
}
/* {from,to} -> the preset with the same dates when there is one (nicer label, shorter URL). */
function normalizeRange(from, to) {
  for (const p of RANGE_PRESETS) {
    if (p.key === 'all') continue;
    const d = rangeDates({ preset: p.key });
    if (d.from === from && d.to === to) return { preset: p.key };
  }
  const f = ymd(from);
  if (f.getDate() === 1 && to === toISODate(new Date(f.getFullYear(), f.getMonth() + 1, 0))) {
    return { preset: `month:${from.slice(0, 7)}` };
  }
  return { from, to };
}
/* ‹ [range] › — the range button from mountRangeButton between two step buttons.
   `group` holds [data-period="prev"], [data-period="pick"] and [data-period="next"]. */
function mountPeriodNav(group, value, onChange) {
  const prev = group.querySelector('[data-period="prev"]');
  const next = group.querySelector('[data-period="next"]');
  const pick = group.querySelector('[data-period="pick"]');
  mountRangeButton(pick, value, onChange);
  [[prev, -1, 'chevron-left'], [next, 1, 'chevron-right']].forEach(([btn, dir, ico]) => {
    const target = shiftRange(value, dir);
    btn.innerHTML = icon(ico, 'ico-sm');
    btn.disabled = !target;
    btn.setAttribute('data-tip', target ? `${dir < 0 ? 'Previous' : 'Next'} period · ${rangeLabel(target)}` : 'No period to step to');
    btn.onclick = () => { const v = shiftRange(value, dir); if (v) { mountPeriodNav(group, v, onChange); onChange(v); } };
  });
}
function stepPeriod(group, value, dir, onChange) {
  const v = shiftRange(value, dir);
  if (!v) return false;
  mountPeriodNav(group, v, onChange);
  onChange(v);
  return true;
}


/* ---------- shared period (session) ----------
   The period a user picks on one page (Dashboard, Transactions, Reports, Insights) follows
   them to the others. Stored as a picker value: {preset:'last-month'|'month:2026-07'|...} or {from,to}. */
const PERIOD_KEY = 'ispend.period';
function periodGet() { try { return JSON.parse(sessionStorage.getItem(PERIOD_KEY) || 'null'); } catch { return null; } }
function periodSet(value) { try { if (value) sessionStorage.setItem(PERIOD_KEY, JSON.stringify(value)); } catch { /* ignore */ } }
/* Explicit URL range wins, then the shared period, then the page default. */
function initialRange(q, fallback = { preset: 'this-month' }) {
  if (q && (q.range || q.from || q.to)) { const v = rangeFromQuery(q, fallback); periodSet(v); return v; }
  return periodGet() || fallback;
}
/* Month (YYYY-MM) that a period sits on — for pages that work per month. */
function periodMonth(value, now = new Date()) {
  const ym = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
  if (!value) return ym(now);
  if (value.preset && value.preset.startsWith('month:')) return value.preset.slice(6);
  if (value.preset === 'last-month') return ym(new Date(now.getFullYear(), now.getMonth() - 1, 1));
  if (value.preset) return ym(now);
  if (value.to) return value.to.slice(0, 7);
  if (value.from) return value.from.slice(0, 7);
  return ym(now);
}
