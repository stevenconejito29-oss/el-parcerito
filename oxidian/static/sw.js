/* ═══════════════════════════════════════════════════════════════
   Oxidian — Service Worker v34
   • Assets propios CSS/JS/IMG: cache-first + actualización en segundo plano
   • HTML público y datos de sesión: network-only
   • API / Admin       : Network-only (nunca cachear dinámico)
   • Push Notifications: Muestra notificaciones + abre URL al click
   ═══════════════════════════════════════════════════════════════ */

const CACHE_STATIC = "ox-static-v34";
const CACHE_PREFIX = "ox-";

const PRECACHE = [
  "/static/css/tokens.css",
  "/static/css/oxidian.css",
  "/static/css/oxidian-ui.css",
  "/static/css/storefront-menu.css",
  "/static/css/storefront-cart.css",
  "/static/css/header-modern.css",
  "/static/css/tailwind.generated.css",
  "/static/js/carrito.js",
  "/static/js/storefront-viewport.js",
  "/static/js/storefront-toast.js",
  "/static/js/header-modern.js",
  "/static/pwa-icon.svg",
  "/static/pwa-icon-192.png",
  "/static/pwa-icon-512.png",
  "/static/pwa-icon-512-maskable.png",
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
<meta name="theme-color" content="#D9961A">
<title>Sin conexión</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:system-ui,sans-serif;background:#FFFDF8;color:#18120A;
display:flex;flex-direction:column;align-items:center;justify-content:center;
min-height:100dvh;padding:2rem;text-align:center;gap:1rem}
.icon{font-size:3.5rem}.title{font-size:1.35rem;font-weight:900}
p{font-size:.95rem;color:#6B5A4E;max-width:340px;line-height:1.5}
a,button{min-height:44px;padding:.75rem 1.5rem;border-radius:.875rem;border:0;
background:#D9961A;color:#1B0A00;font-weight:800;font-size:1rem;text-decoration:none}
</style></head><body>
<div class="icon">📡</div>
<p class="title">Sin conexión</p>
	<p>Esta sección necesita internet para proteger tus datos y confirmar cambios.</p>
<a href="/">Volver al menú</a>
<button onclick="location.reload()">Reintentar</button>
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
  self.skipWaiting();
});

// ── ACTIVATE ─────────────────────────────────────────────────────────────
self.addEventListener("activate", event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(
        keys
          .filter(k => k.startsWith(CACHE_PREFIX) && k !== CACHE_STATIC)
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
    event.respondWith(
      caches.open(CACHE_STATIC).then(async cache => {
        const cached = await cache.match(event.request);
        const update = fetch(event.request, { cache: "no-cache" })
          .then(response => {
            if (canStore(response)) cache.put(event.request, response.clone());
            return response;
          })
          .catch(() => null);
        return cached || await update || Response.error();
      })
    );
    return;
  }

  event.respondWith(fetch(event.request, { cache: "no-store" }).catch(() => offlineResponse()));
});

// ═══════════════════════════════════════════════════════════════
// PUSH NOTIFICATIONS
// ═══════════════════════════════════════════════════════════════

self.addEventListener("push", event => {
  if (!event.data) return;

  let payload;
  try { payload = event.data.json(); }
  catch { payload = { title: "Mi tienda", body: event.data.text() }; }

  const {
    title  = "Mi tienda",
    body   = "",
    icon   = "/static/pwa-icon-192.png",
    badge  = "/static/favicon-32.png",
    url    = "/",
    tag,
    requireInteraction = false,
  } = payload;

  const options = {
    body,
    icon,
    badge,
    tag: tag || "ox-" + Date.now(),
    requireInteraction,
    vibrate: [200, 100, 200],
    data: { url },
    actions: [
      { action: "open",    title: "Ver"     },
      { action: "dismiss", title: "Ignorar" },
    ],
  };

  event.waitUntil(self.registration.showNotification(title, options));
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
    clients.matchAll({ type: "window", includeUncontrolled: true }).then(list => {
      // Si ya hay una ventana abierta con esa URL, enfocarla
      for (const client of list) {
        if (client.url === fullUrl && "focus" in client) {
          return client.focus();
        }
      }
      // Si hay cualquier ventana abierta, navegar ahí
      for (const client of list) {
        if ("focus" in client) {
          client.navigate(fullUrl);
          return client.focus();
        }
      }
      // Ninguna ventana abierta: abrir nueva
      return clients.openWindow(fullUrl);
    })
  );
});

// ── Cierre de notificación sin interacción ────────────────────────────────
self.addEventListener("notificationclose", event => {
  // Analytics: el usuario descartó la notificación
});
