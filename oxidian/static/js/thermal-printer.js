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
  // Servicio BT genérico "Serial Port" (SPP) que usan las térmicas BT
  // clásicas + servicios comunes de fabricantes chinos.
  const BT_SERVICES = [
    '000018f0-0000-1000-8000-00805f9b34fb', // Common thermal printer service
    '49535343-fe7d-4ae5-8fa9-9fafd205e455', // Cypress / Xprinter BT
  ];

  let device = null;
  let transport = null; // 'usb' | 'bt'
  let outEndpoint = null;
  let btChar = null;

  function setPaired(info) {
    try { sessionStorage.setItem(PAIR_KEY, JSON.stringify(info)); } catch (_) {}
  }
  function getPairInfo() {
    try {
      const raw = sessionStorage.getItem(PAIR_KEY);
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
    for (const svcUuid of BT_SERVICES) {
      try {
        const svc = await server.getPrimaryService(svcUuid);
        const chars = await svc.getCharacteristics();
        writeChar = chars.find(c => c.properties.write || c.properties.writeWithoutResponse);
        if (writeChar) break;
      } catch (_) { /* probamos siguiente */ }
    }
    if (!writeChar) {
      await server.disconnect();
      throw new Error('La impresora BT no expone característica de escritura conocida.');
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
    await _writeBytes(buf);
    return { bytes: buf.length, transport };
  }

  // Recuperación de dispositivos ya autorizados al recargar la página.
  async function _restoreUSB() {
    if (!('usb' in navigator)) return;
    try {
      const list = await navigator.usb.getDevices();
      if (!list.length) return;
      const dev = list[0];
      await dev.open();
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
    } catch (_) { /* silencio: el user reempareja si hace falta */ }
  }

  window.ThermalPrinter = {
    pairUSB, pairBT, isPaired, getPairInfo, printTicket,
  };
  document.addEventListener('DOMContentLoaded', _restoreUSB);
})();
