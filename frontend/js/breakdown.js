/* Category → subcategory breakdown table, shared by Dashboard and Reports.
   renderBreakdown(host, { rows, prevRows, total, currency, range:{start,end}, storageKey })
   rows/prevRows: /api/reports/by-category?level=sub payloads (categories arrays). */
(function () {
  const EXPANDED_KEY = 'ispend.breakdown.expanded';

  function readExpanded(key) { try { return new Set(JSON.parse(localStorage.getItem(key || EXPANDED_KEY) || '[]')); } catch { return new Set(); } }
  function writeExpanded(key, set) { try { localStorage.setItem(key || EXPANDED_KEY, JSON.stringify(Array.from(set))); } catch { /* ignore */ } }

  function buildTree(rows, prevRows, categories) {
    const meta = new Map((categories || []).map((c) => [c.id, c]));
    const prev = new Map((prevRows || []).map((r) => [r.id == null ? 'none' : r.id, r]));
    const parents = new Map();
    const orphans = [];
    const ensure = (r) => {
      const key = r.id == null ? 'none' : r.id;
      if (!parents.has(key)) parents.set(key, { id: r.id, name: r.name, color: r.color, icon: r.icon, own: 0, ownCount: 0, total: 0, count: 0, prev: 0, children: [] });
      return parents.get(key);
    };
    for (const r of rows) if (r.parent_id == null) { const p = ensure(r); p.own = r.total; p.ownCount = r.count; }
    for (const r of rows) {
      if (r.parent_id == null) continue;
      const p = parents.get(r.parent_id) || orphans.push(r) && null;
      if (!p) continue;
      p.children.push({ ...r, prev: (prev.get(r.id) || {}).total || 0 });
    }
    for (const r of orphans) {
      const m = meta.get(r.parent_id) || {};
      const p = ensure({ id: r.parent_id, name: m.name || r.parent_name || 'Other', color: m.color || r.color, icon: m.icon || r.icon });
      p.children.push({ ...r, prev: (prev.get(r.id) || {}).total || 0 });
    }
    for (const p of parents.values()) {
      p.total = p.own + p.children.reduce((s, c) => s + c.total, 0);
      p.count = p.ownCount + p.children.reduce((s, c) => s + c.count, 0);
      const pp = prev.get(p.id == null ? 'none' : p.id);
      p.prev = ((pp && pp.total) || 0) + p.children.reduce((s, c) => s + c.prev, 0);
      p.children.sort((a, b) => b.total - a.total);
    }
    return Array.from(parents.values()).filter((p) => p.total > 0 || p.count > 0).sort((a, b) => b.total - a.total);
  }

  function deltaHtml(cur, prev, currency, upIsGood = false) {
    if (!prev) return '<span class="bd-delta text-4">new</span>';
    const pct = (cur - prev) / prev;
    const dir = Math.abs(pct) < 0.005 ? 'flat' : pct > 0 ? 'up' : 'down';
    const cls = dir === 'flat' ? '' : ((dir === 'up') === upIsGood ? 'is-good' : 'is-bad');
    const arrow = dir === 'up' ? '↑' : dir === 'down' ? '↓' : '–';
    return `<span class="bd-delta ${cls}" title="Previous period: ${esc(fmtMoney(prev, currency))}">${arrow} ${esc(fmtPct(Math.abs(pct)))}</span>`;
  }

  function renderBreakdown(host, { rows, prevRows, total, currency, range, storageKey, categories, showHeader = true, upIsGood = false }) {
    const cur = currency || 'USD';
    const tree = buildTree(rows || [], prevRows || [], categories || []);
    const grand = total || tree.reduce((s, p) => s + p.total, 0);
    const key = storageKey || EXPANDED_KEY;
    let expanded = readExpanded(key);
    const color = (c) => (c === 'muted' || !c ? (charts && charts.theme ? charts.theme().muted : 'var(--chart-muted)') : catColor(c));
    const link = (id) => `/transactions.html${toQuery({ cat: id == null ? 'none' : id, from: range.start, to: range.end })}`;
    const draw = () => {
      if (!tree.length) { host.innerHTML = ui.emptyState({ icon: 'pie-chart', title: 'Nothing spent in this period' }); return; }
      const allOpen = tree.every((p) => !p.children.length || expanded.has(String(p.id)));
      const rowsHtml = tree.map((p) => {
        const open = expanded.has(String(p.id));
        const hasKids = p.children.length > 0;
        const share = grand ? p.total / grand : 0;
        const head = `<tr class="bd-row bd-parent ${hasKids ? 'has-kids' : ''}" data-id="${p.id == null ? 'none' : p.id}" ${hasKids ? `aria-expanded="${open}"` : ''} tabindex="0">
          <td class="bd-name"><span class="bd-chev">${hasKids ? icon('chevron-right', 'ico-sm') : ''}</span><span class="cat-icon" style="--c:${color(p.color)}">${icon(p.icon || 'tag')}</span><a class="bd-link" href="${link(p.id)}">${esc(p.name)}</a>${hasKids ? `<span class="bd-kids text-4">${p.children.length}</span>` : ''}</td>
          <td class="bd-share"><span class="share-bar" style="--c:${color(p.color)}"><span style="width:${(share * 100).toFixed(1)}%"></span></span><span class="pct">${fmtPct(share)}</span></td>
          <td class="right num col-count">${fmtNumber(p.count)}</td>
          <td class="right col-delta">${deltaHtml(p.total, p.prev, cur, upIsGood)}</td>
          <td class="right num fw-500">${fmtMoney(p.total, cur)}</td></tr>`;
        if (!hasKids || !open) return head;
        const kids = [];
        if (p.own > 0 && p.children.length) kids.push({ id: p.id, name: `Directly in ${p.name}`, total: p.own, count: p.ownCount, prev: null, direct: true, color: p.color });
        kids.push(...p.children);
        return head + kids.map((c) => `<tr class="bd-row bd-child" data-parent="${p.id == null ? 'none' : p.id}">
          <td class="bd-name"><span class="bd-indent"></span><span class="dot" style="--c:${color(c.color || p.color)}"></span><a class="bd-link ${c.direct ? 'text-3' : ''}" href="${link(c.id)}">${esc(c.name)}</a></td>
          <td class="bd-share"><span class="share-bar is-sub" style="--c:${color(c.color || p.color)}"><span style="width:${grand ? (c.total / grand * 100).toFixed(1) : 0}%"></span></span><span class="pct text-4">${fmtPct(grand ? c.total / grand : 0)}</span></td>
          <td class="right num col-count text-3">${fmtNumber(c.count)}</td>
          <td class="right col-delta">${c.direct ? '' : deltaHtml(c.total, c.prev, cur, upIsGood)}</td>
          <td class="right num">${fmtMoney(c.total, cur)}</td></tr>`).join('');
      }).join('');
      host.innerHTML = `<div class="tbl-wrap bd-wrap"><table class="tbl bd-table">
        ${showHeader ? `<thead><tr><th>Category</th><th>Share</th><th class="right col-count">Transactions</th><th class="right col-delta">vs previous</th><th class="right">Total</th></tr></thead>` : ''}
        <tbody>${rowsHtml}<tr class="totals-row"><td>Total</td><td></td><td class="right num col-count">${fmtNumber(tree.reduce((s, p) => s + p.count, 0))}</td><td></td><td class="right num">${fmtMoney(grand, cur)}</td></tr></tbody></table></div>
        <div class="bd-foot"><button type="button" class="btn btn-ghost btn-xs" data-bd="toggle-all">${allOpen ? 'Collapse all' : 'Expand all'}</button></div>`;
    };
    const toggle = (id) => { if (expanded.has(id)) expanded.delete(id); else expanded.add(id); writeExpanded(key, expanded); draw(); };
    host.onclick = (e) => {
      const b = e.target.closest('[data-bd="toggle-all"]');
      if (b) { const allOpen = tree.every((p) => !p.children.length || expanded.has(String(p.id))); expanded = new Set(allOpen ? [] : tree.filter((p) => p.children.length).map((p) => String(p.id))); writeExpanded(key, expanded); draw(); return; }
      if (e.target.closest('a')) return;
      const r = e.target.closest('.bd-parent.has-kids'); if (r) toggle(r.dataset.id);
    };
    host.onkeydown = (e) => { const r = e.target.closest('.bd-parent.has-kids'); if (!r) return; if (e.key === 'Enter' || e.key === ' ' || e.key === 'ArrowRight' || e.key === 'ArrowLeft') { e.preventDefault(); toggle(r.dataset.id); } };
    draw();
  }

  window.renderBreakdown = renderBreakdown;
})();
