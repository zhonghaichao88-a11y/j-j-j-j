// Service Worker：接收网页推送（来电、消息），点通知打开对应页面。
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));

self.addEventListener("push", (event) => {
  let d = {};
  try { d = event.data ? event.data.json() : {}; } catch { d = { title: "SeeU", body: event.data?.text() || "" }; }
  const isCall = d.type === "call";
  event.waitUntil((async () => {
    // 页面正开着并且在前台时，页面自己会弹来电 / 消息，不再重复通知
    const wins = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    if (wins.some((w) => w.visibilityState === "visible")) return;
    await self.registration.showNotification(d.title || "SeeU", {
      body: d.body || "", tag: d.tag || d.type || "seeu", renotify: true, requireInteraction: isCall,
      icon: "icon.svg", badge: "icon.svg", vibrate: isCall ? [500, 300, 500, 300, 500] : [200],
      data: { url: d.url || "/" },
    });
  })());
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = new URL(event.notification.data?.url || "/", self.location.origin).href;
  event.waitUntil((async () => {
    const wins = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    for (const w of wins) {
      if (new URL(w.url).origin === self.location.origin) {
        await w.focus();
        w.postMessage({ type: "open", url });
        return;
      }
    }
    await self.clients.openWindow(url);
  })());
});
