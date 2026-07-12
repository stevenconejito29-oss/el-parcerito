'use strict';
/**
 * Tests unitarios del adaptador Evolution/Baileys.
 *
 * Cada fixture cubre una variante que hemos observado en producción —
 * este archivo es la trampa que atrapa un cambio de schema de Evolution
 * antes de llegar a prod. Ampliar aquí cada vez que veamos un payload
 * nuevo en los logs.
 */

const assert = require('node:assert/strict');
const test = require('node:test');

const evolution = require('../evolution');

// ─── extractText ────────────────────────────────────────────────────────

test('extractText devuelve conversation cuando es un mensaje de texto plano', () => {
  const msg = { message: { conversation: '  Hola equipo  ' } };
  assert.equal(evolution.extractText(msg), 'Hola equipo');
});

test('extractText prefiere conversation sobre extendedTextMessage.text', () => {
  const msg = {
    message: {
      conversation: 'primero',
      extendedTextMessage: { text: 'segundo' },
    },
  };
  assert.equal(evolution.extractText(msg), 'primero');
});

test('extractText usa extendedTextMessage.text cuando no hay conversation', () => {
  const msg = { message: { extendedTextMessage: { text: 'Con enlace: https://ejemplo.com' } } };
  assert.equal(evolution.extractText(msg), 'Con enlace: https://ejemplo.com');
});

test('extractText toma el caption de imagen si no hay texto', () => {
  const msg = { message: { imageMessage: { caption: 'Mira esta foto' } } };
  assert.equal(evolution.extractText(msg), 'Mira esta foto');
});

test('extractText etiqueta el adjunto cuando llega sin caption', () => {
  const msg = {
    message: {
      audioMessage: { seconds: 12, mimetype: 'audio/ogg' },
    },
  };
  const out = evolution.extractText(msg);
  assert.match(out, /Adjunto recibido: audio/);
  assert.match(out, /audio\/ogg/);
  assert.match(out, /12s/);
});

test('extractText devuelve "" cuando no hay ni texto ni adjunto reconocible', () => {
  assert.equal(evolution.extractText({ message: {} }), '');
  assert.equal(evolution.extractText({}), '');
  assert.equal(evolution.extractText(null), '');
});

// ─── extractQrDataUrl ───────────────────────────────────────────────────

test('extractQrDataUrl detecta un data URL ya formado en payload.qrcode', () => {
  const dataUrl = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkAAIAAAoAAv/lxKUAAAAASUVORK5CYII=';
  const out = evolution.extractQrDataUrl({ qrcode: dataUrl });
  assert.equal(out, dataUrl);
});

test('extractQrDataUrl envuelve base64 desnudo con prefijo PNG', () => {
  const raw = 'A'.repeat(250);
  const out = evolution.extractQrDataUrl({ data: { qrcode: { base64: raw } } });
  assert.ok(out.startsWith('data:image/png;base64,'));
  assert.ok(out.endsWith(raw));
});

test('extractQrDataUrl devuelve null si no encuentra QR en ninguna variante', () => {
  assert.equal(evolution.extractQrDataUrl({}), null);
  assert.equal(evolution.extractQrDataUrl(null), null);
  assert.equal(evolution.extractQrDataUrl({ data: {} }), null);
});

// ─── getMessagesFromPayload ─────────────────────────────────────────────

test('getMessagesFromPayload devuelve array de messages cuando viene bien formado', () => {
  const payload = {
    event: 'messages.upsert',
    data: { messages: [{ key: { id: '1' } }, { key: { id: '2' } }] },
  };
  assert.equal(evolution.getMessagesFromPayload(payload).length, 2);
});

test('getMessagesFromPayload trata data como mensaje único si no hay messages[]', () => {
  const payload = {
    event: 'messages.upsert',
    data: { key: { id: 'x' }, message: { conversation: 'hi' } },
  };
  const out = evolution.getMessagesFromPayload(payload);
  assert.equal(out.length, 1);
  assert.equal(out[0].key.id, 'x');
});

test('getMessagesFromPayload filtra huecos (null) del array', () => {
  const payload = {
    event: 'messages.upsert',
    data: { messages: [null, { key: { id: 'a' } }, null] },
  };
  assert.equal(evolution.getMessagesFromPayload(payload).length, 1);
});

test('getMessagesFromPayload devuelve [] si el evento no es messages.upsert', () => {
  assert.deepEqual(evolution.getMessagesFromPayload({ event: 'connection.update', data: {} }), []);
  assert.deepEqual(evolution.getMessagesFromPayload({}), []);
  assert.deepEqual(evolution.getMessagesFromPayload(null), []);
});

// ─── getMessageMeta ─────────────────────────────────────────────────────

test('getMessageMeta extrae jid, id y sender del formato Baileys estándar', () => {
  const msg = {
    key: { remoteJid: '34600123456@s.whatsapp.net', id: 'ABC123', fromMe: false },
    pushName: 'Juan',
  };
  const meta = evolution.getMessageMeta(msg);
  assert.equal(meta.jid, '34600123456@s.whatsapp.net');
  assert.equal(meta.messageId, 'ABC123');
  assert.equal(meta.senderName, 'Juan');
  assert.equal(meta.isFromMe, false);
  assert.equal(meta.isGroup, false);
});

test('getMessageMeta marca isGroup=true para JIDs terminados en @g.us', () => {
  const meta = evolution.getMessageMeta({ key: { remoteJid: '120363000000000000@g.us' } });
  assert.equal(meta.isGroup, true);
});

test('getMessageMeta cae al participant si no hay pushName', () => {
  const meta = evolution.getMessageMeta({
    key: { remoteJid: '34600000001@s.whatsapp.net', participant: '34600000001@s.whatsapp.net' },
  });
  assert.equal(meta.senderName, '34600000001@s.whatsapp.net');
});

test('getMessageMeta nunca lanza aunque el mensaje esté vacío', () => {
  const meta = evolution.getMessageMeta(null);
  assert.equal(meta.jid, null);
  assert.equal(meta.messageId, null);
  assert.equal(meta.senderName, '');
  assert.equal(meta.isFromMe, false);
  assert.equal(meta.isGroup, false);
});

test('getMessageMeta.isFromMe es siempre boolean (nunca undefined)', () => {
  assert.equal(evolution.getMessageMeta({ key: {} }).isFromMe, false);
  assert.equal(evolution.getMessageMeta({}).isFromMe, false);
});

// ─── Constantes de evento ───────────────────────────────────────────────

test('Las constantes de evento coinciden con los nombres oficiales de Evolution', () => {
  assert.equal(evolution.EVENT_MESSAGE_UPSERT, 'messages.upsert');
  assert.equal(evolution.EVENT_CONNECTION_UPDATE, 'connection.update');
  assert.equal(evolution.EVENT_QRCODE_UPDATED, 'qrcode.updated');
});
