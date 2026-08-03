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
     Orden de intento:
       1. WebUSB / WebBluetooth si el navegador tiene una impresora
          emparejada (típico en la tablet de la oficina con cable OTG).
       2. POST /pos/ticket/<id>/imprimir → servidor manda por IPP a la
          cola CUPS del PC (típico en la ubicación con PC).
       3. Fallback: abrir pestaña con auto_print=1 y confiar en el
          diálogo del navegador (peor UX pero garantiza que algo salga).
  */
  async function tryThermalDirect(form) {
    if (!window.ThermalPrinter || !window.ThermalPrinter.isPaired()) return false;
    const action = form.action || '';
    const match = action.match(/\/pos\/ticket\/(\d+)\/imprimir/);
    if (!match) return false;
    const pedidoId = match[1];
    const reprint = /reprint=1/.test(action);
    await window.ThermalPrinter.printTicket(pedidoId, { reprint });
    return true;
  }

  document.addEventListener('submit', async (event) => {
    const form = event.target.closest('form.ticket-print-form');
    if (!form) return;
    event.preventDefault();
    const button = form.querySelector('button[type="submit"]');
    const originalLabel = button ? button.innerHTML : '';
    if (button) { button.disabled = true; button.innerHTML = '🖨️ Enviando…'; }
    try {
      if (await tryThermalDirect(form)) {
        if (button) button.innerHTML = '✅ Impreso (USB)';
        setTimeout(() => { if (button) { button.innerHTML = originalLabel; button.disabled = false; } }, 2500);
        return;
      }
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
      console.warn('[ticket] impresión falló, fallback a navegador:', err);
      const fallback = form.dataset.fallbackUrl;
      if (fallback) {
        window.open(fallback, '_blank', 'noopener');
      } else {
        alert('No se pudo imprimir. Revisa la impresora.');
      }
      if (button) { button.innerHTML = originalLabel; button.disabled = false; }
    }
  });

  /* Botones opcionales para emparejar impresora desde cualquier panel:
     un <button data-pair-thermal="usb|bt"> dispara el flujo de request
     de permiso del navegador. Un <span data-thermal-status> se
     actualiza con el estado actual. */
  function refreshThermalStatus() {
    const info = window.ThermalPrinter?.getPairInfo?.();
    document.querySelectorAll('[data-thermal-status]').forEach(el => {
      if (info) {
        el.textContent = `Impresora: ${info.name} (${info.transport.toUpperCase()})`;
        el.dataset.paired = 'true';
      } else {
        el.textContent = 'Impresora no emparejada';
        el.dataset.paired = 'false';
      }
    });
  }
  document.addEventListener('click', async (event) => {
    const btn = event.target.closest('[data-pair-thermal]');
    if (!btn) return;
    const mode = btn.dataset.pairThermal;
    btn.disabled = true;
    const orig = btn.textContent;
    btn.textContent = 'Emparejando…';
    try {
      const info = mode === 'bt'
        ? await window.ThermalPrinter.pairBT()
        : await window.ThermalPrinter.pairUSB();
      btn.textContent = `✅ ${info.name}`;
      refreshThermalStatus();
    } catch (err) {
      alert(err.message || 'No se pudo emparejar la impresora.');
      btn.textContent = orig;
    } finally {
      btn.disabled = false;
    }
  });
  document.addEventListener('DOMContentLoaded', refreshThermalStatus);
})();
