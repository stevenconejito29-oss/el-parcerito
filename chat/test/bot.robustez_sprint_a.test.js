'use strict';
/**
 * Tests de los helpers universales de robustez del bot (Sprint A).
 *
 * Cubren:
 *   1. `isEscapeWord` — palabras que sacan de cualquier submenu.
 *   2. `bumpAttempt` + `clearAttempts` — contador de reintentos por handler.
 *   3. `isBotEnabled` — respeta el flag global de pánico.
 *
 * Estos helpers son las piezas base de la refactor. Cualquier regresión
 * silenciosa aquí desbloquearía bucles infinitos o comandos ejecutados
 * en modo pánico. Los mantenemos con tests explícitos.
 */

const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');

const dbDir = fs.mkdtempSync(path.join(os.tmpdir(), 'oxidian-bot-robustez-'));
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
const {
  isEscapeWord, bumpAttempt, clearAttempts, isBotEnabled,
  setSesion, getSesion, setCfg,
} = _test;

// ── isEscapeWord ──
test('isEscapeWord reconoce las 6 palabras universales', () => {
  ['0', 'menu', 'menú', 'inicio', 'salir', 'cancelar'].forEach(w => {
    assert.ok(isEscapeWord(w), `"${w}" debería salir`);
    assert.ok(isEscapeWord(w.toUpperCase()), `"${w.toUpperCase()}" case-insensitive`);
    assert.ok(isEscapeWord('  ' + w + '  '), 'debe ignorar espacios');
  });
});

test('isEscapeWord no confunde palabras normales', () => {
  ['pedido', '2', 'ayuda', 'quiero cancelar mi pedido', '', null, undefined]
    .forEach(w => assert.ok(!isEscapeWord(w), `"${w}" no debería escapar`));
});

// ── bumpAttempt / clearAttempts ──
test('bumpAttempt cuenta reintentos y detecta cap', () => {
  const jid = '34611111100@s.whatsapp.net';
  setSesion(jid, { pending: {} });

  let ses = getSesion(jid);
  assert.equal(bumpAttempt(ses, 'foo', 3), false);
  ses = getSesion(jid);
  assert.equal(bumpAttempt(ses, 'foo', 3), false);
  ses = getSesion(jid);
  assert.equal(bumpAttempt(ses, 'foo', 3), true, 'tercer intento supera cap');
});

test('bumpAttempt cuenta independiente por key', () => {
  const jid = '34611111101@s.whatsapp.net';
  setSesion(jid, { pending: {} });
  const ses = getSesion(jid);
  bumpAttempt(ses, 'a', 5);
  bumpAttempt(ses, 'b', 5);
  bumpAttempt(ses, 'a', 5);
  const refreshed = getSesion(jid);
  assert.equal(refreshed.pending._attempts_a, 2);
  assert.equal(refreshed.pending._attempts_b, 1);
});

test('clearAttempts limpia solo la key indicada', () => {
  const jid = '34611111102@s.whatsapp.net';
  setSesion(jid, { pending: {} });
  let ses = getSesion(jid);
  bumpAttempt(ses, 'x', 5);
  bumpAttempt(ses, 'y', 5);
  ses = getSesion(jid);
  clearAttempts(ses, 'x');
  const refreshed = getSesion(jid);
  assert.equal(refreshed.pending._attempts_x, undefined);
  assert.equal(refreshed.pending._attempts_y, 1);
});

// ── isBotEnabled ──
test('isBotEnabled true por defecto', () => {
  setCfg('bot_enabled', '1');
  assert.equal(isBotEnabled(), true);
});

test('isBotEnabled false con bot_enabled=0 (modo pánico)', () => {
  setCfg('bot_enabled', '0');
  assert.equal(isBotEnabled(), false);
  // Restaurar para no ensuciar otros tests
  setCfg('bot_enabled', '1');
});
