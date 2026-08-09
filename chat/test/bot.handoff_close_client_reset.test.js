'use strict';

/**
 * Regresión bug 2026-08-10: cuando un admin cierra el chat de handoff,
 * la sesión del CLIENTE debe volver a main_menu para que la siguiente
 * interacción sea atendida por el bot con normalidad. Antes solo se
 * reseteaba la sesión del admin; la del cliente quedaba en un estado
 * indefinido y el bot no volvía a contestar.
 */

const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');

const dbDir = fs.mkdtempSync(path.join(os.tmpdir(), 'oxidian-handoff-reset-'));
process.env.BOT_TEST_MODE = '1';
process.env.DB_DIR = dbDir;
process.env.NODE_ENV = 'test';
process.env.SIMULATE_EVO_SEND = 'true';
process.env.OXIDIAN_KEY = 'test-key-value';
process.env.BOT_PANEL_KEY = 'test-panel-key';
process.env.WEBHOOK_SECRET = 'test-webhook-secret';
process.env.OWNER_NUMBER = '34600000010';

const { _test } = require('../bot');
const {
  db,
  assignHandoff,
  closeHumanChat,
  closeHumanChatByClient,
  createHandoffRequest,
  getHandoff,
  getSesion,
  releaseHumanChat,
  saveSesion,
} = _test;

const adminJid = '34600000010@s.whatsapp.net';
const clientJid = '34611111111@s.whatsapp.net';

function seedClientHandoff() {
  db.exec(`
    DELETE FROM handoff_messages;
    DELETE FROM handoffs;
    DELETE FROM sessions;
  `);
  saveSesion({
    jid: clientJid,
    nombre: 'Cliente Test',
    role: 'client',
    estado: 'handoff',
    carrito: [],
    pending: { queued_note: 'x' },
    zona_id: null,
    active_client_jid: null,
  });
  saveSesion({
    jid: adminJid,
    nombre: 'Admin',
    role: 'admin',
    estado: 'admin_chat',
    carrito: [],
    pending: {},
    zona_id: null,
    active_client_jid: clientJid,
  });
  createHandoffRequest(clientJid, { scope: 'global', agents: ['34600000010'] });
  assignHandoff(clientJid, adminJid);
  assert.equal(getHandoff(clientJid).admin_jid, adminJid);
}


test('closeHumanChat también resetea la sesión del cliente a main_menu', async () => {
  seedClientHandoff();
  const sesionAntes = getSesion(clientJid);
  assert.notEqual(sesionAntes.estado, 'main_menu',
    'precondición: el cliente NO estaba en main_menu antes del cierre');

  const closed = await closeHumanChat(adminJid, clientJid, /*notify*/ false);
  assert.equal(closed, true);

  const sesionDespues = getSesion(clientJid);
  assert.equal(sesionDespues.estado, 'main_menu',
    'el cliente debe quedar en main_menu tras el cierre');
  assert.equal(sesionDespues.active_client_jid, null,
    'active_client_jid debe limpiarse');
  assert.deepEqual(sesionDespues.pending, {},
    'pending debe quedar vacío para no arrastrar estado del handoff');

  // Handoff eliminado
  assert.equal(getHandoff(clientJid), null);
});


test('releaseHumanChat también resetea la sesión del cliente', async () => {
  seedClientHandoff();
  const released = await releaseHumanChat(adminJid, clientJid, /*notify*/ false);
  assert.equal(released, true);

  const sesion = getSesion(clientJid);
  assert.equal(sesion.estado, 'main_menu');
  assert.equal(sesion.active_client_jid, null);

  // El handoff sigue existiendo pero sin admin (regresa a la cola).
  const h = getHandoff(clientJid);
  assert.ok(h, 'el handoff debe seguir vivo en cola tras release');
  assert.equal(h.admin_jid, null);
});


test('closeHumanChatByClient también resetea la sesión del cliente', () => {
  seedClientHandoff();
  const removed = closeHumanChatByClient(clientJid);
  assert.ok(removed, 'el handoff debe existir antes del cierre');

  const sesion = getSesion(clientJid);
  assert.equal(sesion.estado, 'main_menu');
  assert.equal(sesion.active_client_jid, null);
  assert.equal(getHandoff(clientJid), null);
});


test('cliente con handoff cerrado no queda en estado zombie ("handoff")', async () => {
  seedClientHandoff();
  await closeHumanChat(adminJid, clientJid, false);
  const sesion = getSesion(clientJid);
  assert.notEqual(sesion.estado, 'handoff',
    'estado "handoff" viejo debe quedar limpiado — el bot debe poder responder');
  // Cualquier estado que main_menu maneje sirve; documentamos que caiga en
  // main_menu explícitamente para consistencia con el resto del router.
  assert.equal(sesion.estado, 'main_menu');
});
