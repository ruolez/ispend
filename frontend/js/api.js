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
      if (xhr.status === 401) { location.href = '/login.html'; return reject(new Error('Not authenticated')); }
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
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}
function sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }
function uid() { return Math.random().toString(36).slice(2, 9); }
