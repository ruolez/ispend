/* API client + tiny DOM/URL helpers shared by every page. */

async function api(path, options = {}) {
  const opts = { credentials: 'same-origin', headers: {}, ...options };
  const isForm = typeof FormData !== 'undefined' && opts.body instanceof FormData;
  if (!isForm) opts.headers = { 'Content-Type': 'application/json', ...(options.headers || {}) };
  if (opts.body && !isForm && typeof opts.body !== 'string') opts.body = JSON.stringify(opts.body);
  let res;
  try {
    res = await fetch(path, opts);
  } catch (err) {
    throw new Error('Network error — is the server reachable?');
  }
  if (res.status === 401 && !location.pathname.endsWith('/login.html')) {
    const next = encodeURIComponent(location.pathname + location.search);
    location.href = `/login.html?next=${next}`;
    throw new Error('Not authenticated');
  }
  let data = null;
  try { data = await res.json(); } catch { /* non-JSON */ }
  if (!res.ok) {
    const err = new Error((data && data.error) || `Request failed (${res.status})`);
    err.status = res.status;
    err.data = data;
    throw err;
  }
  return data;
}

/* Upload with progress (XHR, because fetch has no upload progress). */
function apiUpload(path, formData, { onProgress } = {}) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', path);
    xhr.withCredentials = true;
    if (onProgress) xhr.upload.addEventListener('progress', (e) => { if (e.lengthComputable) onProgress(e.loaded / e.total); });
    xhr.addEventListener('load', () => {
      let data = null;
      try { data = JSON.parse(xhr.responseText); } catch { /* ignore */ }
      if (xhr.status === 401) { location.href = `/login.html?next=${encodeURIComponent(location.pathname + location.search)}`; return reject(new Error('Not authenticated')); }
      if (xhr.status >= 200 && xhr.status < 300) return resolve(data);
      const err = new Error((data && data.error) || `Upload failed (${xhr.status})`);
      err.status = xhr.status;
      reject(err);
    });
    xhr.addEventListener('error', () => reject(new Error('Network error during upload')));
    xhr.addEventListener('abort', () => reject(new Error('Upload cancelled')));
    xhr.send(formData);
  });
}

/* Everything a signed-in user leaves in this browser, except device preferences. Called on sign-out,
   on login, and whenever the signed-in user differs from the one who used this browser last. */
const DEVICE_KEYS = ['ispend.theme', 'ispend.density', 'ispend.sidebar'];
function clearUserState() {
  [localStorage, sessionStorage].forEach((storage) => {
    try {
      const doomed = [];
      for (let i = 0; i < storage.length; i++) {
        const k = storage.key(i);
        if (k && k.startsWith('ispend.') && !DEVICE_KEYS.includes(k)) doomed.push(k);
      }
      doomed.forEach((k) => storage.removeItem(k));
    } catch { /* storage unavailable */ }
  });
}

/* Load a script once (e.g. Chart.js only when a page first needs a chart). */
const _scripts = new Map();
function loadScript(src) {
  if (!_scripts.has(src)) {
    _scripts.set(src, new Promise((resolve, reject) => {
      const s = document.createElement('script');
      s.src = src; s.onload = () => resolve(); s.onerror = () => { _scripts.delete(src); reject(new Error(`Could not load ${src}`)); };
      document.head.appendChild(s);
    }));
  }
  return _scripts.get(src);
}

function esc(s) {
  if (s == null) return '';
  return String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

/* URL query helpers: qs() -> {key: value}; setQs({...}) writes (drops empty keys). */
function qs() {
  const out = {};
  new URLSearchParams(location.search).forEach((v, k) => { out[k] = v; });
  return out;
}
function setQs(obj, { replace = true, merge = true } = {}) {
  const params = merge ? new URLSearchParams(location.search) : new URLSearchParams();
  Object.entries(obj).forEach(([k, v]) => {
    if (v == null || v === '' || v === false || (Array.isArray(v) && !v.length)) params.delete(k);
    else params.set(k, Array.isArray(v) ? v.join(',') : String(v));
  });
  const q = params.toString();
  const url = location.pathname + (q ? `?${q}` : '') + location.hash;
  if (replace) history.replaceState(null, '', url); else history.pushState(null, '', url);
  rememberQuery();
}

/* ---------- per-page query memory (session) ----------
   Filters, sorts and tabs live in the URL; remember each page's last query so returning
   through the sidebar restores it. Keys that open a specific thing are not remembered. */
const PERIOD_QS = ['range', 'from', 'to', 'month'];
const TRANSIENT_QS = { '/transactions.html': ['open'], '/import.html': ['statement'], '/rules.html': ['cat', 'new'], '/statements.html': ['open'] };
function _qsKey(path) { return `ispend.q:${path}`; }
function rememberQuery() {
  try {
    const p = new URLSearchParams(location.search);
    const hadParams = Array.from(p.keys()).length > 0;
    (TRANSIENT_QS[location.pathname] || []).concat(PERIOD_QS).forEach((k) => p.delete(k));
    const q = p.toString();
    if (hadParams && !q) return; // a deep link with only transient keys must not erase remembered filters
    sessionStorage.setItem(_qsKey(location.pathname), (q ? `?${q}` : '') + (location.hash || ''));
  } catch { /* storage unavailable */ }
}
function savedQuery(path) {
  try { return sessionStorage.getItem(_qsKey(path)) || ''; } catch { return ''; }
}
/* Called once at page start (before page scripts read qs()). Returns true when a saved query was applied. */
function restoreQuery() {
  const cur = new URLSearchParams(location.search);
  const transient = TRANSIENT_QS[location.pathname] || [];
  if (Array.from(cur.keys()).some((k) => !transient.includes(k))) { rememberQuery(); return false; }
  const saved = savedQuery(location.pathname);
  if (!saved || (location.hash && !saved.includes('?'))) return false;
  const [q, hash] = saved.split('#');
  // keep transient keys from the deep link (e.g. ?open=<id>) on top of the remembered filters
  const merged = new URLSearchParams(q.replace(/^\?/, ''));
  cur.forEach((v, k) => merged.set(k, v));
  const qs2 = merged.toString();
  history.replaceState(null, '', location.pathname + (qs2 ? `?${qs2}` : '') + (location.hash || (hash ? `#${hash}` : '')));
  return true;
}
function toQuery(obj) {
  const p = new URLSearchParams();
  Object.entries(obj || {}).forEach(([k, v]) => {
    if (v == null || v === '' || v === false || (Array.isArray(v) && !v.length)) return;
    p.set(k, Array.isArray(v) ? v.join(',') : String(v));
  });
  const s = p.toString();
  return s ? `?${s}` : '';
}

function $(sel, root = document) { return root.querySelector(sel); }
function $$(sel, root = document) { return Array.from(root.querySelectorAll(sel)); }
function debounce(fn, ms = 250) {
  let t = null;
  const wrapped = (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
  wrapped.cancel = () => clearTimeout(t);
  return wrapped;
}
function sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }
function uid() { return Math.random().toString(36).slice(2, 9); }
