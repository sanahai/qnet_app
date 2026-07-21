// 큐넷 자격증 시험일정 - 오프라인 지원용 서비스워커
// Play Store(TWA)/App Store(Capacitor) 심사에서 "설치 가능한 PWA"로 인정받으려면
// 서비스워커 등록이 있는 편이 유리합니다.

const CACHE_NAME = "qnet-dashboard-v1";
const PRECACHE_URLS = [
  "./dashboard.html",
  "./manifest.json",
  "./icons/icon-192.png",
  "./icons/icon-512.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(PRECACHE_URLS))
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);

  // data/*.json은 항상 최신을 우선 시도하고, 실패하면(오프라인) 캐시로 대체
  if (url.pathname.includes("/data/")) {
    event.respondWith(
      fetch(event.request)
        .then((res) => {
          const clone = res.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(event.request, clone));
          return res;
        })
        .catch(() => caches.match(event.request))
    );
    return;
  }

  // 나머지 정적 자원은 캐시 우선
  event.respondWith(
    caches.match(event.request).then((cached) => cached || fetch(event.request))
  );
});
