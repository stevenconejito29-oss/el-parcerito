/* Interacciones compartidas de los paneles operativos. Sin dependencias. */
(function () {
  'use strict';

  const DELIVERY_THEME_KEY = 'oxidian.delivery.theme';
  const root = document.documentElement;
  const body = document.body;

  function preferredDeliveryTheme() {
    try {
      const saved = localStorage.getItem(DELIVERY_THEME_KEY);
      if (saved === 'dark' || saved === 'light') return saved;
    } catch (_) {}
    return matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  }

  function setDeliveryTheme(theme, persist) {
    const next = theme === 'dark' ? 'dark' : 'light';
    root.dataset.deliveryTheme = next;
    if (persist) {
      try { localStorage.setItem(DELIVERY_THEME_KEY, next); } catch (_) {}
    }
    document.querySelectorAll('[data-delivery-theme-toggle]').forEach((button) => {
      const dark = next === 'dark';
      button.setAttribute('aria-pressed', dark ? 'true' : 'false');
      button.setAttribute('aria-label', dark ? 'Cambiar a modo día' : 'Cambiar a modo noche');
      const icon = button.querySelector('[data-theme-icon]');
      const label = button.querySelector('[data-theme-label]');
      if (icon) icon.textContent = dark ? '☀️' : '🌙';
      if (label) label.textContent = dark ? 'Modo día' : 'Modo noche';
    });
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta && body.classList.contains('operational-view')) {
      meta.content = next === 'dark' ? '#0b1016' : getComputedStyle(body).getPropertyValue('--brand-primary').trim();
    }
  }

  // Aplica a cualquier rol operativo (repartidor, preparación, cocina, staff).
  // Antes: solo repartidor. Los otros tenían pantalla clara incluso de noche.
  if (body.classList.contains('operational-view')) {
    setDeliveryTheme(preferredDeliveryTheme(), false);
    document.addEventListener('click', (event) => {
      const button = event.target.closest('[data-delivery-theme-toggle]');
      if (!button) return;
      setDeliveryTheme(root.dataset.deliveryTheme === 'dark' ? 'light' : 'dark', true);
    });
  }

  /* Impresión de tickets desde cualquier panel operativo.
     El <form class="ticket-print-form"> hace POST /pos/ticket/<id>/imprimir.
     Interceptamos el submit para hacer fetch (evita perder el contexto de la
     página y muestra feedback inline). Si el servidor no puede alcanzar la
     impresora, caemos al flujo antiguo abriendo la pestaña con auto_print. */
  document.addEventListener('submit', async (event) => {
    const form = event.target.closest('form.ticket-print-form');
    if (!form) return;
    event.preventDefault();
    const button = form.querySelector('button[type="submit"]');
    const originalLabel = button ? button.innerHTML : '';
    if (button) { button.disabled = true; button.innerHTML = '🖨️ Enviando…'; }
    try {
      const resp = await fetch(form.action, {
        method: 'POST',
        body: new FormData(form),
        headers: { 'Accept': 'application/json' },
        credentials: 'same-origin',
      });
      const data = await resp.json().catch(() => ({}));
      if (resp.ok && data.ok) {
        if (button) button.innerHTML = '✅ Impreso';
        setTimeout(() => { if (button) { button.innerHTML = originalLabel; button.disabled = false; } }, 2500);
        return;
      }
      throw new Error(data.error || `HTTP ${resp.status}`);
    } catch (err) {
      console.warn('[ticket] impresión servidor falló, fallback a navegador:', err);
      const fallback = form.dataset.fallbackUrl;
      if (fallback) {
        window.open(fallback, '_blank', 'noopener');
      } else {
        alert('No se pudo imprimir. Revisa la impresora.');
      }
      if (button) { button.innerHTML = originalLabel; button.disabled = false; }
    }
  });
})();
