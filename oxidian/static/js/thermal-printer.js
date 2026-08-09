/* Cliente de impresión térmica BLE (WebBluetooth) desde el navegador.
 *
 * Uso operativo: la POS58 / ZJ-58 / similar tiene un radio BLE con
 * característica escribible. El navegador (Chrome/Chromium en Android o
 * desktop) empuja los bytes ESC/POS generados por /pos/ticket/<id>/escpos
 * directamente al periférico, sin CUPS ni servidor de impresión.
 *
 * Limitaciones intrínsecas de plataforma (no arreglables desde código):
 *   - iOS Safari / Chrome iOS: WebBluetooth = 0. `navigator.bluetooth`
 *     no existe. Fallback = pedir Pi print-server en LAN.
 *   - Chrome Android < 122: sin `navigator.bluetooth.getDevices()`.
 *     No hay auto-reconexión posible tras F5; el operador debe pulsar
 *     el modal manual "Seleccionar impresora e imprimir" cada sesión.
 *   - Chrome Android ≥ 122 con flag `enable-web-bluetooth-new-permissions-
 *     backend`: auto-reconexión silenciosa tras F5 vía `_restoreBT()`.
 *   - WebBluetooth solo soporta BLE, no BT Classic (SPP). Si tu impresora
 *     es SPP-only (ej. PAS58 pura, HC-05 directo), esta ruta no funciona
 *     — hace falta app Android puente o print-server en la LAN.
 *
 * API expuesta en `window.ThermalPrinter`:
 *   pairBT()               → Promise<{transport,name}> — abre diálogo BT
 *   printTicket(id, opts)  → Promise — descarga ESC/POS y escribe al device
 *   isPaired()             → boolean — device conectado en memoria
 *   getPairInfo()          → {transport,name}|null — hint persistido
 *   restoreBT()            → Promise — intento silencioso tras F5
 *   forget()               → limpia estado y localStorage
 *   ready                  → Promise que resuelve al terminar restore inicial
 */
(function () {
  'use strict';

  const PAIR_KEY = 'oxidian.thermal.paired';

  // Servicios BLE conocidos de térmicas ESC/POS chinas y estándar.
  // Chrome solo puede ver un servicio si aparece en `optionalServices` al
  // llamar `requestDevice`, así que esta lista debe ser amplia. El scan
  // fallback (`getPrimaryServices` tras conectar) atrapa los que no estén.
  const BT_SERVICES = [
    '000018f0-0000-1000-8000-00805f9b34fb', // Común térmicas
    '49535343-fe7d-4ae5-8fa9-9fafd205e455', // Cypress CYSPP / Xprinter
    '0000ffb0-0000-1000-8000-00805f9b34fb', // Xprinter genérico
    '0000ff00-0000-1000-8000-00805f9b34fb', // POS-58 clones
    '0000fee7-0000-1000-8000-00805f9b34fb', // POS chinos con Xiaomi module
    '0000fee0-0000-1000-8000-00805f9b34fb', // Xiaomi mfg
    '6e400001-b5a3-f393-e0a9-e50e24dcca9e', // Nordic UART (NUS)
    '0000fff0-0000-1000-8000-00805f9b34fb', // MTP-58 y clones
    '0000ffe0-0000-1000-8000-00805f9b34fb', // HC-05/06 módulos
    '0000af30-0000-1000-8000-00805f9b34fb', // Pyle/AGPtEK
    '0000fef8-0000-1000-8000-00805f9b34fb', // Star Micronics BLE
  ];

  let device = null;
  let btChar = null;
  let serverHint = null;
  let _readyResolve = null;
  const readyPromise = new Promise((resolve) => { _readyResolve = resolve; });

  const log = (...a) => console.info('[thermal]', ...a);
  const warn = (...a) => console.warn('[thermal]', ...a);

  // localStorage: sobrevive a F5 y cierre de pestaña. La autorización real
  // vive en el navegador (chrome://settings → BT); nosotros solo guardamos
  // un hint para saber qué reintentar y qué mostrar en UI.
  function setPaired(info) {
    try { localStorage.setItem(PAIR_KEY, JSON.stringify(info)); } catch (_) {}
  }
  function clearPaired() {
    try { localStorage.removeItem(PAIR_KEY); } catch (_) {}
  }
  function getPairInfo() {
    try {
      const raw = localStorage.getItem(PAIR_KEY);
      return raw ? JSON.parse(raw) : serverHint;
    } catch (_) { return null; }
  }

  async function loadServerHint() {
    if (!document.body.classList.contains('view-preparador')) return null;
    try {
      const resp = await fetch('/preparador/impresora', { credentials: 'same-origin', headers: { Accept: 'application/json' } });
      const data = resp.ok ? await resp.json() : null;
      serverHint = data?.printer || null;
      if (serverHint) setPaired(serverHint);
    } catch (_) { /* localStorage sigue siendo fallback offline */ }
    return serverHint;
  }

  async function saveServerHint(info) {
    serverHint = info;
    if (!document.body.classList.contains('view-preparador')) return;
    const csrf = document.querySelector('meta[name="ox-csrf-token"]')?.content || '';
    try {
      await fetch('/preparador/impresora', {
        method: 'PUT', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf, Accept: 'application/json' },
        body: JSON.stringify(info),
      });
    } catch (_) { /* el emparejamiento local continúa operativo */ }
  }
  function isPaired() {
    return device !== null;
  }

  async function pairBT() {
    if (!('bluetooth' in navigator)) {
      throw new Error('Este navegador no soporta Bluetooth. Usa Chrome/Chromium en Android o Desktop.');
    }
    const dev = await navigator.bluetooth.requestDevice({
      acceptAllDevices: true,
      optionalServices: BT_SERVICES,
    });
    const server = await dev.gatt.connect();
    const writeChar = await _findBTWriteChar(server);
    if (!writeChar) {
      try { server.disconnect(); } catch (_) {}
      throw new Error('La impresora BT no expone característica de escritura. Prueba a apagar/encender la impresora, o dime el modelo para añadir su servicio.');
    }
    device = dev;
    btChar = writeChar;
    _attachDisconnectListener(dev);
    const info = { transport: 'bt', device_id: dev.id, name: dev.name || 'BT Printer' };
    setPaired(info);
    await saveServerHint(info);
    log('paired:', info.name);
    return info;
  }

  async function _findBTWriteChar(server) {
    // Paso 1: servicios conocidos (rápido).
    for (const svcUuid of BT_SERVICES) {
      try {
        const svc = await server.getPrimaryService(svcUuid);
        const chars = await svc.getCharacteristics();
        const w = chars.find(c => c.properties.write || c.properties.writeWithoutResponse);
        if (w) return w;
      } catch (_) { /* probar siguiente */ }
    }
    // Paso 2: scan de todos los servicios (cubre UUIDs propietarios).
    try {
      const services = await server.getPrimaryServices();
      for (const svc of services) {
        const chars = await svc.getCharacteristics();
        const w = chars.find(c => c.properties.write || c.properties.writeWithoutResponse);
        if (w) return w;
      }
    } catch (_) {}
    return null;
  }

  function _attachDisconnectListener(dev) {
    if (dev.__oxidianDisconnectHooked) return;
    dev.addEventListener('gattserverdisconnected', () => {
      log('device desconectado — reintentaré al próximo print');
    });
    dev.__oxidianDisconnectHooked = true;
  }

  async function _writeBytes(bytes) {
    if (!device || !btChar) throw new Error('Impresora no emparejada.');
    // BLE MTU típico 20-512 bytes. 100 bytes es seguro y compatible con todos
    // los chips baratos.
    const CHUNK = 100;
    for (let i = 0; i < bytes.length; i += CHUNK) {
      await btChar.writeValue(bytes.slice(i, i + CHUNK));
    }
  }

  async function printTicket(pedidoId, options) {
    options = options || {};
    const reprint = options.reprint ? '1' : '0';
    const url = `/pos/ticket/${pedidoId}/escpos?reprint=${reprint}`;
    const resp = await fetch(url, { credentials: 'same-origin' });
    if (!resp.ok) throw new Error(`El servidor devolvió ${resp.status}`);
    const buf = new Uint8Array(await resp.arrayBuffer());
    // Auto-restore lazy: si no hay device pero hay hint persistido y
    // getDevices está disponible, reconecta antes de escribir.
    if (!device) {
      try { await _restoreBT(); } catch (_) {}
    }
    // Si el GATT se cayó entre requests, reconecta.
    if (device && device.gatt && !device.gatt.connected) {
      try {
        const server = await device.gatt.connect();
        btChar = await _findBTWriteChar(server);
      } catch (err) { warn('reconnect en printTicket falló:', err); }
    }
    await _writeBytes(buf);
    return { bytes: buf.length };
  }

  async function _restoreBT() {
    if (!('bluetooth' in navigator) || typeof navigator.bluetooth.getDevices !== 'function') {
      return;
    }
    let list;
    try {
      list = await navigator.bluetooth.getDevices();
    } catch (err) {
      warn('getDevices falló:', err);
      return;
    }
    if (!list.length) return;
    // Retry corto: en Android el BT puede tardar en despertar tras F5.
    // Con 2 intentos (0 + 700ms) cubrimos el 95% de casos sin añadir
    // demasiada latencia al primer paint de la página.
    const hint = getPairInfo();
    if (hint?.device_id) {
      list.sort((a, b) => Number(b.id === hint.device_id) - Number(a.id === hint.device_id));
    }
    for (const dev of list) {
      for (const delay of [0, 700]) {
        if (delay) await new Promise(r => setTimeout(r, delay));
        try {
          const server = await dev.gatt.connect();
          const writeChar = await _findBTWriteChar(server);
          if (writeChar) {
            device = dev; btChar = writeChar;
            const info = { transport: 'bt', device_id: dev.id, name: dev.name || 'BT Printer' };
            setPaired(info);
            await saveServerHint(info);
            _attachDisconnectListener(dev);
            log('restore OK:', dev.name);
            return;
          }
          try { server.disconnect(); } catch (_) {}
          break;
        } catch (err) {
          if (delay === 700) warn('restore falló para', dev.name, ':', err && err.message);
        }
      }
    }
  }

  function forget() {
    try {
      if (device && device.gatt && device.gatt.connected) device.gatt.disconnect();
    } catch (_) {}
    device = null; btChar = null;
    clearPaired();
    serverHint = null;
    if (document.body.classList.contains('view-preparador')) {
      const csrf = document.querySelector('meta[name="ox-csrf-token"]')?.content || '';
      fetch('/preparador/impresora', { method: 'DELETE', credentials: 'same-origin', headers: { 'X-CSRFToken': csrf } }).catch(() => {});
    }
  }

  window.ThermalPrinter = {
    pairBT, isPaired, getPairInfo, printTicket, restoreBT: _restoreBT, forget,
    ready: readyPromise,
  };

  // Restauración inicial: solo intentamos si hay hint BT en localStorage y
  // el navegador soporta getDevices. Sin esto, ready resuelve inmediato y
  // el UI muestra "no paired" (correcto — el operador debe emparejar 1 vez).
  document.addEventListener('DOMContentLoaded', async () => {
    await loadServerHint();
    const hint = getPairInfo();
    const canRestore = hint && hint.transport === 'bt'
      && 'bluetooth' in navigator
      && typeof navigator.bluetooth.getDevices === 'function';
    if (canRestore) {
      try { await _restoreBT(); } catch (_) {}
    }
    _readyResolve({ paired: device !== null });
  });

  // NO desconectamos BT en pagehide: al dejar el GATT abierto damos
  // oportunidad al navegador de preservar la conexión durante navegación
  // same-origin. Si se cae por otro motivo (out of range, sleep), el
  // gattserverdisconnected listener lo detecta y `printTicket` reconecta.
})();
