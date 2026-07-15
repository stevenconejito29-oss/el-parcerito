'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');

const dbDir = fs.mkdtempSync(path.join(os.tmpdir(), 'oxidian-bot-mode-'));
process.env.BOT_TEST_MODE = '1';
process.env.DB_DIR = dbDir;
process.env.NODE_ENV = 'test';
process.env.SIMULATE_EVO_SEND = 'true';
process.env.OXIDIAN_KEY = 'test-key-value';
process.env.BOT_PANEL_KEY = 'test-panel-key';
process.env.OWNER_NUMBER = '34600000991';

const { _test } = require('../bot');
const {
  db,
  detectOperationalModeCommand,
  getHandoff,
  getSesion,
  handleMessage,
  isAdminAvailable,
  resetOperationalPresenceForStartup,
  setAdminAvailability,
  setSesion,
} = _test;
const jid = '34600000991@s.whatsapp.net';

test.beforeEach(() => {
  db.exec('DELETE FROM handoffs; DELETE FROM sessions; DELETE FROM admin_availability;');
  setSesion(jid, { jid, nombre: 'Responsable', role: 'admin', estado: 'admin_menu' });
});

test.after(() => {
  db.close();
  fs.rmSync(dbDir, { recursive: true, force: true });
});

test('/offline desactiva atención y conserva flujo cliente para el mismo teléfono', async () => {
  await handleMessage(jid, '/offline', 'Responsable');
  const ses = getSesion(jid);
  assert.equal(ses.estado, 'client_main_menu');
  assert.equal(isAdminAvailable(jid), false);
});

test('/online restaura el contexto operativo y disponibilidad', async () => {
  await handleMessage(jid, '/offline', 'Responsable');
  await handleMessage(jid, '/online', 'Responsable');
  const ses = getSesion(jid);
  assert.equal(ses.role, 'admin');
  assert.equal(ses.estado, 'admin_menu');
  assert.equal(isAdminAvailable(jid), true);
});

test('modo cliente también implica offline para evitar asignaciones accidentales', async () => {
  await handleMessage(jid, 'modo cliente', 'Responsable');
  assert.equal(getSesion(jid).estado, 'client_main_menu');
  assert.equal(isAdminAvailable(jid), false);
});

test('acepta alias de modo con slash, acentos y lenguaje natural', () => {
  for (const value of ['/offline', 'OFF', 'modo cliente', 'fuera de línea', 'comprar como cliente']) {
    assert.equal(detectOperationalModeCommand(value), 'offline', value);
  }
  for (const value of ['/online', 'ON', 'modo admin', 'en línea', 'volver al panel']) {
    assert.equal(detectOperationalModeCommand(value), 'online', value);
  }
  assert.equal(detectOperationalModeCommand('mi pedido'), null);
});

test('un reinicio alinea disponibilidad offline y sesión cliente', () => {
  setAdminAvailability(jid, true);
  assert.equal(resetOperationalPresenceForStartup(), 1);
  assert.equal(isAdminAvailable(jid), false);
  // La identidad sigue siendo admin; el prefijo client_ determina el contexto.
  assert.equal(getSesion(jid).role, 'admin');
  assert.equal(getSesion(jid).estado, 'client_main_menu');
});

test('/offline no se reenvía al cliente durante un chat activo', async () => {
  const clientJid = '34610000991@s.whatsapp.net';
  db.prepare(`INSERT INTO handoffs (client_jid, admin_jid, assigned_at) VALUES (?, ?, unixepoch())`).run(clientJid, jid);
  setSesion(jid, {
    jid, nombre: 'Responsable', role: 'admin', estado: 'admin_chat',
    active_client_jid: clientJid,
  });
  await handleMessage(jid, '/offline', 'Responsable');
  assert.equal(getSesion(jid).estado, 'admin_chat');
  assert.equal(db.prepare(`SELECT COUNT(*) c FROM handoff_messages WHERE client_jid=? AND body='/offline'`).get(clientJid).c, 0);
});

test('/modo no cambia la sesión y funciona online y offline', async () => {
  await handleMessage(jid, '/modo', 'Responsable');
  assert.equal(getSesion(jid).estado, 'admin_menu');
  await handleMessage(jid, '/offline', 'Responsable');
  await handleMessage(jid, '/modo', 'Responsable');
  assert.equal(getSesion(jid).estado, 'client_main_menu');
});

test('Pedido desde modo online explica cómo cambiar a cliente', async () => {
  await handleMessage(jid, 'Pedido', 'Responsable');
  assert.equal(getSesion(jid).estado, 'admin_menu');
  const last = db.prepare(`SELECT detalle FROM logs WHERE evento='send_attempt' ORDER BY id DESC LIMIT 1`).get();
  // El detalle de auditoría conserva deliberadamente solo los primeros 100 caracteres.
  assert.match(last.detalle, /Ahora estás \*online como/i);
  assert.match(last.detalle, /Para comprar o consultar tus pedidos personales/i);
});

test('un comando administrativo no rompe el contexto cliente offline', async () => {
  await handleMessage(jid, '/offline', 'Responsable');
  await handleMessage(jid, '!status', 'Responsable');
  assert.equal(getSesion(jid).estado, 'client_main_menu');
  assert.equal(isAdminAvailable(jid), false);
  const last = db.prepare(`SELECT detalle FROM logs WHERE evento='send_attempt' ORDER BY id DESC LIMIT 1`).get();
  assert.match(last.detalle, /modo cliente \(offline\)/i);
  assert.match(last.detalle, /\/online/i);
});

test('admin offline puede pedir ayuda sin notificarse ni tomarse su propio chat', async () => {
  await handleMessage(jid, '/offline', 'Responsable');
  db.exec('DELETE FROM logs;');

  await handleMessage(jid, 'agente', 'Responsable');

  const handoff = getHandoff(jid);
  assert.ok(handoff);
  assert.equal(handoff.admin_jid, null);
  const selfTakeAlerts = db.prepare(`
    SELECT COUNT(*) c FROM logs
    WHERE evento='send_attempt' AND detalle LIKE '%Cliente en espera%'
  `).get().c;
  assert.equal(selfTakeAlerts, 0);
});

test('0 cierra el soporte pendiente y vuelve al menú cliente offline', async () => {
  await handleMessage(jid, '/offline', 'Responsable');
  await handleMessage(jid, 'agente', 'Responsable');
  assert.ok(getHandoff(jid));

  await handleMessage(jid, '0', 'Responsable');

  assert.equal(getHandoff(jid), null);
  assert.equal(getSesion(jid).estado, 'client_main_menu');
  assert.equal(isAdminAvailable(jid), false);
});

test('0 muestra el menú aunque el superadmin offline tenga un pedido activo', async () => {
  setSesion(jid, {
    jid,
    nombre: 'Responsable',
    role: 'client',
    estado: 'client_pedido_acciones',
    pending: { pedido_id: 77, numero: '#1077' },
  });
  setAdminAvailability(jid, false);
  const originalFetch = global.fetch;
  global.fetch = async (url) => ({
    ok: true,
    status: 200,
    json: async () => String(url).includes('/pedidos?')
      ? { ok: true, pedidos: [{ id: 77, numero: '#1077', estado: 'armando', estado_label: 'En preparación' }] }
      : { ok: true, cliente: null },
  });
  try {
    await handleMessage(jid, '0', 'Responsable');
  } finally {
    global.fetch = originalFetch;
  }

  assert.equal(getSesion(jid).estado, 'client_main_menu');
  const last = db.prepare(`SELECT detalle FROM logs WHERE evento='send_attempt' ORDER BY id DESC LIMIT 1`).get();
  assert.match(last.detalle, /Modo cliente activo|Elige una opción|Qué necesitas/i);
  assert.doesNotMatch(last.detalle, /pedido .*preparación/i);
});

test('/online elimina la solicitud propia sin mostrar conflictos de agentes', async () => {
  await handleMessage(jid, '/offline', 'Responsable');
  await handleMessage(jid, 'agente', 'Responsable');
  assert.ok(getHandoff(jid));
  db.exec('DELETE FROM logs;');

  await handleMessage(jid, '/online', 'Responsable');

  assert.equal(getHandoff(jid), null);
  assert.equal(getSesion(jid).estado, 'admin_menu');
  assert.equal(isAdminAvailable(jid), true);
  const conflictMessages = db.prepare(`
    SELECT COUNT(*) c FROM logs
    WHERE evento='send_attempt'
      AND (detalle LIKE '%Otro administrador tomó%' OR detalle LIKE '%otro agente%')
  `).get().c;
  assert.equal(conflictMessages, 0);
});
