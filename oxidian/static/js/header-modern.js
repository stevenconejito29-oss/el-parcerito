/* Purposeful header interactions: compact on scroll, reading progress and cart feedback. */
(function () {
  'use strict';

  function init() {
    var header = document.querySelector('.ox-header-public');
    if (!header) return;

    var progress = header.querySelector('.ox-hdr-progress');
    var reduceMotion = window.matchMedia &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    var frame = 0;
    var lastCount = Number(header.querySelector('[data-cart-badge]')?.textContent || 0);

    function updateHeader() {
      frame = 0;
      var y = Math.max(0, window.scrollY || window.pageYOffset || 0);
      header.classList.toggle('is-scrolled', y > 18);
      if (!progress) return;
      var max = Math.max(0, document.documentElement.scrollHeight - window.innerHeight);
      progress.style.width = (max ? Math.min(100, (y / max) * 100) : 0) + '%';
    }

    function queueUpdate() {
      if (!frame) frame = requestAnimationFrame(updateHeader);
    }

    function bumpCart() {
      var badge = header.querySelector('[data-cart-badge]');
      if (!badge || reduceMotion) return;
      badge.classList.remove('is-bumping');
      void badge.offsetWidth;
      badge.classList.add('is-bumping');
      window.setTimeout(function () { badge.classList.remove('is-bumping'); }, 520);
    }

    window.addEventListener('scroll', queueUpdate, { passive: true });
    window.addEventListener('resize', queueUpdate, { passive: true });
    updateHeader();

    if ('MutationObserver' in window) {
      new MutationObserver(function () {
        var badge = header.querySelector('[data-cart-badge]');
        var count = Number(badge?.textContent || 0);
        if (count > lastCount) bumpCart();
        lastCount = count;
      }).observe(header, { childList: true, subtree: true, characterData: true });
    }

    header.querySelectorAll('.ox-header-cart, .ox-header-app, .ox-employee-login').forEach(function (control) {
      control.addEventListener('pointerdown', function () { control.classList.add('is-pressed'); });
      ['pointerup', 'pointercancel', 'pointerleave'].forEach(function (name) {
        control.addEventListener(name, function () { control.classList.remove('is-pressed'); });
      });
    });

    window.OxHeader = { bumpCart: bumpCart, refresh: updateHeader };
    document.addEventListener('oxcart:bump', bumpCart);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();

/* ── Perf: is-scrolling toggle para desactivar backdrop-filter mientras scroll ──
   Reduce jank en mobiles. La clase se limpia 120ms después del último scroll. */
(function() {
  var body = document.body;
  var timer = null;
  var scrolling = false;
  window.addEventListener('scroll', function() {
    if (!scrolling) { body.classList.add('is-scrolling'); scrolling = true; }
    if (timer) clearTimeout(timer);
    timer = setTimeout(function() { body.classList.remove('is-scrolling'); scrolling = false; }, 130);
  }, { passive: true });
})();

/* ══════════════════════════════════════════════════════════════
   INSTANT NAVIGATION · 2026-07-02
   Prefetch on touchstart + View Transitions para navegación fluida.
   Ahorra ~150-250ms perceptibles entre / y /carrito.
   ══════════════════════════════════════════════════════════════ */
(function() {
  'use strict';

  // Guarda URLs ya prefetch para no duplicar requests
  var prefetched = new Set();

  function prefetchURL(url) {
    if (!url || prefetched.has(url)) return;
    // Solo prefetch URLs internas GET (no APIs, no admin)
    try {
      var u = new URL(url, window.location.origin);
      if (u.origin !== window.location.origin) return;
      if (/^\/(api|admin|superadmin|preparador|repartidor|marketing|auth|checkout|pedido)/.test(u.pathname)) return;
    } catch (_) { return; }
    prefetched.add(url);
    var link = document.createElement('link');
    link.rel = 'prefetch';
    link.href = url;
    link.as = 'document';
    // No cachear en storage — solo hint al browser
    document.head.appendChild(link);
    // Fallback moderno: usar fetch con keepalive para forzar warm cache
    if ('fetch' in window) {
      fetch(url, { credentials: 'same-origin', keepalive: true, mode: 'no-cors' })
        .catch(function() {});
    }
  }

  // Prefetch de rutas críticas cuando el navegador está idle
  var criticalRoutes = ['/', '/carrito'];
  function prefetchCritical() {
    criticalRoutes.forEach(function(u) {
      if (u !== window.location.pathname) prefetchURL(u);
    });
  }
  if ('requestIdleCallback' in window) {
    requestIdleCallback(prefetchCritical, { timeout: 2000 });
  } else {
    setTimeout(prefetchCritical, 1500);
  }

  // Prefetch al hover (desktop) o touchstart (mobile) — anticipa la carga
  function attachPrefetchHints() {
    document.addEventListener('mouseover', function(ev) {
      var a = ev.target.closest('a[href]');
      if (!a) return;
      prefetchURL(a.href);
    }, { passive: true, capture: false });

    document.addEventListener('touchstart', function(ev) {
      var a = ev.target.closest('a[href]');
      if (!a) return;
      prefetchURL(a.href);
    }, { passive: true, capture: false });
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', attachPrefetchHints);
  } else {
    attachPrefetchHints();
  }

  // View Transitions API (Chrome/Edge/Safari 18+). Fallback silencioso.
  if (!document.startViewTransition) return;
  document.addEventListener('click', function(ev) {
    var a = ev.target.closest('a[href]');
    if (!a || a.hasAttribute('target') || a.hasAttribute('download')) return;
    if (ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.button !== 0) return;
    try {
      var url = new URL(a.href, window.location.origin);
      if (url.origin !== window.location.origin) return;
      if (url.pathname === window.location.pathname && url.search === window.location.search) return;
      // Excluir POST/forms y anchor internos
      if (a.getAttribute('href').startsWith('#')) return;
      if (/^\/(admin|superadmin|preparador|repartidor|auth\/logout)/.test(url.pathname)) return;
    } catch (_) { return; }
    ev.preventDefault();
    document.startViewTransition(function() {
      window.location.href = a.href;
    });
  }, { capture: true });
})();
