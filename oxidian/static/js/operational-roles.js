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
       1. POST /pos/ticket/<id>/imprimir → servidor manda por IPP a la
          cola CUPS. Es el camino por defecto porque no depende del
          navegador (funciona igual en Android 8/Chrome 108 y en Chrome
          151 desktop) y no compite con CUPS por el device físico. Si
          el usuario tiene la impresora colgada al mismo host donde
          corre CUPS, usar WebUSB desde el navegador ROBABA el device
          a la cola. Con CUPS como default eso ya no puede pasar.
       2. WebUSB / WebBluetooth como fallback si CUPS es inalcanzable
          (impresora_no_configurada, impresora_inalcanzable o red caída).
       3. Último recurso: abrir pestaña con auto_print=1.
     Se puede forzar la ruta WebUSB añadiendo `data-force-webusb="1"` al
     `<form>` — útil para setups donde el device solo es visible al
     navegador y no hay CUPS. */
  const FORCE_WEBUSB_ATTR = 'forceWebusb';

  async function tryThermalWebUSB(form) {
    if (!window.ThermalPrinter) return false;
    const hint = window.ThermalPrinter.getPairInfo && window.ThermalPrinter.getPairInfo();
    if (!window.ThermalPrinter.isPaired() && !hint) return false;
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

    const forceWebUSB = form.dataset[FORCE_WEBUSB_ATTR] === '1';

    async function attemptCUPS() {
      const resp = await fetch(form.action, {
        method: 'POST',
        body: new FormData(form),
        headers: { 'Accept': 'application/json' },
        credentials: 'same-origin',
      });
      const data = await resp.json().catch(() => ({}));
      if (resp.ok && data.ok) return { ok: true, via: 'cups' };
      return { ok: false, error: data.error || `HTTP ${resp.status}`, via: 'cups' };
    }

    try {
      // Ruta WebUSB si el operador la fuerza (setups sin CUPS reachable).
      if (forceWebUSB) {
        if (await tryThermalWebUSB(form)) {
          if (button) button.innerHTML = '✅ Impreso (USB)';
          setTimeout(() => { if (button) { button.innerHTML = originalLabel; button.disabled = false; } }, 2500);
          return;
        }
      }
      // Por defecto: CUPS primero. Es el camino robusto en el 99% de
      // los setups (incluye el nuestro: cola `Ticket` en 192.168.1.41).
      const cupsResult = await attemptCUPS();
      if (cupsResult.ok) {
        if (button) button.innerHTML = '✅ Impreso';
        setTimeout(() => { if (button) { button.innerHTML = originalLabel; button.disabled = false; } }, 2500);
        return;
      }
      // Fallback WebUSB si CUPS no responde (impresora_inalcanzable,
      // impresora_no_configurada, red caída).
      if (!forceWebUSB) {
        try {
          if (await tryThermalWebUSB(form)) {
            if (button) button.innerHTML = '✅ Impreso (USB)';
            setTimeout(() => { if (button) { button.innerHTML = originalLabel; button.disabled = false; } }, 2500);
            return;
          }
        } catch (webusbErr) {
          console.warn('[ticket] WebUSB fallback también falló:', webusbErr);
        }
      }
      throw new Error(cupsResult.error);
    } catch (err) {
      console.warn('[ticket] impresión falló, fallback a navegador:', err);
      const fallback = form.dataset.fallbackUrl;
      if (fallback) {
        window.open(fallback, '_blank', 'noopener');
      } else {
        alert('No se pudo imprimir. Revisa la impresora o la cola CUPS.');
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

  /* Compactación de tarjetas operativas.
     Cada `.work-card` en un panel operativo se pliega mostrando solo la
     cabecera (número, cliente, hora, badges); items, notas y acciones se
     ocultan tras un toggle. Motivación: preparador y repartidor ven decenas
     de pedidos y se abruman. Estado (abierto/cerrado) persiste en
     sessionStorage por número de pedido para que un F5 no cambie lo que el
     operador ya había expandido.
     NO altera la estructura HTML por servidor — es progressive enhancement,
     así si el JS falla la pantalla sigue funcional. */
  const DETAIL_SELECTOR = [
    '.work-items',
    '.work-box',
    '.work-note',
    '.work-note-small',
    '.work-action-row',
    '.work-cta-group',
    'details.route-contents',
    'details.route-no-deliver',
  ].join(',');

  function collapseKey(card) {
    const codeEl = card.querySelector('.work-order-code');
    const code = codeEl ? codeEl.textContent.trim() : '';
    if (code) return 'oxidian.card.open:' + code;
    // Fallback: created timestamp + path (peor, pero no crashea).
    return 'oxidian.card.open:' + location.pathname + ':' + (card.dataset.created || '');
  }

  function ensureLaneToggleAll(lane) {
    if (!lane || lane.dataset.laneToggleInit === '1') return;
    const head = lane.querySelector('.work-lane-head');
    if (!head) return;
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'work-lane-toggle-all';
    btn.dataset.cardsToggleAll = '1';
    btn.textContent = 'Expandir todo';
    head.appendChild(btn);
    lane.dataset.laneToggleInit = '1';
  }

  function initCollapsibleCards(scope) {
    if (!body.classList.contains('operational-view')) return;
    const root = scope || document;
    root.querySelectorAll('.work-lane').forEach(ensureLaneToggleAll);
    root.querySelectorAll('.work-card').forEach((card) => {
      if (card.dataset.collapsibleInit === '1') return;
      const detailNodes = Array.from(card.children).filter(
        (child) => child.matches && child.matches(DETAIL_SELECTOR),
      );
      if (!detailNodes.length) return; // Sin detalle: no vale la pena colapsar.
      card.dataset.collapsibleInit = '1';

      const bodyWrap = document.createElement('div');
      bodyWrap.className = 'work-card-body';
      detailNodes.forEach((n) => bodyWrap.appendChild(n));
      card.appendChild(bodyWrap);

      const toggle = document.createElement('button');
      toggle.type = 'button';
      toggle.className = 'work-card-toggle';
      toggle.setAttribute('aria-label', 'Mostrar u ocultar detalle del pedido');
      toggle.innerHTML = '<span aria-hidden="true">▾</span>';
      card.appendChild(toggle);

      const key = collapseKey(card);
      let open = false;
      try { open = sessionStorage.getItem(key) === '1'; } catch (_) {}
      applyState(card, toggle, open);

      // Click en el área de resumen (cualquier lugar de la tarjeta EXCEPTO
      // dentro del body de detalle o de un control interactivo interno).
      card.addEventListener('click', (event) => {
        if (event.target.closest('.work-card-body')) return;
        if (event.target.closest('form, button, a, input, label, select, textarea')) return;
        const nextOpen = card.classList.contains('is-collapsed');
        applyState(card, toggle, nextOpen);
        try { sessionStorage.setItem(key, nextOpen ? '1' : '0'); } catch (_) {}
      });
      toggle.addEventListener('click', (event) => {
        event.stopPropagation();
        const nextOpen = card.classList.contains('is-collapsed');
        applyState(card, toggle, nextOpen);
        try { sessionStorage.setItem(key, nextOpen ? '1' : '0'); } catch (_) {}
      });
    });
  }

  function applyState(card, toggle, open) {
    card.classList.toggle('is-collapsed', !open);
    toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
  }

  document.addEventListener('DOMContentLoaded', () => initCollapsibleCards());
  // Re-inicializar tras auto-refresh que reemplace nodos por AJAX (repartidor
  // y preparador aún recargan la página entera, pero por si en el futuro se
  // introduce swap parcial dejamos este hook idempotente).
  document.addEventListener('oxidian:cards-updated', (event) => {
    initCollapsibleCards(event.detail && event.detail.scope);
  });

  /* Botón "Expandir/Plegar todo" por carril: al pulsarlo se cambia el estado
     de todas las tarjetas del `.work-lane` en el que vive el botón. */
  document.addEventListener('click', (event) => {
    const btn = event.target.closest('[data-cards-toggle-all]');
    if (!btn) return;
    const scope = btn.closest('.work-lane') || document;
    const cards = scope.querySelectorAll('.work-card');
    const anyCollapsed = Array.from(cards).some((c) => c.classList.contains('is-collapsed'));
    cards.forEach((card) => {
      const toggle = card.querySelector('.work-card-toggle');
      if (!toggle) return;
      applyState(card, toggle, anyCollapsed);
      try { sessionStorage.setItem(collapseKey(card), anyCollapsed ? '1' : '0'); } catch (_) {}
    });
    btn.textContent = anyCollapsed ? 'Plegar todo' : 'Expandir todo';
  });
})();
