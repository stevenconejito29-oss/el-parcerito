/* ═══════════════════════════════════════════════════════════════
   Oxidian — Service Worker v52
   • App shell CSS/JS: cache-first + actualización en segundo plano
   • Imágenes: stale-while-revalidate con caché acotada
   • HTML público y datos de sesión: network-only
   • API / Admin       : Network-only (nunca cachear dinámico)
   • Push Notifications: Muestra notificaciones + abre URL al click
   ═══════════════════════════════════════════════════════════════ */

const CACHE_STATIC = "ox-static-v52";
const CACHE_MEDIA = "ox-media-v52";
const CACHE_PREFIX = "ox-";

const PRECACHE = [
  "/static/css/tokens.css",
  "/static/css/oxidian.css",
  "/static/css/oxidian-ui.css",
  "/static/css/storefront-menu.css",
  "/static/css/storefront-cart.css",
  "/static/css/header-modern.css",
  "/static/css/role-shell.css",
  "/static/css/operational-roles.css",
  "/static/css/tailwind.generated.css",
  "/static/js/carrito.js",
  "/static/js/pwa-manager.js",
  "/static/js/storefront-viewport.js",
  "/static/js/storefront-toast.js",
  "/static/js/header-modern.js",
  "/static/js/operational-roles.js",
  "/static/pwa-icon.svg",
  "/static/pwa-icon-192.png",
  "/static/pwa-icon-512.png",
  "/static/pwa-icon-512-maskable.png",
  "/static/pwa-badge-96.png",
  "/static/apple-touch-icon.png",
];

function isNetworkOnly(pathname) {
  return (
    pathname.startsWith("/api/") ||
    pathname.startsWith("/admin") ||
    pathname.startsWith("/superadmin") ||
    pathname.startsWith("/preparador") ||
    pathname.startsWith("/repartidor") ||
    pathname.startsWith("/staff") ||
    pathname.startsWith("/pos") ||
    pathname.startsWith("/auth") ||
    pathname.startsWith("/marketing") ||
    pathname.startsWith("/carrito") ||
    pathname.startsWith("/checkout") ||
    pathname.startsWith("/pedido/") ||
    pathname.startsWith("/perfil") ||
    pathname.startsWith("/puntos/")
  );
}

function isStaticAsset(pathname) {
  return (
    pathname.startsWith("/static/") &&
    /\.(css|js|woff2?|ttf|otf|png|jpg|jpeg|webp|avif|svg|ico|gif)$/i.test(pathname)
  );
}

function isMediaAsset(pathname) {
  return /\.(png|jpg|jpeg|webp|avif|svg|ico|gif)$/i.test(pathname);
}

function canStore(response) {
  return Boolean(
    response &&
    response.ok &&
    response.type === "basic" &&
    !response.headers.has("Set-Cookie") &&
    !/no-store|private/i.test(response.headers.get("Cache-Control") || "")
  );
}

function offlineResponse() {
  return new Response(
    `<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#F4C542">
<title>Sin conexión</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:system-ui,sans-serif;background:#FFFDF8;color:#18120A;
display:flex;flex-direction:column;align-items:center;justify-content:center;
min-height:100dvh;padding:2rem;text-align:center;gap:1rem}
.icon{width:88px;height:88px;border-radius:24px;box-shadow:0 16px 34px #4B21002b}.title{font-size:1.35rem;font-weight:900}
p{font-size:.95rem;color:#6B5A4E;max-width:340px;line-height:1.5}
a,button{min-height:44px;padding:.75rem 1.5rem;border-radius:.875rem;border:0;
background:#F4C542;color:#2B2118;font-weight:800;font-size:1rem;text-decoration:none}
</style></head><body>
<img class="icon" src="/static/pwa-icon-192.png" alt="">
<p class="title">Ahora mismo no hay conexión</p>
<p>Tu app sigue instalada y tus datos están protegidos. Recupera internet para consultar disponibilidad o confirmar cambios.</p>
<a href="/">Volver a intentar</a>
</body></html>`,
    { headers: { "Content-Type": "text/html;charset=utf-8" } }
  );
}

// ── INSTALL ──────────────────────────────────────────────────────────────
self.addEventListener("install", event => {
  event.waitUntil(
    caches.open(CACHE_STATIC).then(async cache => {
      await Promise.allSettled(
        PRECACHE.map(url => cache.add(new Request(url, { cache: "reload" })))
      );
    })
  );
});

// ── ACTIVATE ─────────────────────────────────────────────────────────────
self.addEventListener("activate", event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(
        keys
          .filter(k => k.startsWith(CACHE_PREFIX) && ![CACHE_STATIC, CACHE_MEDIA].includes(k))
          .map(k => caches.delete(k))
      )
    )
  );
  self.clients.claim();
});

// ── MESSAGES ─────────────────────────────────────────────────────────────
self.addEventListener("message", event => {
  if (event.data?.type === "SKIP_WAITING") self.skipWaiting();
});

// ── FETCH ────────────────────────────────────────────────────────────────
self.addEventListener("fetch", event => {
  if (event.request.method !== "GET") return;
  const reqUrl = event.request.url;
  if (!reqUrl.startsWith(self.location.origin)) return;
  const { pathname } = new URL(reqUrl);

  if (event.request.mode === "navigate" || isNetworkOnly(pathname)) {
    event.respondWith(
      fetch(event.request, { cache: "no-store" }).catch(() => offlineResponse())
    );
    return;
  }

  if (isStaticAsset(pathname)) {
    const cacheName = isMediaAsset(pathname) ? CACHE_MEDIA : CACHE_STATIC;
    const refresh = caches.open(cacheName).then(cache =>
      fetch(event.request).then(async response => {
        if (canStore(response)) {
          await cache.put(event.request, response.clone());
          if (isMediaAsset(pathname)) await trimCache(cache, 80);
        }
        return response;
      })
    );
    event.waitUntil(refresh.catch(() => null));
    event.respondWith(
      caches.open(cacheName).then(cache => cache.match(event.request)).then(cached =>
        cached || refresh.catch(() => Response.error())
      )
    );
    return;
  }

  event.respondWith(fetch(event.request, { cache: "no-store" }).catch(() => offlineResponse()));
});

async function trimCache(cache, maximumEntries) {
  const keys = await cache.keys();
  if (keys.length <= maximumEntries) return;
  await Promise.all(keys.slice(0, keys.length - maximumEntries).map(key => cache.delete(key)));
}

// ═══════════════════════════════════════════════════════════════
// PUSH NOTIFICATIONS
// ═══════════════════════════════════════════════════════════════

self.addEventListener("push", event => {
  let payload;
  try { payload = event.data ? event.data.json() : {}; }
  catch { payload = { title: "Mi tienda", body: event.data?.text() || "Tienes una novedad." }; }

  const {
    title  = "Mi tienda",
    body   = "",
    icon   = "/static/pwa-icon-192.png",
    badge  = "/static/pwa-badge-96.png",
    url    = "/",
    tag,
    requireInteraction = false,
    timestamp = Date.now(),
    actions = [{ action: "open", title: "Ver ahora" }],
    badgeCount = 1,
  } = payload;

  const safeTitle = String(title || "Mi tienda").slice(0, 80);
  const safeBody = String(body || "").slice(0, 180);
  const options = {
    body: safeBody,
    icon,
    badge,
    tag: tag || "ox-" + Date.now(),
    requireInteraction,
    renotify: Boolean(tag),
    vibrate: [180, 80, 180],
    silent: false,
    timestamp: Number(timestamp) || Date.now(),
    lang: "es",
    data: { url: typeof url === "string" ? url : "/" },
    actions: Array.isArray(actions)
      ? actions.slice(0, 2).map(action => ({
          action: String(action.action || "open").slice(0, 32),
          title: String(action.title || "Abrir").slice(0, 32),
        }))
      : [{ action: "open", title: "Ver ahora" }],
  };

  event.waitUntil((async () => {
    const windows = await clients.matchAll({ type: "window", includeUncontrolled: true });
    windows.forEach(client => client.postMessage({
      type: "OX_PUSH_RECEIVED",
      payload: { title: safeTitle, body: safeBody, url: options.data.url },
    }));
    if ("setAppBadge" in self.registration && Number(badgeCount) > 0) {
      await self.registration.setAppBadge(Number(badgeCount)).catch(() => {});
    }
    await self.registration.showNotification(safeTitle, options);
  })());
});

// ── Click en la notificación ─────────────────────────────────────────────
self.addEventListener("notificationclick", event => {
  event.notification.close();

  if (event.action === "dismiss") return;

  const targetUrl = event.notification.data?.url || "/";
  const parsedTarget = new URL(targetUrl, self.location.origin);
  const fullUrl = parsedTarget.origin === self.location.origin
    ? parsedTarget.href
    : self.location.origin + "/";

  event.waitUntil(
    Promise.resolve("clearAppBadge" in self.registration
      ? self.registration.clearAppBadge().catch(() => {})
      : null).then(() => clients.matchAll({ type: "window", includeUncontrolled: true })).then(list => {
      // Si ya hay una ventana abierta con esa URL, enfocarla
      for (const client of list) {
        if (client.url === fullUrl && "focus" in client) {
          return client.focus();
        }
      }
      // Si hay cualquier ventana abierta, navegar ahí
      for (const client of list) {
        if ("focus" in client) {
          return Promise.resolve(client.navigate?.(fullUrl)).catch(() => null).then(() => client.focus());
        }
      }
      // Ninguna ventana abierta: abrir nueva
      return clients.openWindow(fullUrl);
    })
  );
});

self.addEventListener("pushsubscriptionchange", event => {
  event.waitUntil(
    clients.matchAll({ type: "window", includeUncontrolled: true }).then(list => {
      list.forEach(client => client.postMessage({ type: "OX_PUSH_SUBSCRIPTION_CHANGED" }));
    })
  );
});

// ── Cierre de notificación sin interacción ────────────────────────────────
self.addEventListener("notificationclose", event => {
  // Analytics: el usuario descartó la notificación
});
