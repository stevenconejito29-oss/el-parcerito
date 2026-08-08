/**
 * Selector de variantes de mensaje con memoria corta anti-repetición.
 *
 * Motivación anti-ban:
 *   WhatsApp Business detecta cuentas que envían strings IDÉNTICOS a
 *   múltiples destinatarios en poco tiempo. Con 30-50 pedidos/día, si
 *   5-8 clientes preguntan "¿horario?" en un par de horas, el bot antes
 *   contestaba con el mismo texto byte-a-byte. Este helper rota entre
 *   variantes semánticamente equivalentes.
 *
 * Diseño:
 *   - Pool = array de N strings (idealmente 3-5). N=1 devuelve directo.
 *   - Memoria en RAM por (jid, faqName) — recuerda el índice usado la
 *     última vez y lo excluye del sorteo siguiente. Así el mismo cliente
 *     preguntando dos veces seguidas recibe respuestas distintas.
 *   - TTL de 30 min: pasado ese tiempo, olvida y puede repetir. Evita
 *     memory leak sin necesidad de cron.
 *   - Aleatoriedad reforzada con jitter de tiempo (unbiased sample).
 *
 * NO reemplaza los otros gates anti-ban (fingerprint global, rate limit,
 * dedupe por destinatario). Complementa: reduce la señal de rigidez.
 */
'use strict';

const MEMORY_TTL_MS = 30 * 60 * 1000; // 30 min
const MAX_ENTRIES = 5000;             // techo defensivo — 500 clientes × 10 FAQs

// key = `${jid}::${faqName}` → { lastIdx, ts }
const _memory = new Map();


function _now() {
  return Date.now();
}


function _prune() {
  // Limpieza oportunista: solo cuando el mapa crece. Cada llamada mira
  // O(1) por bucket; no recorremos todo salvo si superamos el techo.
  if (_memory.size <= MAX_ENTRIES) return;
  const cutoff = _now() - MEMORY_TTL_MS;
  for (const [key, entry] of _memory) {
    if (entry.ts < cutoff) _memory.delete(key);
    if (_memory.size <= MAX_ENTRIES) break;
  }
}


/**
 * Devuelve un elemento del pool evitando repetir el último enviado
 * bajo la misma clave. Si el pool tiene <= 1, devuelve el único
 * (o null si está vacío).
 *
 * @param {Array<string>} pool - variantes semánticamente equivalentes
 * @param {string} key - identificador estable por (cliente, tema).
 *                       Convención: `${jid}::${faqName}`.
 * @returns {string|null}
 */
function pickVariant(pool, key) {
  if (!Array.isArray(pool) || pool.length === 0) return null;
  if (pool.length === 1) return pool[0];

  const now = _now();
  const entry = _memory.get(key);
  const lastIdx = (entry && (now - entry.ts) < MEMORY_TTL_MS) ? entry.lastIdx : -1;

  // Sorteo excluyendo lastIdx. Si lastIdx no es válido (primera vez o
  // memoria caducada), sorteo entre todos los índices.
  const candidates = [];
  for (let i = 0; i < pool.length; i++) {
    if (i !== lastIdx) candidates.push(i);
  }
  const pickedIdx = candidates[Math.floor(Math.random() * candidates.length)];

  _memory.set(key, { lastIdx: pickedIdx, ts: now });
  _prune();
  return pool[pickedIdx];
}


/**
 * Adapta la salida de un `faq.answer(ctx)` que puede devolver string
 * (retro-compat) o array de variantes (nuevo). Si es array, aplica
 * pickVariant con la clave (jid, faqName). Si es string, la pasa tal cual.
 *
 * @param {string|Array<string>|null} value
 * @param {string} jid
 * @param {string} faqName
 * @returns {string|null}
 */
function resolveAnswer(value, jid, faqName) {
  if (value == null) return null;
  if (Array.isArray(value)) {
    return pickVariant(value, `${jid || 'anon'}::${faqName || 'default'}`);
  }
  return value;
}


// Solo para tests: reset del estado interno.
function _reset() {
  _memory.clear();
}


// Solo para tests: inspección del tamaño.
function _size() {
  return _memory.size;
}


module.exports = {
  pickVariant,
  resolveAnswer,
  _reset,
  _size,
  MEMORY_TTL_MS,
};
