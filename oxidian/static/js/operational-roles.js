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
})();
