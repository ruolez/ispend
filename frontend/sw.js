/* Service worker: serves the app shell (pages, CSS, JS, icons, the Latin Inter, Chart.js) from a
   cache so a screen switch never waits on the network for files, and swaps the browser's error page
   for /offline.html when a navigation fails. /api/ is never intercepted — data always comes live.

   nginx/40-ispend-sw.sh stamps BUILD (a content hash of every shell file) and PRECACHE at container
   start, so a deploy changes this file's bytes; the browser then installs the new worker, which
   precaches the whole new shell before taking over — pages never mix files from two deploys.
   Served unstamped, or stamped "dev" (docker-compose.dev.yml), it caches only the offline page. */
const BUILD = '__ISPEND_BUILD__';
const PRECACHE = [/*__ISPEND_PRECACHE__*/];
const CACHING = !BUILD.startsWith('__') && BUILD !== 'dev' && PRECACHE.length > 0;
const CACHE = `ispend-shell-${CACHING ? BUILD : 'dev'}`;
const OFFLINE_URL = '/offline.html';
const SHELL = new Set(PRECACHE);
// Clean URLs nginx serves from a shell file.
const ALIASES = { '/admin': '/admin.html' };

self.addEventListener('install', (event) => {
  const urls = Array.from(new Set([...PRECACHE, OFFLINE_URL]));
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll(urls.map((u) => new Request(u, { cache: 'reload' })))));
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    const names = await caches.keys();
    await Promise.all(names.filter((n) => n.startsWith('ispend-') && n !== CACHE).map((n) => caches.delete(n)));
    // Preload only helps when navigations go to the network; with a cached shell it would be a wasted request.
    if (self.registration.navigationPreload) {
      await (CACHING ? self.registration.navigationPreload.disable() : self.registration.navigationPreload.enable());
    }
    await self.clients.claim();
  })());
});

async function fromShell(path) {
  const cache = await caches.open(CACHE);
  return cache.match(path);
}

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  // Before the navigation test: CSV exports, backup downloads and the Stripe hand-off are
  // navigations to /api/ and must reach nginx untouched (and never land on the offline page).
  if (url.pathname.startsWith('/api/')) return;
  const path = ALIASES[url.pathname] || url.pathname;
  const cached = CACHING && SHELL.has(path);
  if (req.mode !== 'navigate') {
    if (cached) event.respondWith(fromShell(path).then((hit) => hit || fetch(req)));
    return;
  }
  event.respondWith((async () => {
    if (cached) {
      const hit = await fromShell(path);
      if (hit) return hit;
    }
    try {
      const preload = await event.preloadResponse;
      return preload || await fetch(req);
    } catch {
      const offline = await caches.match(OFFLINE_URL);
      return offline || Response.error();
    }
  })());
});
