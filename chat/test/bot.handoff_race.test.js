'use strict';
/**
 * Tests de que `assignHandoff` es seguro ante reclamos concurrentes.
 *
 * Antes: había un pequeño gap entre `adminHasActiveChat` y el UPDATE
 * SET admin_jid=?. Dos claims simultáneos del mismo admin (p. ej.
 * doble-tap desde 2 dispositivos) pasaban ambos pre-checks porque el
 * primero aún no había hecho commit. Resultado: 1 admin con 2 chats
 * activos, con mensajes cruzándose entre clientes.
 *
 * Ahora: la comprobación y el UPDATE viven en un único `db.transaction()`.
 * SQLite (better-sqlite3) usa modo IMMEDIATE por defecto → los writes se
 * serializan y el segundo intento ve el estado del primero.
 */

const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');

const dbDir = fs.mkdtempSync(path.join(os.tmpdir(), 'oxidian-bot-handoff-'));
process.env.BOT_TEST_MODE = '1';
process.env.DB_DIR = dbDir;
process.env.NODE_ENV = 'test';
process.env.SIMULATE_EVO_SEND = 'true';
process.env.OXIDIAN_KEY = 'k';
process.env.BOT_PANEL_KEY = 'k';
process.env.WEBHOOK_SECRET = 'w';
process.env.OWNER_NUMBER = '34600000001';
process.env.SUPERADMINS = '34600000002';

const { _test } = require('../bot');
const { db, assignHandoff, createHandoffRequest, setCfg } = _test;

// Registramos 2 super_admin válidos para que `adminCan(x,'handoff')` pase.
setCfg('whatsapp_role_profiles', JSON.stringify([
  { telefono: '34600000001', rol: 'super_admin', capabilities: ['handoff'] },
  { telefono: '34600000002', rol: 'super_admin', capabilities: ['handoff'] },
]));

const ADMIN_A = '34600000001@s.whatsapp.net';
const ADMIN_B = '34600000002@s.whatsapp.net';

function _wipeHandoffs() {
  db.prepare('DELETE FROM handoffs').run();
}

function _mkClient(phone) {
  const jid = `${phone}@s.whatsapp.net`;
  createHandoffRequest(jid, {});
  return jid;
}

test('dos admins reclaman el mismo cliente: exactamente uno gana', () => {
  _wipeHandoffs();
  const client = _mkClient('34611111111');
  const r1 = assignHandoff(client, ADMIN_A);
  const r2 = assignHandoff(client, ADMIN_B);
  assert.equal(r1.changes + r2.changes, 1);
  const owner = db.prepare('SELECT admin_jid FROM handoffs WHERE client_jid = ?').get(client);
  assert.ok(owner.admin_jid === ADMIN_A || owner.admin_jid === ADMIN_B);
});

test('un admin no puede quedarse con 2 chats por doble-tap', () => {
  _wipeHandoffs();
  const client1 = _mkClient('34611111112');
  const client2 = _mkClient('34611111113');
  // Primer claim: admin A toma client1.
  const r1 = assignHandoff(client1, ADMIN_A);
  assert.equal(r1.changes, 1);
  // Segundo claim: admin A intenta tomar client2 mientras ya tiene chat.
  const r2 = assignHandoff(client2, ADMIN_A);
  assert.equal(r2.changes, 0, 'no debe permitirse un segundo chat');
  const owner2 = db.prepare('SELECT admin_jid FROM handoffs WHERE client_jid = ?').get(client2);
  assert.equal(owner2.admin_jid, null);
});

test('un cliente ya asignado no se reasigna a otro admin', () => {
  _wipeHandoffs();
  const client = _mkClient('34611111114');
  assignHandoff(client, ADMIN_A);
  const r = assignHandoff(client, ADMIN_B);
  assert.equal(r.changes, 0);
  const owner = db.prepare('SELECT admin_jid FROM handoffs WHERE client_jid = ?').get(client);
  assert.equal(owner.admin_jid, ADMIN_A);
});

test('admin no puede tomarse a sí mismo', () => {
  _wipeHandoffs();
  createHandoffRequest(ADMIN_A, {});
  const r = assignHandoff(ADMIN_A, ADMIN_A);
  assert.equal(r.changes, 0);
});

test('jid sin capability handoff no puede reclamar', () => {
  _wipeHandoffs();
  const client = _mkClient('34611111115');
  const fake = '34699999999@s.whatsapp.net'; // no está en profiles
  const r = assignHandoff(client, fake);
  assert.equal(r.changes, 0);
});

test('handoff con agents_json restringido: solo esos jids reclaman', () => {
  _wipeHandoffs();
  const client = '34611111116@s.whatsapp.net';
  // createHandoffRequest normaliza agents contra adminPhones() —
  // solo se conservan phones que son admin. B lo es, así que se guarda.
  createHandoffRequest(client, { agents: ['34600000002'] });
  // A es admin genérico pero no está en la whitelist restrictiva → rechazado.
  const rA = assignHandoff(client, ADMIN_A);
  assert.equal(rA.changes, 0);
  // B sí está permitido.
  const rB = assignHandoff(client, ADMIN_B);
  assert.equal(rB.changes, 1);
});
