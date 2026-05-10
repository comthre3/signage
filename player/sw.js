const PLAYER_VERSION = "__PLAYER_VERSION__";
const CACHE_NAME = `signage-player-${PLAYER_VERSION}`;
const ASSETS = [
  "/",
  "/index.html",
  "/player.js",
  "/styles.css",
  "/config.js",
  "/i18n.js",
  "/i18n/en.json",
  "/i18n/ar.json",
  "/vendor/qrcode.js",
  "/assets/faces/v1_smile.png",
  "/assets/faces/v1_wink.png",
  "/assets/faces/v1_kawaii.png",
  "/assets/faces/v1_heart.png",
  "/assets/faces/v1_star.png",
  "/assets/faces/v1_big.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(ASSETS))
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key)))
    )
  );
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  const isUpload = url.pathname.startsWith("/uploads/");
  if (isUpload) {
    event.respondWith(
      caches.open(CACHE_NAME).then((cache) =>
        cache.match(event.request).then((cached) => {
          const fetchPromise = fetch(event.request)
            .then((response) => {
              cache.put(event.request, response.clone());
              return response;
            })
            .catch(() => cached);
          return cached || fetchPromise;
        })
      )
    );
    return;
  }
  if (url.origin === location.origin) {
    event.respondWith(
      caches.match(event.request).then((cached) => {
        const fetchPromise = fetch(event.request)
          .then((response) => {
            const clone = response.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(event.request, clone));
            return response;
          })
          .catch(() => cached);
        return cached || fetchPromise;
      })
    );
  }
});
