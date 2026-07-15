'use strict';
/**
 * Tests del gate 'admin_confirm'.
 *
 * Antes: una confirmación pendiente vivía indefinidamente. Si el admin
 * cambiaba de tema y luego respondía "sí" a otro mensaje, ejecutaba la
 * acción olvidada. Peor: ventana abierta para un tercero que tomara el
 * WhatsApp.
 *
 * Ahora: `_asked_at` timestamp en `pending`, y `handleAdminConfirm` descarta
 * la solicitud si supera `ADMIN_CONFIRM_TTL_MS`.
 */

const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');

const dbDir = fs.mkdtempSync(path.join(os.tmpdir(), 'oxidian-bot-confirm-'));
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
const { handleAdminConfirm, getSesion, setSesion, ADMIN_CONFIRM_TTL_MS } = _test;

const OWNER_JID = '34600000001@s.whatsapp.net';

test('confirmación expirada se descarta y vuelve al menú', async () => {
  const stale = Date.now() - ADMIN_CONFIRM_TTL_MS - 1000;
  setSesion(OWNER_JID, {
    estado: 'admin_confirm',
    pending: { action: 'close_store', _asked_at: stale },
  });
  await handleAdminConfirm(OWNER_JID, getSesion(OWNER_JID), 'si');
  assert.equal(getSesion(OWNER_JID).estado, 'admin_menu');
});

test('confirmación legacy sin timestamp falla cerrada', async () => {
  setSesion(OWNER_JID, {
    estado: 'admin_confirm',
    pending: { action: 'close_store' },
  });
  await handleAdminConfirm(OWNER_JID, getSesion(OWNER_JID), 'si');
  assert.equal(getSesion(OWNER_JID).estado, 'admin_menu');
});

test('acción no en whitelist se rechaza sin ejecutar', async () => {
  setSesion(OWNER_JID, {
    estado: 'admin_confirm',
    pending: { action: 'malicious_action', _asked_at: Date.now() },
  });
  await handleAdminConfirm(OWNER_JID, getSesion(OWNER_JID), 'si');
  assert.equal(getSesion(OWNER_JID).estado, 'admin_menu');
});

test('NO cancela y vuelve al menú', async () => {
  setSesion(OWNER_JID, {
    estado: 'admin_confirm',
    pending: { action: 'close_store', _asked_at: Date.now() },
  });
  await handleAdminConfirm(OWNER_JID, getSesion(OWNER_JID), 'no');
  assert.equal(getSesion(OWNER_JID).estado, 'admin_menu');
});
