/* UI primitives: modal, confirm, drawer, popover, menu, multiFilter, toast,
   skeletons, empty states, shortcuts, focus trap. All return handles. */
const ui = (() => {
  const layers = []; // stack of {close} for Esc handling (topmost first)

  function root(id) {
    let el = document.getElementById(id);
    if (!el) { el = document.createElement('div'); el.id = id; document.body.appendChild(el); }
    return el;
  }
  const FOCUSABLE = 'a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])';

  function trapFocus(container) {
    function onKey(e) {
      if (e.key !== 'Tab') return;
      const items = $$(FOCUSABLE, container).filter((el) => el.offsetParent !== null || el === document.activeElement);
      if (!items.length) { e.preventDefault(); return; }
      const first = items[0], last = items[items.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    }
    container.addEventListener('keydown', onKey);
    return () => container.removeEventListener('keydown', onKey);
  }
  function focusFirst(container, prefer) {
    const el = (prefer && container.querySelector(prefer)) || container.querySelector('[autofocus]') || $$(FOCUSABLE, container).find((x) => !x.classList.contains('modal-close') && !x.classList.contains('drawer-close')) || container.querySelector(FOCUSABLE);
    if (el) el.focus();
  }
  function pushLayer(layer) { layers.unshift(layer); }
  function popLayer(layer) { const i = layers.indexOf(layer); if (i >= 0) layers.splice(i, 1); }
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && layers.length && !e.defaultPrevented) {
      const top = layers[0];
      if (top.onEsc !== false) { e.preventDefault(); top.close(); }
    }
  });
  function setInert(on) {
    ['sidebar', 'shell', 'bottomnav'].forEach((cls) => { const el = document.querySelector(`.${cls}`); if (el) el.inert = on; });
  }

  /* ---------- Modal ---------- */
  function modal({ title = '', html = '', actions = [], onClose, width, size, closeOnBackdrop = true, dismissible = true } = {}) {
    const host = root('modal-root');
    const backdrop = document.createElement('div');
    backdrop.className = 'modal-backdrop';
    const sizeCls = size === 'lg' ? ' modal-lg' : size === 'xl' ? ' modal-xl' : '';
    const titleId = `modal-title-${uid()}`;
    backdrop.innerHTML = `
      <div class="modal${sizeCls}" role="dialog" aria-modal="true" aria-labelledby="${titleId}" ${width ? `style="max-width:${width}px"` : ''}>
        <header class="modal-head"><h2 id="${titleId}">${esc(title)}</h2>${dismissible ? `<button class="btn btn-icon btn-ghost btn-sm modal-close" aria-label="Close">${icon('x')}</button>` : ''}</header>
        <div class="modal-body"></div>
        ${actions.length ? '<footer class="modal-foot"></footer>' : ''}
      </div>`;
    const el = backdrop.querySelector('.modal');
    const body = el.querySelector('.modal-body');
    if (typeof html === 'string') body.innerHTML = html; else body.appendChild(html);
    const foot = el.querySelector('.modal-foot');
    const prevFocus = document.activeElement;
    let closed = false;
    const handle = {
      el, body,
      close(result) {
        if (closed) return; closed = true;
        popLayer(handle); untrap(); backdrop.remove();
        if (!layers.length) setInert(false);
        if (onClose) onClose(result);
        if (prevFocus && prevFocus.focus) prevFocus.focus();
      },
      setLoading(btnIndex, on) { const b = foot && foot.children[btnIndex]; if (b) b.classList.toggle('is-loading', !!on); },
      setTitle(t) { el.querySelector('.modal-head h2').textContent = t; },
    };
    if (foot) {
      actions.forEach((a) => {
        const b = document.createElement('button');
        b.type = 'button';
        b.className = `btn ${a.primary ? 'btn-primary' : a.danger ? 'btn-danger-solid' : 'btn-secondary'}${a.cls ? ' ' + a.cls : ''}`;
        b.textContent = a.label;
        b.addEventListener('click', async () => {
          if (!a.onClick) return handle.close(a.value);
          b.classList.add('is-loading');
          try {
            const r = await a.onClick(handle);
            if (r !== false && a.keepOpen !== true) handle.close(r);
          } catch (err) { toast(err.message || String(err), { type: 'error' }); }
          finally { b.classList.remove('is-loading'); }
        });
        foot.appendChild(b);
      });
    }
    const closeBtn = el.querySelector('.modal-close');
    if (closeBtn) closeBtn.addEventListener('click', () => handle.close());
    backdrop.addEventListener('mousedown', (e) => { if (closeOnBackdrop && dismissible && e.target === backdrop) handle.close(); });
    handle.onEsc = dismissible;
    host.appendChild(backdrop);
    setInert(true);
    const untrap = trapFocus(el);
    pushLayer(handle);
    requestAnimationFrame(() => focusFirst(el, 'input,select,textarea,.btn-primary'));
    return handle;
  }

  /* `body` is plain text (escaped); pass `html` instead for markup you built with esc() yourself. */
  function confirm({ title = 'Are you sure?', body = '', html = '', confirmText = 'Confirm', cancelText = 'Cancel', danger = false } = {}) {
    return new Promise((resolve) => {
      let result = false;
      modal({
        title,
        html: html || `<p>${esc(body)}</p>`,
        onClose: () => resolve(result),
        actions: [
          { label: cancelText, onClick: () => { result = false; } },
          { label: confirmText, primary: !danger, danger, onClick: () => { result = true; } },
        ],
      });
    });
  }

  /* ---------- Drawer (one at a time) ---------- */
  let currentDrawer = null;
  function drawer({ title = '', html = '', foot = '', width, onClose } = {}) {
    if (currentDrawer) currentDrawer.close({ replaced: true });
    const host = root('drawer-root');
    const wrap = document.createElement('div');
    wrap.innerHTML = `
      <div class="drawer-backdrop"></div>
      <aside class="drawer" role="dialog" aria-modal="true" aria-labelledby="drawer-title" ${width ? `style="width:min(${width}px,100vw)"` : ''}>
        <header class="drawer-head"><h2 id="drawer-title">${esc(title)}</h2>
          <button class="btn btn-icon btn-ghost btn-sm drawer-close" aria-label="Close">${icon('x')}</button></header>
        <div class="drawer-body"></div>
        <footer class="drawer-foot" ${foot ? '' : 'hidden'}></footer>
      </aside>`;
    const el = wrap.querySelector('.drawer');
    const body = el.querySelector('.drawer-body');
    const footEl = el.querySelector('.drawer-foot');
    if (typeof html === 'string') body.innerHTML = html; else body.appendChild(html);
    if (foot) footEl.innerHTML = foot;
    const prevFocus = document.activeElement;
    let closed = false;
    let dirty = false;
    const handle = {
      el, body, foot: footEl,
      close(reason) {
        if (closed) return; closed = true;
        popLayer(handle); untrap();
        Array.from(wrap.children).forEach((c) => c.remove()); wrap.remove();
        if (currentDrawer === handle) currentDrawer = null;
        if (!layers.length) setInert(false);
        if (onClose) onClose(reason);
        if (!(reason && reason.replaced) && prevFocus && prevFocus.focus) prevFocus.focus();
      },
      setBody(h) { body.innerHTML = h; },
      setTitle(t) { el.querySelector('#drawer-title').textContent = t; },
      setFoot(h) { footEl.innerHTML = h; footEl.hidden = !h; },
      setDirty(v) { dirty = !!v; },
    };
    el.querySelector('.drawer-close').addEventListener('click', () => handle.close());
    wrap.querySelector('.drawer-backdrop').addEventListener('click', async () => {
      if (dirty && !(await confirm({ title: 'Discard changes?', body: 'You have unsaved changes.', confirmText: 'Discard', danger: true }))) return;
      handle.close();
    });
    host.appendChild(wrap);
    setInert(true);
    const untrap = trapFocus(el);
    pushLayer(handle);
    currentDrawer = handle;
    requestAnimationFrame(() => focusFirst(el, '.drawer-body input,.drawer-body button,.drawer-close'));
    return handle;
  }

  /* ---------- Popover ---------- */
  function popover(anchor, el, { placement = 'bottom-start', offset = 6, onClose, closeOnOutside = true, matchWidth = false } = {}) {
    el.classList.add('popover');
    document.body.appendChild(el);
    if (matchWidth) el.style.minWidth = `${anchor.getBoundingClientRect().width}px`;
    function position() {
      const r = anchor.getBoundingClientRect();
      const w = el.offsetWidth, h = el.offsetHeight;
      const vw = window.innerWidth, vh = window.innerHeight;
      let top = r.bottom + offset;
      let left = placement.endsWith('end') ? r.right - w : r.left;
      if (top + h > vh - 8 && r.top - offset - h > 8) top = r.top - offset - h;
      if (top + h > vh - 8) top = Math.max(8, vh - 8 - h);
      if (left + w > vw - 8) left = vw - 8 - w;
      if (left < 8) left = 8;
      el.style.top = `${Math.round(top)}px`;
      el.style.left = `${Math.round(left)}px`;
    }
    position();
    let closed = false;
    const handle = {
      el, position,
      close(reason) {
        if (closed) return; closed = true;
        popLayer(handle);
        document.removeEventListener('mousedown', onDown, true);
        window.removeEventListener('resize', position);
        document.removeEventListener('scroll', onScroll, true);
        el.remove();
        if (onClose) onClose(reason);
      },
    };
    function onDown(e) { if (closeOnOutside && !el.contains(e.target) && !anchor.contains(e.target)) handle.close('outside'); }
    function onScroll(e) { if (!el.contains(e.target)) position(); }
    setTimeout(() => {
      document.addEventListener('mousedown', onDown, true);
      window.addEventListener('resize', position);
      document.addEventListener('scroll', onScroll, true);
    }, 0);
    pushLayer(handle);
    return handle;
  }

  /* ---------- Menu ---------- */
  function menu(anchor, items, opts = {}) {
    const el = document.createElement('div');
    el.className = 'menu';
    el.setAttribute('role', 'menu');
    el.innerHTML = items.map((it, i) => {
      if (it.divider) return '<div class="menu-divider" role="separator"></div>';
      if (it.label && it.header) return `<div class="menu-label">${esc(it.label)}</div>`;
      const tag = it.href ? 'a' : 'button';
      return `<${tag} ${it.href ? `href="${esc(it.href)}"` : 'type="button"'} class="menu-item${it.danger ? ' danger' : ''}${it.disabled ? ' is-disabled' : ''}" role="${it.checked != null ? 'menuitemcheckbox' : 'menuitem'}" ${it.checked != null ? `aria-checked="${!!it.checked}"` : ''} data-i="${i}" ${it.disabled ? 'aria-disabled="true"' : ''}>
        ${it.icon ? icon(it.icon) : ''}<span class="grow truncate">${esc(it.label)}</span>
        ${it.checked ? `<span class="menu-check">${icon('check')}</span>` : ''}${it.shortcut ? `<kbd>${esc(it.shortcut)}</kbd>` : ''}
      </${tag}>`;
    }).join('');
    const prevFocus = document.activeElement;
    const pop = popover(anchor, el, { placement: opts.placement || 'bottom-end', onClose: () => { if (prevFocus && prevFocus.focus && !opts.noRefocus) prevFocus.focus(); } });
    el.addEventListener('click', (e) => {
      const btn = e.target.closest('[data-i]');
      if (!btn || btn.getAttribute('aria-disabled') === 'true') return;
      const it = items[Number(btn.dataset.i)];
      if (!it.href) e.preventDefault();
      pop.close('pick');
      if (it.onClick) it.onClick();
    });
    el.setAttribute('aria-orientation', 'vertical');
    el.addEventListener('keydown', (e) => menuKeys(e, $$('[data-i]', el), (b) => b.textContent));
    requestAnimationFrame(() => { const f = el.querySelector('[data-i]'); if (f) f.focus(); });
    return pop;
  }

  /* Shared keyboard model for menu-like lists: arrows wrap, Home/End jump, letters type-ahead. */
  function menuKeys(e, btns, textOf) {
    if (!btns.length) return;
    const i = btns.indexOf(document.activeElement);
    const go = (j) => { e.preventDefault(); btns[(j + btns.length) % btns.length].focus(); };
    if (e.key === 'ArrowDown') return go(i + 1);
    if (e.key === 'ArrowUp') return go(i - 1);
    if (e.key === 'Home') return go(0);
    if (e.key === 'End') return go(btns.length - 1);
    if (e.key.length === 1 && /\S/.test(e.key) && !e.metaKey && !e.ctrlKey && !e.altKey && !(document.activeElement && document.activeElement.matches('input'))) {
      const ch = e.key.toLowerCase();
      for (let k = 1; k <= btns.length; k++) {
        const b = btns[(i + k) % btns.length];
        if ((textOf(b) || '').trim().toLowerCase().startsWith(ch)) { e.preventDefault(); b.focus(); return; }
      }
    }
  }

  /* ---------- Multi-select filter popover ---------- */
  function multiFilter(anchor, { options = [], selected = new Set(), onChange, title, searchable } = {}) {
    const el = document.createElement('div');
    el.className = 'menu';
    el.style.minWidth = '220px';
    const render = (q = '') => {
      const rows = options.filter((o) => !q || o.label.toLowerCase().includes(q.toLowerCase()));
      const nSel = options.filter((o) => selected.has(o.value)).length;
      return `<div class="menu-head"><span class="menu-label">${esc(title || 'Filter')}</span><span class="menu-head-actions"><span class="menu-count">${nSel ? `${nSel} selected` : ''}</span><button type="button" class="btn btn-ghost btn-xs" data-act="all">All</button><button type="button" class="btn btn-ghost btn-xs" data-act="clear">None</button></span></div>
        ${searchable || options.length > 8 ? `<div class="menu-search"><input class="input input-sm" placeholder="Filter…" value="${esc(q)}"></div>` : ''}
        <div class="menu-opts">${rows.map((o) => `
          <button type="button" class="menu-item ${o.indent ? 'menu-item--child' : ''}" role="menuitemcheckbox" aria-checked="${selected.has(o.value)}" data-v="${esc(o.value)}" ${o.indent ? `style="padding-left:${10 + 18 * o.indent}px"` : ''}>
            <input type="checkbox" class="check" tabindex="-1" ${selected.has(o.value) ? 'checked' : ''}>
            ${o.color ? `<i class="dot" style="--c:var(--${esc(o.color)})"></i>` : ''}
            <span class="grow truncate">${esc(o.label)}</span>${o.count != null ? `<span class="menu-count">${fmtNumber(o.count)}</span>` : ''}
          </button>`).join('') || '<div class="palette-empty">No matches</div>'}</div>
        <div class="menu-divider"></div>
        <div class="row" style="padding:2px 4px 4px"><button type="button" class="btn btn-ghost btn-xs" data-act="clear">Clear</button><button type="button" class="btn btn-ghost btn-xs ml-auto" data-act="all">Select all</button></div>`;
      // "All" selects the rows currently shown (search-filtered), so "type Din, All" selects the Dining group.
    };
    el.innerHTML = render();
    el.setAttribute('role', 'menu');
    if (title) el.setAttribute('aria-label', title);
    const prevFocus = document.activeElement;
    const pop = popover(anchor, el, { onClose: () => { if (prevFocus && prevFocus.focus) prevFocus.focus(); } });
    el.addEventListener('keydown', (e) => {
      const opts = $$('[data-v]', el);
      if (document.activeElement && document.activeElement.matches('input.input')) {
        if (e.key === 'ArrowDown' && opts.length) { e.preventDefault(); opts[0].focus(); }
        return;
      }
      if ((e.key === 'ArrowUp') && opts.indexOf(document.activeElement) === 0 && el.querySelector('input.input')) { e.preventDefault(); el.querySelector('input.input').focus(); return; }
      menuKeys(e, opts, (b) => (b.querySelector('.grow') || b).textContent);
    });
    requestAnimationFrame(() => { const f = el.querySelector('input.input') || el.querySelector('[data-v]'); if (f) f.focus(); });
    el.addEventListener('input', (e) => { if (e.target.matches('input.input')) { const v = e.target.value; el.innerHTML = render(v); const inp = el.querySelector('input.input'); inp.focus(); inp.setSelectionRange(v.length, v.length); } });
    el.addEventListener('click', (e) => {
      const b = e.target.closest('[data-v]');
      const act = e.target.closest('[data-act]');
      if (b) {
        const v = b.dataset.v;
        if (selected.has(v)) selected.delete(v); else selected.add(v);
        b.setAttribute('aria-checked', selected.has(v)); b.querySelector('input').checked = selected.has(v);
        onChange && onChange(selected);
      } else if (act) {
        const q = (el.querySelector('input.input') || {}).value || '';
        const shown = options.filter((o) => !q || o.label.toLowerCase().includes(q.toLowerCase()));
        if (act.dataset.act === 'clear') { if (q) shown.forEach((o) => selected.delete(o.value)); else selected.clear(); }
        else shown.forEach((o) => selected.add(o.value));
        el.innerHTML = render(q); onChange && onChange(selected);
        const inp = el.querySelector('input.input'); if (inp && q) { inp.focus(); inp.setSelectionRange(q.length, q.length); }
      }
    });
    return pop;
  }

  /* ---------- Tabs & segmented controls (WAI-ARIA, roving tabindex, arrow keys) ---------- */
  let rovingSeq = 0;
  function rovingGroup(container, items, { attr, activeClass = 'active', onChange, initial }) {
    let current = Math.max(0, items.findIndex((el) => el.getAttribute(attr) === 'true' || el.classList.contains(activeClass)));
    if (typeof initial === 'number') current = initial;
    const paint = () => items.forEach((el, i) => {
      const on = i === current;
      el.setAttribute(attr, on ? 'true' : 'false');
      el.classList.toggle(activeClass, on);
      el.setAttribute('tabindex', on ? '0' : '-1');
    });
    const select = (target, { focus = true, silent = false } = {}) => {
      const i = typeof target === 'number' ? target : items.indexOf(target);
      if (i < 0 || i >= items.length) return;
      const changed = i !== current;
      current = i; paint();
      if (focus) items[i].focus();
      if (changed && !silent && onChange) onChange(items[i], i);
    };
    container.addEventListener('keydown', (e) => {
      const i = items.indexOf(document.activeElement);
      if (i < 0) return;
      const map = { ArrowRight: i + 1, ArrowDown: i + 1, ArrowLeft: i - 1, ArrowUp: i - 1, Home: 0, End: items.length - 1 };
      if (!(e.key in map)) return;
      e.preventDefault();
      select((map[e.key] + items.length) % items.length);
    });
    container.addEventListener('click', (e) => { const i = items.findIndex((el) => el.contains(e.target)); if (i >= 0) select(i); });
    paint();
    return { select, current: () => current, items };
  }
  /* container holds role=tab buttons (or any `.tab`/`.seg-btn` children); panels referenced by
     `aria-controls`/`data-panel` get role=tabpanel + aria-labelledby. onChange(tabEl, index). */
  function tabs(container, { onChange, selector = '[role="tab"]', initial } = {}) {
    if (!container) return null;
    container.setAttribute('role', 'tablist');
    const items = $$(selector, container);
    items.forEach((el) => {
      if (!el.id) el.id = `tab-${++rovingSeq}`;
      const panelId = el.getAttribute('aria-controls') || el.dataset.panel;
      const panel = panelId && document.getElementById(panelId);
      if (panel) { el.setAttribute('aria-controls', panelId); panel.setAttribute('role', 'tabpanel'); panel.setAttribute('aria-labelledby', el.id); if (!panel.hasAttribute('tabindex')) panel.setAttribute('tabindex', '0'); }
    });
    return rovingGroup(container, items, { attr: 'aria-selected', onChange, initial });
  }
  /* container gets role=radiogroup; each .seg-btn becomes role=radio with aria-checked. onChange(btn, index). */
  function segmented(container, { onChange, selector = '.seg-btn', initial } = {}) {
    if (!container) return null;
    container.setAttribute('role', 'radiogroup');
    const items = $$(selector, container);
    items.forEach((el) => { el.setAttribute('role', 'radio'); el.removeAttribute('aria-selected'); el.removeAttribute('aria-pressed'); });
    return rovingGroup(container, items, { attr: 'aria-checked', onChange, initial });
  }

  /* ---------- Toasts ---------- */
  function toastFn(message, { type = 'info', action, duration } = {}) {
    const host = root('toast-root');
    while (host.children.length >= 3) host.firstChild.remove();
    const el = document.createElement('div');
    el.className = `toast toast-${type}`;
    el.setAttribute('role', type === 'error' ? 'alert' : 'status');
    el.innerHTML = `${icon(type === 'success' ? 'check-circle' : type === 'error' ? 'alert-circle' : 'info')}<div class="toast-msg">${esc(message)}</div>
      ${action ? `<button type="button" class="toast-action">${esc(action.label)}</button>` : ''}
      <button type="button" class="toast-close" aria-label="Dismiss">${icon('x', 'ico-sm')}</button>`;
    let timer = null;
    const remove = () => { clearTimeout(timer); el.classList.add('is-leaving'); setTimeout(() => el.remove(), 180); };
    el.querySelector('.toast-close').addEventListener('click', remove);
    if (action) el.querySelector('.toast-action').addEventListener('click', () => { remove(); action.fn && action.fn(); });
    host.appendChild(el);
    const ms = duration ?? (action ? 7000 : type === 'error' ? 6000 : 4000);
    if (ms > 0) timer = setTimeout(remove, ms);
    return { close: remove, el };
  }

  /* ---------- Skeletons / empty ---------- */
  function skeleton(width = '100%', height = 14, cls = '') { return `<span class="skel ${cls}" style="width:${typeof width === 'number' ? width + 'px' : width};height:${height}px"></span>`; }
  function skeletonRows(n = 6, cols = 5) {
    return Array.from({ length: n }, () => `<tr class="skel-row">${Array.from({ length: cols }, (_, i) => `<td>${skeleton(i === 1 ? '70%' : '55%', 12)}</td>`).join('')}</tr>`).join('');
  }
  function skeletonList(n = 5) {
    return Array.from({ length: n }, () => `<div class="list-item"><span class="skel" style="width:28px;height:28px;border-radius:8px"></span><div class="grow col gap-1">${skeleton('50%', 12)}${skeleton('30%', 10)}</div>${skeleton(60, 12)}</div>`).join('');
  }
  function emptyState({ icon: ic = 'inbox', title = 'Nothing here yet', body = '', action } = {}) {
    let btn = '';
    if (action) btn = action.href ? `<a class="btn btn-primary" href="${esc(action.href)}">${esc(action.label)}</a>` : `<button type="button" class="btn btn-primary" data-act="${esc(action.act || 'empty-action')}">${esc(action.label)}</button>`;
    return `<div class="empty"><div class="empty-icon">${icon(ic)}</div><div class="empty-title">${esc(title)}</div>${body ? `<div class="empty-body">${esc(body)}</div>` : ''}${btn}</div>`;
  }
  function errorBox(message, { retry } = {}) {
    return `<div class="error-box">${icon('alert-triangle')}<div class="grow">${esc(message)}</div>${retry ? `<button type="button" class="btn btn-sm btn-secondary" data-act="${esc(retry)}">Retry</button>` : ''}</div>`;
  }

  /* ---------- Keyboard shortcuts (single keys + two-key chords like 'g d') ---------- */
  const shortcuts = (() => {
    const map = new Map();
    let pending = null, pendingTimer = null;
    function typing() {
      const a = document.activeElement;
      return a && (a.matches('input,textarea,select,[contenteditable="true"]'));
    }
    function activating(e) {
      // Enter/Space on a focused control is the control's own activation, not a page shortcut.
      const a = document.activeElement;
      return (e.key === 'Enter' || e.key === ' ') && a && a.matches('button,a[href],summary,[role="button"],[role="link"],[role="tab"],[role="option"],[role="menuitem"]');
    }
    document.addEventListener('keydown', (e) => {
      if (e.defaultPrevented || e.metaKey || e.ctrlKey || e.altKey) return;
      if (typing() || activating(e)) return;
      if (layers.length && !layers[0].allowShortcuts) return;
      const key = e.key;
      if (pending) {
        const combo = `${pending} ${key}`;
        clearTimeout(pendingTimer); pending = null;
        const h = map.get(combo);
        if (h && (!h.when || h.when())) { e.preventDefault(); h.fn(e); return; }
      }
      const isPrefix = Array.from(map.keys()).some((k) => k.startsWith(`${key} `));
      if (isPrefix) { pending = key; pendingTimer = setTimeout(() => { pending = null; }, 900); e.preventDefault(); return; }
      const h = map.get(key);
      if (h && (!h.when || h.when())) { e.preventDefault(); h.fn(e); }
    });
    return {
      register(keys, fn, { when, description } = {}) { map.set(keys, { fn, when, description }); return () => map.delete(keys); },
      unregister(keys) { map.delete(keys); },
      list() { return Array.from(map.entries()).map(([keys, h]) => ({ keys, description: h.description })); },
      isTyping: typing,
    };
  })();

  function shortcutsSheet(extra = []) {
    const groups = [
      { title: 'Global', items: [['⌘K', 'Search'], ['g d', 'Dashboard'], ['g t', 'Transactions'], ['g r', 'Review'], ['g i', 'Import'], ['g c', 'Categories'], ['g p', 'Reports'], ['g s', 'Settings'], ['[', 'Toggle sidebar'], ['?', 'This sheet']] },
      ...extra,
    ];
    modal({ title: 'Keyboard shortcuts', size: 'lg', html: groups.map((g) => `<div class="section-label mb-2 mt-2">${esc(g.title)}</div><div class="shortcuts-grid mb-3">${g.items.map(([k, d]) => `<div><span>${esc(d)}</span><span class="keys">${k.split(' ').map((x) => `<kbd>${esc(x)}</kbd>`).join('')}</span></div>`).join('')}</div>`).join('') });
  }

  return { modal, confirm, drawer, popover, menu, multiFilter, tabs, segmented, toast: toastFn, skeleton, skeletonRows, skeletonList, emptyState, errorBox, shortcuts, shortcutsSheet, trapFocus, focusFirst, layers, closeTop: () => layers[0] && layers[0].close() };
})();
const toast = ui.toast;
window.toast = toast;
const snackbar = (m, type) => toast(m, { type: type === 'error' ? 'error' : type === 'success' ? 'success' : 'info' });
