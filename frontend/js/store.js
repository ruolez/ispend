/* In-memory + sessionStorage cache for reference data (categories, accounts,
   client settings) with stale-while-revalidate and a tiny pub/sub. */
const store = (() => {
  const mem = {};
  const inflight = {};
  const listeners = {};
  const SS_PREFIX = 'ispend.cache.';

  function ssGet(key) {
    try { const raw = sessionStorage.getItem(SS_PREFIX + key); return raw ? JSON.parse(raw) : null; } catch { return null; }
  }
  function ssSet(key, entry) {
    try { sessionStorage.setItem(SS_PREFIX + key, JSON.stringify(entry)); } catch { /* quota */ }
    if (bc) { try { bc.postMessage({ key, entry }); } catch { /* ignore */ } }
  }
  /* Other tabs learn about fresh reference data (a renamed category, a new account) at once.
     The channel is closed while the page sits in the back/forward cache: a message arriving then
     (the next page's own revalidation) would evict it, and Back would become a full reload. */
  let bc = null;
  function openChannel() {
    if (typeof BroadcastChannel === 'undefined' || bc) return;
    bc = new BroadcastChannel('ispend.store');
    bc.onmessage = (e) => {
      const { key, entry, invalidate: inv } = e.data || {};
      if (!key) return;
      if (key === PAGES_ALL) { dropPages(); return; }
      if (inv) { delete mem[key]; return; }
      const changed = JSON.stringify(mem[key] && mem[key].data) !== JSON.stringify(entry && entry.data);
      mem[key] = entry;
      if (changed) emit(`${key}-changed`, entry.data);
    };
  }
  openChannel();
  window.addEventListener('pagehide', (e) => { if (e.persisted && bc) { bc.close(); bc = null; } });
  window.addEventListener('pageshow', (e) => {
    if (!e.persisted) return;
    // Whatever other pages wrote meanwhile is in sessionStorage; drop the in-memory copies.
    Object.keys(mem).forEach((k) => { delete mem[k]; });
    openChannel();
  });

  /* get(key, url, {ttl}) -> Promise<data>. Fresh cache returns immediately; stale
     cache returns immediately AND revalidates in the background (emitting `${key}-changed`). */
  async function get(key, url, { ttl = 60000, force = false } = {}) {
    const now = Date.now();
    const entry = mem[key] || ssGet(key);
    if (entry && !force) {
      if (now - entry.at < ttl) { mem[key] = entry; return entry.data; }
      if (!inflight[key]) revalidate(key, url).catch(() => {});
      mem[key] = entry;
      return entry.data;
    }
    return revalidate(key, url);
  }
  function revalidate(key, url) {
    if (inflight[key]) return inflight[key];
    inflight[key] = api(url).then((data) => {
      const entry = { at: Date.now(), data };
      const changed = JSON.stringify(mem[key] && mem[key].data) !== JSON.stringify(data);
      mem[key] = entry;
      ssSet(key, entry);
      if (changed) emit(`${key}-changed`, data);
      return data;
    }).finally(() => { delete inflight[key]; });
    return inflight[key];
  }
  function invalidate(key) {
    delete mem[key];
    try { sessionStorage.removeItem(SS_PREFIX + key); } catch { /* ignore */ }
    if (bc) { try { bc.postMessage({ key, invalidate: true }); } catch { /* ignore */ } }
  }
  function on(event, fn) {
    (listeners[event] = listeners[event] || []).push(fn);
    return () => { listeners[event] = (listeners[event] || []).filter((f) => f !== fn); };
  }
  function emit(event, detail) {
    (listeners[event] || []).forEach((fn) => { try { fn(detail); } catch (e) { console.error(e); } });
    window.dispatchEvent(new CustomEvent(`ispend:${event}`, { detail }));
  }

  function flatten(tree) {
    const out = [];
    (tree || []).forEach((p) => {
      out.push({ ...p, parent_name: null, path: p.name, depth: 0 });
      (p.children || []).forEach((c) => out.push({ ...c, parent_name: p.name, parent_color: p.color, path: `${p.name} › ${c.name}`, depth: 1 }));
    });
    return out;
  }

  const categories = (opts) => get('categories', '/api/categories', opts);
  const categoriesFlat = async (opts) => flatten(await categories(opts));
  const accounts = (opts) => get('accounts', '/api/accounts?all=1', opts);
  const tags = (opts) => get('tags', '/api/tags', opts);
  const settings = (opts) => get('settings', '/api/settings/client', { ttl: 300000, ...(opts || {}) });

  /* Currency used for aggregate views: the user's preference, else the one currency all
     active accounts share, else the currency carrying the most transactions. */
  async function displayCurrency() {
    const me = window.currentUser || {};
    const pref = me.preferences && me.preferences.currency;
    if (pref) return pref;
    let accts = [];
    try { accts = await accounts(); } catch { return 'USD'; }
    const list = accts.filter((a) => a.is_active).length ? accts.filter((a) => a.is_active) : accts;
    if (!list.length) return 'USD';
    const weight = {};
    list.forEach((a) => { weight[a.currency] = (weight[a.currency] || 0) + 1 + (Number(a.txn_count) || 0); });
    return Object.keys(weight).sort((a, b) => weight[b] - weight[a])[0] || 'USD';
  }

  /* ---------- screen data: show this tab's last copy at once, then refresh ----------
     page(key, load, render) calls render(data, { cached: true }) right away with the copy kept from
     the last visit (if any), then load() (a URL or a function returning a promise) and calls
     render(fresh, { cached: false }) only if the fresh data differs. It resolves with the fresh
     data and rejects if load() fails, with err.shown telling whether a cached copy is on screen.
     A write made while the refresh is in flight wins: its (possibly older) result is not kept, and
     is only rendered when nothing was shown yet. Every write drops every copy (afterWrite). */
  const PAGE_PREFIX = 'page:';
  const PAGES_ALL = 'page:*';
  let writes = 0;
  function dropPages() {
    Object.keys(mem).forEach((k) => { if (k.startsWith(PAGE_PREFIX)) delete mem[k]; });
    try {
      const doomed = [];
      for (let i = 0; i < sessionStorage.length; i++) {
        const k = sessionStorage.key(i);
        if (k && k.startsWith(SS_PREFIX + PAGE_PREFIX)) doomed.push(k);
      }
      doomed.forEach((k) => sessionStorage.removeItem(k));
    } catch { /* storage unavailable */ }
  }
  function keepPage(key, entry, json) {
    mem[key] = entry;
    const raw = `{"at":${entry.at},"data":${json}}`;
    try { sessionStorage.setItem(SS_PREFIX + key, raw); } catch {
      // Full: screen copies are the expendable part of the cache; start them over.
      dropPages();
      mem[key] = entry;
      try { sessionStorage.setItem(SS_PREFIX + key, raw); } catch { /* keep it in memory only */ }
    }
  }
  async function page(key, load, render) {
    key = PAGE_PREFIX + key;
    const entry = mem[key] || ssGet(key);
    const shownJson = entry ? JSON.stringify(entry.data) : null;
    const epoch = writes;
    // The refresh goes out first: painting the cached copy can take a while on a slow phone.
    const fresh = typeof load === 'string' ? apiShared(load) : load();
    fresh.catch(() => {}); // handled below, after the cached paint
    if (entry) render(entry.data, { cached: true });
    let data;
    try {
      data = await fresh;
    } catch (err) {
      err.shown = !!entry;
      throw err;
    }
    const json = JSON.stringify(data);
    if (epoch !== writes) {
      if (!entry) render(data, { cached: false });
      return data;
    }
    keepPage(key, { at: Date.now(), data }, json);
    if (json !== shownJson) render(data, { cached: false });
    return data;
  }
  /* True when this tab holds no copy for key: a write elsewhere dropped it, so data on screen may be old. */
  function pageStale(key) { return !(mem[PAGE_PREFIX + key] || ssGet(PAGE_PREFIX + key)); }

  /* api() calls this after every successful write. Anything cached that the write may have made
     stale is dropped: the signed-in user after a change to /api/auth/me*, the review count and every
     screen copy after any write (almost every write can move money between categories or in and out
     of the review queue). Other tabs drop their screen copies too. */
  function afterWrite(path) {
    writes += 1;
    if (path.startsWith('/api/auth/me')) invalidate('me');
    invalidate('review-count');
    dropPages();
    if (bc) { try { bc.postMessage({ key: PAGES_ALL, invalidate: true }); } catch { /* ignore */ } }
  }

  return { get, invalidate, afterWrite, page, pageStale, on, emit, flatten, categories, categoriesFlat, accounts, tags, settings, displayCurrency };
})();
