"use strict";

const OFFLINE_CACHE_PREFIX = "treadmillrunner-offline-safety-";
const OFFLINE_CACHE = `${OFFLINE_CACHE_PREFIX}v1`;
const OFFLINE_DOCUMENT = "/offline.html";
const GATEWAY_UNAVAILABLE_STATUSES = new Set([502, 503, 504]);

self.addEventListener("install", event => {
  event.waitUntil(
    caches.open(OFFLINE_CACHE)
      .then(cache => cache.add(new Request(OFFLINE_DOCUMENT, { cache: "reload" })))
  );
});

self.addEventListener("activate", event => {
  event.waitUntil(
    caches.keys().then(keys => Promise.all(
      keys
        .filter(key => key.startsWith(OFFLINE_CACHE_PREFIX) && key !== OFFLINE_CACHE)
        .map(key => caches.delete(key))
    ))
  );
});

self.addEventListener("fetch", event => {
  if (event.request.method !== "GET" || event.request.mode !== "navigate") return;

  event.respondWith((async () => {
    try {
      const response = await fetch(event.request);
      if (!GATEWAY_UNAVAILABLE_STATUSES.has(response.status)) return response;
      return (await caches.match(OFFLINE_DOCUMENT)) || response;
    } catch {
      return (await caches.match(OFFLINE_DOCUMENT)) || Response.error();
    }
  })());
});
