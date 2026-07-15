'use strict';
/**
 * Tests del gate de PIN admin.
 *
 * Antes: si el admin dejaba la sesión en 'awaiting_pin' (por distracción o
 * error de digitación), el bot trataba CUALQUIER mensaje futuro como intento
 * de PIN. Sin PIN, sin salida — el admin quedaba bloqueado indefinidamente.
 *
 * Ahora:
 *   - `salir`/`cancelar`/`menu` sale del gate y vuelve al menú.
 *   - Pasado `AWAITING_PIN_TTL_MS` sin actividad, el estado se resetea
 *     automáticamente al primer mensaje siguiente.
 */

const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');

const dbDir = fs.mkdtempSync(path.join(os.tmpdir(), 'oxidian-bot-pin-'));
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
const { requireAdminPin, getSesion, setSesion, setCfg, AWAITING_PIN_TTL_MS } = _test;

// PIN = 1234 → sha256
const PIN_HASH = require('crypto').createHash('sha256').update('1234').digest('hex');

test('escape word (salir) libera el gate y no ejecuta acción', async () => {
  setCfg('admin_pin_hash', PIN_HASH);
  const jid = '34600000001@s.whatsapp.net';
  setSesion(jid, { estado: 'awaiting_pin', pending: { awaiting_pin_prev: 'admin_menu', awaiting_pin_since: Date.now() } });
  const ok = await requireAdminPin(jid, getSesion(jid), 'salir');
  assert.equal(ok, false);
  assert.equal(getSesion(jid).estado, 'admin_menu');
});

test('TTL expirado resetea el estado en el siguiente mensaje', async () => {
  setCfg('admin_pin_hash', PIN_HASH);
  const jid = '34600000003@s.whatsapp.net';
  const stale = Date.now() - AWAITING_PIN_TTL_MS - 1000;
  setSesion(jid, { estado: 'awaiting_pin', pending: { awaiting_pin_prev: 'admin_menu', awaiting_pin_since: stale } });
  const ok = await requireAdminPin(jid, getSesion(jid), 'cualquier cosa');
  assert.equal(ok, false);
  const s = getSesion(jid);
  assert.notEqual(s.estado, 'awaiting_pin');
});

test('PIN correcto desbloquea y limpia awaiting_pin_since', async () => {
  setCfg('admin_pin_hash', PIN_HASH);
  const jid = '34600000001@s.whatsapp.net';
  setSesion(jid, { estado: 'awaiting_pin', pending: { awaiting_pin_prev: 'admin_menu', awaiting_pin_since: Date.now() } });
  const ok = await requireAdminPin(jid, getSesion(jid), '1234');
  assert.equal(ok, false); // devuelve false porque re-muestra el menú
  const s = getSesion(jid);
  assert.equal((s.pending || {}).awaiting_pin_since, undefined);
});

test('primera entrada al gate marca awaiting_pin_since', async () => {
  setCfg('admin_pin_hash', PIN_HASH);
  const jid = '34600000004@s.whatsapp.net';
  setSesion(jid, { estado: 'admin_menu' });
  const ok = await requireAdminPin(jid, getSesion(jid), '2'); // acción de escritura (store)
  assert.equal(ok, false);
  const s = getSesion(jid);
  assert.equal(s.estado, 'awaiting_pin');
  const since = (s.pending || {}).awaiting_pin_since;
  assert.ok(since);
  assert.ok(Date.now() - since < 5000);
});
