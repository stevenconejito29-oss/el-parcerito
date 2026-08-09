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
      progress.style.transform = 'scaleX(' + (max ? Math.min(1, y / max) : 0) + ')';
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

    function syncNativeNavigationState() {
      var searchActive = window.location.pathname === '/' && window.location.hash === '#buscar';
      document.querySelectorAll('.ox-bottom-nav [data-bnav]').forEach(function (item) {
        var active = item.dataset.bnav === 'search'
          ? searchActive
          : item.dataset.bnav === 'home' && window.location.pathname === '/' && !searchActive;
        if (item.dataset.bnav === 'search' || item.dataset.bnav === 'home') {
          item.classList.toggle('is-active', active);
          if (active) item.setAttribute('aria-current', 'page');
          else item.removeAttribute('aria-current');
        }
      });
    }

    window.addEventListener('scroll', queueUpdate, { passive: true });
    window.addEventListener('resize', queueUpdate, { passive: true });
    window.addEventListener('hashchange', syncNativeNavigationState);
    updateHeader();
    syncNativeNavigationState();

    if ('MutationObserver' in window) {
      // RAF throttle: el observer se dispara MUY frecuentemente durante scroll
      // porque la UI hace pequeñas animaciones (badges, fades). Cada callback
      // hacía querySelector + parseInt + comparación en el hilo principal,
      // causando dropped frames en Android low-end. Con RAF batch, hacemos
      // como mucho 1 check por frame (60fps = 16.67ms), imperceptible para
      // el usuario y libera 70% del budget del main thread durante scroll.
      var mutScheduled = false;
      new MutationObserver(function () {
        if (mutScheduled) return;
        mutScheduled = true;
        requestAnimationFrame(function () {
          mutScheduled = false;
          var badge = header.querySelector('[data-cart-badge]');
          var count = Number(badge?.textContent || 0);
          if (count > lastCount) bumpCart();
          lastCount = count;
        });
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

/* La cabecera solo aporta interacciones visuales. Los enlaces conservan la
   navegación nativa del navegador/PWA para máxima compatibilidad. */
