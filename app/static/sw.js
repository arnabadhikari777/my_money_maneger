// My Money Tracker - service worker (served from /sw.js so its scope is "/").
//
// Two jobs:
//   1. Receive Web Push messages and show them as phone notifications.
//   2. Keep a small offline fallback for pages you've already opened.
//
// Caching is NETWORK-FIRST everywhere: when you're online you always get the
// latest page/CSS/JS straight from the server (so updates show up right
// away), and the cache is only used when the network fails.

const CACHE_NAME = "mymoneytracker-cache-v4";

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) =>
      cache.addAll([
        "/static/css/style.css",
        "/static/js/app.js",
        "/static/manifest.json",
      ])
    ).catch(() => {})
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;

  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;          // fonts, etc: let the browser handle it

  const isStatic = url.pathname.startsWith("/static/");
  const isPage = req.mode === "navigate";
  if (!isStatic && !isPage) return;                          // JSON/API calls always go straight to the network

  event.respondWith(
    fetch(req)
      .then((res) => {
        if (res && res.ok) {
          const copy = res.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(req, copy)).catch(() => {});
        }
        return res;
      })
      .catch(() =>
        caches.match(req).then((cached) =>
          cached || new Response("You're offline and this page hasn't been opened before.", {
            status: 503,
            headers: { "Content-Type": "text/plain; charset=utf-8" },
          })
        )
      )
  );
});

// ---------- Web Push ----------
self.addEventListener("push", (event) => {
  let data = { title: "My Money Tracker", body: "Check your spending.", url: "/" };
  try {
    if (event.data) data = Object.assign(data, event.data.json());
  } catch (e) {
    if (event.data) data.body = event.data.text();
  }

  event.waitUntil(
    self.registration.showNotification(data.title, {
      body: data.body,
      icon: "/static/icons/icon-192.png",
      badge: "/static/icons/icon-192.png",
      data: { url: data.url || "/" },
    })
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const target = (event.notification.data && event.notification.data.url) || "/";

  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((windows) => {
      for (const w of windows) {
        if ("focus" in w) {
          w.navigate(target).catch(() => {});
          return w.focus();
        }
      }
      return self.clients.openWindow(target);
    })
  );
});
