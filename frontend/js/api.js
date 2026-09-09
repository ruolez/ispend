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
    loginRedirect();
    throw new Error('Not authenticated');
  }
  let data = null;
  try { data = await res.json(); } catch { /* non-JSON */ }
  /* One place catches every blocked write, so no page has to know the rule. */
  if (res.status === 402 && data && data.code === 'subscription_required') {
    toast(data.error, { type: 'error', duration: 8000,
      action: { label: 'Subscribe', fn: () => { location.href = data.billing_url || '/billing.html'; } } });
    const err = new Error('Subscription required');
    err.status = 402; err.data = data;
    throw err;
  }
  if (!res.ok) {
    const err = new Error((data && data.error) || httpFallback(res.status));
    err.status = res.status;
    err.data = data;
    throw err;
  }
  return data;
}

/* Session gone: back to login. A browser that signed in before (ispend.uid survives until sign-out)
   is told the session expired; a first-time visitor just sees the sign-in form. */
function loginRedirect() {
  let known = false;
  try { known = !!localStorage.getItem('ispend.uid'); } catch { /* storage unavailable */ }
  const next = encodeURIComponent(location.pathname + location.search);
  location.href = `/login.html?next=${next}${known ? '&reason=expired' : ''}`;
}
const HTTP_FALLBACK = {
  403: "You don't have permission to do that",
  404: 'Not found',
  409: 'That conflicts with something that already exists',
  413: 'That file is too large',
  402: 'Your subscription has ended',
  429: 'Too many requests — wait a minute and try again',
};
function httpFallback(status, verb = 'Request failed') {
  if (HTTP_FALLBACK[status]) return HTTP_FALLBACK[status];
  return status >= 500 ? 'Server error — try again in a moment' : `${verb} (${status})`;
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
      if (xhr.status === 401) { loginRedirect(); return reject(new Error('Not authenticated')); }
      if (xhr.status === 402 && data && data.code === 'subscription_required') {
        toast(data.error, { type: 'error', duration: 8000,
          action: { label: 'Subscribe', fn: () => { location.href = data.billing_url || '/billing.html'; } } });
        return reject(Object.assign(new Error('Subscription required'), { status: 402, data }));
      }
      if (xhr.status >= 200 && xhr.status < 300) return resolve(data);
      const err = new Error((data && data.error) || httpFallback(xhr.status, 'Upload failed'));
      err.status = xhr.status;
      return reject(err);
    });
    xhr.addEventListener('error', () => reject(new Error('Network error during upload')));
    xhr.addEventListener('abort', () => reject(new Error('Upload cancelled')));
    xhr.send(formData);
  });
}

/* Raw-body upload with progress. A restore archive is far too large for FormData buffering, and
   the backend reads request.stream directly rather than parsing multipart. */
function apiUploadRaw(path, file, { onProgress } = {}) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', path);
    xhr.withCredentials = true;
    xhr.setRequestHeader('Content-Type', 'application/octet-stream');
    if (onProgress) xhr.upload.addEventListener('progress', (e) => { if (e.lengthComputable) onProgress(e.loaded / e.total); });
    xhr.addEventListener('load', () => {
      let data = null;
      try { data = JSON.parse(xhr.responseText); } catch { /* ignore */ }
      if (xhr.status === 401) { loginRedirect(); return reject(new Error('Not authenticated')); }
      if (xhr.status >= 200 && xhr.status < 300) return resolve(data);
      const err = new Error((data && data.error) || httpFallback(xhr.status, 'Upload failed'));
      err.status = xhr.status;
      return reject(err);
    });
    xhr.addEventListener('error', () => reject(new Error('Network error during upload')));
    xhr.addEventListener('abort', () => reject(new Error('Upload cancelled')));
    xhr.send(file);
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
function setQs(obj, { merge = true } = {}) {
  const params = merge ? new URLSearchParams(location.search) : new URLSearchParams();
  Object.entries(obj).forEach(([k, v]) => {
    if (v == null || v === '' || v === false || (Array.isArray(v) && !v.length)) params.delete(k);
    else params.set(k, Array.isArray(v) ? v.join(',') : String(v));
  });
  const q = params.toString();
  const url = location.pathname + (q ? `?${q}` : '') + location.hash;
  history.replaceState(null, '', url); // filter changes never create history entries
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
function uid() { return Math.random().toString(36).slice(2, 9); }
