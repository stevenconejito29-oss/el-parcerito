'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');

const dbDir = fs.mkdtempSync(path.join(os.tmpdir(), 'oxidian-bot-orders-'));
process.env.BOT_TEST_MODE = '1';
process.env.DB_DIR = dbDir;
process.env.NODE_ENV = 'test';
process.env.SIMULATE_EVO_SEND = 'true';
process.env.OXIDIAN_KEY = 'test-key';
process.env.BOT_PANEL_KEY = 'test-panel-key';
process.env.OWNER_NUMBER = '34600000991';

const { _test } = require('../bot');
const { db, formatOrderItemSummaryLine, getSesion, handleMessage, saveSesion, setCfg } = _test;
const clientJid = '34632907709@s.whatsapp.net';
const adminJid = '34600000991@s.whatsapp.net';
const originalFetch = global.fetch;
let calls = [];
let orders = [];

function jsonResponse(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  });
}

test.beforeEach(() => {
  db.exec('DELETE FROM handoffs; DELETE FROM sessions; DELETE FROM logs; DELETE FROM admin_availability;');
  calls = [];
  orders = [];
  global.fetch = async (url, options = {}) => {
    const parsed = new URL(String(url));
    const route = parsed.pathname.replace(/^\/api\/bot/, '');
    calls.push({ route, method: options.method || 'GET', body: options.body ? JSON.parse(options.body) : null });
    if (route === '/ai/cliente-context') return jsonResponse({ ok: true, cliente: { nombre: 'Danna', pedidos_recientes: [] } });
    if (route === '/pedidos') return jsonResponse({ ok: true, pedidos: orders });
    if (/^\/pedido\/\d+\/cancelar$/.test(route)) {
      return jsonResponse({ ok: true, pedido: { numero: '#1006', estado: 'cancelado' } });
    }
    if (route === '/confirmacion/responder') return jsonResponse({ ok: true, accion: 'confirmado', mensaje: 'confirmado' });
    if (route === '/admin/tienda') return jsonResponse({ ok: true, estado: options.method === 'POST' ? 'abierta' : 'cerrada' });
    return jsonResponse({ ok: true });
  };
});

test.after(() => {
  global.fetch = originalFetch;
  db.close();
  fs.rmSync(dbDir, { recursive: true, force: true });
});

test('SI dentro de confirmar cancelación cancela y no confirma antifraude', async () => {
  orders = [{ id: 42, numero: '#1006', estado: 'pendiente', total: 103, metodo_pago: 'efectivo', pago_confirmado: false }];
  saveSesion({ jid: clientJid, nombre: 'Danna', role: 'client', estado: 'main_menu', pending: {} });
  await handleMessage(clientJid, 'Cancelar', 'Danna');
  assert.equal(getSesion(clientJid).estado, 'confirmar_cancelacion');
  await handleMessage(clientJid, 'SI', 'Danna');
  assert.equal(getSesion(clientJid).estado, 'main_menu');
  assert.equal(calls.some(c => c.route === '/confirmacion/responder'), false);
  assert.equal(calls.some(c => c.route === '/pedido/42/cancelar' && c.method === 'POST'), true);
});

test('entrada inesperada no rompe ni abandona la confirmación de cancelación', async () => {
  saveSesion({
    jid: clientJid, nombre: 'Danna', role: 'client', estado: 'confirmar_cancelacion',
    pending: { pedido_id: 42, numero: '#1006' },
  });
  await handleMessage(clientJid, 'hola, no sé qué poner', 'Danna');
  assert.equal(getSesion(clientJid).estado, 'confirmar_cancelacion');
  assert.equal(calls.some(c => c.route === '/pedido/42/cancelar'), false);
});

test('varios pendientes exigen elegir uno y nunca usan coincidencia parcial', async () => {
  orders = [
    { id: 51, numero: '#1001', estado: 'pendiente', total: 20, metodo_pago: 'efectivo', pago_confirmado: false },
    { id: 52, numero: '#1011', estado: 'pendiente', total: 30, metodo_pago: 'efectivo', pago_confirmado: false },
  ];
  saveSesion({ jid: clientJid, nombre: 'Danna', role: 'client', estado: 'main_menu', pending: {} });
  await handleMessage(clientJid, 'Cancelar', 'Danna');
  assert.equal(getSesion(clientJid).estado, 'seleccionar_cancelacion');

  await handleMessage(clientJid, '2', 'Danna');
  assert.equal(getSesion(clientJid).estado, 'confirmar_cancelacion');
  assert.equal(getSesion(clientJid).pending.pedido_id, 52);
  assert.equal(calls.some(c => c.route.endsWith('/cancelar')), false);

  await handleMessage(clientJid, 'SI', 'Danna');
  assert.equal(calls.some(c => c.route === '/pedido/52/cancelar'), true);
  assert.equal(calls.some(c => c.route === '/pedido/51/cancelar'), false);
});

test('la opción 7 en selección de cancelación no abre atención humana', async () => {
  orders = Array.from({ length: 8 }, (_, index) => ({
    id: 60 + index,
    numero: `#20${index + 1}`,
    estado: 'pendiente',
    total: 10 + index,
    metodo_pago: 'efectivo',
    pago_confirmado: false,
  }));
  saveSesion({ jid: clientJid, nombre: 'Danna', role: 'client', estado: 'main_menu', pending: {} });
  await handleMessage(clientJid, 'Cancelar', 'Danna');
  await handleMessage(clientJid, '7', 'Danna');
  const ses = getSesion(clientJid);
  assert.equal(ses.estado, 'confirmar_cancelacion');
  assert.equal(ses.pending.pedido_id, 66);
  assert.equal(db.prepare('SELECT COUNT(*) c FROM handoffs').get().c, 0);
});

test('confirmación de cancelación expirada no ejecuta cambios', async () => {
  saveSesion({
    jid: clientJid, nombre: 'Danna', role: 'client', estado: 'confirmar_cancelacion',
    pending: { pedido_id: 42, numero: '#1006', _asked_at: 1 },
  });
  await handleMessage(clientJid, 'SI', 'Danna');
  assert.equal(getSesion(clientJid).estado, 'main_menu');
  assert.equal(calls.some(c => c.route === '/pedido/42/cancelar'), false);
  assert.equal(calls.some(c => c.route === '/confirmacion/responder'), false);
});

test('NO dentro de un reporte no cancela la verificación pendiente', async () => {
  saveSesion({
    jid: clientJid, nombre: 'Danna', role: 'client', estado: 'espera_reporte_pedido',
    pending: { pedido_id: 42, numero: '#1006' },
  });
  await handleMessage(clientJid, 'NO', 'Danna');
  assert.equal(getSesion(clientJid).estado, 'espera_reporte_pedido');
  assert.equal(calls.some(c => c.route === '/confirmacion/responder'), false);
  assert.equal(calls.some(c => c.route.endsWith('/cancelar')), false);
});

test('sin activos muestra el último pedido cerrado y conserva acciones guiadas', async () => {
  orders = [{
    id: 41, numero: '#1005', estado: 'entregado', estado_label: 'Entregado',
    total: 28.5, pago_confirmado: true, creado_en: new Date().toISOString(), items: [],
  }];
  saveSesion({ jid: clientJid, nombre: 'Danna', role: 'client', estado: 'main_menu', pending: {} });
  await handleMessage(clientJid, 'Dónde está mi pedido', 'Danna');
  const ses = getSesion(clientJid);
  assert.equal(ses.estado, 'pedido_acciones');
  assert.equal(ses.pending.numero, '#1005');
  const sent = db.prepare(`SELECT detalle FROM logs WHERE evento='send_attempt' ORDER BY id DESC LIMIT 1`).get();
  assert.match(sent.detalle, /No tienes pedidos activos/i);
});

test('el estado del pedido conserva tamaño y sabor en el resumen al cliente', async () => {
  orders = [{
    id: 43, numero: '#1007', estado: 'armando', estado_label: 'En preparación',
    total: 9.5, pago_confirmado: true, creado_en: new Date().toISOString(),
    items: [{
      nombre: 'Lulada', cantidad: 1, notas: '', sabores: ['Mango'],
      presentacion: { tamaño: 'grande', label: 'Grande', extra: 2.5 },
    }],
  }];
  saveSesion({ jid: clientJid, nombre: 'Danna', role: 'client', estado: 'main_menu', pending: {} });

  await handleMessage(clientJid, 'Estado de mi pedido', 'Danna');

  const line = formatOrderItemSummaryLine(orders[0].items[0]);
  assert.match(line, /Tamaño: Grande/);
  assert.match(line, /Sabor: Mango/);
});

test('tres números de pedido inválidos cierran la espera y vuelven al menú', async () => {
  orders = [{
    id: 91, numero: '#CORRECTO-91', estado: 'entregado', estado_label: 'Entregado',
    total: 12, pago_confirmado: true, items: [],
  }];
  saveSesion({
    jid: clientJid, nombre: 'Danna', role: 'client',
    estado: 'espera_numero_pedido', pending: {},
  });

  await handleMessage(clientJid, '1111', 'Danna');
  assert.equal(getSesion(clientJid).pending._attempts_estado_pedido, 1);
  await handleMessage(clientJid, '2222', 'Danna');
  assert.equal(getSesion(clientJid).pending._attempts_estado_pedido, 2);
  await handleMessage(clientJid, '3333', 'Danna');

  assert.equal(getSesion(clientJid).estado, 'main_menu');
  assert.equal(getSesion(clientJid).pending._attempts_estado_pedido, undefined);
});

test('/abrir inicia confirmación y un admin puede ejecutar la apertura', async () => {
  setCfg('whatsapp_role_profiles', JSON.stringify([{
    telefono: '34600000991', rol: 'admin', capabilities: ['store'],
  }]));
  saveSesion({ jid: adminJid, nombre: 'Admin', role: 'admin', estado: 'admin_menu', pending: {} });
  await handleMessage(adminJid, '/abrir', 'Admin');
  assert.equal(getSesion(adminJid).estado, 'admin_confirm');
  await handleMessage(adminJid, 'SI', 'Admin');
  assert.equal(getSesion(adminJid).estado, 'admin_menu');
  assert.equal(calls.some(c => c.route === '/admin/tienda' && c.method === 'POST'), true);
});

test('cerrar sin slash dentro de formulario de producto no cierra la tienda', async () => {
  setCfg('whatsapp_role_profiles', JSON.stringify([{
    telefono: '34600000991', rol: 'admin', capabilities: ['store', 'products'],
  }]));
  saveSesion({ jid: adminJid, nombre: 'Admin', role: 'admin', estado: 'admin_product_toggle_wait', pending: {} });
  await handleMessage(adminJid, 'cerrar', 'Admin');
  assert.equal(getSesion(adminJid).estado, 'admin_product_toggle_wait');
  assert.equal(calls.some(c => c.route === '/admin/tienda'), false);
});
