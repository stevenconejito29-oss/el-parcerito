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
const {
  _tryBudgetCatalogReply,
  _tryCatalogSearchReply,
  db,
  formatOrderItemSummaryLine,
  getSesion,
  handleMessage,
  isOrderStatusIntent,
  saveSesion,
  setCfg,
} = _test;
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
    calls.push({
      route,
      query: Object.fromEntries(parsed.searchParams.entries()),
      method: options.method || 'GET',
      body: options.body ? JSON.parse(options.body) : null,
    });
    if (route === '/ai/cliente-context') return jsonResponse({ ok: true, cliente: { nombre: 'Danna', pedidos_recientes: [] } });
    if (route === '/identity/verify') return jsonResponse({
      ok: true, rol: 'admin', nombre: 'Admin', capabilities: ['store'],
    });
    if (route === '/ayuda') return jsonResponse({ok:true, answer:'Consulta tu pedido en la app.', url:'https://shop.invalid/ayuda'});
    if (route === '/pedidos') return jsonResponse({ ok: true, pedidos: orders });
    if (route === '/cobertura') {
      return jsonResponse({
        ok: true,
        cobertura: {
          ok: true,
          zona_nombre: 'Centro QA',
          distancia_km: 0.4,
          metodo_cobertura: 'ubicacion_dispositivo',
        },
        metodo_cobertura: 'ubicacion_dispositivo',
      });
    }
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

test('una confirmación legacy no cancela ni confirma otro pedido', async () => {
  saveSesion({jid:clientJid,nombre:'Cliente',role:'client',estado:'confirmar_cancelacion',pending:{pedido_id:42}});
  await handleMessage(clientJid, 'SI', 'Cliente');
  assert.equal(getSesion(clientJid).estado, 'main_menu');
  assert.deepEqual(getSesion(clientJid).pending, {});
  assert.equal(calls.some(c => c.route === '/ayuda'), false);
  assert.equal(calls.some(c => c.route.endsWith('/cancelar') || c.route === '/confirmacion/responder' || c.route === '/pedidos' || c.route === '/cobertura'), false);
  assert.equal(db.prepare('SELECT COUNT(*) c FROM handoffs').get().c, 0);
});

test('la ubicación recibida orienta a cobertura de la app sin guardar coordenadas', async () => {
  saveSesion({jid:clientJid,nombre:'Cliente',role:'client',estado:'main_menu',pending:{pedido_id:42}});
  await handleMessage(clientJid, '[Adjunto recibido: ubicacion]', 'Cliente');
  assert.equal(getSesion(clientJid).estado, 'main_menu');
  assert.deepEqual(getSesion(clientJid).pending, {});
  assert.equal(calls.some(c => c.route === '/ayuda'), false);
  assert.equal(calls.some(c => c.route.endsWith('/cancelar') || c.route === '/confirmacion/responder' || c.route === '/pedidos' || c.route === '/cobertura'), false);
  assert.equal(db.prepare('SELECT COUNT(*) c FROM handoffs').get().c, 0);
});

test('una sesión antigua de cancelación vuelve a ayuda sin ejecutar cambios', async () => {
  saveSesion({jid:clientJid,nombre:'Cliente',role:'client',estado:'confirmar_cancelacion',pending:{pedido_id:42}});
  await handleMessage(clientJid, 'hola, no sé qué poner', 'Cliente');
  assert.equal(getSesion(clientJid).estado, 'main_menu');
  assert.deepEqual(getSesion(clientJid).pending, {});
  assert.equal(calls.some(c => c.route === '/ayuda'), false);
  assert.equal(calls.some(c => c.route.endsWith('/cancelar') || c.route === '/confirmacion/responder' || c.route === '/pedidos' || c.route === '/cobertura'), false);
  assert.equal(db.prepare('SELECT COUNT(*) c FROM handoffs').get().c, 0);
});

test('ATRÁS desarma la cancelación antigua y conserva los pedidos', async () => {
  saveSesion({jid:clientJid,nombre:'Cliente',role:'client',estado:'confirmar_cancelacion',pending:{pedido_id:42}});
  await handleMessage(clientJid, 'atrás', 'Cliente');
  assert.equal(getSesion(clientJid).estado, 'main_menu');
  assert.deepEqual(getSesion(clientJid).pending, {});
  assert.equal(calls.some(c => c.route === '/ayuda'), false);
  assert.equal(calls.some(c => c.route.endsWith('/cancelar') || c.route === '/confirmacion/responder' || c.route === '/pedidos' || c.route === '/cobertura'), false);
  assert.equal(db.prepare('SELECT COUNT(*) c FROM handoffs').get().c, 0);
});

test('MENU sigue siendo escape al inicio y no se confunde con ATRÁS', async () => {
  const menuJid = '34632907711@s.whatsapp.net';
  saveSesion({
    jid: menuJid, nombre: 'Danna', role: 'client', estado: 'info_menu', pending: {},
  });
  await handleMessage(menuJid, 'MENU', 'Danna');
  assert.equal(getSesion(menuJid).estado, 'main_menu');
});

test('la ayuda sobre cancelación no consulta ni elige pedidos por el cliente', async () => {
  saveSesion({jid:clientJid,nombre:'Cliente',role:'client',estado:'main_menu',pending:{pedido_id:42}});
  await handleMessage(clientJid, 'Cancelar', 'Cliente');
  assert.equal(getSesion(clientJid).estado, 'main_menu');
  assert.deepEqual(getSesion(clientJid).pending, {});
  assert.equal(calls.some(c => c.route === '/ayuda'), false);
  assert.equal(calls.some(c => c.route.endsWith('/cancelar') || c.route === '/confirmacion/responder' || c.route === '/pedidos' || c.route === '/cobertura'), false);
  assert.equal(db.prepare('SELECT COUNT(*) c FROM handoffs').get().c, 0);
});

test('una selección antigua no abre un handoff ni cancela una compra', async () => {
  saveSesion({jid:clientJid,nombre:'Cliente',role:'client',estado:'seleccionar_cancelacion',pending:{pedido_id:42}});
  await handleMessage(clientJid, '7', 'Cliente');
  assert.equal(getSesion(clientJid).estado, 'main_menu');
  assert.deepEqual(getSesion(clientJid).pending, {});
  assert.equal(calls.some(c => c.route === '/ayuda'), false);
  assert.equal(calls.some(c => c.route.endsWith('/cancelar') || c.route === '/confirmacion/responder' || c.route === '/pedidos' || c.route === '/cobertura'), false);
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
  assert.equal(getSesion(clientJid).estado, 'main_menu');
  assert.equal(calls.some(c => c.route === '/confirmacion/responder'), false);
  assert.equal(calls.some(c => c.route.endsWith('/cancelar')), false);
});

test('confirmar la primera compra limpia acciones antiguas y ofrece el siguiente paso', async () => {
  saveSesion({
    jid: clientJid, nombre: 'Danna', role: 'client', estado: 'pedido_acciones',
    pending: { pedido_id: 42, numero: '#1006', cancelable: true },
  });

  await handleMessage(clientJid, 'SI', 'Danna');

  assert.equal(getSesion(clientJid).estado, 'main_menu');
  assert.deepEqual(getSesion(clientJid).pending, {});
  const sent = db.prepare(`SELECT detalle FROM logs WHERE evento='send_attempt' ORDER BY id DESC LIMIT 1`).get();
  assert.match(sent.detalle, /confirmado/i);
  assert.match(sent.detalle, /Escribe \*2\*/i);
});

test('reconoce preguntas naturales sobre entrega y repartidor como estado de pedido', () => {
  for (const phrase of [
    '¿Ya viene mi pedido?',
    'Quién trae mi pedido',
    'Tengo repartidor asignado',
    'Información de mi entrega',
    'Cómo va mi entrega',
    'Dónde está mi repartidor',
    'Ya viene el repartidor',
    'Seguimiento del repartidor',
  ]) {
    assert.equal(isOrderStatusIntent(phrase), true, phrase);
  }
  assert.equal(isOrderStatusIntent('¿Llegan a mi barrio?'), false);
});

test('catálogo responde solo cuando existe una coincidencia real', async () => {
  db.prepare(`
    INSERT INTO productos_cache
      (id, nombre, descripcion, precio, categoria, stock, tipo_entrega, activo)
    VALUES (?, ?, ?, ?, ?, ?, ?, 1)
  `).run(9001, 'Galletas Festival', '', 2, 'Dulces', 10, 'inmediato');

  const found = await _tryCatalogSearchReply('¿Tienen galletas Festival?', 'https://elparcerito.com');
  const unknown = await _tryCatalogSearchReply('esto no tiene ningún sentido', 'https://elparcerito.com');

  assert.match(found, /Galletas Festival/);
  assert.match(found, /fuente actual de precio y stock/);
  assert.equal(unknown, null);
});

test('recomendador por presupuesto consulta catálogo local y no requiere IA', () => {
  const reply = _tryBudgetCatalogReply('¿Qué puedo comprar con 5 euros?', 'https://elparcerito.com');
  assert.match(reply, /hasta 5\.00 €/i);
  assert.match(reply, /Galletas Festival/i);
  assert.match(reply, /https:\/\/elparcerito\.com/);
  assert.equal(_tryBudgetCatalogReply('cuéntame un chiste', 'https://elparcerito.com'), null);
  assert.equal(_tryBudgetCatalogReply('un pedido para 2 personas', 'https://elparcerito.com'), null);
});

test('STOP y ALTA gestionan consentimiento sin dejar al cliente atrapado', async () => {
  const jid = '34632907788@s.whatsapp.net';
  await handleMessage(jid, 'STOP', 'Cliente');
  assert.ok(db.prepare('SELECT 1 FROM muted_clients WHERE phone = ?').get('34632907788'));
  await handleMessage(jid, 'ALTA', 'Cliente');
  assert.equal(db.prepare('SELECT 1 FROM muted_clients WHERE phone = ?').get('34632907788'), undefined);
});

test('el seguimiento orienta al dispositivo autorizado sin enumerar pedidos', async () => {
  saveSesion({jid:clientJid,nombre:'Cliente',role:'client',estado:'main_menu',pending:{pedido_id:42}});
  await handleMessage(clientJid, 'Dónde está mi pedido', 'Cliente');
  assert.equal(getSesion(clientJid).estado, 'main_menu');
  assert.deepEqual(getSesion(clientJid).pending, {});
  assert.equal(calls.some(c => c.route === '/ayuda'), false);
  assert.equal(calls.some(c => c.route.endsWith('/cancelar') || c.route === '/confirmacion/responder' || c.route === '/pedidos' || c.route === '/cobertura'), false);
  assert.equal(db.prepare('SELECT COUNT(*) c FROM handoffs').get().c, 0);
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
  const mixed = formatOrderItemSummaryLine({
    nombre: 'Caja', cantidad: 1, sabores: ['Mango ×2', 'Lulo ×3'],
  });
  assert.match(mixed, /Sabores: Mango ×2 · Lulo ×3/);
});

test('un número escrito en una espera antigua no revela datos del pedido', async () => {
  saveSesion({jid:clientJid,nombre:'Cliente',role:'client',estado:'espera_numero_pedido',pending:{pedido_id:42}});
  await handleMessage(clientJid, '1111', 'Cliente');
  assert.equal(getSesion(clientJid).estado, 'main_menu');
  assert.deepEqual(getSesion(clientJid).pending, {});
  assert.equal(calls.some(c => c.route === '/ayuda'), false);
  assert.equal(calls.some(c => c.route.endsWith('/cancelar') || c.route === '/confirmacion/responder' || c.route === '/pedidos' || c.route === '/cobertura'), false);
  assert.equal(db.prepare('SELECT COUNT(*) c FROM handoffs').get().c, 0);
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

test('las dudas se redirigen al chat web sin consultar datos comerciales', async () => {
  for (const [index, question] of ['horario', 'quiero comprar', 'cuántos granitos tengo', 'necesito un agente', '123456'].entries()) {
    const jid = `3461999900${index}@s.whatsapp.net`;
    calls = [];
    await handleMessage(jid, question, 'Cliente');
    assert.deepEqual(calls, [], question);
    assert.equal(getSesion(jid).estado, 'main_menu');
    assert.equal(db.prepare('SELECT COUNT(*) c FROM handoffs WHERE client_jid=?').get(jid).c, 0);
  }
  const text = require('../texts').customerChannelNotice('https://shop.invalid/');
  assert.match(text, /chat web/);
  assert.match(text, /https:\/\/shop.invalid\/ayuda/);
  assert.doesNotMatch(text, /invalid\/\/ayuda/);
});
