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

/* ══════════════════════════════════════════════════════════════
   La navegación pública tiene una sola autoridad: spa-nav.js.

   Este módulo se limita a interacciones visuales de la cabecera.
   ══════════════════════════════════════════════════════════════ */
