'use strict';

process.env.BOT_TEST_MODE = '1';
const test = require('node:test');
const assert = require('node:assert/strict');
const { _test } = require('../bot');

const ctx = {
  jid: '34600000000@s.whatsapp.net',
  negocio: 'Tienda Demo',
  telefono: '+34600111222',
  direccion: 'Calle Mayor 1',
  ciudad: 'Carmona',
  horario: 'Abrimos de 10:00 a 22:00',
  tiendaUrl: 'https://tienda.invalid',
  tienda_sin_sede: false,
};

test('FAQ ampliada cubre incidencias, factura, entrega, notas, contacto y privacidad', async () => {
  const cases = [
    ['necesito una factura con IVA', 'factura_ticket'],
    ['para qué sirve el código de entrega', 'codigo_entrega_seguridad'],
    ['mi pedido llegó incompleto', 'pedido_incompleto_incorrecto'],
    ['puedo pedir sin cubiertos y con servilletas', 'cubiertos_salsas_instrucciones'],
    ['cuál es el teléfono del local', 'telefono'],
    ['quiero borrar mis datos', 'privacidad_datos'],
  ];
  for (const [message, expected] of cases) {
    const result = await _test.tryCannedFAQ(message, ctx);
    assert.equal(result?.name, expected, message);
    assert.ok(result?.text.length > 20, message);
  }
});

test('las respuestas sensibles derivan a flujos seguros y no inventan datos', async () => {
  const invoice = await _test.tryCannedFAQ('quiero factura', ctx);
  const incident = await _test.tryCannedFAQ('falta un producto', ctx);
  const code = await _test.tryCannedFAQ('me llegó un código de entrega', ctx);
  assert.match(invoice.text, /AGENTE/);
  assert.match(incident.text, /ESTADO/);
  assert.match(code.text, /únicamente|solo/i);
});
