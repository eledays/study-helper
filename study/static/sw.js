const CACHE = 'study-shell-v6';
const ASSETS = ['/static/offline.html', '/static/style.css', '/static/app.js', '/static/navigation.js', '/static/favicon.svg', '/static/icon-192.png', '/static/icon-512.png', '/static/icon-maskable.png'];
self.addEventListener('install', event => {
 event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(ASSETS)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', event => {
 event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(key => key.startsWith('study-shell-') && key !== CACHE).map(key => caches.delete(key)))).then(() => self.clients.claim()));
});
self.addEventListener('fetch', event => {
 const request = event.request, url = new URL(request.url);
 if (request.method !== 'GET' || url.origin !== self.location.origin) return;
 if (request.mode === 'navigate') {
  // Never cache authenticated pages, CSRF tokens, or results.
  event.respondWith(fetch(request).catch(() => caches.match('/static/offline.html')));
 } else if (ASSETS.includes(url.pathname) && !url.search) {
  event.respondWith(fetch(request).catch(() => caches.match(request)));
 }
});
