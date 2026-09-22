/* Service worker: makes iSpend installable and swaps the browser's error page for /offline.html
   when a page navigation fails. It deliberately caches nothing else — every page needs /api/auth/me
   to show anything, nginx already serves the frontend with no-store, and a cached shell would only
   be a stale copy waiting to be served after a deploy. /api/ is never intercepted. */
const CACHE = 'ispend-offline-v1';
const OFFLINE_URL = '/offline.html';

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.add(new Request(OFFLINE_URL, { cache: 'reload' }))));
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    const names = await caches.keys();
    await Promise.all(names.filter((n) => n.startsWith('ispend-') && n !== CACHE).map((n) => caches.delete(n)));
    if (self.registration.navigationPreload) await self.registration.navigationPreload.enable();
    await self.clients.claim();
  })());
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  // Before the navigation test: CSV exports, backup downloads and the Stripe hand-off are
  // navigations to /api/ and must reach nginx untouched (and never land on the offline page).
  if (url.pathname.startsWith('/api/')) return;
  if (req.mode !== 'navigate') return;
  event.respondWith((async () => {
    try {
      const preload = await event.preloadResponse;
      return preload || await fetch(req);
    } catch {
      const cached = await caches.match(OFFLINE_URL);
      return cached || Response.error();
    }
  })());
});
