'use strict';
require('dotenv').config();

const express  = require('express');
const path     = require('path');
const fs       = require('fs');
const crypto   = require('crypto');
const Database = require('better-sqlite3');

// ─── CONFIG ───────────────────────────────────────────────────────────────────
const EVO_URL        = (process.env.EVOLUTION_API_URL  || 'http://localhost:8080').replace(/\/$/, '');
const EVO_KEY        = process.env.EVOLUTION_API_KEY   || '';
const SIMULATE_EVO_SEND = process.env.SIMULATE_EVO_SEND === undefined
  ? (process.env.NODE_ENV !== 'production')
  : ['1', 'true', 'yes', 'si', 'sí', 'on'].includes(String(process.env.SIMULATE_EVO_SEND).trim().toLowerCase());
const EVO_INSTANCE   = process.env.EVOLUTION_INSTANCE  || 'oxidian';
const BOT_OXIDIAN_URL = (process.env.BOT_OXIDIAN_URL || '').replace(/\/$/, '');
const OXIDIAN_URL    = (process.env.OXIDIAN_URL        || 'http://localhost:5000').replace(/\/$/, '');
const OXIDIAN_KEY    = process.env.OXIDIAN_KEY         || '';
const BOT_PANEL_KEY  = process.env.BOT_PANEL_KEY       || '';
const TIENDA_URL     = (process.env.TIENDA_URL         || OXIDIAN_URL).replace(/\/$/, '');
const WEBHOOK_SECRET = process.env.WEBHOOK_SECRET      || '';
const NEGOCIO        = process.env.NEGOCIO || process.env.NOMBRE_NEGOCIO || 'Mi tienda';
const OWNER_NUMBER   = (process.env.OWNER_NUMBER       || '').replace(/\D/g, '');
const SUPERADMINS_RAW = process.env.SUPERADMINS || '';
const SUPERADMINS = SUPERADMINS_RAW.split(',').map(s => String(s||'').replace(/\D/g, '')).filter(Boolean);
const HOST           = process.env.HOST || process.env.BOT_HOST || '127.0.0.1';
const PORT           = parseInt(process.env.PORT       || '3000', 10);
const SESSION_TTL    = parseInt(process.env.SESSION_TIMEOUT_MIN || '45', 10) * 60_000;
const messageQueues = new Map();
const lastInboundAt = new Map();
const lastAdminActionAt = new Map();
const inboundBuckets = new Map();
const blockedInboundUntil = new Map();
const outboundBuckets = new Map();
const recentOutboundTexts = new Map();
const apiBuckets = new Map();
let lastOutboundAt = 0;
const MIN_INBOUND_MS = parseInt(process.env.BOT_MIN_INBOUND_MS || '900', 10);
const MIN_ADMIN_ACTION_MS = parseInt(process.env.BOT_MIN_ADMIN_ACTION_MS || '2500', 10);
const MIN_OUTBOUND_MS = parseInt(process.env.BOT_MIN_OUTBOUND_MS || '850', 10);
const MAX_MESSAGE_CHARS = 4096;
const MAX_OUTBOUND_CHARS = MAX_MESSAGE_CHARS;
const INBOUND_WINDOW_MS = parseInt(process.env.BOT_INBOUND_WINDOW_MS || '60000', 10);
const MAX_INBOUND_PER_WINDOW = parseInt(process.env.BOT_MAX_INBOUND_PER_WINDOW || '18', 10);
const INBOUND_BLOCK_MS = parseInt(process.env.BOT_INBOUND_BLOCK_MS || '600000', 10);
const OUTBOUND_WINDOW_MS = parseInt(process.env.BOT_OUTBOUND_WINDOW_MS || '3600000', 10);
const MAX_OUTBOUND_PER_TARGET = parseInt(process.env.BOT_MAX_OUTBOUND_PER_TARGET || '45', 10);
const DUPLICATE_OUTBOUND_MS = parseInt(process.env.BOT_DUPLICATE_OUTBOUND_MS || '15000', 10);
const MAX_BROADCAST_MESSAGES = parseInt(process.env.BOT_MAX_BROADCAST_MESSAGES || '20', 10);
const MAX_WEBHOOK_MESSAGES = Math.max(
  1,
  parseInt(process.env.BOT_MAX_WEBHOOK_MESSAGES || '25', 10) || 25,
);
const API_WINDOW_MS = parseInt(process.env.BOT_API_WINDOW_MS || '60000', 10);
const MAX_API_HITS_PER_WINDOW = parseInt(process.env.BOT_MAX_API_HITS_PER_WINDOW || '120', 10);
const ADMIN_ACTIVE_WINDOW_SEC = parseInt(process.env.BOT_ADMIN_ACTIVE_MIN || '15', 10) * 60;
const HANDOFF_LEASE_SEC = parseInt(process.env.BOT_HANDOFF_LEASE_MIN || '30', 10) * 60;

// TODO: internacionalizar los textos de menú cuando el negocio necesite múltiples idiomas.
if (!process.env.OXIDIAN_KEY) {
  console.error('[CRÍTICO] OXIDIAN_KEY no configurada. Las llamadas a Oxidian fallarán.');
  if (process.env.NODE_ENV === 'production') process.exit(1);
}
if (!WEBHOOK_SECRET) {
  console.warn('[AVISO] WEBHOOK_SECRET no configurado. El webhook acepta peticiones sin autenticación.');
  if (process.env.NODE_ENV === 'production') {
    console.error('[CRÍTICO] Configura WEBHOOK_SECRET en producción.');
    process.exit(1);
  }
}

let lastQrDataUrl = null;
let lastQrAt = 0;

function requireApiKey(req, res, opts = {}) {
  const apiKey = opts.panel
    ? (req.headers['x-panel-key'] || req.headers['x-api-key'] || '')
    : (req.headers['x-api-key'] || req.headers['x-bot-key'] || '');
  const expected = opts.panel ? getPanelKey() : getOxidianKey();
  if (!expected) {
    res.status(503).json({
      ok: false,
      error: opts.panel ? 'panel key not configured' : 'api key not configured',
    });
    return false;
  }
  if (!apiKey || apiKey !== expected) {
    res.status(403).json({ ok: false, error: 'invalid api key' });
    return false;
  }
  return true;
}

function normalizePhone(value) {
  let digits = String(value || '').replace(/\D/g, '');
  if (digits.startsWith('00')) digits = digits.slice(2);
  const countryCode = String(
    cfg('whatsapp_country_code', process.env.WHATSAPP_COUNTRY_CODE || ''),
  ).replace(/\D/g, '');
  if (countryCode && digits.length <= 10 && !digits.startsWith(countryCode)) {
    digits = `${countryCode}${digits}`;
  }
  return digits;
}

function asQrDataUrl(value) {
  if (!value || typeof value !== 'string') return null;
  const raw = value.trim();
  if (!raw) return null;
  if (raw.startsWith('data:image/')) return raw;
  if (raw.length > 200 && /^[A-Za-z0-9+/=\r\n]+$/.test(raw)) {
    return `data:image/png;base64,${raw.replace(/\s+/g, '')}`;
  }
  return null;
}

function extractQrDataUrl(payload) {
  const candidates = [
    payload?.qrcode,
    payload?.qrCode,
    payload?.qr,
    payload?.base64,
    payload?.code,
    payload?.data?.qrcode,
    payload?.data?.qrCode,
    payload?.data?.qr,
    payload?.data?.base64,
    payload?.data?.code,
    payload?.qrcode?.base64,
    payload?.qrcode?.code,
    payload?.data?.qrcode?.base64,
    payload?.data?.qrcode?.code,
    payload?.data?.qrCode?.base64,
    payload?.data?.qrCode?.code,
  ];
  for (const candidate of candidates) {
    const dataUrl = asQrDataUrl(candidate);
    if (dataUrl) return dataUrl;
  }
  return null;
}

async function refreshEvolutionQr() {
  const evolutionKey = getEvolutionKey();
  if (!evolutionKey) return null;
  try {
    const r = await fetch(`${getEvolutionUrl()}/instance/connect/${getEvolutionInstance()}`, {
      headers: { apikey: evolutionKey },
      signal: AbortSignal.timeout(6000),
    });
    const d = await r.json().catch(() => ({}));
    const qr = extractQrDataUrl(d);
    if (qr) {
      lastQrDataUrl = qr;
      lastQrAt = Date.now();
      log('info', 'qr_refreshed', 'QR actualizado desde Evolution');
    }
    return qr;
  } catch (e) {
    log('warn', 'qr_refresh_fail', String(e));
    return null;
  }
}

// ─── DATABASE ─────────────────────────────────────────────────────────────────
const DB_DIR = process.env.DB_DIR || path.resolve(__dirname, '..', 'db');
fs.mkdirSync(DB_DIR, { recursive: true });
const db = new Database(path.join(DB_DIR, 'bot.db'));
db.pragma('journal_mode = WAL');

db.exec(`
  CREATE TABLE IF NOT EXISTS sessions (
    jid        TEXT PRIMARY KEY,
    nombre     TEXT,
    role       TEXT DEFAULT 'client',
    estado     TEXT DEFAULT 'idle',
    carrito    TEXT DEFAULT '[]',
    pending_json TEXT DEFAULT '{}',
    zona_id    INTEGER,
    bar_id     INTEGER,
    bar_nombre TEXT,
    active_client_jid TEXT,
    updated_at INTEGER DEFAULT (unixepoch())
  );
  CREATE TABLE IF NOT EXISTS config (
    key   TEXT PRIMARY KEY,
    value TEXT
  );
  CREATE TABLE IF NOT EXISTS productos_cache (
    id               INTEGER PRIMARY KEY,
    nombre           TEXT NOT NULL,
    descripcion      TEXT,
    precio           REAL NOT NULL,
    categoria        TEXT,
    stock            INTEGER DEFAULT -1,
    tipo_entrega     TEXT DEFAULT 'inmediato',
    es_combo         INTEGER DEFAULT 0,
    combo_items_json TEXT,
    activo           INTEGER DEFAULT 1,
    synced_at        INTEGER DEFAULT (unixepoch())
  );
  CREATE TABLE IF NOT EXISTS zonas_cache (
    id                 INTEGER PRIMARY KEY,
    nombre             TEXT,
    precio_envio       REAL DEFAULT 0,
    tiempo_estimado_min INTEGER DEFAULT 30,
    gratis_desde       REAL
  );
  CREATE TABLE IF NOT EXISTS logs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    nivel      TEXT DEFAULT 'info',
    evento     TEXT,
    detalle    TEXT,
    created_at INTEGER DEFAULT (unixepoch())
  );
  CREATE TABLE IF NOT EXISTS handoffs (
    client_jid TEXT PRIMARY KEY,
    admin_jid  TEXT,
    requested_at INTEGER DEFAULT (unixepoch()),
    assigned_at INTEGER
  );
  CREATE TABLE IF NOT EXISTS handoff_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_jid TEXT NOT NULL,
    sender TEXT NOT NULL,
    body TEXT NOT NULL,
    created_at INTEGER DEFAULT (unixepoch()),
    delivered_at INTEGER,
    delivery_cursor INTEGER NOT NULL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at INTEGER,
    last_error TEXT
  );
  CREATE TABLE IF NOT EXISTS muted_clients (
    phone TEXT PRIMARY KEY,
    reason TEXT,
    muted_until INTEGER NOT NULL,
    created_by TEXT,
    created_at INTEGER DEFAULT (unixepoch())
  );
  CREATE TABLE IF NOT EXISTS admin_availability (
    admin_jid TEXT PRIMARY KEY,
    available INTEGER NOT NULL DEFAULT 0,
    updated_at INTEGER DEFAULT (unixepoch())
  );
  CREATE TABLE IF NOT EXISTS inbound_messages (
    message_id TEXT PRIMARY KEY,
    payload_json TEXT NOT NULL,
    created_at INTEGER DEFAULT (unixepoch()),
    processed_at INTEGER,
    attempts INTEGER NOT NULL DEFAULT 0
  );
  CREATE INDEX IF NOT EXISTS ix_handoff_messages_client
    ON handoff_messages (client_jid, created_at);
`);

// Repara asignaciones antiguas duplicadas antes de imponer un chat por admin.
db.transaction(() => {
  db.prepare(`
    UPDATE handoffs
    SET admin_jid = NULL
    WHERE admin_jid IS NOT NULL
      AND rowid NOT IN (
        SELECT MIN(rowid)
        FROM handoffs
        WHERE admin_jid IS NOT NULL
        GROUP BY admin_jid
      )
  `).run();
  db.exec(`
    CREATE UNIQUE INDEX IF NOT EXISTS ux_handoffs_admin_active
    ON handoffs (admin_jid)
    WHERE admin_jid IS NOT NULL
  `);
})();

// Migraciones seguras
[
  `ALTER TABLE sessions ADD COLUMN nombre TEXT`,
  `ALTER TABLE sessions ADD COLUMN role TEXT DEFAULT 'client'`,
  `ALTER TABLE sessions ADD COLUMN zona_id INTEGER`,
  `ALTER TABLE sessions ADD COLUMN bar_id INTEGER`,
  `ALTER TABLE sessions ADD COLUMN bar_nombre TEXT`,
  `ALTER TABLE sessions ADD COLUMN active_client_jid TEXT`,
  `ALTER TABLE sessions ADD COLUMN pending_json TEXT DEFAULT '{}'`,
  `ALTER TABLE productos_cache ADD COLUMN es_combo INTEGER DEFAULT 0`,
  `ALTER TABLE productos_cache ADD COLUMN combo_items_json TEXT`,
  `ALTER TABLE handoffs ADD COLUMN assigned_at INTEGER`,
  `ALTER TABLE handoff_messages ADD COLUMN delivery_cursor INTEGER NOT NULL DEFAULT 0`,
  `ALTER TABLE handoff_messages ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0`,
  `ALTER TABLE handoff_messages ADD COLUMN next_attempt_at INTEGER`,
  `ALTER TABLE handoff_messages ADD COLUMN last_error TEXT`,
].forEach(sql => { try { db.exec(sql); } catch (e) { if (!e.message.includes('duplicate column')) console.warn('[DB migration]', e.message); } });

const _cfgGet = db.prepare(`SELECT value FROM config WHERE key = ?`);
const _cfgSet = db.prepare(`
  INSERT INTO config (key, value) VALUES (?, ?)
  ON CONFLICT(key) DO UPDATE SET value=excluded.value
`);
function cfg(key, fallback = null) {
  try { return _cfgGet.get(key)?.value ?? fallback; } catch { return fallback; }
}

function setCfg(key, value) {
  _cfgSet.run(key, String(value ?? ''));
}

function cleanBaseUrl(value, fallback = '') {
  return String(value || fallback || '').trim().replace(/\/$/, '');
}

function getOxidianUrl() {
  if (BOT_OXIDIAN_URL) return BOT_OXIDIAN_URL;
  return cleanBaseUrl(cfg('oxidian_url', OXIDIAN_URL), OXIDIAN_URL);
}

function getTiendaUrl() {
  return cleanBaseUrl(cfg('tienda_url', TIENDA_URL), TIENDA_URL || getOxidianUrl());
}

function getOxidianKey() {
  return String(cfg('oxidian_key', OXIDIAN_KEY) || '').trim();
}

function getPanelKey() {
  return String(cfg('panel_key', BOT_PANEL_KEY) || '').trim();
}

function getEvolutionUrl() {
  return cleanBaseUrl(cfg('evolution_url', EVO_URL), EVO_URL);
}

function getEvolutionKey() {
  return String(cfg('evolution_key', EVO_KEY) || '').trim();
}

function getEvolutionInstance() {
  return String(cfg('evolution_instance', EVO_INSTANCE) || '').trim() || EVO_INSTANCE;
}

function isBotEnabled() {
  return String(cfg('bot_enabled', '1')).trim() !== '0';
}

function uniquePhones(list) {
  return [...new Set((list || []).map(normalizePhone).filter(Boolean))];
}

function staticAdminPhones() {
  return uniquePhones([OWNER_NUMBER, ...SUPERADMINS]);
}

function runtimeAdminPhones() {
  return uniquePhones(String(cfg('runtime_admins', '') || '').split(','));
}

function adminPhones() {
  return uniquePhones([...staticAdminPhones(), ...runtimeAdminPhones()]);
}

function setRuntimeAdmins(list) {
  const owner = normalizePhone(OWNER_NUMBER);
  const staticPhones = new Set(staticAdminPhones());
  const runtime = uniquePhones(list).filter(phone => phone && phone !== owner && !staticPhones.has(phone));
  setCfg('runtime_admins', runtime.join(','));
  return runtime;
}

function replaceRuntimeAdmins(list) {
  const previous = new Set(runtimeAdminPhones());
  const runtime = setRuntimeAdmins(list);
  const current = new Set(runtime);
  const removed = [...previous].filter(phone => !current.has(phone));

  db.transaction(() => {
    for (const phone of removed) {
      const adminJid = `${phone}@s.whatsapp.net`;
      const assigned = db.prepare(
        `SELECT client_jid FROM handoffs WHERE admin_jid = ?`
      ).all(adminJid);
      db.prepare(`
        UPDATE handoffs
        SET admin_jid = NULL, assigned_at = NULL
        WHERE admin_jid = ?
      `).run(adminJid);
      for (const row of assigned) clearAdminChatForClient(row.client_jid);
      db.prepare(`DELETE FROM admin_availability WHERE admin_jid = ?`).run(adminJid);
      db.prepare(`DELETE FROM sessions WHERE jid = ?`).run(adminJid);
    }
  })();

  sanitizeRuntimeState();
  return { runtime, removed };
}

function isOwnerPhone(phone) {
  return Boolean(normalizePhone(OWNER_NUMBER) && normalizePhone(phone) === normalizePhone(OWNER_NUMBER));
}

function isOwnerJid(jid) {
  return isOwnerPhone(phoneFromJid(jid));
}

function requireWebhookSecret(req, res) {
  const secret = String(cfg('webhook_secret', WEBHOOK_SECRET) || '').trim();
  if (!secret) return true;
  const provided = String(req.headers['x-webhook-secret'] || req.headers['x-api-key'] || '').trim();
  if (provided !== secret) {
    res.status(403).json({ ok: false, error: 'invalid webhook secret' });
    return false;
  }
  return true;
}

function log(nivel, evento, detalle = '') {
  console.log(`[${nivel.toUpperCase()}] ${evento} — ${detalle}`);
  try {
    db.prepare(`INSERT INTO logs (nivel, evento, detalle) VALUES (?,?,?)`).run(nivel, evento, String(detalle).slice(0, 500));
  } catch {}
}

function phoneFromJid(jid) {
  return String(jid || '').replace('@s.whatsapp.net', '').replace('@g.us', '');
}

function isAdminPhone(phone) {
  const clean = normalizePhone(phone);
  return adminPhones().includes(clean);
}

function isAdminJid(jid) {
  return isAdminPhone(phoneFromJid(jid));
}

function normalizeJid(value) {
  const phone = normalizePhone(value);
  return phone ? `${phone}@s.whatsapp.net` : '';
}

function sanitizeRuntimeState() {
  try {
    const rows = db.prepare(`SELECT client_jid, admin_jid FROM handoffs`).all();
    let removed = 0;
    for (const row of rows) {
      if (isAdminJid(row.client_jid)) {
        db.prepare(`DELETE FROM handoffs WHERE client_jid = ?`).run(row.client_jid);
        removed++;
      } else if (row.admin_jid && !isAdminJid(row.admin_jid)) {
        db.prepare(`UPDATE handoffs SET admin_jid = NULL WHERE client_jid = ?`).run(row.client_jid);
        removed++;
      }
    }
    const adminJids = adminPhones().map(phone => `${phone}@s.whatsapp.net`);
    if (adminJids.length) {
      db.prepare(`
        UPDATE sessions
        SET role = CASE WHEN jid IN (${adminJids.map(() => '?').join(',')}) THEN 'admin' ELSE COALESCE(role, 'client') END
      `).run(...adminJids);
    }
    if (removed) log('warn', 'handoff_sanitize', `${removed} handoffs inconsistentes eliminados`);
  } catch (e) {
    log('warn', 'runtime_sanitize_fail', String(e));
  }
}

// Handoff helpers
function getHandoff(clientJid) {
  return db.prepare(`SELECT * FROM handoffs WHERE client_jid = ?`).get(clientJid) || null;
}
function listPendingHandoffs() {
  return db.prepare(`SELECT client_jid, admin_jid, requested_at FROM handoffs WHERE admin_jid IS NULL ORDER BY requested_at ASC`).all()
    .filter(h => !isAdminJid(h.client_jid));
}
function assignHandoff(clientJid, adminJid) {
  if (isAdminJid(clientJid) || !isAdminJid(adminJid)) return { changes: 0 };
  if (adminHasActiveChat(adminJid)) return { changes: 0 };
  try {
    return db.prepare(`
      UPDATE handoffs
      SET admin_jid = ?, assigned_at = unixepoch()
      WHERE client_jid = ? AND admin_jid IS NULL
    `).run(adminJid, clientJid);
  } catch (error) {
    if (String(error?.code || '').startsWith('SQLITE_CONSTRAINT')) {
      log('warn', 'handoff_claim_conflict', `${clientJid} -> ${adminJid}`);
      return { changes: 0 };
    }
    throw error;
  }
}
function createHandoffRequest(clientJid) {
  if (isAdminJid(clientJid)) return false;
  try {
    db.prepare(`INSERT OR IGNORE INTO handoffs (client_jid, admin_jid) VALUES (?,NULL)`).run(clientJid);
    return true;
  } catch { return false; }
}

function queueHandoffMessage(clientJid, sender, text) {
  const body = String(text || '').replace(/\u0000/g, '').trim().slice(0, MAX_MESSAGE_CHARS);
  if (!body || !['client', 'admin'].includes(sender)) return null;
  return db.transaction(() => {
    const handoff = getHandoff(clientJid);
    if (!handoff) return null;
    return db.prepare(`
      INSERT INTO handoff_messages (client_jid, sender, body)
      VALUES (?, ?, ?)
    `).run(clientJid, sender, body);
  })();
}

function queueAssignedHandoffMessage(clientJid, adminJid, sender, text) {
  const body = String(text || '').replace(/\u0000/g, '').trim().slice(0, MAX_MESSAGE_CHARS);
  if (!body || !['client', 'admin'].includes(sender)) return null;
  return db.transaction(() => {
    const handoff = db.prepare(`
      SELECT admin_jid FROM handoffs WHERE client_jid = ?
    `).get(clientJid);
    if (!handoff || handoff.admin_jid !== adminJid) return null;
    db.prepare(`
      UPDATE handoffs SET assigned_at=unixepoch()
      WHERE client_jid=? AND admin_jid=?
    `).run(clientJid, adminJid);
    return db.prepare(`
      INSERT INTO handoff_messages (client_jid, sender, body)
      VALUES (?, ?, ?)
    `).run(clientJid, sender, body);
  })();
}

function pendingHandoffTranscript(clientJid, limit = 20) {
  return db.prepare(`
    SELECT id, sender, body, created_at, delivery_cursor, attempts
    FROM handoff_messages
    WHERE client_jid = ? AND delivered_at IS NULL
      AND (next_attempt_at IS NULL OR next_attempt_at <= unixepoch())
    ORDER BY id ASC
    LIMIT ?
  `).all(clientJid, limit);
}

function markHandoffTranscriptDelivered(clientJid, ids) {
  if (!ids?.length) return;
  const placeholders = ids.map(() => '?').join(',');
  db.prepare(`
    UPDATE handoff_messages
    SET delivered_at = unixepoch(), next_attempt_at = NULL, last_error = NULL
    WHERE client_jid = ? AND id IN (${placeholders})
  `).run(clientJid, ...ids);
}

function recordHandoffDeliveryProgress(id, cursor) {
  db.prepare(`
    UPDATE handoff_messages
    SET delivery_cursor=?, last_error=NULL
    WHERE id=? AND delivered_at IS NULL
  `).run(cursor, id);
}

function recordHandoffDeliveryFailure(id, reason) {
  db.prepare(`
    UPDATE handoff_messages
    SET attempts=attempts+1,
        next_attempt_at=unixepoch() + MIN(300, 20 * (1 << MIN(attempts, 4))),
        last_error=?
    WHERE id=? AND delivered_at IS NULL
  `).run(String(reason || 'send failed').slice(0, 240), id);
}

function adminHasActiveChat(adminJid) {
  return Boolean(db.prepare(`
    SELECT 1 FROM handoffs WHERE admin_jid = ? LIMIT 1
  `).get(adminJid));
}

function setAdminAvailability(adminJid, available) {
  db.prepare(`
    INSERT INTO admin_availability (admin_jid, available, updated_at)
    VALUES (?, ?, unixepoch())
    ON CONFLICT(admin_jid) DO UPDATE SET
      available=excluded.available,
      updated_at=unixepoch()
  `).run(adminJid, available ? 1 : 0);
}

function isAdminAvailable(adminJid) {
  return db.prepare(`
    SELECT available FROM admin_availability WHERE admin_jid = ?
  `).get(adminJid)?.available === 1;
}

function availableAdminJids() {
  const cutoff = Math.floor(Date.now() / 1000) - ADMIN_ACTIVE_WINDOW_SEC;
  return adminPhones()
    .map(phone => `${phone}@s.whatsapp.net`)
    .filter(jid => {
      if (adminHasActiveChat(jid)) return false;
      if (!isAdminAvailable(jid)) return false;
      const session = db.prepare(`
        SELECT estado, updated_at, active_client_jid
        FROM sessions
        WHERE jid = ? AND role = 'admin'
      `).get(jid);
      return Boolean(
        session
        && session.updated_at >= cutoff
        && !session.active_client_jid
        && session.estado !== 'admin_away'
      );
    })
    .sort((a, b) => {
      const aUpdated = _sesGet.get(a)?.updated_at || 0;
      const bUpdated = _sesGet.get(b)?.updated_at || 0;
      return aUpdated - bUpdated;
    });
}

async function deliverQueuedTranscript(clientJid, adminJid, sender = sendText) {
  const rows = pendingHandoffTranscript(clientJid, 100);
  if (!rows.length) return true;
  for (const row of rows) {
    const prefix = row.sender === 'client'
      ? `🧾 *Mensaje pendiente de ${phoneFromJid(clientJid)}:*\n\n`
      : `🧾 *Respuesta pendiente del equipo:*\n\n`;
    const chunks = splitTextForSend(row.body, Math.max(200, MAX_OUTBOUND_CHARS - prefix.length - 20));
    const startAt = Math.min(Number(row.delivery_cursor || 0), chunks.length);
    for (let index = startAt; index < chunks.length; index++) {
      const target = row.sender === 'client' ? adminJid : clientJid;
      const chunkLabel = chunks.length > 1 ? `[${index + 1}/${chunks.length}]\n` : '';
      if (!await sender(target, `${prefix}${chunkLabel}${chunks[index]}`)) {
        recordHandoffDeliveryFailure(row.id, `target=${target} chunk=${index + 1}/${chunks.length}`);
        log('warn', 'handoff_delivery_deferred', `message=${row.id} chunk=${index + 1}/${chunks.length}`);
        return false;
      }
      recordHandoffDeliveryProgress(row.id, index + 1);
    }
    markHandoffTranscriptDelivered(clientJid, [row.id]);
    log('info', 'handoff_message_delivered', `message=${row.id} chunks=${chunks.length}`);
  }
  return true;
}

function splitTextForSend(text, maxLength) {
  const source = String(text || '');
  if (source.length <= maxLength) return [source];
  const chunks = [];
  for (let start = 0; start < source.length; start += maxLength) {
    chunks.push(source.slice(start, start + maxLength));
  }
  return chunks;
}

async function autoAssignPendingHandoff(clientJid) {
  const handoff = getHandoff(clientJid);
  if (!handoff || handoff.admin_jid) return handoff?.admin_jid || null;
  const adminJid = availableAdminJids()[0];
  if (!adminJid) return null;
  const adminSession = getSesion(adminJid);
  const claimed = await takeHandoff(adminJid, adminSession, clientJid, { automatic: true });
  return claimed && getHandoff(clientJid)?.admin_jid === adminJid ? adminJid : null;
}

async function notifyAdminsHandoffQueued(clientJid) {
  const message =
    `📨 *Cliente en espera*\n` +
    `${phoneFromJid(clientJid)} necesita atención humana.\n\n` +
    `Escribe *!take ${phoneFromJid(clientJid)}* para tomar el chat.`;
  for (const phone of adminPhones()) {
    sendText(`${phone}@s.whatsapp.net`, message).catch(() => {});
  }
}

async function iniciarReporteNovedad(clientJid, ses, rawTexto) {
  // Permite especificar el pedido al inicio: "REPORTAR #1024 mensaje".
  const matchId = String(rawTexto || '').match(/^#?(\d+)\s+(.+)$/);
  let pedidoId = null;
  let texto = String(rawTexto || '').trim();
  if (matchId) {
    pedidoId = Number(matchId[1]);
    texto = matchId[2].trim();
  }
  if (!texto || texto.length < 4) {
    return sendText(
      clientJid,
      `🙏 Para reportar una novedad necesito un poco más de detalle.\n\n` +
      `Por ejemplo: *REPORTAR La pizza llegó fría* o *REPORTAR 1024 falta un combo*.`,
    );
  }
  const phone = phoneFromJid(clientJid);
  // Si no especificaron pedido, usamos el más reciente del cliente.
  if (!pedidoId) {
    try {
      const data = await oxidianGet(`/pedidos?telefono=${phone}&estados=pendiente,armando,listo,en_ruta,entregado&limit=1`);
      const pedidos = (data && data.ok && Array.isArray(data.pedidos)) ? data.pedidos : [];
      if (!pedidos.length) {
        return sendText(
          clientJid,
          `No encuentro ningún pedido tuyo asociado a este WhatsApp. ` +
          `Si quieres reportar algo sobre un pedido en concreto, incluye su número: *REPORTAR 1024 tu mensaje*.`,
        );
      }
      pedidoId = pedidos[0].id;
    } catch (error) {
      log('warn', 'reporte_busca_pedido_falla', error?.message || String(error));
      return sendText(clientJid, `No pude consultar tus pedidos ahora mismo. Inténtalo en un par de minutos.`);
    }
  }

  try {
    const resp = await oxidianPost(`/pedido/${pedidoId}/incidencia`, {
      texto,
      telefono: phone,
    });
    if (!resp || resp.ok === false) {
      const msg = (resp && resp.error) ? resp.error : 'Sin respuesta del servidor';
      log('warn', 'reporte_incidencia_falla', `${pedidoId}: ${msg}`);
      return sendText(clientJid, `No pude registrar tu incidencia ahora (${msg}). Por favor, escribe *AGENTE* y te ayudamos.`);
    }
    // Tras registrar, intentamos saber quién despacha el pedido para ofrecer
    // contacto directo si es de un bar. Si es propio, solo confirmamos.
    let contactoExtra = '';
    try {
      const det = await oxidianGet(`/pedido/${pedidoId}`);
      const c = det?.pedido?.bar_contacto;
      if (c && c.tipo === 'bar' && c.whatsapp_url) {
        contactoExtra =
          `\n\n📞 Si quieres conversarlo directamente con quien lo prepara, ` +
          `escríbeles aquí:\n${c.whatsapp_url}`;
      }
    } catch (_) {}
    return sendText(
      clientJid,
      `✅ *Incidencia registrada*\n\n` +
      `Pedido: *${resp.pedido || '#' + pedidoId}*\n` +
      `Tu mensaje: «${texto}»\n\n` +
      `El equipo responsable la verá en su panel.` +
      contactoExtra +
      `\n\nSi necesitas hablar ya, escribe *AGENTE*.`,
    );
  } catch (error) {
    log('warn', 'reporte_incidencia_excepcion', error?.message || String(error));
    return sendText(clientJid, `Ocurrió un error al registrar la incidencia. Escribe *AGENTE* para que te atienda una persona.`);
  }
}


async function derivarSegunUltimoPedido(clientJid) {
  // Devuelve true si ya envió la derivación al cliente (no hace falta handoff
  // general). Buscamos un pedido activo despachado por un bar activo con
  // WhatsApp configurado. Si el cliente tiene varios pedidos, priorizamos
  // los del bar (más urgentes operacionalmente) sobre los propios.
  try {
    const phone = phoneFromJid(clientJid);
    const data = await oxidianGet(`/pedidos?telefono=${phone}&estados=pendiente,armando,listo,en_ruta&limit=5`);
    const lista = (data && data.ok && Array.isArray(data.pedidos)) ? data.pedidos : [];
    if (!lista.length) return false;
    // Orden: pedidos con bar_contacto.tipo='bar' y whatsapp_url primero;
    // dentro de cada grupo, por fecha más reciente.
    const candidato = lista.find(p =>
      p.bar_contacto && p.bar_contacto.tipo === 'bar' && p.bar_contacto.whatsapp_url
    );
    if (!candidato) return false;
    const contacto = candidato.bar_contacto;
    await sendText(
      clientJid,
      `📞 *Te conecto con quien prepara tu pedido*\n\n` +
      `Tu pedido *${candidato.numero}* lo despacha *${contacto.nombre}*. Para resolver dudas o coordinar la entrega, escríbeles directamente aquí:\n` +
      `${contacto.whatsapp_url}\n\n` +
      `Si necesitas algo distinto, escribe *menu* para volver.`,
    );
    return true;
  } catch (error) {
    log('warn', 'derivar_bar_fallo', error?.message || String(error));
    return false;
  }
}


async function requestHumanSupport(clientJid, initialText = '') {
  createHandoffRequest(clientJid);
  if (initialText) queueHandoffMessage(clientJid, 'client', initialText);
  const assignedAdmin = await autoAssignPendingHandoff(clientJid);
  if (assignedAdmin) return true;
  await notifyAdminsHandoffQueued(clientJid);
  return sendText(
    clientJid,
    `💬 *Te he puesto en cola para hablar con una persona.*\n\n` +
    `Ahora mismo no hay agentes libres, pero guardo todos tus mensajes ` +
    `y la primera persona disponible recibirá tu historial completo. ` +
    `No te preocupes, no se pierde nada. 😊\n\n` +
    `Mientras tanto, puedes seguir escribiendo lo que necesites. ` +
    `Si prefieres volver al asistente automático escribe */volver bot*.`,
  );
}

async function closeHumanChat(adminJid, clientJid, notifyClient = true) {
  const closed = db.transaction(() => {
    const removed = db.prepare(`
      DELETE FROM handoffs WHERE client_jid=? AND admin_jid=?
    `).run(clientJid, adminJid);
    if (!removed.changes) return false;
    db.prepare(`DELETE FROM handoff_messages WHERE client_jid = ?`).run(clientJid);
    db.prepare(`
      UPDATE sessions
      SET estado='admin_menu', active_client_jid=NULL, pending_json='{}', updated_at=unixepoch()
      WHERE jid=? AND active_client_jid=?
    `).run(adminJid, clientJid);
    return true;
  })();
  if (!closed) return false;
  log('info', 'handoff_closed', `${clientJid} admin=${adminJid}`);
  if (notifyClient) {
    await sendText(
      clientJid,
      `✅ *La conversación con el agente ha finalizado.*\n\nEl asistente automático vuelve a estar disponible.\n\n${menuPrincipal()}`,
    );
  }
  return true;
}

async function releaseHumanChat(adminJid, clientJid, notifyClient = true) {
  const released = db.transaction(() => {
    const updated = db.prepare(`
      UPDATE handoffs
      SET admin_jid=NULL, assigned_at=NULL
      WHERE client_jid=? AND admin_jid=?
    `).run(clientJid, adminJid);
    if (!updated.changes) return false;
    db.prepare(`
      UPDATE sessions
      SET estado='admin_menu', active_client_jid=NULL, pending_json='{}', updated_at=unixepoch()
      WHERE jid=? AND active_client_jid=?
    `).run(adminJid, clientJid);
    return true;
  })();
  if (!released) return false;
  log('info', 'handoff_released', `${clientJid} admin=${adminJid}`);
  if (notifyClient) {
    await sendText(
      clientJid,
      `🕐 *Tu chat volvió a la cola.*\n\nConservamos el historial y otro agente podrá continuar la conversación.`,
    );
  }
  await notifyAdminsHandoffQueued(clientJid);
  return true;
}

function closeHumanChatByClient(clientJid) {
  return db.transaction(() => {
    const handoff = getHandoff(clientJid);
    if (!handoff) return null;
    db.prepare(`DELETE FROM handoffs WHERE client_jid=?`).run(clientJid);
    db.prepare(`DELETE FROM handoff_messages WHERE client_jid=?`).run(clientJid);
    if (handoff.admin_jid) {
      db.prepare(`
        UPDATE sessions
        SET estado='admin_menu', active_client_jid=NULL, pending_json='{}', updated_at=unixepoch()
        WHERE jid=? AND active_client_jid=?
      `).run(handoff.admin_jid, clientJid);
    }
    return handoff;
  })();
}

async function takeNextQueuedHandoff(adminJid) {
  const waiting = listPendingHandoffs()[0];
  if (!waiting) return false;
  return takeHandoff(adminJid, getSesion(adminJid), waiting.client_jid, { automatic: true });
}

function recoverOrphanedHandoffs(force = false) {
  const cutoff = Math.floor(Date.now() / 1000) - HANDOFF_LEASE_SEC;
  const stale = db.prepare(`
    SELECT h.client_jid, h.admin_jid
    FROM handoffs h
    LEFT JOIN sessions s ON s.jid = h.admin_jid
    WHERE h.admin_jid IS NOT NULL
      AND (
        ? = 1
        OR h.assigned_at IS NULL
        OR h.assigned_at < ?
        OR s.updated_at IS NULL
        OR s.updated_at < ?
      )
  `).all(force ? 1 : 0, cutoff, cutoff);
  for (const row of stale) {
    db.prepare(`
      UPDATE handoffs SET admin_jid=NULL, assigned_at=NULL WHERE client_jid=?
    `).run(row.client_jid);
    clearAdminChatForClient(row.client_jid);
    log('warn', 'handoff_requeued', `${row.client_jid} admin=${row.admin_jid || 'none'}`);
  }
  return stale.length;
}

async function retryPendingHandoffMessages() {
  recoverOrphanedHandoffs(false);
  const active = db.prepare(`
    SELECT DISTINCT h.client_jid, h.admin_jid
    FROM handoffs h
    JOIN handoff_messages m ON m.client_jid=h.client_jid
    WHERE h.admin_jid IS NOT NULL
      AND m.delivered_at IS NULL
      AND m.created_at < unixepoch()-10
      AND (m.next_attempt_at IS NULL OR m.next_attempt_at <= unixepoch())
  `).all();
  for (const row of active) {
    await deliverQueuedTranscript(row.client_jid, row.admin_jid);
  }
}
function releaseHandoffByAdmin(adminJid) {
  try {
    const j = adminJid && adminJid.includes('@') ? adminJid : `${adminJid}@s.whatsapp.net`;
    return db.prepare(`UPDATE handoffs SET admin_jid = NULL WHERE admin_jid = ? OR admin_jid = ?`).run(j, adminJid);
  } catch (e) { return null; }
}
function deleteHandoffsByAdmin(adminJid) {
  try {
    const j = adminJid && adminJid.includes('@') ? adminJid : `${adminJid}@s.whatsapp.net`;
    return db.prepare(`DELETE FROM handoffs WHERE admin_jid = ? OR admin_jid = ?`).run(j, adminJid);
  } catch (e) { return null; }
}
function deleteHandoff(clientJid) {
  return db.prepare(`DELETE FROM handoffs WHERE client_jid = ?`).run(clientJid);
}

function getMutedClient(phone) {
  const clean = normalizePhone(phone);
  if (!clean) return null;
  try {
    const row = db.prepare(`SELECT * FROM muted_clients WHERE phone = ?`).get(clean);
    if (!row) return null;
    if (row.muted_until <= Math.floor(Date.now() / 1000)) {
      db.prepare(`DELETE FROM muted_clients WHERE phone = ?`).run(clean);
      return null;
    }
    return row;
  } catch { return null; }
}

function muteClient(phone, durationMs, reason, adminJid) {
  const clean = normalizePhone(phone);
  const until = Math.floor((Date.now() + durationMs) / 1000);
  db.prepare(`
    INSERT INTO muted_clients (phone, reason, muted_until, created_by)
    VALUES (?, ?, ?, ?)
    ON CONFLICT(phone) DO UPDATE SET
      reason=excluded.reason, muted_until=excluded.muted_until, created_by=excluded.created_by, created_at=unixepoch()
  `).run(clean, String(reason || '').slice(0, 180), until, phoneFromJid(adminJid));
  return { phone: clean, muted_until: until };
}

function unmuteClient(phone) {
  const clean = normalizePhone(phone);
  return db.prepare(`DELETE FROM muted_clients WHERE phone = ?`).run(clean);
}

function listMutedClients(limit = 8) {
  const now = Math.floor(Date.now() / 1000);
  try {
    db.prepare(`DELETE FROM muted_clients WHERE muted_until <= ?`).run(now);
    return db.prepare(`SELECT * FROM muted_clients ORDER BY muted_until DESC LIMIT ?`).all(limit);
  } catch { return []; }
}

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

async function paceOutbound() {
  const elapsed = Date.now() - lastOutboundAt;
  const wait = Math.max(0, MIN_OUTBOUND_MS - elapsed);
  if (wait > 0) await sleep(wait);
  lastOutboundAt = Date.now();
}

function sanitizeOutgoingText(value) {
  const text = String(value || '').replace(/\u0000/g, '').trim();
  if (!text) return '';
  return text.length > MAX_OUTBOUND_CHARS
    ? `${text.slice(0, MAX_OUTBOUND_CHARS - 40)}\n\n[Mensaje recortado por seguridad]`
    : text;
}

function pruneMap(map, maxEntries = 2000) {
  if (map.size <= maxEntries) return;
  const overflow = map.size - maxEntries;
  let removed = 0;
  for (const key of map.keys()) {
    map.delete(key);
    removed++;
    if (removed >= overflow) break;
  }
}

function hitWindow(map, key, windowMs, maxHits) {
  const now = Date.now();
  const bucket = (map.get(key) || []).filter(ts => now - ts < windowMs);
  bucket.push(now);
  map.set(key, bucket);
  pruneMap(map);
  return { allowed: bucket.length <= maxHits, count: bucket.length };
}

function inboundAllowed(jid, admin = false) {
  if (admin) return true;
  const muted = getMutedClient(phoneFromJid(jid));
  if (muted) {
    log('warn', 'message_muted_skip', `${jid} hasta ${new Date(muted.muted_until * 1000).toISOString()}`);
    return false;
  }
  const now = Date.now();
  const blockedUntil = blockedInboundUntil.get(jid) || 0;
  if (blockedUntil > now) {
    log('warn', 'message_blocked_cooldown', `${jid} bloqueado hasta ${new Date(blockedUntil).toISOString()}`);
    return false;
  }
  lastInboundAt.set(jid, now);
  const hit = hitWindow(inboundBuckets, jid, INBOUND_WINDOW_MS, MAX_INBOUND_PER_WINDOW);
  if (!hit.allowed) {
    blockedInboundUntil.set(jid, now + INBOUND_BLOCK_MS);
    log('warn', 'message_abuse_cooldown', `${jid} excedio ${hit.count}/${MAX_INBOUND_PER_WINDOW}`);
    return false;
  }
  pruneMap(blockedInboundUntil);
  return true;
}

function outboundAllowed(target, text) {
  const hit = hitWindow(outboundBuckets, target, OUTBOUND_WINDOW_MS, MAX_OUTBOUND_PER_TARGET);
  if (!hit.allowed) {
    log('warn', 'outbound_target_limited', `${target} excedio ${hit.count}/${MAX_OUTBOUND_PER_TARGET}`);
    return false;
  }
  const fingerprint = `${target}:${text.slice(0, 260)}`;
  const now = Date.now();
  const previous = recentOutboundTexts.get(fingerprint) || 0;
  if (now - previous < DUPLICATE_OUTBOUND_MS) {
    log('warn', 'outbound_duplicate_skip', target);
    return false;
  }
  recentOutboundTexts.set(fingerprint, now);
  pruneMap(recentOutboundTexts, 4000);
  return true;
}

function canRunAdminAction(jid, action, minMs = MIN_ADMIN_ACTION_MS) {
  const key = `${jid}:${action}`;
  const previous = lastAdminActionAt.get(key) || 0;
  const now = Date.now();
  if (now - previous < minMs) return false;
  lastAdminActionAt.set(key, now);
  return true;
}

// ─── EVOLUTION API: ENVIAR MENSAJE ────────────────────────────────────────────
async function sendText(jid, text) {
  const target = normalizePhone(phoneFromJid(jid));
  const safeText = sanitizeOutgoingText(text);
  if (!safeText) return false;
  log('info', 'send_attempt', `to ${target}: ${safeText.slice(0,100)}`);
  if (SIMULATE_EVO_SEND) {
    log('info', 'send_simulated', `Simulating send to ${target}: ${safeText.slice(0,100)}`);
    return true;
  }
  const evolutionKey = getEvolutionKey();
  const evolutionUrl = getEvolutionUrl();
  const evolutionInstance = getEvolutionInstance();
  if (!evolutionKey || evolutionKey.startsWith('tu-')) {
    log('warn', 'evo_no_key', 'Sin API key configurada');
    return false;
  }

  // basic validation
  if (!/^[0-9]{6,15}$/.test(target)) {
    log('warn', 'send_invalid_number', `invalid number ${target}`);
    return false;
  }
  if (!outboundAllowed(target, safeText)) {
    return false;
  }

  const url = `${evolutionUrl}/message/sendText/${evolutionInstance}`;
  const payload = { number: target, text: safeText };

  const maxAttempts = 3;
  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    try {
      await paceOutbound();
      const r = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', apikey: evolutionKey },
        body: JSON.stringify(payload),
        signal: AbortSignal.timeout(8000),
      });
      const bodyText = await r.text().catch(() => '');
      let parsed = null;
      try { parsed = JSON.parse(bodyText); } catch {}
      if (r.ok) {
        log('info', 'send_ok', `${r.status} ${JSON.stringify(parsed) || bodyText}`);
        return true;
      }
      // Do not retry on 4xx (bad request) — log and abort
      if (r.status >= 400 && r.status < 500) {
        log('warn', 'send_fail', `${r.status} ${bodyText.slice(0,200)}`);
        return false;
      }
      // 5xx — retry with backoff
      log('warn', 'send_fail', `${r.status} ${bodyText.slice(0,200)} (attempt ${attempt})`);
    } catch (e) {
      log('warn', 'send_error', `${String(e)} (attempt ${attempt})`);
    }
    // backoff
    await new Promise(res => setTimeout(res, 500 * attempt));
  }
  log('error', 'send_failed_all', `all attempts failed for ${target}`);
  return false;
}

// ─── FLASK API: LLAMADAS OXIDIAN ──────────────────────────────────────────────
async function oxidianGet(path) {
  const r = await fetch(`${getOxidianUrl()}/api/bot${path}`, {
    headers: { 'X-Bot-Key': getOxidianKey() },
    signal: AbortSignal.timeout(8000),
  });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

async function oxidianPost(path, body) {
  const r = await fetch(`${getOxidianUrl()}/api/bot${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Bot-Key': getOxidianKey() },
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(10000),
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) {
    const error = new Error(data.error || `HTTP ${r.status}`);
    error.status = r.status;
    error.data = data;
    throw error;
  }
  return data;
}

// ─── CACHÉ DE CATÁLOGO ────────────────────────────────────────────────────────
async function syncCatalogo() {
  try {
    const data = await oxidianGet('/catalogo/completo');
    if (!data.ok || !Array.isArray(data.productos)) return false;

    const upsert = db.prepare(`
      INSERT INTO productos_cache (id, nombre, descripcion, precio, categoria, stock, tipo_entrega, es_combo, combo_items_json)
      VALUES (?,?,?,?,?,?,?,?,?)
      ON CONFLICT(id) DO UPDATE SET
        nombre=excluded.nombre, descripcion=excluded.descripcion,
        precio=excluded.precio, categoria=excluded.categoria,
        stock=excluded.stock, tipo_entrega=excluded.tipo_entrega,
        es_combo=excluded.es_combo,
        combo_items_json=excluded.combo_items_json, synced_at=unixepoch()
    `);
    db.transaction(() => {
      db.prepare(`UPDATE productos_cache SET activo=0`).run();
      const syncedIds = [];
      for (const p of data.productos) {
        syncedIds.push(Number(p.id));
        upsert.run(
          p.id, p.nombre, p.descripcion || '', p.precio, p.categoria || '',
          p.stock ?? -1, p.tipo_entrega || 'inmediato',
          p.es_combo ? 1 : 0,
          p.combo_items?.length ? JSON.stringify(p.combo_items) : null,
        );
      }
      if (syncedIds.length) {
        const placeholders = syncedIds.map(() => '?').join(',');
        db.prepare(`UPDATE productos_cache SET activo=1 WHERE id IN (${placeholders})`).run(...syncedIds);
      }
    })();
    log('info', 'catalog_sync', `${data.productos.length} productos`);
    return true;
  } catch (e) {
    log('warn', 'catalog_sync_fail', String(e));
    return false;
  }
}

async function syncZonas() {
  try {
    const data = await oxidianGet('/zonas');
    if (!data.ok || !Array.isArray(data.zonas)) return;
    const upsert = db.prepare(`
      INSERT INTO zonas_cache (id, nombre, precio_envio, tiempo_estimado_min, gratis_desde)
      VALUES (?,?,?,?,?)
      ON CONFLICT(id) DO UPDATE SET
        nombre=excluded.nombre, precio_envio=excluded.precio_envio,
        tiempo_estimado_min=excluded.tiempo_estimado_min, gratis_desde=excluded.gratis_desde
    `);
    db.transaction(() => data.zonas.forEach(z =>
      upsert.run(z.id, z.nombre, z.precio_envio, z.tiempo_estimado_min, z.gratis_desde ?? null)
    ))();
  } catch (e) {
    log('warn', 'zonas_sync_fail', String(e));
  }
}

async function syncBusiness() {
  try {
    const data = await oxidianGet('/negocio');
    if (!data?.ok) return;
    setCfg('business_name', data.nombre || NEGOCIO);
    setCfg('whatsapp_country_code', data.whatsapp_country_code || '');
  } catch (e) {
    log('warn', 'business_sync_fail', String(e));
  }
}

// ─── SESIONES ─────────────────────────────────────────────────────────────────
const _sesGet = db.prepare(`SELECT * FROM sessions WHERE jid = ?`);
const _sesUps = db.prepare(`
  INSERT INTO sessions (jid, nombre, role, estado, carrito, pending_json, zona_id, bar_id, bar_nombre, active_client_jid, updated_at)
  VALUES (?,?,?,?,?,?,?,?,?,?,unixepoch())
  ON CONFLICT(jid) DO UPDATE SET
    nombre=excluded.nombre, role=excluded.role, estado=excluded.estado,
    carrito=excluded.carrito, pending_json=excluded.pending_json, zona_id=excluded.zona_id,
    bar_id=excluded.bar_id, bar_nombre=excluded.bar_nombre,
    active_client_jid=excluded.active_client_jid, updated_at=unixepoch()
`);

function parseJsonSafe(value, fallback) {
  try { return JSON.parse(value || ''); } catch { return fallback; }
}

function getSesion(jid) {
  const row = _sesGet.get(jid);
  const role = isAdminJid(jid) ? 'admin' : 'client';
  if (!row) return { jid, nombre: null, role, estado: 'idle', carrito: [], pending: {}, zona_id: null, active_client_jid: null };
  const ttlExpired = (Date.now() / 1000) - row.updated_at > SESSION_TTL / 1000;
  if (ttlExpired) {
    if (role === 'admin') {
      const active = db.prepare(`SELECT client_jid FROM handoffs WHERE admin_jid = ? LIMIT 1`).get(jid);
      if (active) {
        _sesUps.run(jid, row.nombre, role, 'admin_chat', '[]', '{}', null, null, null, active.client_jid);
        return {
          jid,
          nombre: row.nombre,
          role,
          estado: 'admin_chat',
          carrito: [],
          pending: {},
          zona_id: null,
          active_client_jid: active.client_jid,
        };
      }
    }
    _sesUps.run(jid, row.nombre, role, 'idle', '[]', '{}', null, null, null, null);
    return { jid, nombre: row.nombre, role, estado: 'idle', carrito: [], pending: {}, zona_id: null, active_client_jid: null };
  }
  return {
    ...row,
    role: role === 'admin' ? 'admin' : (row.role || 'client'),
    carrito: parseJsonSafe(row.carrito, []),
    pending: parseJsonSafe(row.pending_json, {}),
  };
}

function saveSesion(ses) {
  const role = isAdminJid(ses.jid) ? 'admin' : (ses.role || 'client');
  _sesUps.run(
    ses.jid,
    ses.nombre || null,
    role,
    ses.estado,
    JSON.stringify(ses.carrito || []),
    JSON.stringify(ses.pending || {}),
    ses.zona_id ?? null,
    ses.bar_id ?? null,
    ses.bar_nombre || null,
    ses.active_client_jid || null,
  );
}

function resetSesion(jid, nombre = null, role = null) {
  const resolvedRole = role || (isAdminJid(jid) ? 'admin' : 'client');
  _sesUps.run(jid, nombre, resolvedRole, 'idle', '[]', '{}', null, null, null, null);
}

// ─── HELPERS DE TEXTO ─────────────────────────────────────────────────────────
function extractText(msg) {
  const text = (
    msg.message?.conversation ||
    msg.message?.extendedTextMessage?.text ||
    msg.message?.imageMessage?.caption ||
    msg.message?.videoMessage?.caption ||
    msg.message?.documentMessage?.caption ||
    ''
  ).trim();
  if (text) return text;

  const media = [
    ['audio', msg.message?.audioMessage],
    ['imagen', msg.message?.imageMessage],
    ['video', msg.message?.videoMessage],
    ['documento', msg.message?.documentMessage],
    ['sticker', msg.message?.stickerMessage],
    ['contacto', msg.message?.contactMessage],
    ['ubicacion', msg.message?.locationMessage],
  ].find(([, value]) => value);
  if (!media) return '';

  const [type, value] = media;
  const details = [
    value.fileName,
    value.mimetype,
    value.seconds ? `${value.seconds}s` : '',
  ].filter(Boolean).join(' · ');
  return `[Adjunto recibido: ${type}${details ? ` · ${details}` : ''}]`;
}

function formatPrecio(n) { return `€${parseFloat(n).toFixed(2)}`; }
function businessName() { return String(cfg('business_name', NEGOCIO) || NEGOCIO).trim(); }

function menuPrincipal() {
  return (
    `🍽️ *${businessName()}*\n` +
    `¡Hola! ¿Qué se te antoja hoy? 😊\n\n` +
    `1️⃣  🛒 Ver el menú y los combos\n` +
    `2️⃣  📋 Consultar o cancelar mi pedido\n` +
    `3️⃣  ⭐ Mis puntos de fidelidad\n` +
    `4️⃣  🗺️ Saber si llegamos a tu zona\n` +
    `5️⃣  🌐 Abrir la tienda online\n` +
    `6️⃣  🕐 Horario, dirección y contacto\n` +
    `7️⃣  💬 Hablar con una persona\n\n` +
    `_Puedes responder con el número o decírmelo con tus palabras_\n` +
    `_(por ejemplo: «mi pedido», «cancelar», «menú», «agente»…)_`
  );
}

function adminMenu() {
  return (
    `🔐 *Panel Admin — ${businessName()}*\n\n` +
    `1️⃣  Estado del bot y WhatsApp\n` +
    `2️⃣  Abrir / cerrar tienda\n` +
    `3️⃣  Productos y precios\n` +
    `4️⃣  Clientes y puntos\n` +
    `5️⃣  Administradores WhatsApp\n` +
    `6️⃣  Atención humana (handoff)\n` +
    `7️⃣  Sincronizar sistema\n` +
    `8️⃣  Seguridad / Anti-ban\n` +
    `9️⃣  Modo emergencia\n` +
    `🔟  Pedidos en riesgo\n` +
    `*11* Modo cliente 🧪\n\n` +
    `_Comandos: !status · !sync · !take N · !release_`
  );
}

function adminStoreMenu() {
  return (
    `🏪 *Gestión de tienda*\n\n` +
    `1️⃣ Ver estado actual\n` +
    `2️⃣ Cerrar tienda (con mensaje)\n` +
    `3️⃣ Abrir tienda\n\n` +
    `_0 · volver al menú principal_`
  );
}

function adminProductsMenu() {
  return (
    `🧾 *Productos y precios*\n\n` +
    `1️⃣ Buscar producto por nombre o ID\n` +
    `2️⃣ Cambiar precio\n` +
    `3️⃣ Activar / desactivar producto\n\n` +
    `_0 · volver al menú principal_`
  );
}

function adminPointsMenu() {
  return (
    `⭐ *Clientes y fidelidad*\n\n` +
    `1️⃣ Buscar cliente por teléfono\n` +
    `2️⃣ Añadir puntos\n` +
    `3️⃣ Quitar puntos\n` +
    `4️⃣ Historial de puntos\n\n` +
    `_0 · volver al menú principal_`
  );
}

function adminAdminsMenu(jid) {
  const ownerNote = isOwnerJid(jid)
    ? `2️⃣ Agregar admin\n3️⃣ Eliminar admin\n`
    : `2️⃣ Agregar admin _(solo owner)_\n3️⃣ Eliminar admin _(solo owner)_\n`;
  return (
    `👥 *Administradores WhatsApp*\n\n` +
    `1️⃣ Ver lista de admins\n` +
    ownerNote +
    `\n_0 · volver al menú principal_`
  );
}

function adminHandoffMenu() {
  return (
    `💬 *Atención humana (handoff)*\n\n` +
    `1️⃣ Ver clientes en espera\n` +
    `2️⃣ Soltar mi chat activo\n` +
    `3️⃣ Cerrar todos mis chats\n\n` +
    `_0 · volver al menú principal_`
  );
}

function adminSecurityMenu() {
  return (
    `🛡️ *Seguridad y protección*\n\n` +
    `1️⃣ Estado anti-ban y reputación\n` +
    `2️⃣ Silenciar cliente 1 hora\n` +
    `3️⃣ Silenciar cliente 24 horas\n` +
    `4️⃣ Desbloquear cliente\n` +
    `5️⃣ Ver lista de silenciados\n\n` +
    `_0 · volver al menú principal_`
  );
}

function adminEmergencyMenu() {
  return (
    `🚨 *Modo emergencia*\n\n` +
    `1️⃣ 🔴 Activar emergencia (cierra tienda + pausa bot)\n` +
    `2️⃣ ✅ Volver a normalidad\n` +
    `3️⃣ 🔍 Ver estado actual\n\n` +
    `_0 · volver al menú principal_`
  );
}

function adminChatMenu(clientJid) {
  return (
    `💬 *Chat activo con cliente*\n\n` +
    `👤 ${phoneFromJid(clientJid)}\n\n` +
    `Todo lo que escribas se enviará directamente a este cliente.\n` +
    `• Escribe */cerrar chat* para finalizar y volver al panel\n` +
    `• También puedes usar *!release*`
  );
}

function clientStateFor(jid, estado) {
  return isAdminJid(jid) ? `client_${estado}` : estado;
}

function isAdminClientMode(jid, ses) {
  return isAdminJid(jid) && String(ses?.estado || '').startsWith('client_');
}

function setClientState(ses, estado, pending = {}) {
  ses.role = 'client';
  ses.estado = clientStateFor(ses.jid, estado);
  ses.pending = pending;
  saveSesion(ses);
}

async function startClientMenu(jid, nombre = null) {
  const ses = { jid, nombre, role: 'client', estado: clientStateFor(jid, 'main_menu'), carrito: [], pending: {}, zona_id: null, active_client_jid: null };
  saveSesion(ses);
  // Si el cliente tiene un pedido activo, lo saludamos por su nombre/estado
  // antes de mostrar el menú. Así no necesita escribir "estado" para verlo.
  const resumenPedido = await resumenPedidoActivo(jid).catch(() => '');
  if (resumenPedido) {
    return sendText(jid, `${resumenPedido}\n\n${menuPrincipal()}`);
  }
  return sendText(jid, menuPrincipal());
}

async function resumenPedidoActivo(clientJid) {
  // Si el cliente tiene UN pedido activo (no entregado/cancelado), devolvemos
  // un saludo breve con su número, estado y comandos disponibles. Si tiene
  // varios, devolvemos un listado corto. Si no tiene, '' (sin saludo extra).
  try {
    const phone = phoneFromJid(clientJid);
    const data = await oxidianGet(
      `/pedidos?telefono=${phone}&estados=pendiente,armando,listo,en_ruta&limit=3`,
    );
    const pedidos = (data && data.ok && Array.isArray(data.pedidos)) ? data.pedidos : [];
    if (!pedidos.length) return '';
    if (pedidos.length === 1) {
      const p = pedidos[0];
      const cancelable = (p.estado === 'pendiente');
      const opciones = cancelable
        ? `_Puedes responder *CANCELAR* si aún no quieres recibirlo._`
        : `_Ya no se puede cancelar automáticamente. Escribe *AGENTE* y te conecto con quien lo prepara._`;
      return (
        `👋 *Hola de nuevo*\n\n` +
        `Tienes un pedido en curso:\n` +
        `📦 *${p.numero}* — ${p.estado_label}\n` +
        `${opciones}\n` +
        `_O *REPORTAR <mensaje>* si quieres dejar una nota._`
      );
    }
    const lineas = pedidos.map(p => `• *${p.numero}* — ${p.estado_label}`).join('\n');
    return (
      `👋 *Hola de nuevo*\n\n` +
      `Tienes ${pedidos.length} pedidos en curso:\n${lineas}\n\n` +
      `Escribe *ESTADO* para ver detalles o *CANCELAR <número>* / *REPORTAR <número> <texto>* para acciones.`
    );
  } catch (_) {
    return '';
  }
}

async function identificarBarOperador(clientJid) {
  // Devuelve {id, nombre, telefono} si el JID coincide con el WhatsApp directo
  // de un Proveedor activo. null si no es operador de ningún bar.
  try {
    const phone = phoneFromJid(clientJid);
    const data = await oxidianGet(`/bar/identify?telefono=${encodeURIComponent(phone)}`);
    return (data && data.ok && data.es_bar) ? data.bar : null;
  } catch (_) {
    return null;
  }
}

function barMenu(bar) {
  return (
    `🏪 *Panel de ${bar.nombre}*\n\n` +
    `Estás conectado como operador de tu bar. Desde aquí puedes:\n\n` +
    `1️⃣  📋 Ver mis pedidos pendientes\n` +
    `2️⃣  ✅ Marcar un pedido como preparado\n` +
    `3️⃣  📨 Ver incidencias de clientes\n` +
    `4️⃣  🌐 Abrir mi inventario en la web\n` +
    `5️⃣  💬 Contactar con el administrador general\n\n` +
    `_Responde con el número o con palabras (pedidos, preparado, incidencias…)_`
  );
}

async function startBarMenu(jid, bar, nombre = null) {
  const ses = {
    jid,
    nombre: nombre || bar.nombre,
    role: 'bar',
    estado: 'bar_menu',
    bar_id: bar.id,
    bar_nombre: bar.nombre,
    carrito: [],
    pending: {},
    zona_id: null,
    active_client_jid: null,
  };
  saveSesion(ses);
  return sendText(jid, barMenu(bar));
}

function detectBarIntent(text) {
  const t = String(text || '').toLowerCase().trim();
  if (!t) return null;
  if (/^[1-5]$/.test(t)) return t;
  if (/pedidos?|listado|cola/.test(t)) return '1';
  if (/preparad|listo|terminad/.test(t)) return '2';
  if (/incidencias?|novedad|queja|reclamo/.test(t)) return '3';
  if (/inventario|stock|productos?/.test(t)) return '4';
  if (/admin|ayuda|soporte|gerent|encargad/.test(t)) return '5';
  if (/menu|menú|inicio/.test(t)) return '0';
  return null;
}

async function handleBarMenu(jid, ses, lower, rawText) {
  // Si el operador está en un sub-estado (esperando un número de pedido para
  // marcar preparado), lo gestionamos primero.
  if (ses.estado === 'bar_preparar_pide_id') {
    return handleBarMarcarPreparado(jid, ses, rawText);
  }

  // Guardrail: el operador del bar puede confundirse y escribir "cancelar"
  // (palabra del menú del cliente). Le explicamos que sus acciones son otras.
  if (/^cancelar/i.test(lower)) {
    return sendText(jid,
      `📌 Como operador del bar no puedes cancelar pedidos directamente desde el chat.\n\n` +
      `Si necesitas anular un pedido en curso usa:\n` +
      `• *2* o *PREPARADO <número>* — marcar como preparado.\n` +
      `• Desde el panel web puedes reportar un extravío.\n` +
      `• *5* o *AYUDA* — contactar al administrador general.\n\n` +
      barMenu({ id: ses.bar_id, nombre: ses.bar_nombre })
    );
  }

  const opcion = detectBarIntent(lower);
  const tiendaUrl = getTiendaUrl();

  if (opcion === '0' || !opcion) {
    // Refrescar menú
    return sendText(jid, barMenu({ id: ses.bar_id, nombre: ses.bar_nombre }));
  }

  if (opcion === '1') {
    try {
      const phone = phoneFromJid(jid);
      const data = await oxidianGet(`/bar/pedidos?telefono=${encodeURIComponent(phone)}&estados=pendiente,armando`);
      if (!data || !data.ok) {
        return sendText(jid, `No pude consultar tus pedidos ahora. Inténtalo en un momento.`);
      }
      if (!data.pedidos.length) {
        return sendText(jid, `🎉 *No tienes pedidos pendientes.*\n\nCuando entre uno te avisaremos por aquí.\n\n_Escribe *menu* para volver._`);
      }
      const lineas = data.pedidos.map(p => {
        const items = (p.items || []).map(it => `   • ${it.cantidad}× ${it.nombre}`).join('\n');
        return `📦 *${p.numero}* (${p.estado})\n${items}`;
      }).join('\n\n');
      return sendText(jid,
        `📋 *Tus pedidos pendientes (${data.pedidos.length}):*\n\n${lineas}\n\n` +
        `Para marcar uno como preparado responde *2* o *PREPARADO <número>*.\n_Ej: PREPARADO 1024_`
      );
    } catch (error) {
      log('warn', 'bar_pedidos_fallo', error?.message || String(error));
      return sendText(jid, `Ups, no pude leer tus pedidos. Inténtalo de nuevo.`);
    }
  }

  if (opcion === '2') {
    setSesion(jid, { ...ses, estado: 'bar_preparar_pide_id' });
    return sendText(jid,
      `✅ *Marcar pedido como preparado*\n\n` +
      `Escribe el *número* del pedido (ej. *1024* o *#1024*).\n` +
      `_O escribe *cancelar* para volver al menú._`
    );
  }

  if (opcion === '3') {
    try {
      const phone = phoneFromJid(jid);
      const data = await oxidianGet(`/bar/incidencias?telefono=${encodeURIComponent(phone)}`);
      if (!data || !data.ok) return sendText(jid, `No pude leer las incidencias ahora.`);
      if (!data.incidencias.length) {
        return sendText(jid, `📭 *Sin incidencias pendientes.*\n\nCuando un cliente reporte algo te aparecerá aquí.\n\n_Escribe *menu* para volver._`);
      }
      const lineas = data.incidencias.slice(0, 5).map(i => {
        const flag = i.atendida ? '✓' : '🔴';
        return `${flag} *${i.pedido || '#'}* — «${(i.texto || '').slice(0, 120)}»`;
      }).join('\n\n');
      return sendText(jid,
        `📨 *Incidencias recientes:*\n\n${lineas}\n\n` +
        `_Para gestionarlas completas y marcar como atendidas, entra al panel web:_\n` +
        `${tiendaUrl}/proveedor/incidencias`
      );
    } catch (error) {
      log('warn', 'bar_incidencias_fallo', error?.message || String(error));
      return sendText(jid, `Ups, no pude leer las incidencias.`);
    }
  }

  if (opcion === '4') {
    return sendText(jid,
      `🌐 *Tu inventario online:*\n\n${tiendaUrl}/proveedor/inventario\n\n` +
      `Desde ahí puedes ajustar stock y precios de coste.\n\n_Escribe *menu* para volver._`
    );
  }

  if (opcion === '5') {
    // Derivar al admin general (cola estándar)
    return requestHumanSupport(jid, `${ses.bar_nombre}: necesito hablar con el administrador.`);
  }

  return sendText(jid, barMenu({ id: ses.bar_id, nombre: ses.bar_nombre }));
}

async function handleBarMarcarPreparado(jid, ses, rawText) {
  const text = String(rawText || '').trim();
  if (/^(?:cancelar|salir|menu|menú|inicio|0)$/i.test(text)) {
    setSesion(jid, { ...ses, estado: 'bar_menu' });
    return sendText(jid, `OK, volviendo al menú.\n\n` + barMenu({ id: ses.bar_id, nombre: ses.bar_nombre }));
  }
  const m = text.match(/#?(\d+)/);
  if (!m) {
    return sendText(jid, `Necesito un número de pedido. Por ejemplo *1024* o *#1024*. (Escribe *cancelar* para volver.)`);
  }
  // Buscar el pedido por número en la lista del bar y obtener su id real.
  try {
    const phone = phoneFromJid(jid);
    const lista = await oxidianGet(`/bar/pedidos?telefono=${encodeURIComponent(phone)}&estados=pendiente,armando`);
    if (!lista || !lista.ok) {
      setSesion(jid, { ...ses, estado: 'bar_menu' });
      return sendText(jid, `No pude consultar tus pedidos.`);
    }
    const target = `#${m[1]}`;
    const pedido = lista.pedidos.find(p => p.numero === target || String(p.id) === m[1]);
    if (!pedido) {
      return sendText(jid, `No encuentro el pedido *${target}* entre los pendientes. ¿Otro número? (o *cancelar*)`);
    }
    const resp = await oxidianPost(`/bar/pedido/${pedido.id}/preparado`, { telefono: phone });
    setSesion(jid, { ...ses, estado: 'bar_menu' });
    if (!resp || resp.ok === false) {
      return sendText(jid, `No pude marcar el pedido (${resp?.error || 'error'}). Vuelve al menú.\n\n` + barMenu({ id: ses.bar_id, nombre: ses.bar_nombre }));
    }
    const avanzo = resp.avanzado_a_listo ? `\n\n🚚 El pedido pasa automáticamente a *listo* y se asigna repartidor.` : '';
    return sendText(jid,
      `✅ Pedido *${resp.numero}* marcado como preparado.${avanzo}\n\n` + barMenu({ id: ses.bar_id, nombre: ses.bar_nombre })
    );
  } catch (error) {
    log('warn', 'bar_preparado_fallo', error?.message || String(error));
    setSesion(jid, { ...ses, estado: 'bar_menu' });
    return sendText(jid, `Hubo un error. Inténtalo desde el panel web si es urgente.`);
  }
}

function setSesion(jid, ses) {
  saveSesion({ ...ses, jid });
}

function startAdminMenu(jid, nombre = null) {
  const ses = { jid, nombre, role: 'admin', estado: 'admin_menu', carrito: [], pending: {}, zona_id: null, active_client_jid: null };
  saveSesion(ses);
  return sendText(jid, adminMenu());
}

// ─── ESTADO PRINCIPAL: ROUTER DE MENSAJES ────────────────────────────────────
async function _handleMessage(jid, text, pushName) {
  const ses = getSesion(jid);
  if (!ses.nombre && pushName) { ses.nombre = pushName; }

  const lower = text.toLowerCase().trim();
  const isOwner = isAdminJid(jid);

  if (!isBotEnabled() && !isOwner) {
    const handoff = getHandoff(jid);
    if (handoff && lower === '/volver bot') {
      const closed = closeHumanChatByClient(jid);
      if (closed?.admin_jid) {
        sendText(closed.admin_jid, `ℹ️ El cliente ${phoneFromJid(jid)} salió del chat humano.`).catch(() => {});
      }
      return sendText(jid, 'El chat humano terminó. El asistente automático está temporalmente pausado; vuelve a intentarlo más tarde.');
    }
    if (handoff?.admin_jid) return forwardClientToAdmin(jid, handoff.admin_jid, text);
    if (handoff) {
      queueHandoffMessage(jid, 'client', text);
      await autoAssignPendingHandoff(jid);
      return true;
    }
    return requestHumanSupport(jid, text);
  }

  // Un handoff humano tiene prioridad sobre los comandos generales del bot.
  if (!isOwner) {
    const handoff = getHandoff(jid);
    if (handoff) {
      if (lower === '/volver bot') {
        const closed = closeHumanChatByClient(jid);
        if (closed?.admin_jid) {
          sendText(
            closed.admin_jid,
            `ℹ️ El cliente ${phoneFromJid(jid)} volvió al asistente automático.`,
          ).catch(() => {});
        }
        return startClientMenu(jid, ses.nombre);
      }
      if (handoff.admin_jid) {
        return forwardClientToAdmin(jid, handoff.admin_jid, text);
      }
      queueHandoffMessage(jid, 'client', text);
      const assigned = await autoAssignPendingHandoff(jid);
      if (!assigned) {
        return sendText(jid, `🕐 Tu mensaje quedó guardado en la cola. Te responderemos por este mismo chat.`);
      }
      return true;
    }
  }

  // Durante un handoff, cada mensaje del admin pertenece al chat hasta cerrarlo.
  if (isOwner && ses.estado === 'admin_chat') {
    if (['!release', '/soltar chat', '/soltar'].includes(lower)) {
      const released = await releaseHumanChat(jid, ses.active_client_jid);
      return released
        ? sendText(jid, `✅ Chat devuelto a la cola.\n\n${adminMenu()}`)
        : sendText(jid, `No tienes un chat activo.\n\n${adminMenu()}`);
    }
    if (['/cerrar chat', '/cerrarchat'].includes(lower)) {
      const closed = await closeHumanChat(jid, ses.active_client_jid);
      if (closed && await takeNextQueuedHandoff(jid)) return true;
      return closed
        ? sendText(jid, `✅ Chat finalizado.\n\n${adminMenu()}`)
        : sendText(jid, `No tienes un chat activo.\n\n${adminMenu()}`);
    }
    return handleAdminChat(jid, ses, text);
  }

  // ── Comandos globales (siempre activos) ────────────────────────────────
  if (['cliente', 'modo cliente', 'modo-cliente', 'client'].includes(lower)) {
    deleteHandoff(jid);
    clearAdminChatForClient(jid);
    const aviso = isOwner
      ? `🧪 *Modo cliente de prueba activado.*\nEscribe *admin* para volver al panel.\n\n`
      : '';
    await sendText(jid, aviso + menuPrincipal());
    const next = { jid, nombre: ses.nombre, role: 'client', estado: clientStateFor(jid, 'main_menu'), carrito: [], pending: {}, zona_id: null, active_client_jid: null };
    saveSesion(next);
    return true;
  }

  if (['menu', 'inicio', 'hola', 'hi', 'start', '0'].includes(lower)) {
    if (isOwner && !isAdminClientMode(jid, ses)) {
      return startAdminMenu(jid, ses.nombre);
    }
    deleteHandoff(jid);
    clearAdminChatForClient(jid);
    return startClientMenu(jid, ses.nombre);
  }

  // Las intenciones críticas deben funcionar también en una sesión nueva.
  if (isOwner && lower.startsWith('!')) {
    return handleAdminCmd(jid, text);
  }
  if (!isOwner && /^(?:7|agente|persona|humano|asesor)$|(?:hablar|comunicarme|contactar).*(?:agente|persona|humano|asesor)/i.test(lower)) {
    return requestHumanSupport(jid, text);
  }
  if (!isOwner && /^cancelar(?:\s+pedido)?(?:\s+(.+))?$/i.test(lower)) {
    const identifier = text.match(/^cancelar(?:\s+pedido)?(?:\s+(.+))?$/i)?.[1] || '';
    return iniciarCancelacionPedido(jid, ses, identifier);
  }

  if (!ses || !ses.estado || ses.estado === 'idle') {
    if (isOwner) return startAdminMenu(jid, ses?.nombre || pushName || null);
    // Si el remitente es operador de un bar (su número está en
    // proveedores.telefono), arrancamos su menú propio en vez del cliente.
    const bar = await identificarBarOperador(jid);
    if (bar) {
      return startBarMenu(jid, bar, ses?.nombre || pushName || null);
    }
    if (!isOwner && /^[1-7]$/.test(lower)) {
      ses.role = 'client';
      ses.estado = 'main_menu';
      saveSesion(ses);
      return handleMainMenu(jid, ses, lower);
    }
    return startClientMenu(jid, ses?.nombre || pushName || null);
  }

  // Si la sesión es 'bar', enrutamos a su handler propio
  if (ses && ses.role === 'bar') {
    return handleBarMenu(jid, ses, lower, text);
  }

  if (lower === 'salir' || (isOwner && !isAdminClientMode(jid, ses) && lower === 'cancelar')) {
    if (isAdminClientMode(jid, ses)) {
      return startClientMenu(jid, ses.nombre);
    }
    resetSesion(jid, ses.nombre, isOwner ? 'admin' : 'client');
    return sendText(jid, `De acuerdo, acción cancelada. ✅\n\n` + (isOwner ? adminMenu() : menuPrincipal()));
  }

  if (isAdminClientMode(jid, ses)) {
    if (lower === 'admin') return startAdminMenu(jid, ses.nombre);
    if (lower.startsWith('!')) return handleAdminCmd(jid, text);
    if (/^cancelar(?:\s+pedido)?(?:\s+(.+))?$/i.test(lower)) {
      const identifier = text.match(/^cancelar(?:\s+pedido)?(?:\s+(.+))?$/i)?.[1] || '';
      return iniciarCancelacionPedido(jid, ses, identifier);
    }
    switch (ses.estado) {
      case 'client_main_menu': return handleMainMenu(jid, ses, lower);
      case 'client_espera_numero_pedido': return handleEstadoPedido(jid, ses, text);
      case 'client_confirmar_cancelacion': return confirmarCancelacionPedido(jid, ses, lower);
      case 'client_espera_direccion_cobertura': return handleCoberturaDelivery(jid, ses, text);
      default:
        return startClientMenu(jid, ses.nombre);
    }
  }

  if (isOwner) {
    ses.role = 'admin';
    if (ses.estado === 'admin_chat' && ['/cerrar chat', '/cerrarchat'].includes(lower)) {
      const closed = await closeHumanChat(jid, ses.active_client_jid);
      return closed
        ? sendText(jid, `✅ Chat finalizado.\n\n${adminMenu()}`)
        : sendText(jid, `No tienes un chat activo.\n\n${adminMenu()}`);
    }
    if (lower === 'admin') return startAdminMenu(jid, ses.nombre);
    if (lower.startsWith('!')) return handleAdminCmd(jid, text);
    switch (ses.estado) {
      case 'admin_menu': return handleAdminMenu(jid, ses, lower);
      case 'admin_store_menu': return handleAdminStoreMenu(jid, ses, lower);
      case 'admin_store_close_message': return handleAdminStoreCloseMessage(jid, ses, text);
      case 'admin_products_menu': return handleAdminProductsMenu(jid, ses, lower);
      case 'admin_product_search': return handleAdminProductSearch(jid, ses, text);
      case 'admin_product_price_wait': return handleAdminProductPriceWait(jid, ses, text);
      case 'admin_product_toggle_wait': return handleAdminProductToggleWait(jid, ses, text);
      case 'admin_points_menu': return handleAdminPointsMenu(jid, ses, lower);
      case 'admin_customer_search': return handleAdminCustomerSearch(jid, ses, text);
      case 'admin_points_adjust_wait': return handleAdminPointsAdjustWait(jid, ses, text);
      case 'admin_points_history_wait': return handleAdminPointsHistoryWait(jid, ses, text);
      case 'admin_admins_menu': return handleAdminAdminsMenu(jid, ses, lower);
      case 'admin_admin_add_wait': return handleAdminAddWait(jid, ses, text);
      case 'admin_admin_remove_wait': return handleAdminRemoveWait(jid, ses, text);
      case 'admin_handoff_menu': return handleAdminHandoffMenu(jid, ses, lower);
      case 'admin_security_menu': return handleAdminSecurityMenu(jid, ses, lower);
      case 'admin_mute_wait': return handleAdminMuteWait(jid, ses, text);
      case 'admin_emergency_menu': return handleAdminEmergencyMenu(jid, ses, lower);
      case 'admin_confirm': return handleAdminConfirm(jid, ses, lower);
      case 'admin_take_wait': return handleAdminTakeWait(jid, ses, lower);
      case 'admin_chat': return handleAdminChat(jid, ses, text);
      default:
        return startAdminMenu(jid, ses.nombre);
    }
  }

  if (ses.role === 'admin' || String(ses.estado || '').startsWith('admin_')) {
    log('warn', 'session_role_repair', `${jid} tenia estado admin siendo cliente`);
    return startClientMenu(jid, ses.nombre);
  }

  if (lower.startsWith('!')) {
    return sendText(jid, `Los comandos administrativos no están disponibles para clientes.\n\n${menuPrincipal()}`);
  }

  if (/^(?:7|agente|persona|humano|asesor)$|(?:hablar|comunicarme|contactar).*(?:agente|persona|humano|asesor)/i.test(lower)) {
    return requestHumanSupport(jid, text);
  }

  if (/^cancelar(?:\s+pedido)?(?:\s+(.+))?$/i.test(lower)) {
    const identifier = text.match(/^cancelar(?:\s+pedido)?(?:\s+(.+))?$/i)?.[1] || '';
    return iniciarCancelacionPedido(jid, ses, identifier);
  }

  // Reportar una novedad sobre un pedido. Formato libre:
  //   REPORTAR <texto>                → usa el último pedido activo del cliente
  //   REPORTAR #1024 <texto>          → reporta sobre ese pedido concreto
  //   REPORTAR 1024 <texto>           → idem
  // También aceptamos sinónimos: incidencia, problema, novedad, queja.
  const reportarMatch = text.match(/^(reportar|incidencia|problema|novedad|queja)\s+(.+)$/i);
  if (reportarMatch) {
    return iniciarReporteNovedad(jid, ses, reportarMatch[2]);
  }

  // ── Estado de cliente ──────────────────────────────────────────────────
  switch (ses.estado) {
    case 'idle':
    case 'main_menu': return handleMainMenu(jid, ses, lower);
    case 'espera_numero_pedido': return handleEstadoPedido(jid, ses, text);
    case 'confirmar_cancelacion': return confirmarCancelacionPedido(jid, ses, lower);
    case 'espera_direccion_cobertura': return handleCoberturaDelivery(jid, ses, text);
    default:
      return startClientMenu(jid, ses.nombre);
  }
}

async function forwardClientToAdmin(clientJid, adminJid, text) {
  const queued = queueAssignedHandoffMessage(clientJid, adminJid, 'client', text);
  if (!queued) {
    log('warn', 'handoff_forward_stale', `${clientJid} -> ${adminJid}`);
    return false;
  }
  const forwarded = `💬 Mensaje de ${phoneFromJid(clientJid)}:\n\n${text}`;
  const sent = await sendText(adminJid, forwarded);
  if (sent && queued?.lastInsertRowid) {
    markHandoffTranscriptDelivered(clientJid, [Number(queued.lastInsertRowid)]);
  }
  return sent;
}

async function handleAdminChat(jid, ses, text) {
  const clientJid = ses.active_client_jid;
  if (!clientJid || getHandoff(clientJid)?.admin_jid !== jid) {
    ses.estado = 'admin_menu';
    ses.active_client_jid = null;
    saveSesion(ses);
    return sendText(jid, `No tienes un chat humano activo.\n\n${adminMenu()}`);
  }
  const queued = queueAssignedHandoffMessage(clientJid, jid, 'admin', text);
  if (!queued) {
    ses.estado = 'admin_menu';
    ses.active_client_jid = null;
    saveSesion(ses);
    return sendText(jid, `El chat se cerró antes de enviar el mensaje.\n\n${adminMenu()}`);
  }
  const sent = await sendText(clientJid, `👤 *Respuesta del equipo:*\n\n${text}`);
  if (sent && queued?.lastInsertRowid) {
    markHandoffTranscriptDelivered(clientJid, [Number(queued.lastInsertRowid)]);
  }
  saveSesion(ses);
  return sent;
}

async function handleAdminCmd(jid, text) {
  const cmd = text.slice(1).trim();
  const lowerCmd = cmd.toLowerCase();

  if (lowerCmd === 'status') {
    const sesiones = db.prepare(`SELECT COUNT(*) as c FROM sessions`).get().c;
    const clientes = db.prepare(`SELECT COUNT(*) as c FROM sessions WHERE role = 'client'`).get().c;
    const admins = db.prepare(`SELECT COUNT(*) as c FROM sessions WHERE role = 'admin'`).get().c;
    const pending = listPendingHandoffs().length;
    const assigned = db.prepare(`SELECT COUNT(*) as c FROM handoffs WHERE admin_jid IS NOT NULL`).get().c;
    const logs = db.prepare(`SELECT COUNT(*) as c FROM logs WHERE created_at >= unixepoch()-86400`).get().c;
    const prods = db.prepare(`SELECT COUNT(*) as c FROM productos_cache WHERE activo=1`).get().c;
    return sendText(jid,
      `🤖 *Bot Status*\n\n` +
      `Sesiones: ${sesiones} (${clientes} clientes / ${admins} admins)\n` +
      `Handoffs: ${pending} pendientes / ${assigned} activos\n` +
      `Catálogo cache: ${prods} productos\n` +
      `Logs 24h: ${logs}\n` +
      `Evolution: ${getEvolutionUrl()}\n` +
      `Instancia: ${getEvolutionInstance()}\n` +
      `Oxidian: ${getOxidianUrl()}`
    );
  }

  if (lowerCmd === 'menu') {
    return startAdminMenu(jid, getSesion(jid).nombre);
  }

  if (lowerCmd === 'sync') {
    await syncCatalogo();
    await syncZonas();
    const prods = db.prepare(`SELECT COUNT(*) as c FROM productos_cache WHERE activo=1`).get().c;
    return sendText(jid, `✅ Catálogo sincronizado. ${prods} productos activos.`);
  }

  if (lowerCmd.startsWith('send ')) {
    if (!canRunAdminAction(jid, 'manual_send', 5000)) {
      return sendText(jid, 'Espera unos segundos antes de enviar otro mensaje manual.');
    }
    const parts = cmd.slice(5).trim().split(/\s+/);
    const to = normalizePhone(parts[0]);
    const msg = parts.slice(1).join(' ');
    if (/^[0-9]{6,15}$/.test(to) && msg) {
      await sendText(`${to}@s.whatsapp.net`, msg);
      return sendText(jid, `✅ Mensaje enviado a ${to}`);
    }
    return sendText(jid, 'Uso: !send NUMERO mensaje');
  }

  if (lowerCmd === 'limpiar') {
    if (!isOwnerJid(jid)) {
      return sendText(jid, 'Solo el owner puede limpiar sesiones del bot.');
    }
    if (!canRunAdminAction(jid, 'clear_sessions', 10000)) {
      return sendText(jid, 'Espera unos segundos antes de repetir la limpieza de sesiones.');
    }
    db.prepare(`DELETE FROM sessions WHERE role = 'client' OR role IS NULL`).run();
    db.prepare(`DELETE FROM handoffs`).run();
    db.prepare(`DELETE FROM handoff_messages`).run();
    return sendText(jid, '✅ Sesiones de clientes y handoffs limpiados. Las sesiones admin se conservan.');
  }

  if (lowerCmd.startsWith('take ')) {
    const clientJid = normalizeJid(lowerCmd.slice(5));
    if (!clientJid) return sendText(jid, 'Uso: !take NUMERO');
    return takeHandoff(jid, getSesion(jid), clientJid);
  }

  if (lowerCmd === 'release' || lowerCmd.startsWith('release ')) {
    const arg = lowerCmd.split(/\s+/)[1];
    if (arg) {
      const client = normalizeJid(arg);
      const released = await releaseHumanChat(jid, client);
      return sendText(jid, released
        ? `✅ Chat de ${arg} devuelto a la cola.`
        : `No tienes asignado el chat de ${arg}.`);
    }
    const ses = getSesion(jid);
    if (ses.active_client_jid) {
      const released = await releaseHumanChat(jid, ses.active_client_jid);
      return sendText(jid, released ? '✅ Chat activo devuelto a la cola.' : 'No tienes un chat activo.');
    }
    ses.estado = 'admin_menu';
    ses.active_client_jid = null;
    saveSesion(ses);
    return sendText(jid, 'No tienes un chat activo.');
  }

  if (lowerCmd === 'disponible') {
    const ses = getSesion(jid);
    if (ses.active_client_jid || adminHasActiveChat(jid)) {
      return sendText(jid, 'Ya tienes un chat activo. Ciérralo antes de tomar otro.');
    }
    ses.role = 'admin';
    ses.estado = 'admin_menu';
    saveSesion(ses);
    setAdminAvailability(jid, true);
    const waiting = listPendingHandoffs()[0];
    if (waiting) return takeHandoff(jid, ses, waiting.client_jid, { automatic: true });
    return sendText(jid, '✅ Estás disponible. Te asignaré el próximo cliente automáticamente.');
  }

  if (lowerCmd === 'ausente') {
    const ses = getSesion(jid);
    if (ses.active_client_jid || adminHasActiveChat(jid)) {
      return sendText(jid, 'Cierra tu chat activo antes de marcarte como ausente.');
    }
    ses.role = 'admin';
    ses.estado = 'admin_away';
    saveSesion(ses);
    setAdminAvailability(jid, false);
    return sendText(jid, '⏸️ Quedaste como ausente. Usa *!disponible* para volver.');
  }

  return sendText(jid, `Comandos: !menu · !status · !sync · !take NUM · !release · !disponible · !ausente · !send NUM mensaje`);
}

function clearAdminChatForClient(clientJid) {
  try {
    db.prepare(`UPDATE sessions SET estado='admin_menu', active_client_jid=NULL WHERE active_client_jid = ?`).run(clientJid);
  } catch {}
}

async function takeHandoff(adminJid, ses, clientJid, options = {}) {
  if (!clientJid || isAdminJid(clientJid)) {
    await sendText(adminJid, 'No puedo tomar como cliente un número administrativo.');
    return false;
  }
  if (adminHasActiveChat(adminJid)) {
    const active = db.prepare(`SELECT client_jid FROM handoffs WHERE admin_jid = ? LIMIT 1`).get(adminJid);
    if (active?.client_jid !== clientJid) {
      await sendText(adminJid, `Ya atiendes a ${phoneFromJid(active?.client_jid)}. Cierra ese chat antes de tomar otro.`);
      return false;
    }
  }
  const existing = getHandoff(clientJid);
  if (!existing) {
    createHandoffRequest(clientJid);
  } else if (existing.admin_jid && existing.admin_jid !== adminJid) {
    await sendText(adminJid, 'Ese cliente ya está siendo atendido por otro administrador.');
    return false;
  }
  if (!getHandoff(clientJid)?.admin_jid) {
    const claimed = assignHandoff(clientJid, adminJid);
    if (!claimed.changes) {
      await sendText(adminJid, 'Otro administrador tomó ese chat antes.');
      return false;
    }
  }
  ses.role = 'admin';
  ses.estado = 'admin_chat';
  ses.active_client_jid = clientJid;
  saveSesion(ses);
  await sendText(clientJid, `👨‍💼 *Te hemos conectado con una persona.*\n\nPuedes escribir aquí con normalidad. Para volver al asistente usa */volver bot*.`);
  await deliverQueuedTranscript(clientJid, adminJid);
  await sendText(adminJid, adminChatMenu(clientJid));
  log('info', options.automatic ? 'handoff_auto_assigned' : 'handoff_taken', `${clientJid} -> ${adminJid}`);
  return true;
}

async function handleMessage(jid, text, pushName) {
  const admin = isAdminJid(jid);
  if (!inboundAllowed(jid, admin)) return false;
  const adminSession = admin ? getSesion(jid) : null;
  const handoff = admin
    ? (adminSession?.active_client_jid ? getHandoff(adminSession.active_client_jid) : null)
    : getHandoff(jid);
  const queueKey = handoff?.client_jid || adminSession?.active_client_jid || jid;
  const previous = messageQueues.get(queueKey) || Promise.resolve();
  const current = previous
    .catch(() => {})
    .then(() => _handleMessage(jid, text, pushName));
  messageQueues.set(queueKey, current);
  try {
    return await current;
  } finally {
    if (messageQueues.get(queueKey) === current) messageQueues.delete(queueKey);
  }
}

function deliveryLabel(tipo) {
  const value = String(tipo || 'inmediato').toLowerCase();
  if (['programado', 'encargo', 'fecha_fija'].includes(value)) return 'fecha fija';
  return 'delivery';
}

async function catalogPreviewText() {
  let rows = [];
  try {
    rows = db.prepare(`
      SELECT id, nombre, precio, categoria, tipo_entrega, es_combo
      FROM productos_cache
      WHERE activo=1
      ORDER BY es_combo DESC, categoria COLLATE NOCASE, nombre COLLATE NOCASE
      LIMIT 10
    `).all();
  } catch {}

  if (!rows.length) {
    await syncCatalogo();
    try {
      rows = db.prepare(`
        SELECT id, nombre, precio, categoria, tipo_entrega, es_combo
        FROM productos_cache
        WHERE activo=1
        ORDER BY es_combo DESC, categoria COLLATE NOCASE, nombre COLLATE NOCASE
        LIMIT 10
      `).all();
    } catch {}
  }

  if (!rows.length) {
    return 'El catálogo no está sincronizado ahora mismo. Abre la tienda online para ver disponibilidad en tiempo real.';
  }

  return rows.map((p, index) => {
    const combo = Number(p.es_combo) ? 'Combo' : 'Producto';
    const categoria = p.categoria ? ` · ${p.categoria}` : '';
    return `${index + 1}. ${p.nombre} · ${formatPrecio(p.precio)} · ${combo} · ${deliveryLabel(p.tipo_entrega)}${categoria}`;
  }).join('\n');
}

function clientHelpText() {
  return (
    `Las compras se hacen en la tienda online para garantizar stock actualizado, opciones de combos y pago seguro. 🛒\n` +
    `Por aquí puedo ayudarte con información, cobertura, puntos y seguimiento de pedidos.`
  );
}

// Diccionario de palabras clave por opción. Cubre variantes con/sin acentos,
// errores de tipeo comunes y formas naturales del español de Andalucía/LATAM.
const CLIENT_INTENT_KEYWORDS = {
  '1': ['menu', 'menú', 'carta', 'cartas', 'comida', 'que tienen', 'que tenes',
        'que hay', 'productos', 'catalogo', 'catálogo', 'platos', 'arepas',
        'combos', 'que venden', 'que ofrecen', 'precio', 'precios'],
  '2': ['pedido', 'pedidos', 'mi pedido', 'mis pedidos', 'estado', 'orden',
        'donde esta mi pedido', 'donde está mi pedido', 'seguimiento',
        'rastreo', 'cancelar', 'cancela', 'anular', 'anular pedido',
        'ya llega', 'cuanto falta', 'cuánto falta', 'donde anda'],
  '3': ['puntos', 'club', 'fidelidad', 'descuento', 'descuentos', 'recompensa',
        'recompensas', 'mis puntos', 'cuantos puntos', 'cuántos puntos',
        'beneficios'],
  '4': ['cobertura', 'reparten', 'llegan', 'zona', 'zonas', 'direccion',
        'dirección', 'envian', 'envían', 'reparto', 'delivery', 'a donde',
        'llegais', 'llegáis', 'envio', 'envío'],
  '5': ['tienda', 'tienda online', 'comprar', 'pedir', 'hacer pedido',
        'hacer un pedido', 'realizar pedido', 'web', 'online', 'pagina',
        'página', 'sitio'],
  '6': ['horario', 'horarios', 'abierto', 'cerrado', 'donde estan',
        'dónde están', 'donde están', 'donde estais', 'telefono',
        'teléfono', 'contacto', 'numero', 'número', 'info',
        'información', 'informacion', 'ubicacion', 'ubicación'],
  '7': ['agente', 'humano', 'persona', 'ayuda', 'ayudar', 'hablar', 'soporte',
        'asistencia', 'atencion', 'atención', 'reclamo', 'reclamacion',
        'reclamación', 'queja', 'problema'],
};

function detectClientIntent(text) {
  const normalized = String(text || '').toLowerCase().trim();
  if (!normalized) return null;
  // Match prioritario: opción numérica.
  if (/^[1-7]$/.test(normalized)) return normalized;
  // Atajo: el cliente escribe sólo "estado" → consulta de pedidos.
  if (/^estado$/.test(normalized)) return '2';
  if (/^cancelar$/.test(normalized) || /^cancelar\b/.test(normalized)) return '2';
  if (/^agente$/.test(normalized) || /^humano$/.test(normalized)) return '7';
  if (/^menu$/.test(normalized) || /^menú$/.test(normalized)) return '1';
  // Match por keyword: la opción con más matches "fuertes" gana.
  let mejor = { opcion: null, score: 0 };
  for (const [opt, keywords] of Object.entries(CLIENT_INTENT_KEYWORDS)) {
    let score = 0;
    for (const kw of keywords) {
      if (kw.length <= 4) {
        // Palabras cortas: exigir match como palabra entera para no romper.
        const re = new RegExp(`\\b${kw.replace(/[.*+?^${}()|[\\]\\\\]/g, '\\$&')}\\b`, 'i');
        if (re.test(normalized)) score += 2;
      } else if (normalized.includes(kw)) {
        score += 3;
      }
    }
    if (score > mejor.score) mejor = { opcion: opt, score };
  }
  return mejor.score >= 2 ? mejor.opcion : null;
}

// ─── MENÚ CLIENTE ────────────────────────────────────────────────────────────
async function handleMainMenu(jid, ses, opcion) {
  // Si el cliente escribió una palabra natural en vez de "1", "2"…, la
  // traducimos a la opción correspondiente. Si no detectamos intención clara,
  // dejamos que la rama `default` muestre la ayuda.
  const detectada = detectClientIntent(opcion);
  if (detectada) opcion = detectada;
  log('info', 'main_menu_choice', String(opcion));
  const tiendaUrl = getTiendaUrl();
  switch (opcion) {
    case '1': {
      try {
        const catalogo = await catalogPreviewText();
        return sendText(jid,
          `🍽️ *Menú y combos disponibles*\n\n` +
          `${catalogo}\n\n` +
          `📱 Fotos, opciones de combos y pedido directo:\n👉 ${tiendaUrl}\n\n` +
          `_Escribe *menu* para volver al inicio._`
        );
      } catch {
        return sendText(jid, `No pude leer el catálogo ahora mismo. Puedes verlo aquí:\n👉 ${tiendaUrl}\n\n_Escribe *menu* para volver._`);
      }
    }
    case '2': {
      setClientState(ses, 'espera_numero_pedido');
      return sendText(jid,
        `🔍 *Estado o cancelación de pedido*\n\n` +
        `Escribe el *número de tu pedido* o *ULTIMO* para ver el más reciente.\n\n` +
        `También puedes escribir *CANCELAR* o *CANCELAR número-pedido*.`
      );
    }
    case '3': {
      try {
        const phone = phoneFromJid(jid);
        const data = await oxidianGet(`/puntos?telefono=${phone}`);
        if (data.ok && data.existe !== false) {
          return sendText(jid,
            `⭐ *Tu club de fidelidad*\n\n` +
            `Tienes *${data.puntos} puntos* 🎉\n` +
            `Equivalen a *${formatPrecio(data.valor_euro)}* de descuento posible.\n\n` +
            `*¿Cómo canjearlos?*\n` +
            `1. Haz tu pedido en la tienda 🛒\n` +
            `2. En el paso de confirmación verifica este WhatsApp\n` +
            `3. Elige descuento o producto de regalo 🎁\n\n` +
            `👉 *Ver tu historial de puntos:*\n${tiendaUrl}/club\n\n` +
            `👉 *Hacer un pedido:*\n${tiendaUrl}\n\n` +
            `_Escribe *menu* para volver._`
          );
        }
        return sendText(jid,
          `⭐ *Aún no tienes puntos acumulados*\n\n` +
          `¡Pero podrías ganarlos ya mismo! 🚀\n\n` +
          `Cada pedido suma puntos automáticamente a este número de WhatsApp. Sin registro ni contraseñas.\n\n` +
          `👉 *Hacer tu primer pedido:*\n${tiendaUrl}\n\n` +
          `_Escribe *menu* para volver._`
        );
      } catch {
        return sendText(jid, `⚠️ No pude consultar tus puntos ahora. Intenta de nuevo.\n\n_Escribe *menu* para volver._`);
      }
    }
    case '4': {
      setClientState(ses, 'espera_direccion_cobertura');
      return sendText(jid,
        `🗺️ *¿Llegamos a tu zona?*\n\n` +
        `Escribe tu dirección completa y la verificamos ahora mismo.\n\n` +
        `📍 Ejemplo: Calle Mayor 12, piso 2\n\n` +
        `_Nota: solo la uso para verificar cobertura, no la guardo._`
      );
    }
    case '5': {
      return sendText(jid,
        `🌐 *Tienda online — ${businessName()}*\n\n` +
        `👉 ${tiendaUrl}\n\n` +
        `Catálogo completo, precios actualizados y pago seguro.\n\n` +
        `¿Necesitas ayuda con el pedido? Escribe *7* y te atendemos. 😊`
      );
    }
    case '6': {
      try {
        const data = await oxidianGet('/negocio');
        if (data.ok) {
          return sendText(jid,
            `ℹ️ *${data.nombre || businessName()}*\n\n` +
            `🕐 Horario: ${data.horario_apertura || '09:00'} – ${data.horario_cierre || '22:30'}\n` +
            `📍 Dirección: ${data.direccion || 'No configurada'}\n` +
            `📞 Teléfono: ${data.telefono || 'No configurado'}\n\n` +
            `🌐 Tienda: ${tiendaUrl}\n\n` +
            `_Escribe *menu* para volver._`
          );
        }
      } catch {}
      return sendText(jid, `ℹ️ Información no disponible ahora mismo.\n\n🌐 Tienda: ${tiendaUrl}\n\n_Escribe *menu* para volver._`);
    }
    case '7': {
      if (isAdminJid(jid)) {
        return sendText(jid, `Estás en modo prueba de cliente desde un número admin.\n\nEscribe *admin* para volver al panel o *menu* para reiniciar.\n\n${menuPrincipal()}`);
      }
      // Si el cliente tiene un pedido reciente despachado por un bar,
      // lo derivamos directamente al WhatsApp de ese bar — más rápido y
      // específico que la cola general. Si el pedido es propio o no hay,
      // cae al handoff estándar.
      const derivado = await derivarSegunUltimoPedido(jid);
      if (derivado) return;
      return requestHumanSupport(jid);
    }
    default: {
      return sendText(jid,
        `¡Disculpa! No estoy seguro de qué necesitas. 🤔\n\n` +
        `Puedes escribirme con palabras (por ejemplo: «mi pedido», «menú», ` +
        `«cancelar», «horario», «agente») o elegir un número de la lista. ` +
        `Estoy aquí para ayudarte. 💛\n\n${menuPrincipal()}`
      );
    }
  }
}

// ─── ESTADO Y CANCELACIÓN DE PEDIDO ──────────────────────────────────────────
async function iniciarCancelacionPedido(jid, ses, identifier = '') {
  const phone = phoneFromJid(jid);
  const requested = String(identifier || '').trim().replace(/^#/, '').toLowerCase();
  try {
    const data = await oxidianGet(`/pedidos?telefono=${phone}&limit=20`);
    const pedidos = Array.isArray(data.pedidos) ? data.pedidos : [];
    const pedido = requested
      ? pedidos.find(item => {
        const numero = String(item.numero || '').replace(/^#/, '').toLowerCase();
        return numero === requested || numero.includes(requested);
      })
      : pedidos.find(item => item.estado === 'pendiente');

    if (!pedido) {
      setClientState(ses, 'main_menu');
      return sendText(
        jid,
        requested
          ? `No encontré ese pedido asociado a tu WhatsApp.\n\n${menuPrincipal()}`
          : `No tienes pedidos pendientes que el asistente pueda cancelar.\n\n${menuPrincipal()}`,
      );
    }

    if (pedido.estado !== 'pendiente') {
      setClientState(ses, 'main_menu');
      // El pedido ya está más allá de "recibido". Para cancelar tiene que
      // hablar con quien lo prepara: el bar (si el pedido es de un bar) o
      // nuestro equipo (si es propio). Le damos el contacto directo en lugar
      // de meterlo en cola de soporte.
      const contacto = pedido.bar_contacto || null;
      if (contacto && contacto.whatsapp_url) {
        await sendText(
          jid,
          `El pedido *${pedido.numero}* ya está en *${pedido.estado_label || pedido.estado}* y no puedo cancelarlo automáticamente.\n\n` +
          `Contacta directamente con *${contacto.nombre}* para cancelar o resolverlo:\n` +
          `${contacto.whatsapp_url}`,
        );
        return;
      }
      await sendText(
        jid,
        `El pedido *${pedido.numero}* ya está en *${pedido.estado_label || pedido.estado}* y no puedo cancelarlo automáticamente. Te conectaré con el equipo.`,
      );
      return requestHumanSupport(jid, `Necesito cancelar el pedido ${pedido.numero}, actualmente ${pedido.estado}.`);
    }

    if (pedido.metodo_pago === 'bizum' && pedido.pago_confirmado) {
      setClientState(ses, 'main_menu');
      const contacto = pedido.bar_contacto || null;
      if (contacto && contacto.whatsapp_url) {
        await sendText(
          jid,
          `El Bizum del pedido *${pedido.numero}* ya fue confirmado. Contacta directamente con *${contacto.nombre}* para gestionar la devolución:\n${contacto.whatsapp_url}`,
        );
        return;
      }
      await sendText(jid, `El pago del pedido *${pedido.numero}* ya fue confirmado. Un agente debe gestionar la cancelación y posible devolución.`);
      return requestHumanSupport(jid, `Necesito cancelar el pedido ${pedido.numero}; el Bizum ya está confirmado.`);
    }

    setClientState(ses, 'confirmar_cancelacion', {
      pedido_id: pedido.id,
      numero: pedido.numero,
    });
    return sendText(
      jid,
      `⚠️ *Confirmar cancelación*\n\n` +
      `Pedido: *${pedido.numero}*\n` +
      `Total: *${formatPrecio(pedido.total)}*\n\n` +
      `Solo se cancelará si todavía no inició preparación.\n\n` +
      `Responde *SI* para cancelar o *NO* para conservarlo.`,
    );
  } catch (error) {
    log('warn', 'cancel_order_lookup_fail', String(error));
    setClientState(ses, 'main_menu');
    return sendText(jid, `No pude consultar tus pedidos ahora mismo. Intenta de nuevo o escribe *AGENTE*.\n\n${menuPrincipal()}`);
  }
}

async function confirmarCancelacionPedido(jid, ses, answer) {
  const lower = String(answer || '').trim().toLowerCase();
  // Escape: si el cliente escribe MENU / SALIR / 0 le devolvemos al menú
  // principal (no queda atrapado en el flujo de confirmación).
  if (['no', 'n', 'salir', 'menu', 'menú', '0', 'inicio'].includes(lower)) {
    setClientState(ses, 'main_menu');
    return sendText(jid, `De acuerdo, el pedido se conserva.\n\n${menuPrincipal()}`);
  }
  if (!['si', 'sí', 's', 'confirmar'].includes(lower)) {
    return sendText(jid,
      `Responde *SI* para cancelar el pedido o *NO* para conservarlo.\n` +
      `Si te equivocaste, escribe *menu* para volver al inicio.`);
  }

  const pending = { ...(ses.pending || {}) };
  if (!pending.pedido_id) {
    setClientState(ses, 'main_menu');
    return sendText(jid, `La confirmación venció. Vuelve a escribir *CANCELAR*.\n\n${menuPrincipal()}`);
  }

  try {
    const data = await oxidianPost(`/pedido/${pending.pedido_id}/cancelar`, {
      telefono: phoneFromJid(jid),
    });
    setClientState(ses, 'main_menu');
    return sendText(
      jid,
      `✅ *Pedido ${data.pedido?.numero || pending.numero} cancelado.*\n\n` +
      `El equipo ya recibió la actualización.\n\n${menuPrincipal()}`,
    );
  } catch (error) {
    setClientState(ses, 'main_menu');
    if (error.data?.requiere_agente) {
      // Antes de mandar al cliente a la cola general, intentamos derivar
      // directamente al WhatsApp del bar si el pedido lo despacha uno activo.
      let contacto = null;
      try {
        const det = await oxidianGet(`/pedido/${pending.pedido_id}`);
        contacto = det?.pedido?.bar_contacto || null;
      } catch (_) {}
      if (contacto && contacto.tipo === 'bar' && contacto.whatsapp_url) {
        await sendText(
          jid,
          `${error.message}\n\n` +
          `El pedido *${pending.numero}* lo despacha *${contacto.nombre}*. ` +
          `Escríbeles directamente para resolverlo:\n${contacto.whatsapp_url}`,
        );
        return;
      }
      await sendText(jid, `${error.message}\n\nTe conectaré con el equipo para revisarlo.`);
      return requestHumanSupport(jid, `No pude cancelar automáticamente el pedido ${pending.numero}: ${error.message}`);
    }
    log('warn', 'cancel_order_fail', `${pending.pedido_id}: ${String(error)}`);
    return sendText(jid, `No se pudo cancelar el pedido ahora mismo. No se aplicó ningún cambio. Escribe *AGENTE* para recibir ayuda.\n\n${menuPrincipal()}`);
  }
}

async function handleEstadoPedido(jid, ses, numero) {
  setClientState(ses, 'main_menu');
  const consulta = String(numero || '').trim();

  // Buscar por número de pedido en los pedidos del teléfono
  try {
    const phone = phoneFromJid(jid);
    const data  = await oxidianGet(`/pedidos?telefono=${phone}&limit=20`);
    if (data.ok && Array.isArray(data.pedidos)) {
      const pedido = /^ultimo|último$/i.test(consulta)
        ? data.pedidos[0]
        : data.pedidos.find(p =>
          p.numero.toLowerCase() === consulta.toLowerCase() ||
          p.numero.toLowerCase().includes(consulta.toLowerCase())
        );
      if (pedido) {
        const ESTADOS = {
          pendiente: { emoji: '⏳', label: 'Recibido — pendiente de preparación' },
          armando:   { emoji: '🔥', label: 'En preparación ahora mismo' },
          listo:     { emoji: '✅', label: 'Listo — saliendo pronto' },
          en_ruta:   { emoji: '🚀', label: 'En camino hacia ti' },
          entregado: { emoji: '🎊', label: '¡Entregado con éxito!' },
          cancelado: { emoji: '❌', label: 'Cancelado' },
        };
        const est = ESTADOS[pedido.estado] || { emoji: '•', label: pedido.estado.replace('_', ' ') };
        const cancelHint = pedido.estado === 'pendiente'
          ? `\nPara cancelarlo antes de preparación escribe *CANCELAR ${pedido.numero}*.\n`
          : '';
        return sendText(jid,
          `${est.emoji} *Pedido ${pedido.numero}*\n\n` +
          `Estado: *${est.label}*\n` +
          `Total: *${formatPrecio(pedido.total)}*\n` +
          cancelHint +
          `\n` +
          `_Escribe *menu* para volver._`
        );
      }
    }
    return sendText(jid,
      `❓ No encontramos ese pedido asociado a tu número.\n\n` +
      `Usa el número exacto (ej. *#0042*) o escribe *ULTIMO*.\n\n` +
      `_Escribe *menu* para volver._`
    );
  } catch {
    return sendText(jid, `⚠️ No pudimos consultar el estado ahora. Intenta de nuevo.\n\n_Escribe *menu* para volver._`);
  }
}

async function handleCoberturaDelivery(jid, ses, direccion) {
  setClientState(ses, 'main_menu');
  const clean = String(direccion || '').trim().slice(0, 240);
  if (clean.length < 6) {
    return sendText(jid,
      `Necesito una dirección un poco más completa para verificarla. 📍\n\n` +
      `Ejemplo: Calle Mayor 12, piso 2\n\n` +
      `${menuPrincipal()}`
    );
  }
  try {
    const data = await oxidianGet(`/cobertura?direccion=${encodeURIComponent(clean)}`);
    const coverage = data.cobertura || data;
    const distancia = coverage.distancia_km !== null && coverage.distancia_km !== undefined
      ? `\n📏 Distancia aprox.: ${Number(coverage.distancia_km).toFixed(2)} km`
      : '';
    const radio = data.radio_km ? `\n🗺️ Radio de cobertura: ${data.radio_km} km` : '';
    if (coverage.ok) {
      return sendText(jid,
        `✅ *¡Llegamos a tu zona!*\n\n` +
        `${coverage.mensaje || '¡Tu dirección está dentro de nuestra área de delivery!'}${distancia}${radio}\n\n` +
        `🛵 Para hacer el pedido entra aquí:\n👉 ${getTiendaUrl()}\n\n` +
        `_Escribe *menu* para volver._`
      );
    }
    return sendText(jid,
      `😔 *Lo sentimos, aún no llegamos ahí*\n\n` +
      `${coverage.mensaje || coverage.error || 'La dirección parece estar fuera de nuestra zona de cobertura actual.'}${distancia}${radio}\n\n` +
      `¿Tienes dudas? Escribe *7* para hablar con el equipo. 💬\n\n` +
      `_Escribe *menu* para volver._`
    );
  } catch (e) {
    return sendText(jid,
      `No pude validar la dirección ahora mismo. ⚠️\n\n` +
      `La tienda verificará la cobertura antes de confirmar el pedido:\n👉 ${getTiendaUrl()}\n\n` +
      `_Escribe *menu* para volver._`
    );
  }
}

function setAdminState(ses, estado, pending = {}) {
  ses.role = 'admin';
  ses.estado = estado;
  ses.pending = pending;
  saveSesion(ses);
}

function askAdminConfirm(jid, ses, pending, message) {
  setAdminState(ses, 'admin_confirm', pending);
  return sendText(jid, `⚠️ *Confirmación requerida*\n\n${message}\n\nResponde *SI* para confirmar o *NO* para cancelar.`);
}

function isYes(text) {
  return ['si', 'sí', 's', 'confirmar', 'ok'].includes(String(text || '').trim().toLowerCase());
}

function isNo(text) {
  return ['no', 'n', 'cancelar', 'salir', '0'].includes(String(text || '').trim().toLowerCase());
}

function parsePrice(value) {
  const clean = String(value || '').replace(',', '.').replace(/[^\d.]/g, '');
  const price = Number.parseFloat(clean);
  return Number.isFinite(price) ? Math.round(price * 100) / 100 : null;
}

function productLine(p) {
  const estado = p.activo ? 'activo' : 'inactivo';
  const tipo = p.es_combo ? 'combo' : (p.tipo_entrega || 'producto');
  return `#${p.id} ${p.nombre} · ${formatPrecio(p.precio)} · ${estado} · ${tipo}`;
}

function customerLine(c) {
  return `#${c.id} ${c.nombre || 'Cliente'} · ${c.telefono || 'sin teléfono'} · ${c.puntos || 0} puntos`;
}

async function findProductById(productId) {
  const data = await oxidianGet(`/admin/productos/buscar?q=${encodeURIComponent(productId)}`);
  const productos = Array.isArray(data.productos) ? data.productos : [];
  return productos.find(p => Number(p.id) === Number(productId)) || null;
}

async function findCustomerByPhone(phone) {
  const data = await oxidianGet(`/admin/clientes/buscar?telefono=${encodeURIComponent(phone)}`);
  return data.cliente || null;
}

function adminListText() {
  const owner = normalizePhone(OWNER_NUMBER);
  const statics = staticAdminPhones();
  const runtime = runtimeAdminPhones();
  const all = adminPhones();
  const lines = all.map(phone => {
    if (phone === owner) return `${phone} · owner`;
    if (statics.includes(phone)) return `${phone} · fijo por entorno`;
    if (runtime.includes(phone)) return `${phone} · agregado por WhatsApp`;
    return phone;
  });
  return lines.length ? lines.join('\n') : 'No hay admins configurados.';
}

function logCount(evento, seconds = 3600, nivel = null) {
  try {
    if (nivel) {
      return db.prepare(`SELECT COUNT(*) as c FROM logs WHERE evento = ? AND nivel = ? AND created_at >= unixepoch()-?`).get(evento, nivel, seconds).c;
    }
    return db.prepare(`SELECT COUNT(*) as c FROM logs WHERE evento = ? AND created_at >= unixepoch()-?`).get(evento, seconds).c;
  } catch { return 0; }
}

function formatAntiBanStatus() {
  const muted = listMutedClients(50).length;
  const cooldowns = [...blockedInboundUntil.values()].filter(until => until > Date.now()).length;
  const sends = logCount('send_ok', 3600);
  const failed = logCount('send_fail', 3600) + logCount('send_error', 3600) + logCount('send_failed_all', 3600);
  const duplicates = logCount('outbound_duplicate_skip', 3600);
  const targetLimited = logCount('outbound_target_limited', 3600);
  const inboundLimited = logCount('message_rate_limited', 3600) + logCount('message_abuse_cooldown', 3600);
  const apiLimited = logCount('api_rate_limited', 3600);
  const broadcastRejected = logCount('broadcast_rejected', 3600);
  const pressure = sends >= 35 || failed >= 5 || duplicates >= 3 || targetLimited >= 1 || inboundLimited >= 10;
  return (
    `🛡️ *Estado Anti-ban / Reputación*\n\n` +
    `Últimos 60 min:\n` +
    `Enviados OK: ${sends}\n` +
    `Errores envío: ${failed}\n` +
    `Duplicados bloqueados: ${duplicates}\n` +
    `Destinatarios limitados: ${targetLimited}\n` +
    `Broadcasts rechazados: ${broadcastRejected}\n` +
    `Entradas limitadas: ${inboundLimited}\n` +
    `APIs limitadas: ${apiLimited}\n\n` +
    `Cooldowns activos: ${cooldowns}\n` +
    `Clientes silenciados: ${muted}\n` +
    `Bot automático: ${isBotEnabled() ? 'activo' : 'pausado'}\n\n` +
    `Lectura: *${pressure ? 'vigilar / bajar ritmo' : 'estable'}*`
  );
}

function formatMutedList() {
  const rows = listMutedClients(8);
  if (!rows.length) return 'No hay clientes silenciados.';
  return rows.map(row => {
    const min = Math.max(0, Math.ceil((row.muted_until * 1000 - Date.now()) / 60000));
    return `${row.phone} · ${min} min · ${row.reason || 'sin motivo'}`;
  }).join('\n');
}

function formatRiskList(title, rows) {
  if (!Array.isArray(rows) || !rows.length) return `${title}: 0`;
  const lines = rows.slice(0, 5).map(p =>
    `#${p.numero} · ${p.estado} · ${p.edad_min} min · ${p.cliente || 'cliente'}`
  );
  return `${title}: ${rows.length}\n${lines.join('\n')}`;
}

// Admin interactive menu (numeric choices)
async function handleAdminMenu(jid, ses, opcion) {
  const lower = String(opcion || '').trim();
  switch (lower) {
    case '1':
      return handleAdminCmd(jid, '!status');
    case '2':
      setAdminState(ses, 'admin_store_menu');
      return sendText(jid, adminStoreMenu());
    case '3':
      setAdminState(ses, 'admin_products_menu');
      return sendText(jid, adminProductsMenu());
    case '4':
      setAdminState(ses, 'admin_points_menu');
      return sendText(jid, adminPointsMenu());
    case '5':
      setAdminState(ses, 'admin_admins_menu');
      return sendText(jid, adminAdminsMenu(jid));
    case '6':
      setAdminState(ses, 'admin_handoff_menu');
      return sendText(jid, adminHandoffMenu());
    case '7':
      return handleAdminCmd(jid, '!sync');
    case '8':
      setAdminState(ses, 'admin_security_menu');
      return sendText(jid, adminSecurityMenu());
    case '9':
      setAdminState(ses, 'admin_emergency_menu');
      return sendText(jid, adminEmergencyMenu());
    case '10':
    case '🔟':
      return handleAdminRiskOrders(jid, ses);
    case '11': {
      deleteHandoff(jid);
      clearAdminChatForClient(jid);
      const clientSes = { jid, nombre: ses.nombre, role: 'client', estado: clientStateFor(jid, 'main_menu'), carrito: [], pending: {}, zona_id: null, active_client_jid: null };
      saveSesion(clientSes);
      return sendText(jid, `🧪 *Modo cliente de prueba activado.*\nEscribe *admin* para volver al panel.\n\n${menuPrincipal()}`);
    }
    default:
      return sendText(jid, adminMenu());
  }
}

async function handleAdminStoreMenu(jid, ses, opcion) {
  switch (opcion) {
    case '0':
      return startAdminMenu(jid, ses.nombre);
    case '1': {
      try {
        const data = await oxidianGet('/admin/tienda');
        return sendText(jid, `🏪 *Estado de tienda*\n\nEstado: *${data.estado || 'desconocido'}*\nMensaje de cierre: ${data.mensaje_cierre || 'sin mensaje'}\n\n${adminStoreMenu()}`);
      } catch (e) {
        return sendText(jid, `No pude leer el estado de tienda: ${e.message}\n\n${adminStoreMenu()}`);
      }
    }
    case '2':
      setAdminState(ses, 'admin_store_close_message');
      return sendText(jid, 'Escribe el mensaje de cierre para los clientes. Si no quieres mensaje, escribe *sin mensaje*.');
    case '3':
      return askAdminConfirm(jid, ses, { action: 'open_store' }, 'Vas a abrir la tienda para pedidos web.');
    default:
      return sendText(jid, adminStoreMenu());
  }
}

async function handleAdminStoreCloseMessage(jid, ses, text) {
  const msg = /^sin mensaje$/i.test(String(text || '').trim()) ? '' : String(text || '').trim().slice(0, 240);
  return askAdminConfirm(jid, ses, { action: 'close_store', message: msg }, `Vas a cerrar la tienda temporalmente.${msg ? `\nMensaje: ${msg}` : ''}`);
}

async function handleAdminProductsMenu(jid, ses, opcion) {
  switch (opcion) {
    case '0':
      return startAdminMenu(jid, ses.nombre);
    case '1':
      setAdminState(ses, 'admin_product_search');
      return sendText(jid, 'Escribe nombre o ID del producto.');
    case '2':
      setAdminState(ses, 'admin_product_price_wait');
      return sendText(jid, 'Escribe *ID PRECIO*. Ejemplo: 12 4.50');
    case '3':
      setAdminState(ses, 'admin_product_toggle_wait');
      return sendText(jid, 'Escribe *ID activar* o *ID desactivar*. Ejemplo: 12 desactivar');
    default:
      return sendText(jid, adminProductsMenu());
  }
}

async function handleAdminProductSearch(jid, ses, text) {
  try {
    const data = await oxidianGet(`/admin/productos/buscar?q=${encodeURIComponent(String(text || '').trim())}`);
    const productos = Array.isArray(data.productos) ? data.productos : [];
    setAdminState(ses, 'admin_products_menu');
    if (!productos.length) return sendText(jid, `No encontré productos.\n\n${adminProductsMenu()}`);
    return sendText(jid, `Resultados:\n\n${productos.map(productLine).join('\n')}\n\n${adminProductsMenu()}`);
  } catch (e) {
    setAdminState(ses, 'admin_products_menu');
    return sendText(jid, `Error buscando producto: ${e.message}\n\n${adminProductsMenu()}`);
  }
}

async function handleAdminProductPriceWait(jid, ses, text) {
  const parts = String(text || '').trim().split(/\s+/);
  const productId = Number.parseInt(parts[0], 10);
  const price = parsePrice(parts[1]);
  if (!productId || !price || price <= 0 || price > 1000) {
    return sendText(jid, 'Formato inválido. Escribe *ID PRECIO*. Ejemplo: 12 4.50');
  }
  try {
    const product = await findProductById(productId);
    if (!product) return sendText(jid, 'Producto no encontrado. Escribe *0* para volver o intenta con otro ID.');
    return askAdminConfirm(
      jid,
      ses,
      { action: 'product_price', productId, price },
      `Vas a cambiar el precio:\n${productLine(product)}\nNuevo precio: *${formatPrecio(price)}*`
    );
  } catch (e) {
    return sendText(jid, `No pude validar el producto: ${e.message}`);
  }
}

async function handleAdminProductToggleWait(jid, ses, text) {
  const parts = String(text || '').trim().toLowerCase().split(/\s+/);
  const productId = Number.parseInt(parts[0], 10);
  const word = parts[1] || '';
  const active = ['activar', 'activo', 'on', '1', 'abrir'].includes(word)
    ? true
    : ['desactivar', 'inactivo', 'off', '0', 'cerrar'].includes(word)
      ? false
      : null;
  if (!productId || active === null) {
    return sendText(jid, 'Formato inválido. Escribe *ID activar* o *ID desactivar*.');
  }
  try {
    const product = await findProductById(productId);
    if (!product) return sendText(jid, 'Producto no encontrado. Escribe *0* para volver o intenta con otro ID.');
    return askAdminConfirm(
      jid,
      ses,
      { action: 'product_active', productId, active },
      `Vas a ${active ? 'activar' : 'desactivar'}:\n${productLine(product)}`
    );
  } catch (e) {
    return sendText(jid, `No pude validar el producto: ${e.message}`);
  }
}

async function handleAdminPointsMenu(jid, ses, opcion) {
  switch (opcion) {
    case '0':
      return startAdminMenu(jid, ses.nombre);
    case '1':
      setAdminState(ses, 'admin_customer_search');
      return sendText(jid, 'Escribe el teléfono del cliente. Ejemplo: 622663874');
    case '2':
      setAdminState(ses, 'admin_points_adjust_wait', { sign: 1 });
      return sendText(jid, 'Escribe *TELEFONO PUNTOS*. Ejemplo: 622663874 50');
    case '3':
      setAdminState(ses, 'admin_points_adjust_wait', { sign: -1 });
      return sendText(jid, 'Escribe *TELEFONO PUNTOS*. Ejemplo: 622663874 50');
    case '4':
      setAdminState(ses, 'admin_points_history_wait');
      return sendText(jid, 'Escribe el teléfono del cliente para ver historial.');
    default:
      return sendText(jid, adminPointsMenu());
  }
}

async function handleAdminCustomerSearch(jid, ses, text) {
  try {
    const customer = await findCustomerByPhone(text);
    setAdminState(ses, 'admin_points_menu');
    return sendText(jid, `${customerLine(customer)}\n\n${adminPointsMenu()}`);
  } catch (e) {
    setAdminState(ses, 'admin_points_menu');
    return sendText(jid, `Cliente no encontrado o no disponible.\n\n${adminPointsMenu()}`);
  }
}

async function handleAdminPointsAdjustWait(jid, ses, text) {
  const parts = String(text || '').trim().split(/\s+/);
  const phone = normalizePhone(parts[0]);
  const amount = Number.parseInt(parts[1], 10);
  const sign = ses.pending?.sign === -1 ? -1 : 1;
  if (!/^[0-9]{6,15}$/.test(phone) || !amount || amount <= 0 || amount > 10000) {
    return sendText(jid, 'Formato inválido. Escribe *TELEFONO PUNTOS*. Ejemplo: 622663874 50');
  }
  try {
    const customer = await findCustomerByPhone(phone);
    const delta = amount * sign;
    return askAdminConfirm(
      jid,
      ses,
      { action: 'points_adjust', customerId: customer.id, delta },
      `Vas a ${delta > 0 ? 'agregar' : 'quitar'} *${Math.abs(delta)} puntos* a:\n${customerLine(customer)}`
    );
  } catch (e) {
    return sendText(jid, 'Cliente no encontrado. Revisa el teléfono e intenta otra vez.');
  }
}

async function handleAdminPointsHistoryWait(jid, ses, text) {
  try {
    const customer = await findCustomerByPhone(text);
    const data = await oxidianGet(`/admin/clientes/${customer.id}/puntos/historial`);
    const rows = Array.isArray(data.historial) ? data.historial : [];
    setAdminState(ses, 'admin_points_menu');
    const history = rows.length
      ? rows.map(h => `${h.cantidad > 0 ? '+' : ''}${h.cantidad} · ${h.tipo} · ${h.descripcion || 'sin descripción'}`).join('\n')
      : 'Sin movimientos recientes.';
    return sendText(jid, `${customerLine(data.cliente || customer)}\n\n${history}\n\n${adminPointsMenu()}`);
  } catch (e) {
    setAdminState(ses, 'admin_points_menu');
    return sendText(jid, `No pude consultar historial.\n\n${adminPointsMenu()}`);
  }
}

async function handleAdminAdminsMenu(jid, ses, opcion) {
  switch (opcion) {
    case '0':
      return startAdminMenu(jid, ses.nombre);
    case '1':
      return sendText(jid, `Admins configurados:\n\n${adminListText()}\n\n${adminAdminsMenu(jid)}`);
    case '2':
      if (!isOwnerJid(jid)) return sendText(jid, `Solo el owner puede agregar admins.\n\n${adminAdminsMenu(jid)}`);
      setAdminState(ses, 'admin_admin_add_wait');
      return sendText(jid, 'Escribe el número que quieres agregar como admin.');
    case '3':
      if (!isOwnerJid(jid)) return sendText(jid, `Solo el owner puede eliminar admins.\n\n${adminAdminsMenu(jid)}`);
      setAdminState(ses, 'admin_admin_remove_wait');
      return sendText(jid, 'Escribe el número admin que quieres eliminar. Solo se eliminan admins agregados por WhatsApp.');
    default:
      return sendText(jid, adminAdminsMenu(jid));
  }
}

async function handleAdminAddWait(jid, ses, text) {
  const phone = normalizePhone(text);
  if (!/^[0-9]{6,15}$/.test(phone)) return sendText(jid, 'Número inválido. Intenta de nuevo o escribe 0 para volver.');
  return askAdminConfirm(jid, ses, { action: 'admin_add', phone }, `Vas a agregar como admin WhatsApp a: ${phone}`);
}

async function handleAdminRemoveWait(jid, ses, text) {
  const phone = normalizePhone(text);
  if (!runtimeAdminPhones().includes(phone)) {
    setAdminState(ses, 'admin_admins_menu');
    return sendText(jid, `Ese número no es un admin agregado por WhatsApp.\n\n${adminAdminsMenu(jid)}`);
  }
  return askAdminConfirm(jid, ses, { action: 'admin_remove', phone }, `Vas a eliminar como admin WhatsApp a: ${phone}`);
}

async function handleAdminHandoffMenu(jid, ses, opcion) {
  switch (opcion) {
    case '0':
      return startAdminMenu(jid, ses.nombre);
    case '1': {
      const pending = listPendingHandoffs();
      if (!pending.length) return sendText(jid, `✅ No hay chats pendientes.\n\n${adminHandoffMenu()}`);
      const lista = pending.map((p, i) => `${i + 1}. ${phoneFromJid(p.client_jid)} — solicitado`).join('\n');
      setAdminState(ses, 'admin_take_wait', {
        handoff_client_jids: pending.map(row => row.client_jid),
      });
      return sendText(jid, `📨 Chats pendientes:\n\n${lista}\n\nResponde con el número para tomar el chat.`);
    }
    case '2':
      return handleAdminCmd(jid, '!release');
    case '3':
      for (const row of db.prepare(`SELECT client_jid FROM handoffs WHERE admin_jid=?`).all(jid)) {
        await closeHumanChat(jid, row.client_jid);
      }
      ses.estado = 'admin_handoff_menu';
      ses.active_client_jid = null;
      ses.pending = {};
      saveSesion(ses);
      return sendText(jid, `✅ Chats asignados a ti cerrados.\n\n${adminHandoffMenu()}`);
    default:
      return sendText(jid, adminHandoffMenu());
  }
}

async function handleAdminSecurityMenu(jid, ses, opcion) {
  switch (opcion) {
    case '0':
      return startAdminMenu(jid, ses.nombre);
    case '1':
      return sendText(jid, `${formatAntiBanStatus()}\n\n${adminSecurityMenu()}`);
    case '2':
      setAdminState(ses, 'admin_mute_wait', { durationMs: 60 * 60 * 1000 });
      return sendText(jid, 'Escribe el número del cliente que quieres silenciar por 1 hora.');
    case '3':
      setAdminState(ses, 'admin_mute_wait', { durationMs: 24 * 60 * 60 * 1000 });
      return sendText(jid, 'Escribe el número del cliente que quieres silenciar por 24 horas.');
    case '4':
      setAdminState(ses, 'admin_mute_wait', { durationMs: 0, unmute: true });
      return sendText(jid, 'Escribe el número del cliente que quieres desbloquear.');
    case '5':
      return sendText(jid, `🔇 *Clientes silenciados*\n\n${formatMutedList()}\n\n${adminSecurityMenu()}`);
    default:
      return sendText(jid, adminSecurityMenu());
  }
}

async function handleAdminMuteWait(jid, ses, text) {
  const phone = normalizePhone(text);
  if (!/^[0-9]{6,15}$/.test(phone)) {
    return sendText(jid, 'Número inválido. Escribe un teléfono válido o 0 para volver.');
  }
  if (isAdminPhone(phone)) {
    setAdminState(ses, 'admin_security_menu');
    return sendText(jid, `No puedo silenciar un número administrativo.\n\n${adminSecurityMenu()}`);
  }
  if (ses.pending?.unmute) {
    unmuteClient(phone);
    setAdminState(ses, 'admin_security_menu');
    return sendText(jid, `✅ Cliente ${phone} desbloqueado.\n\n${adminSecurityMenu()}`);
  }
  const durationMs = Number(ses.pending?.durationMs || 60 * 60 * 1000);
  const hours = Math.round(durationMs / 3600000);
  return askAdminConfirm(
    jid,
    ses,
    { action: 'mute_client', phone, durationMs },
    `Vas a silenciar al cliente ${phone} durante ${hours === 1 ? '1 hora' : `${hours} horas`}.`
  );
}

async function handleAdminEmergencyMenu(jid, ses, opcion) {
  switch (opcion) {
    case '0':
      return startAdminMenu(jid, ses.nombre);
    case '1':
      return askAdminConfirm(
        jid,
        ses,
        { action: 'emergency_on' },
        'Vas a activar emergencia: cerrar tienda, pausar bot automático y mostrar mensaje de incidencia.'
      );
    case '2':
      return askAdminConfirm(
        jid,
        ses,
        { action: 'emergency_off' },
        'Vas a volver a normalidad: abrir tienda y activar bot automático.'
      );
    case '3': {
      try {
        const data = await oxidianGet('/admin/tienda');
        return sendText(jid,
          `🚨 *Estado emergencia*\n\n` +
          `Tienda: ${data.estado}\n` +
          `Bot automático: ${isBotEnabled() ? 'activo' : 'pausado'}\n` +
          `Mensaje: ${data.mensaje_cierre || 'sin mensaje'}\n\n` +
          adminEmergencyMenu()
        );
      } catch (e) {
        return sendText(jid, `No pude consultar estado: ${e.message}\n\n${adminEmergencyMenu()}`);
      }
    }
    default:
      return sendText(jid, adminEmergencyMenu());
  }
}

async function handleAdminRiskOrders(jid, ses) {
  try {
    const data = await oxidianGet('/admin/pedidos/riesgo');
    const sections = [
      formatRiskList('Pendientes lentos', data.pendientes_lentos),
      formatRiskList('Armando lentos', data.armando_lentos),
      formatRiskList('Sin preparador', data.sin_preparador),
      formatRiskList('Sin repartidor', data.sin_repartidor),
      formatRiskList('Listos lentos', data.listos_lentos),
      formatRiskList('En ruta lentos', data.ruta_lentos),
    ];
    return sendText(jid, `📦 *Pedidos en riesgo*\n\n${sections.join('\n\n')}\n\n${adminMenu()}`);
  } catch (e) {
    return sendText(jid, `No pude consultar pedidos en riesgo: ${e.message}\n\n${adminMenu()}`);
  }
}

async function handleAdminConfirm(jid, ses, text) {
  const pending = ses.pending || {};
  if (isNo(text)) {
    setAdminState(ses, 'admin_menu');
    return sendText(jid, `❌ Acción cancelada.\n\n${adminMenu()}`);
  }
  if (!isYes(text)) {
    return sendText(jid, `Responde *SI* para confirmar o *NO* para cancelar.`);
  }

  try {
    if (!canRunAdminAction(jid, pending.action || 'unknown')) {
      return sendText(jid, 'Espera unos segundos antes de repetir una acción administrativa.');
    }

    if (pending.action === 'close_store' || pending.action === 'open_store') {
      const cerrada = pending.action === 'close_store';
      const data = await oxidianPost('/admin/tienda', {
        forzar_cerrada: cerrada,
        mensaje_cierre: pending.message || '',
      });
      setAdminState(ses, 'admin_menu');
      return sendText(jid, `✅ *Tienda ${cerrada ? 'cerrada' : 'abierta'}.*\nEstado actual: *${data.estado}*\n\n${adminMenu()}`);
    }

    if (pending.action === 'emergency_on') {
      const msg = 'Estamos resolviendo una incidencia operativa. La tienda queda pausada temporalmente.';
      const data = await oxidianPost('/admin/tienda', {
        forzar_cerrada: true,
        mensaje_cierre: msg,
      });
      setCfg('bot_enabled', '0');
      log('warn', 'emergency_on', `admin=${phoneFromJid(jid)}`);
      setAdminState(ses, 'admin_menu');
      return sendText(jid, `🚨 Emergencia activada.\nTienda: ${data.estado}\nBot automático: pausado\n\n${adminMenu()}`);
    }

    if (pending.action === 'emergency_off') {
      const data = await oxidianPost('/admin/tienda', {
        forzar_cerrada: false,
        mensaje_cierre: '',
      });
      setCfg('bot_enabled', '1');
      log('warn', 'emergency_off', `admin=${phoneFromJid(jid)}`);
      setAdminState(ses, 'admin_menu');
      return sendText(jid, `✅ Normalidad restaurada.\nTienda: ${data.estado}\nBot automático: activo\n\n${adminMenu()}`);
    }

    if (pending.action === 'mute_client') {
      const result = muteClient(
        pending.phone,
        Number(pending.durationMs || 3600000),
        `Silenciado por admin ${phoneFromJid(jid)}`,
        jid
      );
      log('warn', 'client_muted', `${result.phone} until=${result.muted_until}`);
      setAdminState(ses, 'admin_security_menu');
      return sendText(jid, `🔇 Cliente ${result.phone} silenciado.\n\n${adminSecurityMenu()}`);
    }

    if (pending.action === 'product_price') {
      const data = await oxidianPost(`/admin/productos/${pending.productId}/precio`, {
        precio: pending.price,
        motivo: `Cambio por WhatsApp admin ${phoneFromJid(jid)}`,
      });
      await syncCatalogo().catch(() => {});
      setAdminState(ses, 'admin_menu');
      return sendText(jid, `✅ *Precio actualizado* correctamente.\n${productLine(data.producto)}\n\n${adminMenu()}`);
    }

    if (pending.action === 'product_active') {
      const data = await oxidianPost(`/admin/productos/${pending.productId}/activo`, {
        activo: Boolean(pending.active),
      });
      await syncCatalogo().catch(() => {});
      setAdminState(ses, 'admin_menu');
      return sendText(jid, `✅ *Producto actualizado* correctamente.\n${productLine(data.producto)}\n\n${adminMenu()}`);
    }

    if (pending.action === 'points_adjust') {
      const data = await oxidianPost(`/admin/clientes/${pending.customerId}/puntos`, {
        delta: pending.delta,
        motivo: `Ajuste por WhatsApp admin ${phoneFromJid(jid)}`,
      });
      setAdminState(ses, 'admin_menu');
      return sendText(jid, `✅ *Puntos actualizados.*\n${customerLine(data.cliente)}\nAntes: *${data.puntos_antes}* · Después: *${data.puntos_despues}*\n\n${adminMenu()}`);
    }

    if (pending.action === 'admin_add') {
      const list = setRuntimeAdmins([...runtimeAdminPhones(), pending.phone]);
      sanitizeRuntimeState();
      setAdminState(ses, 'admin_menu');
      return sendText(jid, `✅ Admin agregado.\n\nAdmins por WhatsApp: ${list.join(', ') || 'ninguno'}\n\n${adminMenu()}`);
    }

    if (pending.action === 'admin_remove') {
      const list = setRuntimeAdmins(runtimeAdminPhones().filter(phone => phone !== pending.phone));
      sanitizeRuntimeState();
      setAdminState(ses, 'admin_menu');
      return sendText(jid, `✅ Admin eliminado.\n\nAdmins por WhatsApp: ${list.join(', ') || 'ninguno'}\n\n${adminMenu()}`);
    }

    setAdminState(ses, 'admin_menu');
    return sendText(jid, `Acción no reconocida.\n\n${adminMenu()}`);
  } catch (e) {
    setAdminState(ses, 'admin_menu');
    return sendText(jid, `No se pudo completar la acción: ${e.message}\n\n${adminMenu()}`);
  }
}

async function handleAdminTakeWait(jid, ses, opcion) {
  const idx = parseInt(String(opcion || '').trim(), 10);
  if (isNaN(idx)) {
    ses.estado = 'admin_menu'; saveSesion(ses);
    return sendText(jid, 'Número inválido. Volviendo al menú admin.');
  }
  const snapshot = Array.isArray(ses.pending?.handoff_client_jids)
    ? ses.pending.handoff_client_jids
    : [];
  if (idx < 1 || idx > snapshot.length) {
    ses.estado = 'admin_menu'; saveSesion(ses);
    return sendText(jid, 'Índice fuera de rango. Volviendo al menú admin.');
  }
  const clientJid = snapshot[idx - 1];
  const current = getHandoff(clientJid);
  if (!current || current.admin_jid) {
    ses.estado = 'admin_handoff_menu';
    ses.pending = {};
    saveSesion(ses);
    return sendText(jid, `Ese chat ya no está disponible. Abre de nuevo la lista para actualizarla.\n\n${adminHandoffMenu()}`);
  }
  return takeHandoff(jid, ses, clientJid);
}

// ─── WEBHOOK HANDLER ──────────────────────────────────────────────────────────
async function handleEvolutionEvent(payload, messageHandler = handleMessage) {
  // Evolution API puede enviar varios eventos
  const event = payload.event;

  if (event === 'messages.upsert') {
    const msgs = Array.isArray(payload.data?.messages)
      ? payload.data.messages
      : [payload.data];

    for (const msg of msgs) {
      if (!msg || msg.key?.fromMe) continue;                     // ignorar propios
      const jid  = msg.key?.remoteJid;
      if (!jid || jid.endsWith('@g.us')) continue;               // ignorar grupos

      const text = extractText(msg);
      if (!text) continue;
      if (text.length > MAX_MESSAGE_CHARS) {
        log('warn', 'message_too_long_skip', `${jid} chars=${text.length}`);
        continue;
      }

      const name = msg.pushName || msg.key?.participant || '';
      log('info', 'message_in', `${jid} → ${text.slice(0, 50)}`);

      await messageHandler(jid, text, name);
    }
  }

  // Evento de conexión
  if (event === 'connection.update') {
    const state = payload.data?.state;
    log('info', 'connection', state || 'unknown');
    const qr = extractQrDataUrl(payload);
    if (qr) {
      lastQrDataUrl = qr;
      lastQrAt = Date.now();
      log('info', 'qr_updated', 'QR recibido desde Evolution');
    }
    if (state === 'open') log('info', 'wa_connected', 'WhatsApp listo');
  }

  if (event === 'qrcode.updated') {
    const qr = extractQrDataUrl(payload);
    if (qr) {
      lastQrDataUrl = qr;
      lastQrAt = Date.now();
      log('info', 'qr_updated', 'QR recibido desde Evolution');
    }
  }
}

let drainingInbound = false;
function persistInboundMessages(payload) {
  if (payload?.event !== 'messages.upsert') return 0;
  const msgs = Array.isArray(payload.data?.messages) ? payload.data.messages : [payload.data];
  const insert = db.prepare(`
    INSERT OR IGNORE INTO inbound_messages (message_id, payload_json)
    VALUES (?, ?)
  `);
  const persistBatch = db.transaction(batch => {
    let changes = 0;
    for (const msg of batch) {
      if (!msg) continue;
      const messageId = String(
        msg.key?.id
        || crypto.createHash('sha256').update(JSON.stringify(msg)).digest('hex')
      );
      changes += insert.run(messageId, JSON.stringify({
        event: 'messages.upsert',
        data: msg,
      })).changes;
    }
    return changes;
  });
  let inserted = 0;
  for (let offset = 0; offset < msgs.length; offset += MAX_WEBHOOK_MESSAGES) {
    inserted += persistBatch(msgs.slice(offset, offset + MAX_WEBHOOK_MESSAGES));
  }
  return inserted;
}

async function drainInboundMessages(eventHandler = handleEvolutionEvent) {
  if (drainingInbound) return;
  drainingInbound = true;
  try {
    while (true) {
      const row = db.prepare(`
        SELECT message_id, payload_json, attempts
        FROM inbound_messages
        WHERE processed_at IS NULL
        ORDER BY created_at, rowid
        LIMIT 1
      `).get();
      if (!row) break;
      db.prepare(`UPDATE inbound_messages SET attempts=attempts+1 WHERE message_id=?`).run(row.message_id);
      const attempts = Number(row.attempts || 0) + 1;
      try {
        await eventHandler(JSON.parse(row.payload_json));
        db.prepare(`
          UPDATE inbound_messages SET processed_at=unixepoch() WHERE message_id=?
        `).run(row.message_id);
      } catch (error) {
        log('error', 'inbound_process_fail', `${row.message_id} attempt=${attempts}: ${String(error)}`);
        if (attempts >= 5) {
          db.prepare(`
            UPDATE inbound_messages SET processed_at=unixepoch() WHERE message_id=?
          `).run(row.message_id);
          log('error', 'inbound_dead_letter', `${row.message_id} attempts=${attempts}`);
          continue;
        }
        break;
      }
    }
    db.prepare(`DELETE FROM inbound_messages WHERE processed_at < unixepoch()-86400`).run();
  } finally {
    drainingInbound = false;
  }
}

// ─── EXPRESS SERVER ───────────────────────────────────────────────────────────
const app = express();
const _CORS_ORIGIN = process.env.CORS_ORIGIN || TIENDA_URL || OXIDIAN_URL;
function clientIp(req) {
  return String(req.headers['x-forwarded-for'] || req.socket?.remoteAddress || 'unknown').split(',')[0].trim();
}

function apiRateLimit(req, res, next) {
  const key = `${clientIp(req)}:${req.path}`;
  const hit = hitWindow(apiBuckets, key, API_WINDOW_MS, MAX_API_HITS_PER_WINDOW);
  if (!hit.allowed) {
    log('warn', 'api_rate_limited', key);
    return res.status(429).json({ ok: false, error: 'rate limit exceeded' });
  }
  return next();
}

app.use((req, res, next) => {
  const origin = req.headers.origin || '';
  const isLocal = origin.startsWith('http://localhost') || origin.startsWith('http://127.0.0.1');
  if (origin === _CORS_ORIGIN || (process.env.NODE_ENV !== 'production' && isLocal)) {
    res.setHeader('Access-Control-Allow-Origin', origin);
  } else {
    res.setHeader('Access-Control-Allow-Origin', _CORS_ORIGIN);
  }
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type, X-API-Key, X-Bot-Key, X-Panel-Key');
  res.setHeader('Access-Control-Allow-Methods', 'GET,POST,OPTIONS');
  if (req.method === 'OPTIONS') return res.sendStatus(204);
  next();
});
app.use(express.json({ limit: process.env.BOT_JSON_LIMIT || '512kb' }));
app.use(apiRateLimit);
app.use(express.static(path.join(__dirname, 'public')));

// Webhook principal de Evolution API
app.post('/webhook/evolution', (req, res) => {
  if (!requireWebhookSecret(req, res)) return;
  if (req.body?.event === 'messages.upsert') {
    try {
      const inserted = persistInboundMessages(req.body);
      res.status(200).json({ ok: true, queued: inserted });
      drainInboundMessages().catch(e => log('error', 'webhook_drain', String(e)));
    } catch (error) {
      log('error', 'webhook_persist_fail', String(error));
      res.status(500).json({ ok: false, error: 'inbound persistence failed' });
    }
    return;
  }
  res.status(200).json({ ok: true });
  handleEvolutionEvent(req.body).catch(e => log('error', 'webhook', String(e)));
});

// Health check
app.get('/health', (req, res) => {
  res.json({
    ok: true,
    service: 'chatbot',
    engine: 'evolution-api',
    evolution_url: EVO_URL,
    instance: EVO_INSTANCE,
    simulate_send: SIMULATE_EVO_SEND,
    ts: new Date().toISOString(),
  });
});

// Estado para el panel de admin de Flask
app.get('/api/status', async (req, res) => {
  if (!requireApiKey(req, res, { panel: true })) return;
  const prods = db.prepare(`SELECT COUNT(*) as c FROM productos_cache WHERE activo=1`).get().c;
  const logs24 = db.prepare(`SELECT COUNT(*) as c FROM logs WHERE nivel='error' AND created_at >= unixepoch()-86400`).get().c;
  const sesiones = db.prepare(`SELECT COUNT(*) as c FROM sessions`).get().c;
  const clientSessions = db.prepare(`SELECT COUNT(*) as c FROM sessions WHERE role='client'`).get().c;
  const adminSessions = db.prepare(`SELECT COUNT(*) as c FROM sessions WHERE role='admin'`).get().c;
  const pendingHandoffs = listPendingHandoffs().length;
  const activeHandoffs = db.prepare(`SELECT COUNT(*) as c FROM handoffs WHERE admin_jid IS NOT NULL`).get().c;
  const availableAdmins = availableAdminJids().length;
  const undeliveredMessages = db.prepare(`SELECT COUNT(*) as c FROM handoff_messages WHERE delivered_at IS NULL`).get().c;
  const deliveryRetries = db.prepare(`
    SELECT COALESCE(SUM(attempts), 0) AS total,
           COALESCE(MAX(attempts), 0) AS max_attempts,
           MIN(created_at) AS oldest
    FROM handoff_messages
    WHERE delivered_at IS NULL
  `).get();
  const inboundQueue = db.prepare(`
    SELECT COUNT(*) AS pending,
           COALESCE(MAX(attempts), 0) AS max_attempts
    FROM inbound_messages
    WHERE processed_at IS NULL
  `).get();
  const oldestPending = db.prepare(`
    SELECT requested_at FROM handoffs WHERE admin_jid IS NULL ORDER BY requested_at LIMIT 1
  `).get();
  const lastCatalogSync = db.prepare(`SELECT created_at, detalle FROM logs WHERE evento='catalog_sync' ORDER BY id DESC LIMIT 1`).get() || null;
  let evolutionState = 'unknown';
  try {
    const r = await fetch(`${getEvolutionUrl()}/instance/connectionState/${getEvolutionInstance()}`, {
      headers: { apikey: getEvolutionKey() },
      signal: AbortSignal.timeout(2500),
    });
    const d = await r.json().catch(() => ({}));
    evolutionState = d.instance?.state || d.state || evolutionState;
  } catch {}
  if (evolutionState === 'open') {
    lastQrDataUrl = null;
    lastQrAt = 0;
  } else if (!lastQrDataUrl || Date.now() - lastQrAt > 55_000) {
    await refreshEvolutionQr();
  }
  res.json({
    ok: true,
    connected: evolutionState === 'open',
    engine: 'evolution-api',
    instance: getEvolutionInstance(),
    evolution_state: evolutionState,
    qrDataUrl: lastQrDataUrl,
    evolution_url: getEvolutionUrl(),
    oxidian_url: getOxidianUrl(),
    tienda_url: getTiendaUrl(),
    productos_cache: prods,
    errores_24h: logs24,
    activeSessions: sesiones,
    sessions: { total: sesiones, client: clientSessions, admin: adminSessions },
    handoffs: {
      pending: pendingHandoffs,
      active: activeHandoffs,
      available_admins: availableAdmins,
      undelivered_messages: undeliveredMessages,
      delivery_retry_attempts: Number(deliveryRetries.total || 0),
      delivery_max_attempts: Number(deliveryRetries.max_attempts || 0),
      oldest_undelivered_seconds: deliveryRetries.oldest
        ? Math.max(0, Math.floor(Date.now() / 1000) - deliveryRetries.oldest)
        : 0,
      oldest_pending_seconds: oldestPending
        ? Math.max(0, Math.floor(Date.now() / 1000) - oldestPending.requested_at)
        : 0,
    },
    admins: {
      configured: adminPhones(),
      static: staticAdminPhones(),
      runtime: runtimeAdminPhones(),
    },
    inbound_queue: {
      pending: Number(inboundQueue.pending || 0),
      max_attempts: Number(inboundQueue.max_attempts || 0),
    },
    last_catalog_sync: lastCatalogSync,
    webhook_secret_configured: Boolean(String(cfg('webhook_secret', WEBHOOK_SECRET) || '').trim()),
    evolution_key_configured: Boolean(getEvolutionKey()),
    oxidian_key_configured: Boolean(getOxidianKey()),
    panel_key_configured: Boolean(getPanelKey()),
    pedidosHoy: 0,
    botEnabled: isBotEnabled(),
    panicMode: !isBotEnabled(),
    ai: { groq: { activo: false }, gemini: { activo: false }, quota: { exhausted: false } },
    uptime: process.uptime(),
  });
});

app.post('/api/bot/set-key', (req, res) => {
  try {
    if (!requireApiKey(req, res, { panel: true })) return;
    const key = String(req.body?.key || '').trim();
    const panelKey = String(req.body?.panel_key || req.body?.panelKey || '').trim();
    if (!key || key.length < 8) {
      return res.status(400).json({ ok: false, error: 'key must have at least 8 characters' });
    }
    setCfg('oxidian_key', key);
    if (panelKey) setCfg('panel_key', panelKey);
    log('info', 'config_set_key', 'Credenciales runtime actualizadas');
    return res.json({ ok: true, message: 'Credenciales del bot actualizadas' });
  } catch (e) {
    log('error', 'config_set_key', String(e));
    return res.status(500).json({ ok: false, error: String(e) });
  }
});

app.post('/api/oxidian/key', (req, res) => {
  try {
    if (!requireApiKey(req, res, { panel: true })) return;
    const key = String(req.body?.key || '').trim();
    const panelKey = String(req.body?.panel_key || req.body?.panelKey || '').trim();
    const url = cleanBaseUrl(req.body?.url, '');
    const tiendaUrl = cleanBaseUrl(req.body?.tienda_url || req.body?.tiendaUrl, '');
    if (!key || key.length < 8) {
      return res.status(400).json({ ok: false, error: 'key must have at least 8 characters' });
    }
    if (url) setCfg('oxidian_url', url);
    if (tiendaUrl) setCfg('tienda_url', tiendaUrl);
    setCfg('oxidian_key', key);
    if (panelKey) setCfg('panel_key', panelKey);
    log('info', 'config_oxidian', `Oxidian runtime configurado: ${url || getOxidianUrl()}`);
    return res.json({ ok: true, message: 'Conexión Oxidian del bot actualizada' });
  } catch (e) {
    log('error', 'config_oxidian', String(e));
    return res.status(500).json({ ok: false, error: String(e) });
  }
});

app.post('/api/evolution/config', (req, res) => {
  try {
    if (!requireApiKey(req, res, { panel: true })) return;
    const evolutionUrl = cleanBaseUrl(req.body?.evolution_url || req.body?.evolutionUrl, '');
    const evolutionKey = String(req.body?.evolution_key || req.body?.evolutionKey || '').trim();
    const evolutionInstance = String(req.body?.evolution_instance || req.body?.evolutionInstance || '').trim();
    const webhookSecret = String(req.body?.webhook_secret || req.body?.webhookSecret || '').trim();
    if (evolutionUrl) setCfg('evolution_url', evolutionUrl);
    if (evolutionKey) setCfg('evolution_key', evolutionKey);
    if (evolutionInstance) setCfg('evolution_instance', evolutionInstance);
    if (webhookSecret) setCfg('webhook_secret', webhookSecret);
    lastQrDataUrl = null;
    lastQrAt = 0;
    log('info', 'config_evolution', `Evolution runtime configurado: ${evolutionUrl || getEvolutionUrl()} / ${evolutionInstance || getEvolutionInstance()}`);
    return res.json({ ok: true, message: 'Conexión Evolution del bot actualizada' });
  } catch (e) {
    log('error', 'config_evolution', String(e));
    return res.status(500).json({ ok: false, error: String(e) });
  }
});

app.post('/api/admins/config', (req, res) => {
  try {
    if (!requireApiKey(req, res, { panel: true })) return;
    const raw = Array.isArray(req.body?.admins)
      ? req.body.admins
      : String(req.body?.admins || '').split(',');
    const invalid = raw
      .map(value => String(value || '').trim())
      .filter(Boolean)
      .filter(value => {
        const phone = normalizePhone(value);
        return phone.length < 7 || phone.length > 15;
      });
    if (invalid.length) {
      return res.status(400).json({ ok: false, error: 'invalid admin phone list' });
    }
    const result = replaceRuntimeAdmins(raw);
    log(
      'info',
      'config_admins',
      `runtime=${result.runtime.length} removed=${result.removed.length}`,
    );
    return res.json({
      ok: true,
      message: 'Administradores del chatbot actualizados',
      admins: {
        configured: adminPhones(),
        static: staticAdminPhones(),
        runtime: result.runtime,
        removed: result.removed,
      },
    });
  } catch (e) {
    log('error', 'config_admins', String(e));
    return res.status(500).json({ ok: false, error: 'could not update admins' });
  }
});

app.post('/api/bot/power', (req, res) => {
  try {
    if (!requireApiKey(req, res, { panel: true })) return;
    const enabled = req.body?.enabled !== false && req.body?.enabled !== '0';
    setCfg('bot_enabled', enabled ? '1' : '0');
    log('info', 'bot_power', enabled ? 'enabled' : 'paused');
    return res.json({ ok: true, message: enabled ? 'Bot activado' : 'Bot pausado', botEnabled: enabled });
  } catch (e) {
    log('error', 'bot_power', String(e));
    return res.status(500).json({ ok: false, error: String(e) });
  }
});

app.post('/api/bot/reset', async (req, res) => {
  try {
    if (!requireApiKey(req, res, { panel: true })) return;
    const full = req.body?.full === true || req.body?.full === '1';
    db.prepare(`DELETE FROM sessions`).run();
    db.prepare(`DELETE FROM handoffs`).run();
    db.prepare(`DELETE FROM handoff_messages`).run();
    db.prepare(`DELETE FROM admin_availability`).run();
    if (full) {
      db.prepare(`DELETE FROM productos_cache`).run();
      db.prepare(`DELETE FROM zonas_cache`).run();
      db.prepare(`DELETE FROM logs WHERE created_at < unixepoch()-86400`).run();
    }
    await refreshEvolutionQr();
    log('warn', 'bot_reset', full ? 'full' : 'sessions');
    return res.json({ ok: true, message: full ? 'Cache y sesiones reiniciadas' : 'Sesiones reiniciadas' });
  } catch (e) {
    log('error', 'bot_reset', String(e));
    return res.status(500).json({ ok: false, error: String(e) });
  }
});

// Endpoint simple para recibir llamadas desde Oxidian u otros servicios
app.post('/api/bot/message', async (req, res) => {
  try {
    if (!requireApiKey(req, res)) return;
    const { telefono, mensaje } = req.body || {};
    if (!telefono || !mensaje) return res.status(400).json({ ok: false, error: 'missing fields' });
    if (String(mensaje || '').length > MAX_OUTBOUND_CHARS) {
      return res.status(400).json({ ok: false, error: 'message too long' });
    }
    const jid = `${normalizePhone(telefono)}@s.whatsapp.net`;
    const sent = await sendText(jid, mensaje);
    return res.json({ ok: !!sent });
  } catch (e) {
    log('error', 'api_send', String(e));
    return res.status(500).json({ ok: false, error: String(e) });
  }
});

app.post('/api/bot/broadcast', async (req, res) => {
  try {
    if (!requireApiKey(req, res)) return;
    const mensajes = Array.isArray(req.body?.mensajes) ? req.body.mensajes : [];
    const validos = mensajes.filter(m => normalizePhone(m.telefono) && String(m.mensaje || '').trim());
    if (!validos.length) return res.status(400).json({ ok: false, error: 'mensajes[] requerido' });
    if (validos.length > MAX_BROADCAST_MESSAGES) {
      log('warn', 'broadcast_rejected', `${validos.length} mensajes excede ${MAX_BROADCAST_MESSAGES}`);
      return res.status(413).json({
        ok: false,
        error: `broadcast limit exceeded (${MAX_BROADCAST_MESSAGES})`,
      });
    }
    let enviados = 0;
    for (const msg of validos) {
      const ok = await sendText(`${normalizePhone(msg.telefono)}@s.whatsapp.net`, String(msg.mensaje).trim());
      if (ok) enviados++;
    }
    return res.json({ ok: true, total: validos.length, enviados });
  } catch (e) {
    log('error', 'api_broadcast', String(e));
    return res.status(500).json({ ok: false, error: String(e) });
  }
});

app.post('/api/bot/review-request', async (req, res) => {
  try {
    if (!requireApiKey(req, res)) return;
    const { telefono, pedido_id, numero_pedido } = req.body || {};
    const phone = normalizePhone(telefono);
    if (!phone || !pedido_id) return res.status(400).json({ ok: false, error: 'telefono y pedido_id requeridos' });
    const texto =
      `⭐ *¿Cómo estuvo tu pedido ${numero_pedido || pedido_id}?*\n\n` +
      `¡Tu opinión nos importa mucho! 😊\n` +
      `Responde con una nota del *1 al 5* y, si quieres, cuéntanos cómo fue.\n\n` +
      `Tu feedback nos ayuda a seguir mejorando. ¡Gracias! 💛`;
    const sent = await sendText(`${phone}@s.whatsapp.net`, texto);
    return res.json({ ok: !!sent });
  } catch (e) {
    log('error', 'api_review_request', String(e));
    return res.status(500).json({ ok: false, error: String(e) });
  }
});

app.post('/api/oxidian/sync', async (req, res) => {
  try {
    if (!requireApiKey(req, res, { panel: true })) return;
    const catalogo = await syncCatalogo();
    await syncZonas();
    const prods = db.prepare(`SELECT COUNT(*) as c FROM productos_cache WHERE activo=1`).get().c;
    return res.json({ ok: true, catalogo, productos_cache: prods });
  } catch (e) {
    log('error', 'api_sync', String(e));
    return res.status(500).json({ ok: false, error: String(e) });
  }
});

function gracefulShutdown(signal) {
  log('warn', 'shutdown', `Señal ${signal} recibida. Cerrando limpiamente...`);
  try {
    db.close();
    log('info', 'shutdown', 'SQLite cerrado correctamente');
  } catch {}
  process.exit(signal === 'uncaughtException' ? 1 : 0);
}

function startServer() {
  process.on('SIGTERM', () => gracefulShutdown('SIGTERM'));
  process.on('SIGINT',  () => gracefulShutdown('SIGINT'));
  process.on('uncaughtException', (err) => {
    log('error', 'uncaught', err?.message || String(err));
    gracefulShutdown('uncaughtException');
  });
  process.on('unhandledRejection', (reason) => {
    log('error', 'unhandledRejection', String(reason));
  });

  return app.listen(PORT, HOST, () => {
    sanitizeRuntimeState();
    db.prepare(`UPDATE admin_availability SET available=0, updated_at=unixepoch()`).run();
    recoverOrphanedHandoffs(false);
    drainInboundMessages().catch(error => log('warn', 'inbound_resume_fail', String(error)));
    console.log(`\n🚀 Oxidian Bot (Evolution API) arrancado en ${HOST}:${PORT}`);
    console.log(`   Evolution: ${EVO_URL} / instancia: ${EVO_INSTANCE}`);
    console.log(`   Oxidian:   ${getOxidianUrl()}`);
    console.log(`   Webhook:   POST /webhook/evolution\n`);

    setTimeout(async () => {
      const cacheCount = db.prepare('SELECT COUNT(*) as c FROM productos_cache WHERE activo=1').get().c;
      if (cacheCount === 0) {
        await syncCatalogo().catch(err => log('warn', 'init-sync', `Sync inicial fallido: ${err.message}`));
      } else {
        await syncCatalogo().catch(() => {});
      }
      await syncZonas().catch(() => {});
      await syncBusiness().catch(() => {});
    }, 3000);
    setInterval(() => syncCatalogo().catch(() => {}), 5 * 60_000);
    setInterval(() => syncBusiness().catch(() => {}), 5 * 60_000);
    setInterval(() => retryPendingHandoffMessages().catch(
      error => log('warn', 'handoff_retry_fail', String(error))
    ), 15_000);
    setInterval(() => drainInboundMessages().catch(
      error => log('warn', 'inbound_drain_fail', String(error))
    ), 10_000);
  });
}

if (require.main === module && process.env.BOT_TEST_MODE !== '1') {
  startServer();
}

module.exports = {
  app,
  startServer,
  _test: {
    db,
    assignHandoff,
    closeHumanChat,
    releaseHumanChat,
    closeHumanChatByClient,
    createHandoffRequest,
    deliverQueuedTranscript,
    drainInboundMessages,
    extractText,
    getHandoff,
    handleEvolutionEvent,
    handleAdminTakeWait,
    pendingHandoffTranscript,
    persistInboundMessages,
    queueAssignedHandoffMessage,
    queueHandoffMessage,
    getSesion,
    saveSesion,
    setAdminState,
    splitTextForSend,
  },
};
