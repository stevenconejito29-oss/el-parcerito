'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');

const dbDir = fs.mkdtempSync(path.join(os.tmpdir(), 'oxidian-bot-test-'));
process.env.BOT_TEST_MODE = '1';
process.env.DB_DIR = dbDir;
process.env.NODE_ENV = 'test';
process.env.SIMULATE_EVO_SEND = 'true';
process.env.OXIDIAN_KEY = 'test-key-value';
process.env.BOT_PANEL_KEY = 'test-panel-key';
process.env.WEBHOOK_SECRET = 'test-webhook-secret';
process.env.OWNER_NUMBER = '34600000001';
process.env.SUPERADMINS = '34600000002,34600000003';

const { app, _test } = require('../bot');
const {
  db,
  assignHandoff,
  closeHumanChatByClient,
  createHandoffRequest,
  deliverQueuedTranscript,
  drainInboundMessages,
  extractText,
  getHandoff,
  handleAdminTakeWait,
  handleEvolutionEvent,
  persistInboundMessages,
  queueAssignedHandoffMessage,
  queueHandoffMessage,
  releaseHumanChat,
} = _test;

const adminA = '34600000001@s.whatsapp.net';
const adminB = '34600000002@s.whatsapp.net';
const clientA = '34610000001@s.whatsapp.net';
const clientB = '34610000002@s.whatsapp.net';

function clearState() {
  db.exec(`
    DELETE FROM handoff_messages;
    DELETE FROM handoffs;
    DELETE FROM sessions;
    DELETE FROM inbound_messages;
    DELETE FROM logs;
  `);
}

function adminSession(jid, pending = {}) {
  return {
    jid,
    nombre: 'Admin',
    role: 'admin',
    estado: 'admin_take_wait',
    carrito: [],
    pending,
    zona_id: null,
    active_client_jid: null,
  };
}

test.beforeEach(clearState);

test.after(() => {
  db.close();
  fs.rmSync(dbDir, { recursive: true, force: true });
});

test('un administrador no puede reclamar dos clientes activos', () => {
  createHandoffRequest(clientA);
  createHandoffRequest(clientB);

  assert.equal(assignHandoff(clientA, adminA).changes, 1);
  assert.equal(assignHandoff(clientB, adminA).changes, 0);
  assert.equal(getHandoff(clientA).admin_jid, adminA);
  assert.equal(getHandoff(clientB).admin_jid, null);
});

test('encolar exige que la asignacion siga vigente', () => {
  createHandoffRequest(clientA);
  assert.equal(assignHandoff(clientA, adminA).changes, 1);
  assert.ok(queueAssignedHandoffMessage(clientA, adminA, 'client', 'antes del cierre'));

  const closed = closeHumanChatByClient(clientA);
  assert.equal(closed.admin_jid, adminA);
  assert.equal(queueAssignedHandoffMessage(clientA, adminA, 'client', 'despues del cierre'), null);
  assert.equal(db.prepare('SELECT COUNT(*) AS c FROM handoff_messages').get().c, 0);
});

test('soltar un chat lo reencola sin borrar el historial', async () => {
  createHandoffRequest(clientA);
  assert.equal(assignHandoff(clientA, adminA).changes, 1);
  queueAssignedHandoffMessage(clientA, adminA, 'client', 'mensaje conservado');
  db.prepare(`
    INSERT INTO sessions (jid, role, estado, active_client_jid)
    VALUES (?, 'admin', 'admin_chat', ?)
  `).run(adminA, clientA);

  const released = await releaseHumanChat(adminA, clientA, false);

  assert.equal(released, true);
  assert.equal(getHandoff(clientA).admin_jid, null);
  assert.equal(db.prepare('SELECT COUNT(*) AS c FROM handoff_messages WHERE client_jid=?').get(clientA).c, 1);
  assert.equal(
    db.prepare('SELECT active_client_jid FROM sessions WHERE jid=?').get(adminA).active_client_jid,
    null,
  );
});

test('el comando release no reasigna inmediatamente el chat soltado', async () => {
  createHandoffRequest(clientA);
  assert.equal(assignHandoff(clientA, adminA).changes, 1);
  queueAssignedHandoffMessage(clientA, adminA, 'client', 'mensaje conservado');
  db.prepare(`
    INSERT INTO sessions (jid, role, estado, active_client_jid)
    VALUES (?, 'admin', 'admin_chat', ?)
  `).run(adminA, clientA);

  await handleEvolutionEvent({
    event: 'messages.upsert',
    data: {
      key: { id: 'release-command', remoteJid: adminA },
      pushName: 'Admin',
      message: { conversation: '!release' },
    },
  });

  assert.equal(getHandoff(clientA).admin_jid, null);
  assert.equal(
    db.prepare('SELECT active_client_jid FROM sessions WHERE jid=?').get(adminA).active_client_jid,
    null,
  );
  assert.equal(db.prepare('SELECT COUNT(*) AS c FROM handoff_messages WHERE client_jid=?').get(clientA).c, 1);
});

test('la seleccion numerica usa el snapshot mostrado al administrador', async () => {
  createHandoffRequest(clientA);
  createHandoffRequest(clientB);
  assert.equal(assignHandoff(clientA, adminB).changes, 1);

  const ses = adminSession(adminA, {
    handoff_client_jids: [clientA, clientB],
  });
  const result = await handleAdminTakeWait(adminA, ses, '2');

  assert.equal(result, true);
  assert.equal(getHandoff(clientB).admin_jid, adminA);
});

test('eventos inbound repetidos se persisten una sola vez', () => {
  const payload = {
    event: 'messages.upsert',
    data: {
      key: { id: 'stable-message-id', remoteJid: clientA },
      message: { conversation: 'hola' },
    },
  };

  assert.equal(persistInboundMessages(payload), 1);
  assert.equal(persistInboundMessages(payload), 0);
  assert.equal(db.prepare('SELECT COUNT(*) AS c FROM inbound_messages').get().c, 1);
});

test('un lote webhook mayor al maximo se persiste completo', () => {
  const messages = Array.from({ length: 31 }, (_, index) => ({
    key: { id: `batch-message-${index}`, remoteJid: clientA },
    message: { conversation: `mensaje ${index}` },
  }));

  assert.equal(persistInboundMessages({
    event: 'messages.upsert',
    data: { messages },
  }), messages.length);
  assert.equal(
    db.prepare('SELECT COUNT(*) AS c FROM inbound_messages').get().c,
    messages.length,
  );
});

test('los fallos del manejador se propagan y el drenador reintenta el inbound', async () => {
  const payload = {
    event: 'messages.upsert',
    data: {
      key: { id: 'retry-message-id', remoteJid: clientA },
      message: { conversation: 'reintentar' },
    },
  };
  assert.equal(persistInboundMessages(payload), 1);

  await drainInboundMessages(async event => {
    await handleEvolutionEvent(event, async () => {
      throw new Error('fallo dirigido');
    });
  });

  let row = db.prepare(`
    SELECT attempts, processed_at
    FROM inbound_messages
    WHERE message_id='retry-message-id'
  `).get();
  assert.equal(row.attempts, 1);
  assert.equal(row.processed_at, null);
  assert.equal(
    db.prepare(`
      SELECT COUNT(*) AS c FROM logs
      WHERE evento='inbound_process_fail'
        AND detalle LIKE '%retry-message-id attempt=1%'
    `).get().c,
    1,
  );

  await drainInboundMessages(async () => {});
  row = db.prepare(`
    SELECT attempts, processed_at
    FROM inbound_messages
    WHERE message_id='retry-message-id'
  `).get();
  assert.equal(row.attempts, 2);
  assert.ok(row.processed_at);
});

test('un inbound corrupto pasa a dead letter y no bloquea indefinidamente', async () => {
  db.prepare(`
    INSERT INTO inbound_messages (message_id, payload_json)
    VALUES ('broken', '{')
  `).run();

  for (let attempt = 0; attempt < 5; attempt++) {
    await drainInboundMessages();
  }

  const row = db.prepare(`
    SELECT attempts, processed_at FROM inbound_messages WHERE message_id='broken'
  `).get();
  assert.equal(row.attempts, 5);
  assert.ok(row.processed_at);
  assert.equal(
    db.prepare(`
      SELECT COUNT(*) AS c FROM logs WHERE evento='inbound_dead_letter'
    `).get().c,
    1,
  );
});

test('los adjuntos sin texto generan una descripcion util', () => {
  assert.equal(
    extractText({
      message: {
        documentMessage: {
          fileName: 'factura.pdf',
          mimetype: 'application/pdf',
        },
      },
    }),
    '[Adjunto recibido: documento · factura.pdf · application/pdf]',
  );
  assert.equal(
    extractText({ message: { audioMessage: { seconds: 12 } } }),
    '[Adjunto recibido: audio · 12s]',
  );
});

test('la entrega fragmentada marca la fila solo al completar todos los fragmentos', async () => {
  createHandoffRequest(clientA);
  assert.equal(assignHandoff(clientA, adminA).changes, 1);
  const inserted = queueHandoffMessage(clientA, 'client', 'x'.repeat(4096));

  assert.equal(await deliverQueuedTranscript(clientA, adminA), true);
  const row = db.prepare(`
    SELECT delivered_at, delivery_cursor, attempts
    FROM handoff_messages WHERE id=?
  `).get(inserted.lastInsertRowid);
  assert.ok(row.delivered_at);
  assert.equal(row.delivery_cursor, 2);
  assert.equal(row.attempts, 0);
});

test('un reintento fragmentado continua desde el ultimo fragmento confirmado', async () => {
  createHandoffRequest(clientA);
  assert.equal(assignHandoff(clientA, adminA).changes, 1);
  const inserted = queueHandoffMessage(clientA, 'client', 'y'.repeat(4096));
  let firstAttemptCalls = 0;

  const firstResult = await deliverQueuedTranscript(clientA, adminA, async () => {
    firstAttemptCalls++;
    return firstAttemptCalls !== 2;
  });
  assert.equal(firstResult, false);

  let row = db.prepare(`
    SELECT delivered_at, delivery_cursor, attempts, next_attempt_at
    FROM handoff_messages WHERE id=?
  `).get(inserted.lastInsertRowid);
  assert.equal(row.delivered_at, null);
  assert.equal(row.delivery_cursor, 1);
  assert.equal(row.attempts, 1);
  assert.ok(row.next_attempt_at);

  db.prepare('UPDATE handoff_messages SET next_attempt_at=NULL WHERE id=?')
    .run(inserted.lastInsertRowid);
  let retryCalls = 0;
  assert.equal(
    await deliverQueuedTranscript(clientA, adminA, async () => {
      retryCalls++;
      return true;
    }),
    true,
  );

  row = db.prepare(`
    SELECT delivered_at, delivery_cursor FROM handoff_messages WHERE id=?
  `).get(inserted.lastInsertRowid);
  assert.ok(row.delivered_at);
  assert.equal(row.delivery_cursor, 2);
  assert.equal(retryCalls, 1);
});

test('status usa solo la clave de panel y message solo la clave de Oxidian', async () => {
  const server = app.listen(0, '127.0.0.1');
  await new Promise((resolve, reject) => {
    server.once('listening', resolve);
    server.once('error', reject);
  });
  const { port } = server.address();
  const baseUrl = `http://127.0.0.1:${port}`;

  try {
    let response = await fetch(`${baseUrl}/api/status`, {
      headers: { 'X-API-Key': process.env.OXIDIAN_KEY },
    });
    assert.equal(response.status, 403);

    response = await fetch(`${baseUrl}/api/status`, {
      headers: { 'X-Panel-Key': process.env.BOT_PANEL_KEY },
    });
    assert.equal(response.status, 200);

    response = await fetch(`${baseUrl}/api/bot/message`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Panel-Key': process.env.BOT_PANEL_KEY,
      },
      body: JSON.stringify({
        telefono: '34610000009',
        mensaje: 'solo Oxidian',
      }),
    });
    assert.equal(response.status, 403);

    response = await fetch(`${baseUrl}/api/bot/message`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-API-Key': process.env.OXIDIAN_KEY,
      },
      body: JSON.stringify({
        telefono: '34610000009',
        mensaje: 'x'.repeat(4096),
      }),
    });
    assert.equal(response.status, 200);

    response = await fetch(`${baseUrl}/api/bot/message`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-API-Key': process.env.OXIDIAN_KEY,
      },
      body: JSON.stringify({
        telefono: '34610000009',
        mensaje: 'x'.repeat(4097),
      }),
    });
    assert.equal(response.status, 400);
  } finally {
    await new Promise(resolve => server.close(resolve));
  }
});

test('la configuracion runtime de admins reencola chats al retirar un numero', async () => {
  clearState();
  const server = app.listen(0, '127.0.0.1');
  await new Promise((resolve, reject) => {
    server.once('listening', resolve);
    server.once('error', reject);
  });
  const { port } = server.address();
  const baseUrl = `http://127.0.0.1:${port}`;
  const runtimePhone = '34600000004';
  const runtimeJid = `${runtimePhone}@s.whatsapp.net`;

  try {
    let response = await fetch(`${baseUrl}/api/admins/config`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Panel-Key': process.env.BOT_PANEL_KEY,
      },
      body: JSON.stringify({ admins: [runtimePhone] }),
    });
    assert.equal(response.status, 200);
    let payload = await response.json();
    assert.deepEqual(payload.admins.runtime, [runtimePhone]);

    createHandoffRequest(clientA);
    assert.equal(assignHandoff(clientA, runtimeJid).changes, 1);
    db.prepare(`
      INSERT INTO sessions (jid, role, estado, active_client_jid)
      VALUES (?, 'admin', 'admin_chat', ?)
    `).run(runtimeJid, clientA);

    response = await fetch(`${baseUrl}/api/admins/config`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Panel-Key': process.env.BOT_PANEL_KEY,
      },
      body: JSON.stringify({ admins: [] }),
    });
    assert.equal(response.status, 200);
    payload = await response.json();
    assert.deepEqual(payload.admins.runtime, []);
    assert.equal(getHandoff(clientA).admin_jid, null);
    assert.equal(
      db.prepare(`SELECT COUNT(*) AS c FROM sessions WHERE jid=?`).get(runtimeJid).c,
      0,
    );
  } finally {
    server.close();
    db.prepare(`DELETE FROM config WHERE key='runtime_admins'`).run();
    clearState();
  }
});
