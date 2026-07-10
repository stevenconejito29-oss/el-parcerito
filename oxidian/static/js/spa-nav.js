/* ═══════════════════════════════════════════════════════════════════
   spa-nav.js — Navegación SPA-lite universal
   Intercepta clics a rutas internas, hace fetch del HTML, extrae <main>
   y reemplaza en el DOM actual sin recarga completa.

   Prioridad de transición:
     1. View Transitions API (Chrome/Edge modernos)  →  transición nativa
     2. Fallback CSS fade                             →  cross-browser

   Preserva:
     - scroll de la página previa (restaura al hacer back)
     - <main> anterior descartado limpiamente
     - scripts inline del nuevo <main> re-ejecutados en orden
     - title, meta viewport y evento spa:navigated para hidratar carrito/header

   Fallback total: navegación normal (location.assign) si el fetch falla o
   no encuentra <main>. Usa `data-no-spa` para forzar navegación clásica en
   enlaces puntuales.
   ═══════════════════════════════════════════════════════════════════ */
(function () {
  if (typeof window === 'undefined') return;

  const supportsVT = typeof document.startViewTransition === 'function';
  const SCROLL_HISTORY = new Map(); // href → { top, left }
  const parser = new DOMParser();
  const PREFETCH_SAFE = !/^\/(auth|checkout|carrito|pedido|puntos)\b/.test(location.pathname)
    && !document.querySelector('form[method="post" i]');

  // Rutas del backend que hacen su propio manejo pesado (paneles con auto-refresh,
  // formularios con CSRF, etc.). Mejor no interceptar para evitar sorpresas.
  const HEAVY_RE = /^\/(api|admin|superadmin|preparador|repartidor|proveedor|auth|webhook|pos|uploads|logout|carrito|checkout|pedido|puntos)\b/;

  function sameOrigin(url) {
    try { return new URL(url, location.href).origin === location.origin; }
    catch { return false; }
  }

  function shouldIntercept(a, ev) {
    if (!a || !a.href) return false;
    if (ev.defaultPrevented) return false;
    if (ev.button !== 0 || ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.altKey) return false;
    if (a.target && a.target !== '_self') return false;
    if (a.hasAttribute('download')) return false;
    if (a.dataset.noSpa === '' || a.dataset.noSpa === '1' || a.dataset.noSpa === 'true') return false;
    const rawHref = a.getAttribute('href') || '';
    if (rawHref.startsWith('#')) return false;
    if (rawHref.startsWith('mailto:') || rawHref.startsWith('tel:') || rawHref.startsWith('wa.me')) return false;
    if (!sameOrigin(a.href)) return false;
    const url = new URL(a.href, location.href);
    if (url.pathname === location.pathname && url.search === location.search) return false;
    if (HEAVY_RE.test(url.pathname)) return false;
    return true;
  }

  async function fetchPage(url) {
    const res = await fetch(url, {
      headers: { 'X-SPA-Nav': '1', 'Accept': 'text/html' },
      credentials: 'same-origin',
      cache: 'no-store',
    });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const html = await res.text();
    return parser.parseFromString(html, 'text/html');
  }

  function swap(doc, url, restoreScroll) {
    const newMain = doc.querySelector('main');
    const oldMain = document.querySelector('main');
    if (!newMain || !oldMain) throw new Error('No <main>');
    oldMain.replaceWith(newMain);

    const newTitle = doc.querySelector('title')?.textContent;
    if (newTitle) document.title = newTitle;

    // Re-ejecuta <script> inline dentro del nuevo main. Los externos ya se
    // cargaron una vez en base.html — no hace falta refrescarlos.
    newMain.querySelectorAll('script').forEach(s => {
      if (s.src) return;
      const clone = document.createElement('script');
      if (s.type) clone.type = s.type;
      clone.textContent = s.textContent;
      s.replaceWith(clone);
    });

    // Restaurar scroll: si es back/forward, al punto guardado; si es click,
    // arriba (comportamiento clásico de nueva página).
    if (restoreScroll) {
      window.scrollTo({ top: restoreScroll.top || 0, left: restoreScroll.left || 0, behavior: 'instant' });
    } else {
      window.scrollTo({ top: 0, left: 0, behavior: 'instant' });
    }

    // Actualizar el estado activo del bottom nav (comparando href con location).
    updateActiveNav();
    // Sincronizar el badge del carrito flotante del bottom nav (vive fuera
    // de <main>, así que hay que extraerlo del doc nuevo manualmente).
    syncCartBadge(doc);
    // App badge del PWA (icono en el home screen del usuario).
    syncPwaBadge();

    // Hidratar módulos externos (carrito, header modern, etc.).
    document.dispatchEvent(new CustomEvent('spa:navigated', {
      detail: { url, path: new URL(url, location.href).pathname },
    }));
  }

  function syncCartBadge(doc) {
    const newBadge = doc.querySelector('.ox-bottom-nav .ox-bnav-badge');
    const oldBadgeWrap = document.querySelector('.ox-bottom-nav .ox-bnav-cart .ox-bnav-svg');
    if (!oldBadgeWrap) return;
    const oldBadge = oldBadgeWrap.querySelector('.ox-bnav-badge');
    if (newBadge) {
      if (oldBadge) oldBadge.textContent = newBadge.textContent;
      else oldBadgeWrap.appendChild(newBadge.cloneNode(true));
    } else if (oldBadge) {
      oldBadge.remove();
    }
  }

  function normalizePath(p) {
    // Normaliza trailing slash y elimina "index" para que "/" y "/index" y
    // "/carrito" y "/carrito/" cuenten como la misma ruta al comparar.
    if (!p) return '/';
    return p.replace(/\/index(\.html?)?$/i, '/').replace(/\/+$/, '') || '/';
  }

  function updateActiveNav() {
    // Marca .is-active en el item del bottom nav cuyo href coincida con
    // la ruta actual. Robusto ante recarga o llegada por back button.
    //
    // Bug fix: en el menú público, "Home" y "Búsqueda" comparten pathname
    // (ambos apuntan a `/`; la búsqueda solo añade `?q=`). Si comparamos
    // solo pathname, ambos se marcan activos a la vez. Consideramos el
    // query string clave `q` para discriminar entre menú y modo búsqueda.
    const path = normalizePath(location.pathname);
    const locHasSearchQuery = new URLSearchParams(location.search).has('q');
    document.querySelectorAll('.ox-bottom-nav .ox-bnav-item[href], .ox-admin-bnav a[href], .ox-admin-bnav-item[href]').forEach(a => {
      const linkUrl = new URL(a.href, location.href);
      const linkPath = normalizePath(linkUrl.pathname);
      const linkHasSearchQuery = linkUrl.searchParams.has('q');
      let active = linkPath === path;
      // Discrimina entre variantes que solo se distinguen por ?q=
      // - Link con `?q=` (búsqueda) → activo SOLO si estamos en modo búsqueda
      // - Link sin `?q=` (home)      → activo SOLO si NO estamos en búsqueda
      if (active && linkHasSearchQuery !== locHasSearchQuery) {
        active = false;
      }
      a.classList.toggle('is-active', active);
      // También aplicamos .active para compatibilidad con estilos antiguos
      // del bottom nav admin que usan esa clase en lugar de is-active.
      a.classList.toggle('active', active);
      if (active) a.setAttribute('aria-current', 'page');
      else a.removeAttribute('aria-current');
    });
  }

  /**
   * Sincroniza el "app badge" nativo del PWA (icono en el launcher) con
   * el número de items del carrito. El backend inyecta el conteo actual
   * en el HTML como .ox-bnav-badge; lo extraemos y llamamos setAppBadge.
   */
  function syncPwaBadge() {
    if (!('setAppBadge' in navigator)) return;
    const badge = document.querySelector('.ox-bottom-nav .ox-bnav-badge');
    const n = badge ? parseInt(badge.textContent, 10) || 0 : 0;
    try {
      if (n > 0) navigator.setAppBadge(n);
      else navigator.clearAppBadge();
    } catch (_) { /* ignore */ }
  }

  function withTransition(cb) {
    if (supportsVT) {
      return document.startViewTransition(cb).finished.catch(() => {});
    }
    // Fallback CSS fade (~180ms)
    return new Promise((resolve) => {
      document.body.classList.add('spa-fade-out');
      setTimeout(() => {
        cb();
        document.body.classList.remove('spa-fade-out');
        document.body.classList.add('spa-fade-in');
        setTimeout(() => {
          document.body.classList.remove('spa-fade-in');
          resolve();
        }, 180);
      }, 120);
    });
  }

  async function navigate(url, opts = {}) {
    const isBack = !!opts.isBack;
    // Guardar scroll de la URL actual antes de saltar
    if (!isBack) {
      SCROLL_HISTORY.set(location.href, {
        top: window.scrollY, left: window.scrollX,
      });
    }
    document.body.classList.add('is-navigating');
    try {
      const doc = await fetchPage(url);
      const restore = isBack ? SCROLL_HISTORY.get(url) : null;
      await withTransition(() => swap(doc, url, restore));
      if (!isBack) history.pushState({ spa: true }, '', url);
    } catch (err) {
      console.warn('[spa-nav] fallback', err);
      location.assign(url);
    } finally {
      document.body.classList.remove('is-navigating');
    }
  }

  document.addEventListener('click', (ev) => {
    const a = ev.target.closest('a[href]');
    if (!shouldIntercept(a, ev)) return;
    ev.preventDefault();
    navigate(a.href);
  }, true);

  // Prefetch on hover/focus/touchstart para calentar cache antes del clic.
  const prefetched = new Set();
  function prefetch(url) {
    if (!PREFETCH_SAFE) return;
    if (prefetched.has(url)) return;
    prefetched.add(url);
    fetchPage(url).catch(() => prefetched.delete(url));
  }
  document.addEventListener('mouseover', (ev) => {
    const a = ev.target.closest('a[href]');
    if (a && sameOrigin(a.href) && !HEAVY_RE.test(new URL(a.href).pathname)) {
      prefetch(a.href);
    }
  }, { passive: true });
  document.addEventListener('touchstart', (ev) => {
    const a = ev.target.closest('a[href]');
    if (a && sameOrigin(a.href) && !HEAVY_RE.test(new URL(a.href).pathname)) prefetch(a.href);
  }, { passive: true });

  window.addEventListener('popstate', () => {
    navigate(location.href, { isBack: true });
  });

  // Estado activo inicial (por si la clase se pinta con Jinja pero el usuario
  // navega por SPA — mantenemos coherencia).
  updateActiveNav();
  // Badge PWA en primera carga (por si el usuario ya tenía cosas en el carrito).
  syncPwaBadge();
})();
