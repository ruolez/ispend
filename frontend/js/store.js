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
  }

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
  const categoryById = async (id) => (await categoriesFlat()).find((c) => c.id === Number(id)) || null;
  const accounts = (opts) => get('accounts', '/api/accounts?all=1', opts);
  const accountById = async (id) => (await accounts()).find((a) => a.id === Number(id)) || null;
  const settings = (opts) => get('settings', '/api/settings/client', { ttl: 300000, ...(opts || {}) });

  return { get, invalidate, on, emit, flatten, categories, categoriesFlat, categoryById, accounts, accountById, settings };
})();
