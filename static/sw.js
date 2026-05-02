const CACHE='pwa-v1'; const ASSETS=['/','/inspect/success','https://cdn.tailwindcss.com','https://cdn.jsdelivr.net/npm/sortablejs@1.15.0/Sortable.min.js','/static/offline.js','/static/register-sw.js'];
self.addEventListener('install', e => { self.skipWaiting(); e.waitUntil(caches.open(CACHE).then(c => c.addAll(ASSETS))); });
self.addEventListener('activate', e => { self.clients.claim(); e.waitUntil(caches.keys().then(k => Promise.all(k.filter(x => x!==CACHE).map(x => caches.delete(x)))); });
self.addEventListener('fetch', e => { 
  if(e.request.method==='GET') {
    e.respondWith(caches.match(e.request).then(c => c || fetch(e.request).catch(() => caches.match('/inspect/success'))));
  }
});