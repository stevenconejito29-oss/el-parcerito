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
  if (body.classList.contains('operational-view')) {
    setDeliveryTheme(preferredDeliveryTheme(), false);
    document.addEventListener('click', (event) => {
      const button = event.target.closest('[data-delivery-theme-toggle]');
      if (!button) return;
      setDeliveryTheme(root.dataset.deliveryTheme === 'dark' ? 'light' : 'dark', true);
    });
  }

  /* ────────────────────────────────────────────────────────────────
     IMPRESIÓN DE TICKETS
     Un solo flujo, un solo path:
       - Reimprimir / Listo → si BT emparejado en la sesión, imprime
         silencioso vía ThermalPrinter.printTicket(). Si no, abre el
         modal BT-pick con UN botón: "🔵 Seleccionar impresora e
         imprimir" — pair + print en un click.
       - Sin WebBluetooth (iOS Safari): botón deshabilitado con mensaje
         claro. La operación fluye por otros canales (Pi print-server
         en la LAN, app nativa, etc — no responsabilidad del navegador).
     Fuera del scope: WebUSB (impresora POS58 dual USB+BT usa BT
     desde el navegador), CUPS server-side (opt-in por form attr).
  ─────────────────────────────────────────────────────────────────*/
  const log = (...a) => console.info('[thermal]', ...a);

  function hasBT() {
    return typeof navigator !== 'undefined' && 'bluetooth' in navigator;
  }
  function hasPersistentBT() {
    return hasBT() && typeof navigator.bluetooth.getDevices === 'function';
  }

  async function tryPrintSilent(pedidoId, reprint) {
    const tp = window.ThermalPrinter;
    if (!tp || !tp.isPaired()) return false;
    try {
      await tp.printTicket(pedidoId, { reprint });
      return true;
    } catch (err) {
      log('print silent falló:', err && err.message);
      return false;
    }
  }

  function openPrintModal(pedidoId, reprint) {
    // Modal único con un botón grande "Seleccionar impresora e imprimir".
    // Reutiliza `pairBT()` (misma función del chip flotante) → un click
    // dispara el diálogo BT del sistema + `printTicket()` en secuencia.
    const existing = document.getElementById('thermal-modal');
    if (existing) existing.remove();
    const modal = document.createElement('div');
    modal.id = 'thermal-modal';
    modal.className = 'print-after-overlay';
    modal.setAttribute('role', 'dialog');
    modal.setAttribute('aria-modal', 'true');
    const btAvailable = hasBT();
    modal.innerHTML = `
      <div class="print-after-content" style="text-align:center">
        <h3>🖨️ Imprimir ticket</h3>
        <p>${btAvailable
          ? 'Pulsa el botón, elige tu impresora Bluetooth y confirma. El ticket se enviará al conectar.'
          : 'Este navegador no soporta Bluetooth. Usa Chrome/Chromium en Android o Desktop, o pide impresora en red al equipo técnico.'
        }</p>
        <button type="button" class="print-after-btn print-after-btn-close" data-thermal-do
                ${btAvailable ? '' : 'disabled'}
                style="font-size:1rem;padding:1rem;min-height:64px;width:100%">
          🔵 Seleccionar impresora e imprimir
        </button>
        <div class="print-after-actions" style="margin-top:.7rem">
          <button type="button" class="print-after-btn" data-thermal-close>Cerrar</button>
        </div>
        <p id="thermal-status" style="margin-top:.5rem;font-size:.75rem;min-height:1em;opacity:.7"></p>
      </div>`;
    document.body.appendChild(modal);
    const status = modal.querySelector('#thermal-status');
    const close = () => {
      modal.remove();
      try {
        const u = new URL(window.location.href);
        u.searchParams.delete('print_after');
        window.history.replaceState({}, '', u);
      } catch (_) {}
    };
    modal.querySelector('[data-thermal-close]').addEventListener('click', close);
    modal.addEventListener('click', (e) => { if (e.target === modal) close(); });
    document.addEventListener('keydown', function onKey(e) {
      if (e.key === 'Escape') { close(); document.removeEventListener('keydown', onKey); }
    });
    const doBtn = modal.querySelector('[data-thermal-do]');
    if (btAvailable && doBtn) {
      doBtn.addEventListener('click', async () => {
        doBtn.disabled = true;
        status.textContent = 'Abriendo selector Bluetooth…';
        try {
          const tp = window.ThermalPrinter;
          if (!tp.isPaired()) await tp.pairBT();
          status.textContent = 'Imprimiendo…';
          await tp.printTicket(pedidoId, { reprint });
          status.textContent = '✅ Ticket enviado';
          refreshChip();
          setTimeout(close, 800);
        } catch (err) {
          log('modal print falló:', err && err.message);
          status.textContent = (err && err.message) || 'No se pudo imprimir.';
          doBtn.disabled = false;
        }
      });
    }
  }

  // Interceptor único para formularios de imprimir/reimprimir ticket.
  // Prioridad: BT silencioso si paired → modal manual si no.
  document.addEventListener('submit', async (event) => {
    const form = event.target.closest('form.ticket-print-form');
    if (!form) return;
    event.preventDefault();
    const match = (form.action || '').match(/\/pos\/ticket\/(\d+)\/imprimir/);
    if (!match) return;
    const pedidoId = parseInt(match[1], 10);
    const reprint = /reprint=1/.test(form.action);
    const btn = form.querySelector('button[type="submit"]');
    const orig = btn ? btn.innerHTML : '';
    if (btn) { btn.disabled = true; btn.innerHTML = '🖨️ Enviando…'; }
    try {
      if (await tryPrintSilent(pedidoId, reprint)) {
        if (btn) btn.innerHTML = '✅ Impreso';
        setTimeout(() => { if (btn) { btn.innerHTML = orig; btn.disabled = false; } }, 2000);
        return;
      }
      openPrintModal(pedidoId, reprint);
    } finally {
      if (btn && btn.innerHTML !== '✅ Impreso') { btn.innerHTML = orig; btn.disabled = false; }
    }
  });

  // Auto-disparo tras marcar Listo (?print_after=<id>): espera a que
  // termine el restore inicial (Promise `ThermalPrinter.ready`), y luego
  // decide: paired → print silent; no paired → modal manual.
  document.addEventListener('DOMContentLoaded', async () => {
    const params = new URLSearchParams(window.location.search);
    const raw = params.get('print_after');
    if (!raw) return;
    const pedidoId = parseInt(raw, 10);
    const tp = window.ThermalPrinter;
    if (tp && tp.ready) {
      try { await tp.ready; } catch (_) {}
    }
    if (await tryPrintSilent(pedidoId, false)) {
      // Silent OK: retira el modal server-side (partial) y limpia URL.
      const partial = document.getElementById('print-after-modal');
      if (partial) partial.remove();
      try {
        const u = new URL(window.location.href);
        u.searchParams.delete('print_after');
        window.history.replaceState({}, '', u);
      } catch (_) {}
      return;
    }
    // No silent: quitamos el partial (si el server lo pintó) y mostramos
    // nuestro modal BT-pick unificado.
    const partial = document.getElementById('print-after-modal');
    if (partial) partial.remove();
    openPrintModal(pedidoId, false);
  });

  /* Chip flotante: 2 estados. Verde = conectada; Azul = tocar para
     emparejar. El estado "amarillo reconectar" se eliminó — al pulsar
     azul, si hay hint persistido en localStorage se intenta primero
     `restoreBT()` silencioso; si falla, cae a `pairBT()` con diálogo. */
  function ensureChip() {
    if (!body.classList.contains('operational-view')) return;
    if (!hasBT()) return;
    if (document.querySelector('.thermal-pair-chip')) return;
    const chip = document.createElement('button');
    chip.type = 'button';
    chip.className = 'thermal-pair-chip';
    chip.setAttribute('data-thermal-status', '');
    chip.setAttribute('aria-label', 'Estado de la impresora Bluetooth');
    chip.textContent = '🔵 Emparejar impresora';
    document.body.appendChild(chip);
  }

  function refreshChip() {
    const tp = window.ThermalPrinter;
    document.querySelectorAll('[data-thermal-status]').forEach(el => {
      if (tp && tp.isPaired()) {
        const info = tp.getPairInfo() || {};
        el.textContent = `🟢 ${info.name || 'BT'}`;
        el.dataset.paired = 'true';
        el.title = 'Impresora conectada. Los tickets salen automáticos.';
      } else {
        el.textContent = '🔵 Emparejar impresora';
        el.dataset.paired = 'false';
        el.title = hasPersistentBT()
          ? 'Toca para emparejar. Tras la primera vez, tu tablet reconecta sola tras F5.'
          : 'Toca para emparejar. Este navegador olvida el emparejamiento al recargar.';
      }
    });
  }

  document.addEventListener('click', async (event) => {
    const btn = event.target.closest('.thermal-pair-chip');
    if (!btn) return;
    const tp = window.ThermalPrinter;
    if (!tp) return;
    if (tp.isPaired()) {
      // Ya conectada: dar feedback y salir.
      const orig = btn.textContent;
      btn.textContent = '🟢 Conectada';
      setTimeout(() => { btn.textContent = orig; refreshChip(); }, 1200);
      return;
    }
    btn.disabled = true;
    const orig = btn.textContent;
    try {
      // Primer intento: restore silencioso si tenemos hint.
      if (tp.getPairInfo() && hasPersistentBT()) {
        btn.textContent = 'Reconectando…';
        try { await tp.restoreBT(); } catch (_) {}
        if (tp.isPaired()) {
          refreshChip();
          return;
        }
      }
      // Segundo intento: pairBT() abre el diálogo BT del sistema.
      btn.textContent = 'Emparejando…';
      await tp.pairBT();
      refreshChip();
    } catch (err) {
      alert(err.message || 'No se pudo emparejar.');
      btn.textContent = orig;
    } finally {
      btn.disabled = false;
    }
  });

  // Logging de entorno en cada carga operativa. Volcamos a consola para
  // diagnóstico sin acceso remoto al device.
  function logEnv() {
    if (!body.classList.contains('operational-view')) return;
    const ua = navigator.userAgent || '';
    const android = (ua.match(/Android (\d+(?:\.\d+)?)/) || [])[1] || null;
    const chromium = parseInt((ua.match(/Chrom(?:e|ium)\/(\d+)/) || [])[1] || '0', 10) || null;
    const info = {
      android, chromium,
      webBluetooth: hasBT(),
      getDevices: hasPersistentBT(),
      hint: window.ThermalPrinter?.getPairInfo?.() || null,
    };
    log('env', info);
    if (info.webBluetooth && !info.getDevices) {
      console.warn('[thermal] Este navegador NO soporta reconexión BT automática. '
        + 'Activa chrome://flags/#enable-web-bluetooth-new-permissions-backend '
        + 'o usa Chrome 122+ / Cromite / Brave.');
    }
  }

  document.addEventListener('DOMContentLoaded', async () => {
    ensureChip();
    logEnv();
    // Esperamos al restore inicial y actualizamos el chip UNA vez con
    // el estado real. Antes hacíamos 2 refreshes (antes y después) que
    // solo servía para parpadear del azul al verde.
    if (window.ThermalPrinter?.ready) {
      try { await window.ThermalPrinter.ready; } catch (_) {}
    }
    refreshChip();
  });

  /* ────────────────────────────────────────────────────────────────
     COMPACTACIÓN DE TARJETAS OPERATIVAS
     Cada `.work-card` en un panel operativo se pliega mostrando solo la
     cabecera; items/notas/acciones ocultas tras un toggle. Progressive
     enhancement: si el JS falla la pantalla sigue funcional.
  ─────────────────────────────────────────────────────────────────*/
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
    const rootScope = scope || document;
    rootScope.querySelectorAll('.work-lane').forEach(ensureLaneToggleAll);
    rootScope.querySelectorAll('.work-card').forEach((card) => {
      if (card.dataset.collapsibleInit === '1') return;
      const detailNodes = Array.from(card.children).filter(
        (child) => child.matches && child.matches(DETAIL_SELECTOR),
      );
      if (!detailNodes.length) return;
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
      applyCardState(card, toggle, open);

      card.addEventListener('click', (event) => {
        if (event.target.closest('.work-card-body')) return;
        if (event.target.closest('form, button, a, input, label, select, textarea')) return;
        const nextOpen = card.classList.contains('is-collapsed');
        applyCardState(card, toggle, nextOpen);
        try { sessionStorage.setItem(key, nextOpen ? '1' : '0'); } catch (_) {}
      });
      toggle.addEventListener('click', (event) => {
        event.stopPropagation();
        const nextOpen = card.classList.contains('is-collapsed');
        applyCardState(card, toggle, nextOpen);
        try { sessionStorage.setItem(key, nextOpen ? '1' : '0'); } catch (_) {}
      });
    });
  }

  function applyCardState(card, toggle, open) {
    card.classList.toggle('is-collapsed', !open);
    toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
  }

  document.addEventListener('DOMContentLoaded', () => initCollapsibleCards());
  document.addEventListener('oxidian:cards-updated', (event) => {
    initCollapsibleCards(event.detail && event.detail.scope);
  });

  document.addEventListener('click', (event) => {
    const btn = event.target.closest('[data-cards-toggle-all]');
    if (!btn) return;
    const scope = btn.closest('.work-lane') || document;
    const cards = scope.querySelectorAll('.work-card');
    const anyCollapsed = Array.from(cards).some((c) => c.classList.contains('is-collapsed'));
    cards.forEach((card) => {
      const toggle = card.querySelector('.work-card-toggle');
      if (!toggle) return;
      applyCardState(card, toggle, anyCollapsed);
      try { sessionStorage.setItem(collapseKey(card), anyCollapsed ? '1' : '0'); } catch (_) {}
    });
    btn.textContent = anyCollapsed ? 'Plegar todo' : 'Expandir todo';
  });
})();
