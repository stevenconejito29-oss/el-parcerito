/* Interacciones compartidas de los paneles operativos. Sin dependencias. */
(function () {
  'use strict';

  const root = document.documentElement;
  const body = document.body;

  // Prioriza la etapa actual; sin JavaScript las dos colas siguen visibles.
  document.querySelectorAll('[data-work-focus]').forEach((switcher) => {
    const area = switcher.closest('.work-area');
    if (!area) return;
    const panels = Array.from(area.querySelectorAll('[data-work-focus-panel]'));
    const buttons = Array.from(switcher.querySelectorAll('[data-work-focus-button]'));
    function select(value) {
      panels.forEach((panel) => { panel.hidden = value !== 'all' && panel.dataset.workFocusPanel !== value; });
      buttons.forEach((button) => {
        button.setAttribute('aria-pressed', String(button.dataset.workFocusButton === value));
      });
      area.classList.toggle('work-focus-single', value !== 'all');
    }
    switcher.addEventListener('click', (event) => {
      const button = event.target.closest('[data-work-focus-button]');
      if (button) select(button.dataset.workFocusButton);
    });
    select(switcher.dataset.defaultFocus);
    area.classList.add('work-focus-enabled');
    switcher.hidden = false;
  });

  root.dataset.deliveryTheme = 'light';

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
     USB directo, BLE y red comparten los tickets y permisos del servidor.
  ─────────────────────────────────────────────────────────────────*/
  const log = (...a) => console.info('[thermal]', ...a);

  function hasBT() {
    return Boolean(window.ThermalPrinter?.capabilities?.().bt);
  }
  function hasPersistentBT() {
    return hasBT() && typeof navigator.bluetooth.getDevices === 'function';
  }

  async function tryPrintSilent(pedidoId, reprint) {
    const tp = window.ThermalPrinter;
    if (!tp) return false;
    if (!tp.isPaired()) { try { await tp.restore(); } catch (_) {} }
    if (!tp.isPaired()) return false;
    try {
      await tp.printTicket(pedidoId, { reprint });
      return true;
    } catch (err) {
      log('print silent falló:', err && err.message);
      return false;
    }
  }

  function openPrintModal(pedidoId, reprint, onClose) {
    document.getElementById('thermal-modal')?.remove();
    const tp = window.ThermalPrinter;
    const caps = tp?.capabilities?.() || {};
    const modal = document.createElement('div');
    modal.id = 'thermal-modal';
    modal.className = 'print-after-overlay';
    modal.setAttribute('role', 'dialog');
    modal.setAttribute('aria-modal', 'true');
    modal.setAttribute('aria-labelledby', 'thermal-title');
    modal.innerHTML = `
      <div class="print-after-content">
        <h3 id="thermal-title">Imprimir ticket</h3>
        <p>Elige cómo enviar este ticket. Si hubo un error, comprueba el papel antes de reimprimir.</p>
        <div class="print-after-actions">
          ${caps.usb ? '<button type="button" class="print-after-btn" data-thermal-print="usb">USB directo</button>' : ''}
          ${caps.serial ? '<button type="button" class="print-after-btn" data-thermal-print="serial">Bluetooth clásico / puerto serie</button>' : ''}
          ${caps.bt ? '<button type="button" class="print-after-btn" data-thermal-print="bt">Bluetooth BLE</button>' : ''}
          ${tp?.canPrintNetwork?.() ? '<button type="button" class="print-after-btn" data-thermal-print="network">Impresora del negocio</button>' : ''}
          <a class="print-after-btn" href="/pos/ticket/${Number(pedidoId)}?autoprint=1&reprint=${reprint ? '1' : '0'}" target="_blank" rel="noopener">Impresión del sistema / AirPrint</a>
        </div>
        <p>En iPhone usa una impresora compatible con AirPrint o la impresora de red configurada por el negocio. Bluetooth directo requiere BLE; Para Bluetooth clásico vincula primero la impresora ESC/POS en el sistema y usa puerto serie si aparece disponible. En otros equipos usa la impresión del sistema o un puente.</p>
        <p id="thermal-status" role="status"></p>
        <button type="button" class="print-after-btn" data-thermal-close>Cerrar</button>
      </div>`;
    const previousFocus = document.activeElement;
    document.body.appendChild(modal);
    const status = modal.querySelector('#thermal-status');
    let busy = false;
    const close = () => {
      if (busy) return;
      document.removeEventListener('keydown', onKey);
      modal.remove();
      previousFocus?.focus?.();
      if (onClose) onClose();
      try {
        const u = new URL(window.location.href);
        u.searchParams.delete('print_after');
        window.history.replaceState({}, '', u);
      } catch (_) {}
    };
    const onKey = event => {
      if (event.key === 'Escape') close();
      if (event.key !== 'Tab') return;
      const controls = [...modal.querySelectorAll('button:not(:disabled), a[href]')];
      const first = controls[0], last = controls[controls.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    document.addEventListener('keydown', onKey);
    modal.querySelector('[data-thermal-close]').addEventListener('click', close);
    modal.addEventListener('click', event => { if (event.target === modal) close(); });
    modal.querySelector('button, a')?.focus();
    modal.querySelectorAll('[data-thermal-print]').forEach(button => {
      button.addEventListener('click', async () => {
        if (busy) return;
        busy = true;
        modal.querySelectorAll('button').forEach(item => { item.disabled = true; });
        status.textContent = 'Conectando con la impresora…';
        try {
          const transport = button.dataset.thermalPrint;
          if (transport === 'network') await tp.printNetwork(pedidoId, { reprint });
          else {
            if (!tp.isPaired() || tp.getPairInfo()?.transport !== transport) {
              if (transport === 'serial') await tp.pairSerial();
              else if (transport === 'usb') await tp.pairUSB();
              else await tp.pairBT();
            }
            status.textContent = 'Enviando ticket…';
            await tp.printTicket(pedidoId, { reprint });
          }
          status.textContent = 'Ticket enviado. Comprueba la impresión.';
          refreshChip();
        } catch (error) {
          status.textContent = error.message || 'No se pudo imprimir. Comprueba el papel antes de reintentar.';
        } finally {
          busy = false;
          modal.querySelectorAll('button').forEach(item => { item.disabled = false; });
        }
      });
    });
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

  // Mantiene la conexión física durante el POST de Listo. Recargar antes de
  // enviar perdía el objeto BLE en navegadores sin restauración getDevices().
  let readySubmitting = false;
  document.addEventListener('submit', async event => {
    const form = event.target.closest('form[action]');
    if (!form || event.defaultPrevented || !window.ThermalPrinter?.isPaired()) return;
    const action = new URL(form.action, window.location.href);
    const match = action.pathname.match(/^\/preparador\/pedidos\/(\d+)\/listo$/);
    if (!match || action.origin !== location.origin) return;
    event.preventDefault();
    if (readySubmitting) return;
    readySubmitting = true;
    const button = event.submitter || form.querySelector('button[type="submit"]');
    const original = button?.textContent;
    if (button) { button.disabled = true; button.textContent = 'Confirmando pedido…'; }
    try {
      const response = await fetch(action.href, {
        method: 'POST', credentials: 'same-origin', body: new FormData(form),
        headers: { Accept: 'application/json' },
      });
      if (!response.ok || response.redirected || !(response.headers.get('content-type') || '').includes('application/json')) {
        if (response.redirected) location.assign(response.url);
        else throw new Error('No se pudo confirmar. Actualiza los pedidos antes de reintentar.');
        return;
      }
      const data = await response.json();
      if (!data.ok || Number(data.print_order_id) !== Number(match[1])) throw new Error('No se confirmó el pedido. Actualiza la lista.');
      const next = new URL(data.next_url || '/preparador/pedidos', location.origin);
      const goBack = () => location.assign(next.origin === location.origin ? next.href : '/preparador/pedidos');
      if (button) button.textContent = 'Listo · enviando ticket…';
      if (await tryPrintSilent(Number(match[1]), false)) goBack();
      else openPrintModal(Number(match[1]), false, goBack);
    } catch (error) {
      let status = form.querySelector('[data-print-status]');
      if (!status) { status = document.createElement('p'); status.dataset.printStatus = ''; status.setAttribute('role', 'alert'); form.append(status); }
      status.textContent = error.message;
    } finally {
      readySubmitting = false;
      if (button) { button.disabled = false; button.textContent = original; }
    }
  });

  // Auto-disparo tras marcar Listo (?print_after=<id>): espera a que
  // termine el restore inicial (Promise `ThermalPrinter.ready`), y luego
  // decide: paired → print silent; no paired → modal manual.
  document.addEventListener('DOMContentLoaded', async () => {
    const params = new URLSearchParams(window.location.search);
    const raw = params.get('print_after');
    if (!raw) return;
    const pedidoId = Number(raw);
    if (!Number.isSafeInteger(pedidoId) || pedidoId <= 0) return;
    // Consumir la intención antes de enviar: recargar tras un corte de conexión
    // nunca debe reenviar automáticamente un ticket posiblemente ya impreso.
    const cleanURL = new URL(window.location.href);
    cleanURL.searchParams.delete('print_after');
    window.history.replaceState({}, '', cleanURL);
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
    if (!body.classList.contains('view-preparador')) return;
    if (!hasBT() && !window.ThermalPrinter?.capabilities?.().usb) return;
    if (document.querySelector('[data-thermal-status]')) return;
    const chip = document.createElement('button');
    chip.type = 'button';
    chip.className = 'thermal-pair-chip';
    chip.setAttribute('data-thermal-status', '');
    chip.setAttribute('aria-label', 'Estado de la impresora Bluetooth');
    chip.textContent = '🔵 Emparejar impresora';
    const toolbar = document.querySelector('.slotops-hero');
    if (toolbar) {
      chip.classList.add('is-inline');
      toolbar.appendChild(chip);
    } else document.body.appendChild(chip);
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
          ? 'Toca para emparejar. Se intentará recuperar esta impresora tras recargar.'
          : 'Toca para emparejar. Puede ser necesario seleccionar la impresora tras recargar.';
      }
    });
  }

  document.addEventListener('click', async (event) => {
    const btn = event.target.closest('.thermal-pair-chip,[data-pair-thermal]');
    if (!btn) return;
    const tp = window.ThermalPrinter;
    if (!tp) return;
    const transport = btn.dataset.pairThermal || (hasBT() ? 'bt' : 'usb');
    if (tp.isPaired() && tp.getPairInfo()?.transport === transport) {
      // Ya conectada: dar feedback y salir.
      const orig = btn.textContent;
      btn.textContent = '🟢 Conectada';
      setTimeout(() => { btn.textContent = orig; refreshChip(); }, 1200);
      return;
    }
    btn.disabled = true;
    const orig = btn.textContent;
    try {
      // El selector requiere el gesto del usuario: no hacer awaits de red antes.
      btn.textContent = 'Emparejando…';
      if (transport === 'serial') await tp.pairSerial();
              else if (transport === 'usb') await tp.pairUSB();
      else await tp.pairBT();
      refreshChip();
    } catch (err) {
      alert(err.message || 'No se pudo emparejar.');
      btn.textContent = orig;
    } finally {
      btn.textContent = orig;
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

  }

  document.addEventListener('DOMContentLoaded', async () => {
    ensureChip();
    const caps = window.ThermalPrinter?.capabilities?.() || {};
    document.querySelectorAll('[data-pair-thermal]').forEach(button => {
      button.disabled = !caps[button.dataset.pairThermal];
      if (button.disabled) button.title = caps.secure ? 'Este navegador no admite esta conexión. Usa impresión del sistema o por red.' : 'Abre la tienda por HTTPS para conectar dispositivos.';
    });
    const help = document.querySelector('[data-thermal-help]');
    if (help) help.textContent = !caps.secure
      ? 'Abre la tienda por HTTPS para usar USB o Bluetooth. En cada ticket también puedes elegir impresión del sistema.'
      : (!caps.usb && !caps.bt ? 'En este dispositivo usa la opción Impresión del sistema / AirPrint del ticket, o la impresora de red del negocio.' : 'USB requiere una impresora ESC/POS compatible y cable OTG en Android. Bluetooth directo funciona con BLE.');
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

  function initCollapsibleCards(scope) {
    if (!body.classList.contains('operational-view')) return;
    const rootScope = scope || document;
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

      const key = collapseKey(card).replace('oxidian.card.open:', 'oxidian.card.v2.open:');
      let stored = null;
      try { stored = sessionStorage.getItem(key); } catch (_) {}
      // Nunca abrir comandas por sorpresa después de un polling o recarga.
      // Solo se restaura una apertura que el propio operador eligió.
      const open = stored === '1';
      applyCardState(card, toggle, open);

      const setExclusiveState = (nextOpen) => {
        if (nextOpen) {
          card.closest('.work-lane')?.querySelectorAll('.work-card:not(.is-collapsed)').forEach((sibling) => {
            if (sibling === card) return;
            const siblingToggle = sibling.querySelector('.work-card-toggle');
            applyCardState(sibling, siblingToggle, false);
            const siblingKey = collapseKey(sibling).replace('oxidian.card.open:', 'oxidian.card.v2.open:');
            try { sessionStorage.setItem(siblingKey, '0'); } catch (_) {}
          });
        }
        applyCardState(card, toggle, nextOpen);
        try { sessionStorage.setItem(key, nextOpen ? '1' : '0'); } catch (_) {}
      };

      card.addEventListener('click', (event) => {
        if (event.target.closest('.work-card-body')) return;
        if (event.target.closest('form, button, a, input, label, select, textarea')) return;
        const nextOpen = card.classList.contains('is-collapsed');
        setExclusiveState(nextOpen);
      });
      toggle.addEventListener('click', (event) => {
        event.stopPropagation();
        const nextOpen = card.classList.contains('is-collapsed');
        setExclusiveState(nextOpen);
      });
    });
    rootScope.querySelectorAll('.work-lane').forEach((lane) => {
      const abiertas = Array.from(lane.querySelectorAll('.work-card:not(.is-collapsed)'));
      abiertas.slice(1).forEach((card) => {
        applyCardState(card, card.querySelector('.work-card-toggle'), false);
        const key = collapseKey(card).replace('oxidian.card.open:', 'oxidian.card.v2.open:');
        try { sessionStorage.setItem(key, '0'); } catch (_) {}
      });
    });
  }

  function applyCardState(card, toggle, open) {
    card.classList.toggle('is-collapsed', !open);
    if (toggle) toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
  }

  document.addEventListener('DOMContentLoaded', () => initCollapsibleCards());
  document.addEventListener('oxidian:cards-updated', (event) => {
    initCollapsibleCards(event.detail && event.detail.scope);
  });

  function initRiderTracking() {
    const panel = document.querySelector('[data-rider-tracking]');
    if (!panel || panel.dataset.trackingBound === '1') return;
    panel.dataset.trackingBound = '1';
    const button = panel.querySelector('[data-rider-tracking-toggle]');
    const copy = panel.querySelector('[data-rider-tracking-copy]');
    const csrf = document.querySelector('meta[name="ox-csrf-token"]')?.content || '';
    const endpoint = panel.dataset.endpoint;
    let watchId = null;
    let lastSentAt = 0;
    let starting = false;

    const setState = (state, message) => {
      panel.dataset.state = state;
      if (copy && message) copy.textContent = message;
      if (!button) return;
      const active = state === 'active' || state === 'waiting';
      button.setAttribute('aria-pressed', active ? 'true' : 'false');
      button.textContent = state === 'waiting' ? 'Buscando GPS…' : active ? 'Detener GPS' : 'Activar GPS';
      button.disabled = state === 'waiting' || panel.dataset.hasRoute !== '1';
    };

    const stop = async (removeServerPoint = true) => {
      if (watchId !== null && navigator.geolocation) navigator.geolocation.clearWatch(watchId);
      watchId = null;
      starting = false;
      setState('idle', panel.dataset.hasRoute === '1'
        ? 'Ubicación detenida. Actívala al comenzar la ruta.'
        : 'Se habilitará cuando salgas con un pedido.');
      if (removeServerPoint) {
        try { await fetch(endpoint, { method: 'DELETE', headers: { 'X-CSRFToken': csrf, Accept: 'application/json' } }); } catch (_) {}
      }
    };

    const send = async (position) => {
      const now = Date.now();
      if (now - lastSentAt < 12000) return;
      lastSentAt = now;
      const payload = {
        lat: position.coords.latitude,
        lng: position.coords.longitude,
        accuracy_m: position.coords.accuracy,
        heading: Number.isFinite(position.coords.heading) ? position.coords.heading : null,
        speed_mps: Number.isFinite(position.coords.speed) ? position.coords.speed : null,
      };
      try {
        const response = await fetch(endpoint, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf, Accept: 'application/json' },
          body: JSON.stringify(payload),
        });
        const result = await response.json().catch(() => ({}));
        if (response.status === 409) return stop(false);
        if (!response.ok) throw new Error(result.error || 'No se pudo compartir la ubicación.');
        try { localStorage.setItem('oxidian.rider.tracking', '1'); } catch (_) {}
        setState('active', `GPS activo · precisión aproximada ${Math.round(position.coords.accuracy)} m · solo con esta pantalla abierta.`);
      } catch (error) {
        setState('error', error.message || 'No se pudo actualizar la ubicación.');
      }
    };

    const start = () => {
      if (!navigator.geolocation) return setState('error', 'Este dispositivo no admite geolocalización web.');
      if (watchId !== null || starting || panel.dataset.hasRoute !== '1') return;
      starting = true;
      setState('waiting', 'Solicitando permiso de ubicación…');
      try {
        watchId = navigator.geolocation.watchPosition(send, (error) => {
          const message = error.code === 1
            ? 'Permiso de ubicación bloqueado. Actívalo en los ajustes de la app.'
            : 'No hay señal GPS fiable. Comprueba ubicación y conexión.';
          if (watchId !== null) navigator.geolocation.clearWatch(watchId);
          watchId = null;
          starting = false;
          setState('error', message);
        }, { enableHighAccuracy: true, maximumAge: 10000, timeout: 15000 });
        starting = false;
      } catch (_) {
        watchId = null;
        starting = false;
        setState('error', 'No fue posible iniciar el GPS. Revisa los permisos del navegador.');
      }
    };

    button?.addEventListener('click', () => {
      if (watchId === null) start();
      else {
        try { localStorage.removeItem('oxidian.rider.tracking'); } catch (_) {}
        stop(true);
      }
    });
    document.addEventListener('visibilitychange', () => {
      if (document.hidden && watchId !== null) {
        // El navegador web no garantiza GPS en segundo plano; lo declaramos y
        // conservamos el último punto solo durante su ventana de frescura.
        setState('active', 'App en segundo plano: se mostrará la última posición reciente.');
      } else if (!document.hidden && panel.dataset.hasRoute === '1' && watchId === null) {
        start();
      }
    });

    // Una entrega activa debe compartir ubicación sin depender de descubrir
    // un botón secundario. El navegador conserva siempre la última palabra:
    // si aún no hay permiso mostrará su diálogo; si fue denegado, la tarjeta
    // queda en error con instrucciones y nunca se inventa una posición.
    if (panel.dataset.autoStart === '1') {
      window.setTimeout(start, 350);
    }
  }

  document.addEventListener('DOMContentLoaded', initRiderTracking);
})();
