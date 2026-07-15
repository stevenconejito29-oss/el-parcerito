'use strict';
/**
 * Tests de que `handleAdminConfirm` no vuelve a abrir/cerrar la tienda si
 * el estado actual ya coincide con la acción pedida.
 *
 * Antes: cada `SI` disparaba un POST /admin/tienda al backend, aunque la
 * tienda ya estuviera en ese estado. Genera ruido en el log de eventos
 * de auditoría y arriesga carreras si el admin da doble tap por prisa.
 *
 * Ahora: `_estadoTiendaBestEffort` lee el estado real y aborta con
 * mensaje amable si no hay nada que cambiar. Si la lectura falla,
 * fail-open (ejecuta igual, mejor operar que bloquear).
 *
 * El test levanta un servidor HTTP local que simula el backend Oxidian y
 * apunta el bot a `http://127.0.0.1:<port>` vía OXIDIAN_URL.
 */

const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');

const dbDir = fs.mkdtempSync(path.join(os.tmpdir(), 'oxidian-bot-tienda-'));

// Env DEBE fijarse antes del require de bot.js (los defaults se leen
// una sola vez al cargar el módulo). En particular, cooldown 0 para no
// bloquear tests que repiten la misma acción rápido.
process.env.BOT_TEST_MODE = '1';
process.env.DB_DIR = dbDir;
process.env.NODE_ENV = 'test';
process.env.SIMULATE_EVO_SEND = 'true';
process.env.OXIDIAN_KEY = 'k';
process.env.BOT_PANEL_KEY = 'k';
process.env.WEBHOOK_SECRET = 'w';
process.env.OWNER_NUMBER = '34600000001';
process.env.SUPERADMINS = '34600000002';
process.env.BOT_MIN_ADMIN_ACTION_MS = '0';

const server = http.createServer();
let serverAddr = null;
let currentEstado = 'abierta';
let postCount = 0;

server.on('request', (req, res) => {
  if (req.url.startsWith('/api/bot/admin/tienda')) {
    if (req.method === 'GET') {
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: true, estado: currentEstado, mensaje_cierre: '' }));
    } else if (req.method === 'POST') {
      postCount++;
      let body = '';
      req.on('data', c => body += c);
      req.on('end', () => {
        try {
          const p = JSON.parse(body || '{}');
          currentEstado = p.forzar_cerrada ? 'cerrada' : 'abierta';
        } catch (_) {}
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ ok: true, estado: currentEstado }));
      });
    }
  } else {
    res.writeHead(404); res.end('{}');
  }
});

test.before(async () => {
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  serverAddr = server.address();
  // `OXIDIAN_URL` es const capturada en require. Redirigimos vía setCfg,
  // que `getOxidianUrl` consulta en runtime.
  setCfg('oxidian_url', `http://127.0.0.1:${serverAddr.port}`);
  // OWNER_JID debe reconocerse como super_admin para pasar `adminCan`.
  // Registramos el perfil vía cfg (misma vía que produce el sync desde
  // Oxidian). Sin esto, todas las acciones caen a "no tienes permiso".
  setCfg('whatsapp_role_profiles', JSON.stringify([
    { telefono: '34600000001', rol: 'super_admin', capabilities: ['store', 'emergency'] },
  ]));
});

test.after(async () => {
  await new Promise((r) => server.close(r));
});

const { _test } = require('../bot');
const { handleAdminConfirm, getSesion, setSesion, setCfg } = _test;

const OWNER_JID = '34600000001@s.whatsapp.net';

function _resetPost() { postCount = 0; }

test('close_store cuando ya está cerrada NO llama al backend', async () => {
  currentEstado = 'cerrada'; _resetPost();
  setSesion(OWNER_JID, {
    estado: 'admin_confirm',
    pending: { action: 'close_store', _asked_at: Date.now() },
  });
  await handleAdminConfirm(OWNER_JID, getSesion(OWNER_JID), 'si');
  assert.equal(postCount, 0, 'no debería haber hecho POST');
  assert.equal(getSesion(OWNER_JID).estado, 'admin_menu');
});

test('open_store cuando ya está abierta NO llama al backend', async () => {
  currentEstado = 'abierta'; _resetPost();
  setSesion(OWNER_JID, {
    estado: 'admin_confirm',
    pending: { action: 'open_store', _asked_at: Date.now() },
  });
  await handleAdminConfirm(OWNER_JID, getSesion(OWNER_JID), 'si');
  assert.equal(postCount, 0);
  assert.equal(getSesion(OWNER_JID).estado, 'admin_menu');
});

test('close_store cuando está abierta SÍ llama al backend', async () => {
  currentEstado = 'abierta'; _resetPost();
  setSesion(OWNER_JID, {
    estado: 'admin_confirm',
    pending: { action: 'close_store', _asked_at: Date.now() },
  });
  await handleAdminConfirm(OWNER_JID, getSesion(OWNER_JID), 'si');
  assert.equal(postCount, 1);
  assert.equal(currentEstado, 'cerrada');
});

test('open_store cuando está cerrada SÍ llama al backend', async () => {
  currentEstado = 'cerrada'; _resetPost();
  setSesion(OWNER_JID, {
    estado: 'admin_confirm',
    pending: { action: 'open_store', _asked_at: Date.now() },
  });
  await handleAdminConfirm(OWNER_JID, getSesion(OWNER_JID), 'si');
  assert.equal(postCount, 1);
  assert.equal(currentEstado, 'abierta');
});
