'use strict';
/**
 * Tests de que fallos de Evolution API no rompen el bot.
 *
 * Escenarios cubiertos:
 *  - Timeout / network error → `sendText` devuelve false sin lanzar.
 *  - HTTP 5xx repetido → agota reintentos, devuelve false.
 *  - HTTP 4xx → no reintenta, devuelve false.
 *  - HTTP 200 con body OK → devuelve true.
 *
 * Antes: sin tests para estos casos. Un fallo temporal de Evolution podía
 * pasar desapercibido en desarrollo y desestabilizar producción.
 */

const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');

const dbDir = fs.mkdtempSync(path.join(os.tmpdir(), 'oxidian-bot-evo-'));
process.env.BOT_TEST_MODE = '1';
process.env.DB_DIR = dbDir;
process.env.NODE_ENV = 'test';
// IMPORTANTE: NO simular — queremos que `sendText` intente el HTTP real
// (contra nuestro mock fetch) para ejercitar la lógica de reintentos.
process.env.SIMULATE_EVO_SEND = 'false';
process.env.EVOLUTION_URL = 'http://mock-evolution.local';
process.env.EVOLUTION_INSTANCE = 'test-inst';
process.env.EVOLUTION_API_KEY = 'test-key';
process.env.OXIDIAN_KEY = 'k';
process.env.BOT_PANEL_KEY = 'k';
process.env.WEBHOOK_SECRET = 'w';
process.env.OWNER_NUMBER = '34600000001';
process.env.SUPERADMINS = '34600000002';
// Acorta backoff entre reintentos y desactiva humanized delay para no
// bloquear el test.
process.env.BOT_MIN_OUTBOUND_MS = '0';

const { _test } = require('../bot');
const { setCfg } = _test;

// Permitir que sendText pase el gate 24h (necesitamos que el cliente
// haya escrito antes). Como no hay `sendText` exportado directamente,
// usamos el mismo db para insertar un lastInbound. Alternativa: setCfg.
// Más simple: bypasear vía opts.transactional cuando llamamos.

// Mock global fetch. Guardamos el original para restaurar.
const _origFetch = globalThis.fetch;
let _fetchMode = 'ok';
let _fetchCalls = 0;

globalThis.fetch = async (url, opts) => {
  _fetchCalls++;
  // Presence endpoint del humanize: responder 200 siempre para no
  // ensuciar la métrica del principal.
  if (String(url).includes('/chat/sendPresence/')) {
    return new Response(JSON.stringify({ ok: true }), { status: 200 });
  }
  if (_fetchMode === 'timeout') {
    const err = new Error('The operation was aborted');
    err.name = 'AbortError';
    throw err;
  }
  if (_fetchMode === 'network') {
    throw new TypeError('fetch failed');
  }
  if (_fetchMode === '500') {
    return new Response('{"error":"internal"}', { status: 500 });
  }
  if (_fetchMode === '400') {
    return new Response('{"error":"bad request"}', { status: 400 });
  }
  // ok
  return new Response('{"ok":true,"key":{"id":"m1"}}', { status: 200 });
};

test.after(() => { globalThis.fetch = _origFetch; });

// Necesitamos el sendText real. Está fuera de _test — lo obtenemos por
// require.cache.
const botModule = require.cache[require.resolve('../bot')].exports;
const sendText = botModule.app._router
  ? require('../bot').sendText
  : null;

// Como sendText no está exportado, usamos un caller que sí lo invoca:
// creamos un cliente con inbound reciente y usamos oxidianGet path.
// La forma más simple: tests de gate. Alternativa: exportar sendText.

// Voy a exportar sendText via _test para poder testear directo.
const _sendText = _test.sendText;

test('smoke: sendText existe en _test', () => {
  assert.ok(typeof _sendText === 'function', 'sendText debe estar exportado en _test');
});

test('HTTP 200 devuelve true', async () => {
  _fetchMode = 'ok'; _fetchCalls = 0;
  const ok = await _sendText('34611111111@s.whatsapp.net', 'hola', { transactional: true, humanize: false });
  assert.equal(ok, true);
  assert.ok(_fetchCalls >= 1);
});

test('HTTP 4xx no reintenta y devuelve false', async () => {
  _fetchMode = '400'; _fetchCalls = 0;
  const ok = await _sendText('34611111112@s.whatsapp.net', 'hola', { transactional: true, humanize: false });
  assert.equal(ok, false);
  assert.equal(_fetchCalls, 1, 'no debería reintentar en 4xx');
});

test('HTTP 5xx reintenta y agota (≤3 intentos)', async () => {
  _fetchMode = '500'; _fetchCalls = 0;
  const ok = await _sendText('34611111113@s.whatsapp.net', 'hola', { transactional: true, humanize: false });
  assert.equal(ok, false);
  assert.equal(_fetchCalls, 3, 'debe intentar 3 veces exactas');
});

test('timeout / abort no lanza — devuelve false', async () => {
  _fetchMode = 'timeout'; _fetchCalls = 0;
  const ok = await _sendText('34611111114@s.whatsapp.net', 'hola', { transactional: true, humanize: false });
  assert.equal(ok, false);
  assert.equal(_fetchCalls, 3, 'reintentos hasta agotar');
});

test('error de red genérico no lanza — devuelve false', async () => {
  _fetchMode = 'network'; _fetchCalls = 0;
  const ok = await _sendText('34611111115@s.whatsapp.net', 'hola', { transactional: true, humanize: false });
  assert.equal(ok, false);
  assert.equal(_fetchCalls, 3);
});
