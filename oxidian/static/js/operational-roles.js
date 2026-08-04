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

  /* Orden de prioridad al imprimir un ticket desde cualquier botón:
       1. WebBluetooth via ThermalPrinter.printTicket() — impresora emparejada
          al navegador (BT). Silencioso, sin diálogos. Funciona en tablets
          modernas de forma persistente (Chromium ≥122 auto-reconecta) y en
          Chrome 108 una vez que el operador haya pulsado "Emparejar" en la
          sesión actual.
       2. Diálogo nativo de Chrome vía openPrintModal() — abre el modal con
          iframe al ticket con ?auto_print=1, Chrome lanza su print dialog
          y el operador puede elegir CUALQUIER impresora que su sistema
          tenga configurada (incluye BT emparejada a nivel OS/CUPS).
       3. CUPS server-side vía IPP — SOLO si el operador añade
          `data-server-cups="1"` al form. Deshabilitado por defecto porque
          en el setup del usuario la impresora es BT en la tablet, no USB
          conectada al servidor. Se mantiene el código por si otro operario
          usa un setup diferente. */
  async function tryBluetooth(pedidoId, reprint) {
    if (!window.ThermalPrinter) return false;
    const hint = window.ThermalPrinter.getPairInfo && window.ThermalPrinter.getPairInfo();
    if (!window.ThermalPrinter.isPaired() && !hint) return false;
    try {
      await window.ThermalPrinter.printTicket(pedidoId, { reprint });
      return true;
    } catch (err) {
      console.warn('[ticket] BT falló:', err);
      return false;
    }
  }

  function openPrintModal(pedidoId, reprint) {
    // Reutilizamos el mismo modal que sirve `_print_after_modal.html`
    // (creado por el server tras `marcar_listo`). Aquí lo montamos en
    // caliente desde JS para que también funcione con Reimprimir sin
    // navegar. Si ya existe, sólo cambiamos el src del iframe.
    let modal = document.getElementById('print-after-modal');
    const url = `/pos/ticket/${pedidoId}?auto_print=1${reprint ? '&reprint=1' : ''}`;
    if (modal) {
      const frame = modal.querySelector('.print-after-frame');
      if (frame) frame.src = url;
      return;
    }
    modal = document.createElement('div');
    modal.id = 'print-after-modal';
    modal.className = 'print-after-overlay';
    modal.setAttribute('role', 'dialog');
    modal.setAttribute('aria-modal', 'true');
    modal.innerHTML = `
      <div class="print-after-content">
        <h3>🖨️ Imprime el ticket</h3>
        <p>Chrome abrirá su diálogo de impresión. Elige tu impresora (Bluetooth, USB o la que tengas configurada) y confirma.</p>
        <iframe title="Ticket" src="${url}" class="print-after-frame"></iframe>
        <div class="print-after-actions">
          <button type="button" class="print-after-btn print-after-btn-print" data-print-again>🔁 Volver a imprimir</button>
          <button type="button" class="print-after-btn print-after-btn-close" data-print-close>✅ Ya imprimí — cerrar</button>
        </div>
      </div>`;
    document.body.appendChild(modal);
    const close = () => {
      modal.remove();
      try {
        const u = new URL(window.location.href);
        u.searchParams.delete('print_after');
        window.history.replaceState({}, '', u);
      } catch (_) {}
    };
    modal.querySelector('[data-print-close]').addEventListener('click', close);
    modal.querySelector('[data-print-again]').addEventListener('click', () => {
      const f = modal.querySelector('.print-after-frame');
      if (f) f.src = f.src;
    });
    modal.addEventListener('click', (e) => { if (e.target === modal) close(); });
    document.addEventListener('keydown', function onEsc(e) {
      if (e.key === 'Escape') { close(); document.removeEventListener('keydown', onEsc); }
    });
  }

  document.addEventListener('submit', async (event) => {
    const form = event.target.closest('form.ticket-print-form');
    if (!form) return;
    event.preventDefault();
    const button = form.querySelector('button[type="submit"]');
    const originalLabel = button ? button.innerHTML : '';
    if (button) { button.disabled = true; button.innerHTML = '🖨️ Enviando…'; }

    const action = form.action || '';
    const match = action.match(/\/pos\/ticket\/(\d+)\/imprimir/);
    if (!match) {
      if (button) { button.innerHTML = originalLabel; button.disabled = false; }
      return;
    }
    const pedidoId = match[1];
    const reprint = /reprint=1/.test(action);
    const serverCups = form.dataset.serverCups === '1';

    try {
      // Prioridad 1: Bluetooth silencioso si ya está emparejada.
      if (await tryBluetooth(pedidoId, reprint)) {
        if (button) button.innerHTML = '✅ Impreso (BT)';
        setTimeout(() => { if (button) { button.innerHTML = originalLabel; button.disabled = false; } }, 2500);
        return;
      }
      // Prioridad 2: si el operador pide expresamente CUPS del servidor.
      if (serverCups) {
        const resp = await fetch(form.action, {
          method: 'POST', body: new FormData(form),
          headers: { 'Accept': 'application/json' }, credentials: 'same-origin',
        });
        const data = await resp.json().catch(() => ({}));
        if (resp.ok && data.ok) {
          if (button) button.innerHTML = '✅ Impreso';
          setTimeout(() => { if (button) { button.innerHTML = originalLabel; button.disabled = false; } }, 2500);
          return;
        }
      }
      // Prioridad 3: modal con diálogo nativo de Chrome. Siempre funciona
      // porque no depende de emparejamiento previo ni de que CUPS alcance
      // la impresora — el usuario elige del diálogo la impresora que quiera.
      openPrintModal(pedidoId, reprint);
      if (button) { button.innerHTML = originalLabel; button.disabled = false; }
    } catch (err) {
      console.warn('[ticket] impresión falló, abriendo modal:', err);
      openPrintModal(pedidoId, reprint);
      if (button) { button.innerHTML = originalLabel; button.disabled = false; }
    }
  });

  /* Detecta si el navegador soporta reconexión Bluetooth persistente.
     `navigator.bluetooth.getDevices()` llegó en Chromium 122 (enero
     2024). Con esa API `_restoreBT()` de thermal-printer.js reengancha
     el device autorizado en cada carga de página sin gesto humano →
     el pairing efectivamente persiste toda la sesión y más allá. */
  function browserSupportsPersistentBT() {
    return typeof navigator !== 'undefined'
      && 'bluetooth' in navigator
      && typeof navigator.bluetooth.getDevices === 'function';
  }

  /* Auto-disparo tras `?print_after=<id>` (tras marcar Listo/Empacar):
     Regla simple y única:
       - Si la impresora está emparejada en esta sesión → imprime
         silencioso vía WebBluetooth. Sin UI, sin diálogo.
       - Si NO está emparejada → aparece el modal con el diálogo nativo
         de Chrome para que el operador elija la impresora manualmente
         y confirme el trabajo.

     La diferencia práctica entre tablet vieja y moderna:
       - Moderna (Chromium ≥122): `_restoreBT` reconecta al cargar la
         página → `isPaired()` = true → prints silencioso siempre.
       - Vieja (Chrome 108 del Huawei): pairing sólo dura hasta el
         siguiente F5. Tras cada F5 → `isPaired()` = false → aparece
         el diálogo de Chrome cuando marca Listo. Si el operador
         pulsa el chip 🔵 al inicio de la sesión, el pairing dura
         mientras no recargue la app entre pedidos → prints silencioso
         durante ese lapso. */
  document.addEventListener('DOMContentLoaded', async () => {
    const params = new URLSearchParams(window.location.search);
    const pedidoRaw = params.get('print_after');
    if (!pedidoRaw) return;
    const pedidoId = parseInt(pedidoRaw, 10);

    // Path silencioso: impresora emparejada en esta sesión.
    if (window.ThermalPrinter && window.ThermalPrinter.isPaired()) {
      if (await tryBluetooth(pedidoId, false)) {
        const modal = document.getElementById('print-after-modal');
        if (modal) modal.remove();
        try {
          const u = new URL(window.location.href);
          u.searchParams.delete('print_after');
          window.history.replaceState({}, '', u);
        } catch (_) {}
        return;
      }
    }

    // Path modal Chrome dialog: sin pairing en memoria. El partial
    // `_print_after_modal.html` ya está inyectado en el DOM cuando la
    // URL trae ?print_after — su iframe interno dispara window.print()
    // automáticamente y Chrome abre el diálogo nativo para elegir
    // impresora. Nada más que hacer aquí: el modal se auto-gestiona.
  });

  /* Chip flotante para emparejar impresora BT desde cualquier panel
     operativo. Aparece en la esquina inferior derecha, siempre visible.
     En tablets con Chromium ≥122, `_restoreBT()` (thermal-printer.js)
     reconecta al cargar la página. En tablets con Chrome viejo (Huawei
     Android 8/Chrome 108), el operador pulsa este chip una vez por
     sesión para volver a autorizar el device. Es idempotente:
     `ThermalPrinter.pairBT()` reutiliza la autorización previa si el
     navegador la conserva. */
  function ensureThermalPairChip() {
    if (!body.classList.contains('operational-view')) return;
    if (!('bluetooth' in navigator)) return;
    if (document.querySelector('.thermal-pair-chip')) return;
    const chip = document.createElement('button');
    chip.type = 'button';
    chip.className = 'thermal-pair-chip';
    chip.dataset.pairThermal = 'bt';
    chip.setAttribute('data-thermal-status', '');
    chip.setAttribute('aria-label', 'Emparejar o ver estado de la impresora Bluetooth');
    chip.textContent = 'Impresora BT…';
    document.body.appendChild(chip);
  }

  function refreshThermalStatus() {
    const info = window.ThermalPrinter?.getPairInfo?.();
    const persistent = browserSupportsPersistentBT();
    document.querySelectorAll('[data-thermal-status]').forEach(el => {
      if (info) {
        // En navegadores modernos el pairing sobrevive a F5 vía
        // _restoreBT(). En viejos, sólo mientras el navegador tenga
        // el device en memoria de la sesión — al recargar se pierde
        // y hay que reconectar (modal manual por pedido).
        el.textContent = persistent
          ? `🖨️ ${info.name}`
          : `🖨️ ${info.name} · sesión`;
        el.dataset.paired = 'true';
      } else {
        el.textContent = persistent
          ? '🔵 Emparejar impresora BT'
          : '🔵 Emparejar BT (esta sesión)';
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
  document.addEventListener('DOMContentLoaded', () => {
    ensureThermalPairChip();
    refreshThermalStatus();
  });

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
