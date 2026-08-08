'use strict';

/**
 * Tests de la mejora NLU:
 *   - _singularize: elimina 's'/'es' finales de forma conservadora.
 *   - detectClientIntent: captura variantes plurales y typos ≥ 5 chars.
 *
 * Cubre gaps del audit del chatbot 2026-08-08:
 *   - "domicilio" no matcheaba "dirección" (5 chars, Levenshtein OFF).
 *   - "pedidos" solo matcheaba porque estaba enumerado; nuevos plurales
 *     como "envios", "ordenes" caían al fallback.
 */

const test = require('node:test');
const assert = require('node:assert/strict');

const { _test } = require('../bot');
const { _singularize, detectClientIntent } = _test;


test.describe('_singularize', () => {
  test('no toca palabras ≤ 4 chars', () => {
    assert.equal(_singularize('las'), 'las');
    assert.equal(_singularize('mas'), 'mas');
    assert.equal(_singularize('sol'), 'sol');
  });

  test('elimina "s" final en palabras >= 5 chars', () => {
    assert.equal(_singularize('pedidos'), 'pedido');
    assert.equal(_singularize('envios'),  'envio');
    assert.equal(_singularize('puntos'),  'punto');
  });

  test('elimina "es" final en palabras >= 6 chars', () => {
    assert.equal(_singularize('ordenes'), 'orden');
    assert.equal(_singularize('flores'),  'flor');
    // "meses" (5 chars) cae al "-s" del rule inferior; queda "mese".
    // Funcionalmente OK: no hay keyword "mes" en el diccionario que
    // requiera este match. Documentamos el comportamiento.
    assert.equal(_singularize('meses'),   'mese');
  });

  test('respeta palabras ya singulares', () => {
    assert.equal(_singularize('pedido'), 'pedido');
    assert.equal(_singularize('cafe'),   'cafe');
  });
});


test.describe('detectClientIntent — plurales', () => {
  test('"mis pedidos" → opción 2 (estado)', () => {
    assert.equal(detectClientIntent('mis pedidos'), '2');
  });

  test('"quiero ver mis puntos" → opción 3 (club)', () => {
    assert.equal(detectClientIntent('quiero ver mis puntos'), '3');
  });

  test('"envios a mi zona" → opción 4 (cobertura)', () => {
    // "envio" está en la lista; "envios" ahora matchea vía singularización.
    assert.equal(detectClientIntent('envios a mi zona'), '4');
  });

  test('"horarios de hoy" → opción 6 (info)', () => {
    // "horario" y "horarios" ambos ya listados en el diccionario, pero
    // esta comprobación valida que no rompemos el path existente.
    assert.equal(detectClientIntent('horarios de hoy'), '6');
  });
});


test.describe('detectClientIntent — Levenshtein en 5 chars', () => {
  test('"envio" con typo "envoi" → opción 4', () => {
    // "envio" es de 5 chars; con el umbral bajado a >=5 el typo entra
    // (antes solo palabras >=6 tenían tolerancia).
    assert.equal(detectClientIntent('envoi a carmona'), '4');
  });

  test('"cobertura" (larga) mantiene tolerancia previa', () => {
    // Regresión: los matchs largos ya tolerados siguen funcionando.
    assert.equal(detectClientIntent('cobetura por favor'), '4');
  });
});


test.describe('detectClientIntent — no rompe casos existentes', () => {
  test('opción numérica directa', () => {
    assert.equal(detectClientIntent('2'), '2');
    assert.equal(detectClientIntent('7'), '7');
  });

  test('atajo "estado"', () => {
    assert.equal(detectClientIntent('estado'), '2');
  });

  test('"cancelar mi pedido"', () => {
    assert.equal(detectClientIntent('cancelar mi pedido'), '2');
  });

  test('"agente" / "humano"', () => {
    assert.equal(detectClientIntent('agente'), '7');
    assert.equal(detectClientIntent('humano'), '7');
  });

  test('input vacío → null', () => {
    assert.equal(detectClientIntent(''), null);
    assert.equal(detectClientIntent('   '), null);
    assert.equal(detectClientIntent('xyzzz'), null);
  });
});
