/* Cliente de impresión térmica directa desde el navegador (WebUSB / WebBT).
 *
 * Uso desde la tablet cuando la impresora está enchufada por cable OTG
 * o emparejada por Bluetooth: el navegador empuja los bytes ESC/POS que
 * genera /pos/ticket/<id>/escpos directo al periférico, sin pasar por
 * ninguna cola CUPS ni servidor de impresión. La app oficial también
 * puede seguir usando el flujo IPP del servidor (POST /imprimir), que
 * queda como fallback natural cuando el navegador no soporta WebUSB.
 *
 * API expuesta:
 *   window.ThermalPrinter.pairUSB()      -> Promise<pair-info>
 *   window.ThermalPrinter.pairBT()       -> Promise<pair-info>
 *   window.ThermalPrinter.isPaired()     -> boolean
 *   window.ThermalPrinter.printTicket(pedidoId, {reprint}) -> Promise
 *   window.ThermalPrinter.getPairInfo()  -> { transport, name } | null
 *
 * Persistencia: WebUSB y WebBluetooth guardan la autorización en el
 * navegador (Chromium mantiene la lista `chrome://device-log` /
 * `chrome://bluetooth-internals`). En Chrome Android el permiso vive
 * mientras no se limpien los datos del sitio. `sessionStorage` guarda
 * un flag "usuario ya emparejó" para no molestar en cada carga.
 */
(function () {
  'use strict';

  const PAIR_KEY = 'oxidian.thermal.paired';
  // VID de fabricantes comunes de térmicas ESC/POS 58 mm.
  const USB_FILTERS = [
    { vendorId: 0x28e9 },  // GDMicroelectronics (nuestra ZJ-58 actual)
    { vendorId: 0x0416 },  // Winbond (POS-58 clones)
    { vendorId: 0x0483 },  // STMicroelectronics
    { vendorId: 0x0fe6 },  // ICS Advent / Xprinter
    { vendorId: 0x04b8 },  // Epson TM
    { vendorId: 0x0dd4 },  // Custom / SNBC
    { vendorId: 0x1504 },  // Bixolon
    { vendorId: 0x0a5f },  // Zebra
    { classCode: 7 },      // Cualquier device clase 7 (Printer)
  ];
  // Servicios BLE conocidos de térmicas 58 mm chinas. Chrome solo puede
  // ver un servicio si aparece en `optionalServices` al pedir el
  // dispositivo, así que esta lista debe ser lo más amplia posible.
  const BT_SERVICES = [
    '000018f0-0000-1000-8000-00805f9b34fb', // Common thermal printer
    '49535343-fe7d-4ae5-8fa9-9fafd205e455', // Cypress CYSPP / Xprinter
    '0000ffb0-0000-1000-8000-00805f9b34fb', // Xprinter genérico
    '0000ff00-0000-1000-8000-00805f9b34fb', // POS-58 clones / RD
    '0000fee7-0000-1000-8000-00805f9b34fb', // Xiaomi / POS chino
    '0000fee0-0000-1000-8000-00805f9b34fb', // Xiaomi mfg
    '6e400001-b5a3-f393-e0a9-e50e24dcca9e', // Nordic UART Service (NUS)
    'e7810a71-73ae-499d-8c15-faa9aef0c3f2', // Casio-like BT printers
    '0000fff0-0000-1000-8000-00805f9b34fb', // MTP-58 y clones
    '0000ffe0-0000-1000-8000-00805f9b34fb', // HC-05/HC-06 modules
    '0000af30-0000-1000-8000-00805f9b34fb', // Pyle/AGPtEK
    '0000fef8-0000-1000-8000-00805f9b34fb', // Star Micronics BLE
  ];

  let device = null;
  let transport = null; // 'usb' | 'bt'
  let outEndpoint = null;
  let btChar = null;

  // localStorage (no sessionStorage): el emparejamiento debe sobrevivir a
  // recargas y cierres de pestaña. La autorización nativa (permission)
  // de Chrome ya persiste en el navegador; nosotros solo guardamos el
  // "hint" para saber qué transporte reintentar y qué mostrar en UI.
  function setPaired(info) {
    try { localStorage.setItem(PAIR_KEY, JSON.stringify(info)); } catch (_) {}
  }
  function clearPaired() {
    try { localStorage.removeItem(PAIR_KEY); } catch (_) {}
  }
  function getPairInfo() {
    try {
      const raw = localStorage.getItem(PAIR_KEY);
      return raw ? JSON.parse(raw) : null;
    } catch (_) { return null; }
  }
  function isPaired() {
    return device !== null;
  }

  async function pairUSB() {
    if (!('usb' in navigator)) {
      throw new Error('WebUSB no soportado en este navegador. Usa Chrome/Chromium.');
    }
    const dev = await navigator.usb.requestDevice({ filters: USB_FILTERS });
    await dev.open();
    if (dev.configuration === null) await dev.selectConfiguration(1);
    // Buscar interfaz con endpoint OUT bulk.
    let iface = null, endpoint = null;
    for (const cfg of dev.configurations) {
      for (const intf of cfg.interfaces) {
        const alt = intf.alternate;
        const ep = alt.endpoints.find(e => e.direction === 'out' && e.type === 'bulk');
        if (ep) { iface = intf; endpoint = ep; break; }
      }
      if (iface) break;
    }
    if (!iface) {
      await dev.close();
      throw new Error('No se encontró endpoint USB de escritura. ¿Es la impresora correcta?');
    }
    try { await dev.claimInterface(iface.interfaceNumber); } catch (err) {
      // Android/Linux a veces necesita release-then-claim.
      try { await dev.releaseInterface(iface.interfaceNumber); } catch (_) {}
      await dev.claimInterface(iface.interfaceNumber);
    }
    device = dev;
    outEndpoint = endpoint.endpointNumber;
    transport = 'usb';
    const info = { transport: 'usb', name: dev.productName || dev.manufacturerName || 'USB Printer' };
    setPaired(info);
    return info;
  }

  async function pairBT() {
    if (!('bluetooth' in navigator)) {
      throw new Error('WebBluetooth no soportado. Usa Chrome/Chromium con BT.');
    }
    const dev = await navigator.bluetooth.requestDevice({
      // Aceptamos cualquier dispositivo — el usuario elige — pero pedimos
      // acceso a los servicios comunes de térmicas para poder escribir.
      acceptAllDevices: true,
      optionalServices: BT_SERVICES,
    });
    const server = await dev.gatt.connect();
    let writeChar = null;
    // Paso 1: intentar los servicios conocidos primero (más rápido).
    for (const svcUuid of BT_SERVICES) {
      try {
        const svc = await server.getPrimaryService(svcUuid);
        const chars = await svc.getCharacteristics();
        writeChar = chars.find(
          c => c.properties.write || c.properties.writeWithoutResponse,
        );
        if (writeChar) {
          console.info('[thermal-BT] servicio conocido:', svcUuid);
          break;
        }
      } catch (_) { /* probamos siguiente */ }
    }
    // Paso 2 (fallback): descubrir TODOS los servicios y buscar cualquier
    // característica escribible. Cubre impresoras con UUIDs propietarios
    // que no están en nuestra lista.
    if (!writeChar) {
      try {
        const services = await server.getPrimaryServices();
        for (const svc of services) {
          const chars = await svc.getCharacteristics();
          writeChar = chars.find(
            c => c.properties.write || c.properties.writeWithoutResponse,
          );
          if (writeChar) {
            console.info('[thermal-BT] servicio detectado por scan:', svc.uuid);
            break;
          }
        }
      } catch (err) {
        console.warn('[thermal-BT] getPrimaryServices falló:', err);
      }
    }
    if (!writeChar) {
      await server.disconnect();
      throw new Error(
        'La impresora BT no expone característica de escritura. Prueba a apagar y reencender la impresora, o dime el modelo exacto para añadir su servicio.',
      );
    }
    device = dev;
    btChar = writeChar;
    transport = 'bt';
    const info = { transport: 'bt', name: dev.name || 'BT Printer' };
    setPaired(info);
    return info;
  }

  async function _writeBytes(bytes) {
    if (!device) throw new Error('Impresora no emparejada.');
    if (transport === 'usb') {
      // La ZJ-58 acepta bloques grandes, pero por seguridad partimos en
      // chunks de 4 KB (algunos chips fallan silencioso >8 KB).
      const CHUNK = 4096;
      for (let i = 0; i < bytes.length; i += CHUNK) {
        await device.transferOut(outEndpoint, bytes.slice(i, i + CHUNK));
      }
    } else if (transport === 'bt') {
      // BT LE tiene MTU típico 20-512 bytes. 100 bytes por escritura es
      // seguro y compatible con todos los chips.
      const CHUNK = 100;
      for (let i = 0; i < bytes.length; i += CHUNK) {
        await btChar.writeValue(bytes.slice(i, i + CHUNK));
      }
    } else {
      throw new Error('Transporte desconocido.');
    }
  }

  async function printTicket(pedidoId, options) {
    options = options || {};
    const reprint = options.reprint ? '1' : '0';
    const url = `/pos/ticket/${pedidoId}/escpos?reprint=${reprint}`;
    const resp = await fetch(url, { credentials: 'same-origin' });
    if (!resp.ok) throw new Error(`El servidor devolvió ${resp.status}`);
    const buf = new Uint8Array(await resp.arrayBuffer());
    // Impresora "fija": intentamos reabrir el transporte que ya fue
    // emparejado antes (persistido en localStorage). Si BT se cayó
    // por navegación de página, reconectamos GATT sin volver a pedir
    // permiso al usuario. Si USB perdió el `claim`, reintentamos.
    if (!device) {
      const hint = getPairInfo();
      try {
        if (hint && hint.transport === 'bt') await _restoreBT();
        else await _restoreUSB();
      } catch (_) {}
    }
    // BT tiende a caer entre requests. Verificamos GATT antes de escribir
    // y reconectamos si hace falta.
    if (transport === 'bt' && device && device.gatt && !device.gatt.connected) {
      try { await _reconnectBT(); } catch (_) {}
    }
    await _writeBytes(buf);
    return { bytes: buf.length, transport };
  }

  // Recuperación de dispositivos USB ya autorizados al recargar la página.
  async function _restoreUSB() {
    if (!('usb' in navigator)) return;
    try {
      const list = await navigator.usb.getDevices();
      if (!list.length) return;
      const dev = list[0];
      if (!dev.opened) await dev.open();
      if (dev.configuration === null) await dev.selectConfiguration(1);
      let iface = null, endpoint = null;
      for (const cfg of dev.configurations) {
        for (const intf of cfg.interfaces) {
          const alt = intf.alternate;
          const ep = alt.endpoints.find(e => e.direction === 'out' && e.type === 'bulk');
          if (ep) { iface = intf; endpoint = ep; break; }
        }
        if (iface) break;
      }
      if (!iface) return;
      try { await dev.claimInterface(iface.interfaceNumber); } catch (_) {}
      device = dev; outEndpoint = endpoint.endpointNumber; transport = 'usb';
      setPaired({ transport: 'usb', name: dev.productName || 'USB Printer' });
    } catch (_) { /* silencio: el user reempareja si hace falta */ }
  }

  // Recuperación de dispositivos BT ya autorizados. Requiere Chrome ≥122
  // con la flag `chrome://flags/#enable-web-bluetooth-new-permissions-backend`
  // habilitada, o el permiso permanente ya concedido. Si el navegador no
  // expone `getDevices`, no podemos reconectar sin gesto del usuario.
  async function _restoreBT() {
    if (!('bluetooth' in navigator) || typeof navigator.bluetooth.getDevices !== 'function') {
      console.info('[thermal-BT] getDevices no disponible — sin restore');
      return;
    }
    let list;
    try {
      list = await navigator.bluetooth.getDevices();
    } catch (err) {
      console.warn('[thermal-BT] getDevices falló:', err);
      return;
    }
    if (!list.length) {
      console.info('[thermal-BT] getDevices devolvió 0 devices autorizados');
      return;
    }
    // Retry con backoff: en Android el subsistema BT puede tardar en
    // despertar. Un gatt.connect() puede fallar en el primer intento
    // (device dormido / GATT server ocupado) y funcionar al segundo.
    const RETRY_DELAYS_MS = [0, 400, 900, 1800];
    for (const dev of list) {
      for (let i = 0; i < RETRY_DELAYS_MS.length; i++) {
        if (RETRY_DELAYS_MS[i] > 0) await new Promise(r => setTimeout(r, RETRY_DELAYS_MS[i]));
        try {
          const server = await dev.gatt.connect();
          const writeChar = await _findBTWriteChar(server);
          if (writeChar) {
            device = dev; btChar = writeChar; transport = 'bt';
            setPaired({ transport: 'bt', name: dev.name || 'BT Printer' });
            console.info('[thermal-BT] restore OK en intento', i + 1, '- device:', dev.name);
            if (!dev.__oxidianDisconnectHooked) {
              dev.addEventListener('gattserverdisconnected', () => {
                console.info('[thermal-BT] desconectado — reintentaré al próximo print.');
              });
              dev.__oxidianDisconnectHooked = true;
            }
            return;
          }
          try { server.disconnect(); } catch (_) {}
          console.warn('[thermal-BT] conectado pero sin writeChar en', dev.name);
          break;  // no reintentar en este device, probar siguiente
        } catch (err) {
          console.info('[thermal-BT] intento', i + 1, 'falló para', dev.name || '?', ':', err && err.message);
          // Sigue reintentando con el siguiente delay.
        }
      }
    }
    console.warn('[thermal-BT] restore falló tras', RETRY_DELAYS_MS.length, 'intentos por device');
  }

  async function _findBTWriteChar(server) {
    for (const svcUuid of BT_SERVICES) {
      try {
        const svc = await server.getPrimaryService(svcUuid);
        const chars = await svc.getCharacteristics();
        const w = chars.find(c => c.properties.write || c.properties.writeWithoutResponse);
        if (w) return w;
      } catch (_) {}
    }
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

  async function _reconnectBT() {
    if (!device || transport !== 'bt') return;
    const server = await device.gatt.connect();
    btChar = await _findBTWriteChar(server);
    if (!btChar) throw new Error('BT reconectado sin característica de escritura.');
  }

  window.ThermalPrinter = {
    pairUSB, pairBT, isPaired, getPairInfo, printTicket,
    // Expuestos para reintentar bajo demanda desde cualquier panel: el
    // botón `[data-thermal-restore]` los invoca sin re-abrir el diálogo
    // de emparejamiento nativo.
    restoreUSB: _restoreUSB,
    restoreBT: _restoreBT,
    forget: function () {
      try {
        if (device && device.gatt && device.gatt.connected) device.gatt.disconnect();
        if (device && device.close) device.close();
      } catch (_) {}
      device = null; btChar = null; outEndpoint = null; transport = null;
      clearPaired();
    },
  };
  // Restauración transparente al cargar la página, PERO solo para BT
  // (que no interfiere con CUPS porque el device es un GATT server BLE,
  // no /dev/usb/lpX). Además la reconexión BT solo la intentamos si el
  // navegador soporta `getDevices()` (Chrome ≥122). En Chrome más antiguo
  // (Android 8 y similares) queda como no-op silencioso: la ruta CUPS
  // server-side de `operational-roles.js` cubre la impresión sin depender
  // del navegador. Para USB seguimos esperando gesto del usuario o el
  // reintento lazy dentro de `printTicket`, para no robar el device a la
  // cola CUPS local.
  // Promise que expone el ciclo de restore inicial. Los consumers
  // (operational-roles.js:openBTPickModal, print_after handler) esperan
  // este `ready` antes de decidir si mostrar el modal manual o imprimir
  // silencioso. Sin awaited, había un race: 100ms no bastaba porque el
  // GATT connect en Android puede tardar 500-1500ms — el chequeo
  // `isPaired()` corría antes y aparecía el modal aunque hubiera
  // pairing persistido, incluso en tablets modernas (ej. Samsung 2026
  // con Chrome 150+).
  let _readyResolve = null;
  window.ThermalPrinter.ready = new Promise((resolve) => { _readyResolve = resolve; });
  document.addEventListener('DOMContentLoaded', async () => {
    const hint = getPairInfo();
    const canRestore = hint && hint.transport === 'bt'
      && 'bluetooth' in navigator
      && typeof navigator.bluetooth.getDevices === 'function';
    if (canRestore) {
      try {
        await _restoreBT();
        console.info('[thermal-BT] restore OK — device:', device ? (device.name || '?') : 'none');
      } catch (err) {
        console.warn('[thermal-BT] restore falló:', err);
      }
    } else {
      console.info('[thermal-BT] sin hint o sin getDevices — pairing manual requerido');
    }
    if (_readyResolve) _readyResolve({ paired: device !== null });
  });

  // Antes de descargar la página SOLO liberamos USB si lo teníamos
  // reclamado — el resource USB compite con CUPS en Ubuntu/Linux (si
  // el mismo host publica la cola `Ticket`, el navegador conserva el
  // `claim` sobre /dev/usb/lpX y CUPS reporta "Waiting for printer").
  //
  // NO desconectamos BT en pagehide: hacerlo forzaba a `_restoreBT()`
  // a reconectar GATT desde cero en cada F5, y en Android eso falla
  // frecuentemente por timing, sleep del device o permisos — síntoma:
  // "cada vez que se refresca la página ambas tablets se desconectan".
  // Al no cerrar manualmente, el navegador tiene oportunidad de
  // preservar el GATT server durante navegación same-origin. El event
  // handler `gattserverdisconnected` (line 269) detecta si el device
  // se cae por otro motivo (out of range, sleep) para logs.
  function _releaseDevice() {
    try {
      if (device && transport === 'usb' && device.opened) {
        device.close();
      }
    } catch (_) { /* silencio: la página está a punto de morir */ }
    // Solo limpiar refs si era USB. Para BT dejamos las refs para que
    // subsecuentes pageloads puedan reutilizar el GATT si sigue abierto.
    if (transport === 'usb') {
      device = null; outEndpoint = null;
    }
  }
  window.addEventListener('pagehide', _releaseDevice);
  window.addEventListener('beforeunload', _releaseDevice);
})();
