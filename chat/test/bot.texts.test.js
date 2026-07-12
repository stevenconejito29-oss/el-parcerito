'use strict';
/**
 * Tests unitarios del módulo `texts.js`.
 *
 * texts.js es puro (recibe contexto explícito, no toca BD ni env) para que
 * este archivo pueda ejercitarlo sin arrancar el bot. Añadir aquí un test
 * por cada texto que un cliente pueda ver o cada cambio en las opciones
 * del menú — es la barrera para no regresionar sobre la UX del bot.
 */

const assert = require('node:assert/strict');
const test = require('node:test');

const texts = require('../texts');

test('menuPrincipal incluye el nombre del negocio y las capacidades habilitadas', () => {
  const out = texts.menuPrincipal({
    nombreNegocio: 'El Parcerito',
    loyaltyEnabled: true,
    deliveryEnabled: true,
  });
  assert.match(out, /El Parcerito/);
  assert.match(out, /consultar tus puntos/);
  assert.match(out, /comprobar cobertura/);
});

test('menuPrincipal oculta capacidades cuando el feature está apagado', () => {
  const out = texts.menuPrincipal({
    nombreNegocio: 'Tienda X',
    loyaltyEnabled: false,
    deliveryEnabled: false,
  });
  assert.doesNotMatch(out, /puntos/);
  assert.doesNotMatch(out, /cobertura/);
});

test('menuPrincipal soporta activar solo uno de los dos features', () => {
  const soloLoyalty = texts.menuPrincipal({
    nombreNegocio: 'X',
    loyaltyEnabled: true,
    deliveryEnabled: false,
  });
  assert.match(soloLoyalty, /consultar tus puntos/);
  assert.doesNotMatch(soloLoyalty, /cobertura/);
});

test('clientMenuLines muestra opciones 1, 2, 6 y 7 siempre', () => {
  const out = texts.clientMenuLines({
    verticalLabel: 'Menú',
    loyaltyEnabled: false,
    deliveryEnabled: false,
  });
  for (const opt of ['*1*', '*2*', '*6*', '*7*']) {
    assert.match(out, new RegExp(opt.replace(/\*/g, '\\*')));
  }
  // 3 y 4 solo aparecen cuando el feature está activo
  assert.doesNotMatch(out, /\*3\*/);
  assert.doesNotMatch(out, /\*4\*/);
});

test('clientMenuLines añade 3 y 4 cuando corresponde', () => {
  const out = texts.clientMenuLines({
    verticalLabel: 'Catálogo',
    loyaltyEnabled: true,
    deliveryEnabled: true,
  });
  assert.match(out, /\*3\*.*puntos/i);
  assert.match(out, /\*4\*.*entrega/i);
  assert.match(out, /catálogo en la web/);
});

test('clientMenuLines cae al default "Menú" si no viene verticalLabel', () => {
  const out = texts.clientMenuLines({
    loyaltyEnabled: false,
    deliveryEnabled: false,
  });
  assert.match(out, /menú en la web/);
});

test('clientCapabilityText refleja los flags activos', () => {
  const con = texts.clientCapabilityText({ loyaltyEnabled: true, deliveryEnabled: true });
  assert.match(con, /puntos/);
  assert.match(con, /cobertura/);
  const sin = texts.clientCapabilityText({ loyaltyEnabled: false, deliveryEnabled: false });
  assert.doesNotMatch(sin, /puntos/);
  assert.doesNotMatch(sin, /cobertura/);
});

test('barMenu incluye el nombre del bar y las 8 opciones numeradas', () => {
  const out = texts.barMenu({ nombreBar: 'Bar Test' });
  assert.match(out, /Bar Test/);
  for (const num of ['1️⃣', '2️⃣', '3️⃣', '4️⃣', '5️⃣', '6️⃣', '7️⃣', '8️⃣']) {
    assert.match(out, new RegExp(num));
  }
});

test('handoffClosedMessage anida el menú principal recibido', () => {
  const out = texts.handoffClosedMessage('---MENU-MOCK---');
  assert.match(out, /agente ha finalizado/);
  assert.match(out, /---MENU-MOCK---/);
});

test('HANDOFF_QUEUED avisa al cliente que puede escribir /volver bot', () => {
  assert.match(texts.HANDOFF_QUEUED, /\/volver bot/);
});

test('HANDOFF_REQUEUED confirma que el historial se conserva', () => {
  assert.match(texts.HANDOFF_REQUEUED, /historial/i);
});

test('ESCAPE_HINT y FALLBACK_HINT están definidos y coinciden con la convención MENU', () => {
  assert.ok(texts.ESCAPE_HINT.length > 0);
  assert.match(texts.ESCAPE_HINT, /\*MENU\*/);
  assert.match(texts.FALLBACK_HINT, /\*MENU\*/);
});
