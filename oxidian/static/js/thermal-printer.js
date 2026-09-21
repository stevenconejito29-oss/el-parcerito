/* Impresión ESC/POS por USB o Bluetooth BLE desde un contexto HTTPS.
 * La compatibilidad se comprueba mediante capacidades del navegador.
 * Bluetooth clásico requiere un puente; Safari/iPhone puede imprimir por
 * el diálogo del sistema/AirPrint o por la impresora de red del negocio.
 * El permiso del periférico es local al navegador: el perfil solo guarda
 * una preferencia. Nunca se restaura otra impresora distinta por fallback.
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
  let usbDevice = null;
  let usbEndpoint = null;
  let printing = false;
  let serverHint = null;
  let networkAvailable = false;
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
    const localHint = getPairInfo();
    try {
      const resp = await fetch('/preparador/impresora', { credentials: 'same-origin', headers: { Accept: 'application/json' } });
      const data = resp.ok ? await resp.json() : null;
      serverHint = data?.printer || null;
      networkAvailable = Boolean(data?.network_available);
      if (serverHint && !localHint) setPaired(serverHint);
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
    return Boolean(usbDevice?.opened || device?.gatt?.connected);
  }

  function capabilities() {
    return {
      secure: window.isSecureContext,
      usb: window.isSecureContext && typeof navigator.usb?.requestDevice === 'function',
      bt: window.isSecureContext && typeof navigator.bluetooth?.requestDevice === 'function',
    };
  }

  const usbId = dev => `${dev.vendorId}:${dev.productId}:${dev.serialNumber || ''}`;

  async function connectUSB(dev) {
    try {
      if (!dev.opened) await dev.open();
      if (!dev.configuration) await dev.selectConfiguration(dev.configurations[0]?.configurationValue || 1);
      let selected;
      for (const iface of dev.configuration.interfaces) {
        for (const alternate of iface.alternates) {
          if (![7, 255].includes(alternate.interfaceClass)) continue;
          const endpoint = alternate.endpoints.find(item => item.direction === 'out' && item.type === 'bulk');
          if (endpoint) { selected = { iface, alternate, endpoint }; break; }
        }
        if (selected) break;
      }
      if (!selected) throw new Error('Esta impresora no expone una conexión USB compatible con ESC/POS. Usa impresión del sistema o por red.');
      await dev.claimInterface(selected.iface.interfaceNumber);
      if (selected.iface.alternate.alternateSetting !== selected.alternate.alternateSetting) {
        await dev.selectAlternateInterface(selected.iface.interfaceNumber, selected.alternate.alternateSetting);
      }
      if (device?.gatt?.connected) device.gatt.disconnect();
      if (usbDevice && usbDevice !== dev && usbDevice.opened) await usbDevice.close();
      device = null; btChar = null;
      usbDevice = dev; usbEndpoint = selected.endpoint.endpointNumber;
      const info = { transport: 'usb', device_id: usbId(dev), name: dev.productName || 'Impresora USB' };
      setPaired(info);
      await saveServerHint(info);
      return info;
    } catch (error) {
      try { if (dev.opened) await dev.close(); } catch (_) {}
      throw error;
    }
  }

  async function pairUSB() {
    if (!capabilities().usb) throw new Error('USB directo requiere HTTPS y un navegador compatible. Puedes usar impresión del sistema o por red.');
    if (printing) throw new Error('Espera a que termine el ticket actual.');
    const dev = await navigator.usb.requestDevice({ filters: [{ classCode: 7 }, { classCode: 255 }] });
    return connectUSB(dev);
  }

  async function restoreUSB() {
    const hint = getPairInfo();
    if (hint?.transport !== 'usb' || !capabilities().usb || !navigator.usb.getDevices) return;
    const matches = (await navigator.usb.getDevices()).filter(dev => usbId(dev) === hint.device_id);
    // Dos impresoras sin número de serie requieren selección explícita.
    if (matches.length === 1) await connectUSB(matches[0]);
  }

  async function pairBT() {
    if (printing) throw new Error('Espera a que termine el ticket actual.');
    if (!capabilities().bt) {
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
    if (usbDevice?.opened) await usbDevice.close();
    usbDevice = null; usbEndpoint = null;
    if (device && device !== dev && device.gatt?.connected) device.gatt.disconnect();
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
    if (usbDevice?.opened && usbEndpoint !== null) {
      for (let offset = 0; offset < bytes.length; offset += 4096) {
        const chunk = bytes.slice(offset, offset + 4096);
        const result = await usbDevice.transferOut(usbEndpoint, chunk);
        if (result.status !== 'ok' || result.bytesWritten !== chunk.length) {
          throw new Error('La impresora recibió un ticket incompleto. Comprueba el papel antes de reimprimir.');
        }
      }
      return;
    }
    if (!device || !btChar) throw new Error('Impresora no emparejada.');
    // Bloques conservadores para dispositivos con MTU BLE mínimo.
    const CHUNK = 20;
    for (let i = 0; i < bytes.length; i += CHUNK) {
      const chunk = bytes.slice(i, i + CHUNK);
      if (btChar.properties.write && btChar.writeValueWithResponse) await btChar.writeValueWithResponse(chunk);
      else if (btChar.properties.writeWithoutResponse && btChar.writeValueWithoutResponse) await btChar.writeValueWithoutResponse(chunk);
      else await btChar.writeValue(chunk);
    }
  }

  async function printTicket(pedidoId, options) {
    if (printing) throw new Error('Ya hay un ticket enviándose. Espera antes de reimprimir.');
    printing = true;
    try {
    options = options || {};
    const reprint = options.reprint ? '1' : '0';
    const url = `/pos/ticket/${pedidoId}/escpos?reprint=${reprint}`;
    const resp = await fetch(url, { credentials: 'same-origin' });
    if (!resp.ok) throw new Error(`El servidor devolvió ${resp.status}`);
    if (resp.redirected || !(resp.headers.get('content-type') || '').includes('application/vnd.escpos')) {
      throw new Error('No se recibió un ticket válido. Revisa tu sesión antes de imprimir.');
    }
    const buf = new Uint8Array(await resp.arrayBuffer());
    // Auto-restore lazy: si no hay device pero hay hint persistido y
    // getDevices está disponible, reconecta antes de escribir.
    if (!isPaired()) {
      try { await restore(); } catch (_) {}
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
    } finally { printing = false; }
  }

  async function restore() {
    if (getPairInfo()?.transport === 'usb') return restoreUSB();
    return _restoreBT();
  }

  async function printNetwork(pedidoId, options = {}) {
    const csrf = document.querySelector('meta[name="ox-csrf-token"]')?.content || '';
    const response = await fetch(`/pos/ticket/${pedidoId}/imprimir?reprint=${options.reprint ? '1' : '0'}`, {
      method: 'POST', credentials: 'same-origin', headers: { 'X-CSRFToken': csrf, Accept: 'application/json' },
    });
    const data = await response.json();
    if (!response.ok || !data.ok) throw new Error('No pudimos enviar el ticket a la impresora del negocio. Revisa su conexión y configuración.');
    return data;
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
    if (!hint?.device_id || hint.transport !== 'bt') return;
    list = list.filter(dev => dev.id === hint.device_id);
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
    if (printing) return;
    if (usbDevice?.opened) usbDevice.close().catch(() => {});
    usbDevice = null; usbEndpoint = null;
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
    pairBT, pairUSB, capabilities, isPaired, getPairInfo, printTicket,
    printNetwork, canPrintNetwork: () => networkAvailable,
    restore, restoreUSB, restoreBT: _restoreBT, forget,
    ready: readyPromise,
  };

  // Restauración inicial: solo intentamos si hay hint BT en localStorage y
  // el navegador soporta getDevices. Sin esto, ready resuelve inmediato y
  // el UI muestra "no paired" (correcto — el operador debe emparejar 1 vez).
  document.addEventListener('DOMContentLoaded', async () => {
    await loadServerHint();
    const hint = getPairInfo();
    if (hint) { try { await restore(); } catch (_) {} }
    _readyResolve({ paired: isPaired() });
  });

  // NO desconectamos BT en pagehide: al dejar el GATT abierto damos
  // oportunidad al navegador de preservar la conexión durante navegación
  // same-origin. Si se cae por otro motivo (out of range, sleep), el
  // gattserverdisconnected listener lo detecta y `printTicket` reconecta.
})();
