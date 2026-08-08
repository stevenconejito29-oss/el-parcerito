'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const variantPicker = require('../utils/variantPicker');


test.beforeEach(() => variantPicker._reset());


test('pickVariant devuelve null con pool vacío o inválido', () => {
  assert.equal(variantPicker.pickVariant([], 'k'), null);
  assert.equal(variantPicker.pickVariant(null, 'k'), null);
  assert.equal(variantPicker.pickVariant(undefined, 'k'), null);
});


test('pickVariant devuelve el único elemento cuando pool.length === 1', () => {
  assert.equal(variantPicker.pickVariant(['A'], 'k'), 'A');
  assert.equal(variantPicker.pickVariant(['A'], 'k'), 'A');  // permitido repetir
});


test('pickVariant nunca repite el último enviado bajo la misma key', () => {
  const pool = ['A', 'B', 'C'];
  const key = 'jid1::horario';
  let last = variantPicker.pickVariant(pool, key);
  for (let i = 0; i < 20; i++) {
    const next = variantPicker.pickVariant(pool, key);
    assert.notEqual(next, last, `iteración ${i}: no debe repetir ${last}`);
    last = next;
  }
});


test('pickVariant con pool.length === 2 alterna estrictamente', () => {
  const pool = ['ping', 'pong'];
  const key = 'jid2::topic';
  const seq = [];
  for (let i = 0; i < 6; i++) seq.push(variantPicker.pickVariant(pool, key));
  // Cada elemento debe ser distinto del anterior
  for (let i = 1; i < seq.length; i++) {
    assert.notEqual(seq[i], seq[i - 1]);
  }
});


test('pickVariant es independiente por key (jid distinto)', () => {
  const pool = ['X', 'Y'];
  // Cliente A recibió X. Cliente B nunca ha recibido nada: puede recibir X.
  variantPicker.pickVariant(pool, 'jidA::t');
  const jidB = variantPicker.pickVariant(pool, 'jidB::t');
  assert.ok(pool.includes(jidB));
});


test('resolveAnswer con string retorna el string tal cual (retro-compat)', () => {
  const out = variantPicker.resolveAnswer('respuesta fija', 'jid', 'faq');
  assert.equal(out, 'respuesta fija');
});


test('resolveAnswer con null retorna null', () => {
  assert.equal(variantPicker.resolveAnswer(null, 'jid', 'faq'), null);
  assert.equal(variantPicker.resolveAnswer(undefined, 'jid', 'faq'), null);
});


test('resolveAnswer con array llama a pickVariant', () => {
  const pool = ['V1', 'V2', 'V3'];
  const out = variantPicker.resolveAnswer(pool, 'jid', 'faq');
  assert.ok(pool.includes(out));
});


test('resolveAnswer distingue keys distintas correctamente', () => {
  const pool = ['A', 'B'];
  // Mismo jid, distintos faqNames → memoria separada
  const first = variantPicker.resolveAnswer(pool, 'jid1', 'horario');
  const second = variantPicker.resolveAnswer(pool, 'jid1', 'direccion');
  // 'direccion' no tiene memoria previa, así que puede coincidir o no.
  // Lo que sí es cierto: 'horario' bajo jid1 no debe repetir 'first'.
  const third = variantPicker.resolveAnswer(pool, 'jid1', 'horario');
  assert.notEqual(third, first);
  assert.ok(pool.includes(second));
});


test('distribución sobre muchas llamadas: no es determinista', () => {
  // Con pool de 3 y 100 iteraciones, cada variante debería salir varias
  // veces. Test suave para detectar bugs de determinismo.
  const pool = ['A', 'B', 'C'];
  const counts = { A: 0, B: 0, C: 0 };
  for (let i = 0; i < 100; i++) {
    // Key distinto en cada iteración para no activar la memoria de
    // exclusión — así medimos aleatoriedad pura.
    const v = variantPicker.pickVariant(pool, `k${i}`);
    counts[v]++;
  }
  // Ninguna variante debería quedar en 0 (probabilidad ≈ 0)
  assert.ok(counts.A > 0 && counts.B > 0 && counts.C > 0,
    `distribución degenerada: ${JSON.stringify(counts)}`);
});
