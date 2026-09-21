/*
  motion-boot.js — Integra HTMX + NProgress con configuración común.
  Se carga con defer tras htmx y nprogress. No requiere Alpine (Alpine se
  auto-inicializa). Progressive: si htmx o NProgress no cargan, el sitio
  sigue funcionando sin animaciones.
*/
(function () {
  "use strict";

  // ── NProgress: barra superior en cada request HTMX ──────────────────
  if (window.NProgress) {
    NProgress.configure({ showSpinner: false, trickleSpeed: 180 });
    document.addEventListener("htmx:beforeRequest", function () {
      NProgress.start();
    });
    document.addEventListener("htmx:afterRequest", function () {
      NProgress.done();
    });
    document.addEventListener("htmx:responseError", function () {
      NProgress.done();
    });
    // Navegación tradicional: mostrar barra al hacer submit o cambiar de página
    window.addEventListener("beforeunload", function () {
      if (!document.hidden) NProgress.start();
    });
    document.addEventListener("visibilitychange", function () {
      if (document.hidden) NProgress.done();
    });
  }

  // ── HTMX: envía el CSRF token de Flask-WTF en cada request ─────────
  if (window.htmx) {
    document.addEventListener("htmx:configRequest", function (evt) {
      var meta = document.querySelector('meta[name="ox-csrf-token"]');
      var token = meta ? meta.getAttribute("content") : null;
      if (token && ["POST", "PUT", "PATCH", "DELETE"].indexOf(evt.detail.verb.toUpperCase()) !== -1) {
        evt.detail.headers["X-CSRFToken"] = token;
      }
    });
    // HTMX conserva su ciclo de actualización. El polling de pedidos no debe
    // crear transiciones globales superpuestas ni sustituir manualmente el DOM.
  }
})();
