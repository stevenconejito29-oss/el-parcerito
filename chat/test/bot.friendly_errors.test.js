'use strict';
/**
 * Tests de `friendlyOxidianError`.
 *
 * Antes: los errores de `oxidianGet`/`oxidianPost` se enviaban al admin como
 * `Error: ${e.message}` — se filtraban URLs internas, tracebacks Python en
 * `data.error`, y códigos crudos. Además de ruido técnico, era un vector de
 * mapeo de topología del backend si el WhatsApp del admin caía en malas
 * manos. Ahora el helper devuelve un mensaje corto por categoría, y el
 * error real se loguea con `log('warn', ...)`.
 */

const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');

const dbDir = fs.mkdtempSync(path.join(os.tmpdir(), 'oxidian-bot-friendlyerr-'));
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
const { friendlyOxidianError } = _test;

test('NO_BOT_KEY devuelve mensaje sin URL interna', () => {
  const err = new Error('X-Bot-Key no configurada — revisa BOT_API_KEY en /superadmin/config');
  err.code = 'NO_BOT_KEY';
  const out = friendlyOxidianError(err);
  assert.match(out, /super_admin/i);
  assert.doesNotMatch(out, /BOT_API_KEY|superadmin\/config/);
});

test('NET_ERROR no expone la URL fallida', () => {
  const err = new Error('Sin conexión con Oxidian (TimeoutError): http://oxidian:5000/api/bot/status');
  err.code = 'NET_ERROR';
  const out = friendlyOxidianError(err);
  assert.match(out, /sin conexión/i);
  assert.doesNotMatch(out, /oxidian:5000|api\/bot/);
});

test('HTTP 401/403 → mensaje de permiso, sin status crudo', () => {
  const err = new Error('HTTP 403 en POST /admin/tienda');
  err.status = 403;
  const out = friendlyOxidianError(err);
  assert.match(out, /permiso/i);
  assert.doesNotMatch(out, /HTTP 403|\/admin\/tienda/);
});

test('HTTP 404 → mensaje genérico', () => {
  const err = new Error('HTTP 404 en GET /admin/producto/9999');
  err.status = 404;
  assert.match(friendlyOxidianError(err), /no existe/i);
});

test('HTTP 422 usa data.error truncado (mensaje de validación de negocio)', () => {
  const err = new Error('HTTP 422 en POST /admin/producto');
  err.status = 422;
  err.data = { error: 'El precio debe ser > 0' };
  assert.match(friendlyOxidianError(err), /precio debe ser/i);
});

test('HTTP 500 no filtra tracebacks ni paths', () => {
  const err = new Error('HTTP 500 en POST /admin/tienda');
  err.status = 500;
  err.data = { error: 'Traceback (most recent call last):\n  File "/app/routes/admin.py", line 42' };
  const out = friendlyOxidianError(err);
  assert.match(out, /error interno/i);
  assert.doesNotMatch(out, /Traceback|routes\/admin|line 42/);
});

test('4xx genérico con data.error sí muestra el mensaje (truncado)', () => {
  const err = new Error('HTTP 400 en POST /admin/tienda');
  err.status = 400;
  err.data = { error: 'Campo obligatorio faltante: forzar_cerrada' };
  assert.match(friendlyOxidianError(err), /Campo obligatorio/);
});

test('Error sin código ni status devuelve fallback', () => {
  const out = friendlyOxidianError(new Error('cualquier cosa rara'));
  assert.match(out, /no pude completar/i);
  assert.doesNotMatch(out, /cualquier cosa/);
});

test('Argumento null / undefined no crashea', () => {
  assert.doesNotThrow(() => friendlyOxidianError(null));
  assert.doesNotThrow(() => friendlyOxidianError(undefined));
});
