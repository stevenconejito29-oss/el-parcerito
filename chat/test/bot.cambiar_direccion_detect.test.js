'use strict';

/**
 * Tests del detector esCambiarDireccion — asegura que:
 *   - matchea las frases naturales frecuentes del cliente
 *   - NO matchea consultas de estado ni menciones inocentes
 *
 * El backend de cambio de dirección se prueba manualmente contra
 * pedidos reales; este test cubre solo la puerta de entrada del bot.
 */

const test = require('node:test');
const assert = require('node:assert/strict');
const { _test } = require('../bot');
const { esCambiarDireccion } = _test;


test.describe('esCambiarDireccion — matchea frases naturales', () => {
  test('"cambiar la dirección"', () => {
    assert.equal(esCambiarDireccion('cambiar la dirección'), true);
    assert.equal(esCambiarDireccion('quiero cambiar la direccion'), true);
    assert.equal(esCambiarDireccion('cambiar direccion, por favor'), true);
  });

  test('"cambiar el domicilio"', () => {
    assert.equal(esCambiarDireccion('cambiar el domicilio'), true);
    assert.equal(esCambiarDireccion('cambia mi domicilio'), true);
  });

  test('"modificar la dirección"', () => {
    assert.equal(esCambiarDireccion('modificar la dirección del pedido'), true);
  });

  test('"otra dirección"', () => {
    assert.equal(esCambiarDireccion('lo entregas en otra dirección'), true);
    assert.equal(esCambiarDireccion('mejor en otra direccion'), true);
  });

  test('"no es esa la dirección"', () => {
    assert.equal(esCambiarDireccion('no es esa la dirección'), true);
    assert.equal(esCambiarDireccion('no es esa direccion, corrige'), true);
  });

  test('"me equivoqué de dirección"', () => {
    assert.equal(esCambiarDireccion('me equivoqué de dirección'), true);
    assert.equal(esCambiarDireccion('me equivoque de direccion'), true);
  });

  test('"mejor me lo entregan en..."', () => {
    assert.equal(esCambiarDireccion('mejor me lo entregan en la oficina'), true);
    assert.equal(esCambiarDireccion('mejor me lo mandan a otra casa'), true);
  });
});


test.describe('esCambiarDireccion — NO matchea falsos positivos', () => {
  test('consulta de estado genérica', () => {
    assert.equal(esCambiarDireccion('donde está mi pedido'), false);
    assert.equal(esCambiarDireccion('cuánto tarda el pedido'), false);
  });

  test('menciones inocentes de dirección', () => {
    // El cliente pregunta por la dirección del negocio, no quiere cambiar la suya.
    assert.equal(esCambiarDireccion('cuál es tu dirección'), false);
    assert.equal(esCambiarDireccion('dirección del local'), false);
  });

  test('saludo puro', () => {
    assert.equal(esCambiarDireccion('hola'), false);
    assert.equal(esCambiarDireccion('buenos días'), false);
  });

  test('input vacío o basura', () => {
    assert.equal(esCambiarDireccion(''), false);
    assert.equal(esCambiarDireccion(null), false);
    assert.equal(esCambiarDireccion(undefined), false);
    assert.equal(esCambiarDireccion('asdfghjkl'), false);
  });
});
