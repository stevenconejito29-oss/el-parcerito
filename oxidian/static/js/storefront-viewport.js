/* storefront-viewport.js
 * Mantiene --vh (1% del viewport útil) y --safe-bottom/--safe-top en sintonía
 * con el viewport real, no el teórico. Crítico para iOS Safari (barra
 * dinámica), Instagram in-app browser y teclado abierto en Android.
 * También expone window.OxViewport para que otros scripts puedan leer
 * altura útil sin recalcular.
 */
(function () {
  'use strict';

  var root = document.documentElement;
  var vv = window.visualViewport;

  function readSafeArea(side) {
    // Lee el safe-area-inset desde una variable CSS env() vía un elemento
    // sonda. Devuelve px (number). Si no hay safe area, 0.
    try {
      var probe = document.createElement('div');
      probe.style.cssText =
        'position:fixed;top:0;left:0;height:0;width:0;visibility:hidden;' +
        'padding-' + side + ':env(safe-area-inset-' + side + ',0px);';
      document.body.appendChild(probe);
      var px = parseFloat(
        getComputedStyle(probe).getPropertyValue('padding-' + side)
      ) || 0;
      probe.remove();
      return px;
    } catch (e) {
      return 0;
    }
  }

  function apply() {
    var h = (vv && vv.height) || window.innerHeight;
    root.style.setProperty('--vh', h * 0.01 + 'px');
    root.style.setProperty('--app-height', h + 'px');
    if (document.body) {
      var sb = readSafeArea('bottom');
      var st = readSafeArea('top');
      root.style.setProperty('--safe-bottom', sb + 'px');
      root.style.setProperty('--safe-top', st + 'px');
    }
  }

  // Throttle con rAF
  var pending = false;
  function schedule() {
    if (pending) return;
    pending = true;
    requestAnimationFrame(function () {
      pending = false;
      apply();
    });
  }

  apply();
  window.addEventListener('resize', schedule, { passive: true });
  window.addEventListener('orientationchange', schedule, { passive: true });
  if (vv) {
    vv.addEventListener('resize', schedule);
    vv.addEventListener('scroll', schedule);
  }
  document.addEventListener('DOMContentLoaded', apply);

  window.OxViewport = {
    height: function () {
      return (vv && vv.height) || window.innerHeight;
    },
    safeBottom: function () {
      return parseFloat(getComputedStyle(root).getPropertyValue('--safe-bottom')) || 0;
    },
    safeTop: function () {
      return parseFloat(getComputedStyle(root).getPropertyValue('--safe-top')) || 0;
    },
    refresh: apply
  };
})();
