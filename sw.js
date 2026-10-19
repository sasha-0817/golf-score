/* GOLF SC. service worker — 電波がなくても開けるようにする */
const CACHE = "golfsc-v2";
const CORE = [
  "./", "./index.html", "./manifest.webmanifest",
  "./icon.png", "./icon-192.png", "./icon-512.png", "./mark.png", "./ball.png",
  "https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js",
  "https://cdnjs.cloudflare.com/ajax/libs/html2canvas/1.4.1/html2canvas.min.js",
];

self.addEventListener("install", e => {
  e.waitUntil((async () => {
    const c = await caches.open(CACHE);
    // 1つ失敗しても他は入れておく
    await Promise.all(CORE.map(u => c.add(new Request(u, { mode: u.startsWith("http") ? "cors" : "same-origin" })).catch(() => {})));
    self.skipWaiting();
  })());
});

self.addEventListener("activate", e => {
  e.waitUntil((async () => {
    for (const k of await caches.keys()) if (k !== CACHE) await caches.delete(k);
    await self.clients.claim();
  })());
});

self.addEventListener("fetch", e => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.hostname.endsWith("script.google.com") || url.hostname.endsWith("googleusercontent.com")) return; // データの読み書きは素通し
  if (url.searchParams.has("check")) return; // 新しい版の確認は必ずネットへ

  // ページ本体: ネット優先、つながらなければ保存しておいた版
  if (req.mode === "navigate" || (url.origin === location.origin && url.pathname.endsWith(".html"))) {
    e.respondWith((async () => {
      try {
        const res = await fetch(req);
        const c = await caches.open(CACHE); c.put("./index.html", res.clone());
        return res;
      } catch (err) {
        return (await caches.match("./index.html")) || (await caches.match("./")) || Response.error();
      }
    })());
    return;
  }

  // 画像・ライブラリ・フォント: 保存済みならそれを使い、裏で更新
  e.respondWith((async () => {
    const hit = await caches.match(req);
    const net = fetch(req).then(async res => {
      if (res && (res.ok || res.type === "opaque")) { const c = await caches.open(CACHE); c.put(req, res.clone()); }
      return res;
    }).catch(() => null);
    return hit || (await net) || Response.error();
  })());
});
