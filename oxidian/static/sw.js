/* ═══════════════════════════════════════════════════════════════
   Oxidian — Service Worker v59
   • App shell CSS/JS: cache-first + actualización en segundo plano
   • HTML público (menu, producto): NETWORK-FIRST con timeout 1200ms →
     cuando hay red los usuarios reciben SIEMPRE contenido fresco (logos,
     precios, imágenes recientes). Cache solo cubre offline o red lenta.
   • Uploads (/uploads/*): NETWORK-FIRST → si el admin re-sube una imagen con
     nombre igual, la PWA no queda atrapada en la versión vieja.
   • HTML session-specific (carrito, checkout, admin): network-only
   • Static assets versionados por hash: SWR (cache-first + refresh bg)
   • API / Admin: Network-only (nunca cachear dinámico)
   • Push Notifications: Muestra notificaciones + abre URL al click
   • v59: elimina staleness Android — HTML y /uploads/ pasan a network-first
     con fallback a cache. Purga total de buckets v58 y anteriores. Bump icons.
   ═══════════════════════════════════════════════════════════════ */

const CACHE_STATIC = "ox-static-v59";
const CACHE_MEDIA = "ox-media-v59";
const CACHE_HTML = "ox-html-v59";
const CACHE_PREFIX = "ox-";

const PRECACHE = [
  "/static/css/tokens.css",
  "/static/css/oxidian.css",
  "/static/css/oxidian-ui.css",
  "/static/css/storefront-menu.css",
  "/static/css/storefront-cart.css",
  "/static/css/header-modern.css",
  "/static/css/heritage.css",
  "/static/css/role-shell.css",
  "/static/css/operational-roles.css",
  "/static/css/tailwind.generated.css",
  "/static/js/carrito.js",
  "/static/js/cart-ui.js",
  "/static/js/pwa-manager.js",
  "/static/js/storefront-viewport.js",
  "/static/js/storefront-toast.js",
  "/static/js/header-modern.js",
  "/static/js/spa-nav.js",
  "/static/js/operational-roles.js",
  "/static/colombia-pattern.svg",
  "/static/pwa-icon.svg?v=59",
  "/static/pwa-icon-192.png?v=59",
  "/static/pwa-icon-512.png?v=59",
  "/static/pwa-icon-512-maskable.png?v=59",
  "/static/pwa-badge-96.png?v=59",
  "/static/apple-touch-icon.png?v=59",
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
<img class="icon" src="/static/pwa-icon-192.png?v=59" alt="">
<p class="title">Ahora mismo no hay conexión</p>
<p>Tu app sigue instalada y tus datos están protegidos. Recupera internet para consultar disponibilidad o confirmar cambios.</p>
<a href="/">Volver a intentar</a>
</body></html>`,
    { headers: { "Content-Type": "text/html;charset=utf-8" } }
  );
}

// ── INSTALL ──────────────────────────────────────────────────────────────
// `skipWaiting()` en install → el SW nuevo se activa YA, sin esperar a que
// todas las tabs abiertas se cierren. Combinado con `clients.claim()` en
// activate, garantiza que el bump de versión (ej. v54→v59) sirve al usuario
// contenido nuevo en < 1s desde el próximo refresh, sin "datos antiguos".
self.addEventListener("install", event => {
  self.skipWaiting();
  event.waitUntil(
    caches.open(CACHE_STATIC).then(async cache => {
      await Promise.allSettled(
        PRECACHE.map(url => cache.add(new Request(url, { cache: "reload" })))
      );
    })
  );
});

// ── ACTIVATE ─────────────────────────────────────────────────────────────
// Purga TODO cache con el prefijo `ox-` que NO sea la versión actual. Cubre
// buckets de versiones anteriores incluso si no siguen el naming v53/v54.
self.addEventListener("activate", event => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    const keep = new Set([CACHE_STATIC, CACHE_MEDIA, CACHE_HTML]);
    await Promise.all(
      keys
        .filter(k => k.startsWith(CACHE_PREFIX) && !keep.has(k))
        .map(k => caches.delete(k))
    );
    await self.clients.claim();
    // Aviso a las tabs abiertas para que refresquen si el usuario está mirando.
    const clientsList = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    for (const client of clientsList) {
      client.postMessage({ type: "SW_UPDATED", version: CACHE_STATIC });
    }
  })());
});

// ── MESSAGES ─────────────────────────────────────────────────────────────
self.addEventListener("message", event => {
  if (event.data?.type === "SKIP_WAITING") self.skipWaiting();
});

// ── FETCH ────────────────────────────────────────────────────────────────
// Estrategias:
//   - navigate (HTML público catalogo/producto/carrito): stale-while-revalidate
//     con TTL corto → sensación de app nativa (respuesta instantánea desde
//     cache local) + fresh network en background para el próximo hit.
//   - API/admin/auth: siempre network-only (datos dinámicos, sensibles).
//   - Static assets: SWR estándar (cache-first + refresh en background).
//   - Media/uploads: SWR con trim (max 80 entries).
function isFreshHtmlRoute(pathname) {
  // Rutas donde SWR es seguro: catálogo/producto/index — no cambian por sesión.
  // NO incluimos /carrito ni /checkout: son session-specific.
  if (pathname === "/" || pathname === "") return true;
  if (pathname.startsWith("/producto/")) return true;
  if (pathname.startsWith("/menu")) return true;
  return false;
}

/* Network-first con timeout corto: prioriza contenido fresco cuando la red
   responde en <1200ms; si no, sirve la última copia cacheada. Reemplaza al
   SWR anterior que mostraba HTML/logos viejos hasta el 2º refresh. */
const NETWORK_TIMEOUT_MS = 1200;

function withTimeout(promise, ms) {
  return new Promise((resolve, reject) => {
    const t = setTimeout(() => reject(new Error("net-timeout")), ms);
    promise.then(v => { clearTimeout(t); resolve(v); },
                 e => { clearTimeout(t); reject(e); });
  });
}

async function networkFirstHtml(request) {
  const cache = await caches.open(CACHE_HTML);
  try {
    const response = await withTimeout(
      fetch(request, { cache: "no-store" }),
      NETWORK_TIMEOUT_MS,
    );
    if (response && response.ok && response.type === "basic") {
      try { await cache.put(request, response.clone()); } catch (_) {}
      trimCache(cache, 30).catch(() => {});
    }
    return response;
  } catch (_) {
    const cached = await cache.match(request);
    return cached || offlineResponse();
  }
}

/* Uploads: network-first sin timeout artificial. Si hay red, se descarga la
   versión actual y se cachea; si no, sirve la última copia disponible. */
async function networkFirstMedia(request) {
  const cache = await caches.open(CACHE_MEDIA);
  try {
    const response = await fetch(request);
    if (canStore(response)) {
      try { await cache.put(request, response.clone()); } catch (_) {}
      trimCache(cache, 80).catch(() => {});
    }
    return response;
  } catch (_) {
    const cached = await cache.match(request);
    return cached || Response.error();
  }
}

function isUploadAsset(pathname) {
  return pathname.startsWith("/uploads/");
}

self.addEventListener("fetch", event => {
  if (event.request.method !== "GET") return;
  const reqUrl = event.request.url;
  if (!reqUrl.startsWith(self.location.origin)) return;
  const { pathname } = new URL(reqUrl);

  // Navegaciones HTML públicas → network-first con timeout corto para que el
  // usuario vea contenido fresco (logos, imágenes) inmediatamente si hay red.
  if (event.request.mode === "navigate" && isFreshHtmlRoute(pathname)) {
    event.respondWith(networkFirstHtml(event.request));
    return;
  }

  // Cualquier otra navegación (carrito, checkout, auth, admin) → network-only
  // con fallback offline. Datos siempre frescos.
  if (event.request.mode === "navigate" || isNetworkOnly(pathname)) {
    event.respondWith(
      fetch(event.request, { cache: "no-store" }).catch(() => offlineResponse())
    );
    return;
  }

  // Imágenes subidas por el admin (logos, hero, productos): siempre buscar
  // primero red. Evita servir un logo antiguo cacheado si el admin lo cambió.
  if (isUploadAsset(pathname)) {
    event.respondWith(networkFirstMedia(event.request));
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
    icon   = "/static/pwa-icon-192.png?v=59",
    badge  = "/static/pwa-badge-96.png?v=59",
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
