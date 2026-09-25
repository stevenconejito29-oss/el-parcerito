'use strict';
// Ritmo de conexión y de interfaz. No garantiza evitar restricciones del proveedor.
function milliseconds(value, fallback, max = 15000) {
  if (value === undefined || String(value).trim() === '') return fallback;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? Math.min(max, Math.max(0, Math.floor(parsed))) : fallback;
}
function range(env, prefix, defaults) {
  const min = milliseconds(env[`${prefix}_MIN_MS`], defaults[0]);
  return [min, Math.max(min, milliseconds(env[`${prefix}_MAX_MS`], defaults[1]))];
}
function between(bounds, random = Math.random) {
  return Math.floor(bounds[0] + random() * (bounds[1] - bounds[0]));
}
function proportional(text, base, perChar, max, random = Math.random) {
  return Math.min(max, Math.floor((base + String(text || '').length * perChar) * (0.85 + random() * 0.3)));
}
function createPacing(env = process.env) {
  const reply = [milliseconds(env.BOT_HUMANIZE_BASE_MS, 450), milliseconds(env.BOT_HUMANIZE_PER_CHAR_MS, 22, 100), milliseconds(env.BOT_HUMANIZE_MAX_MS, 4500)];
  const reading = [milliseconds(env.BOT_READING_BASE_MS, 300), milliseconds(env.BOT_READING_PER_CHAR_MS, 15, 100), milliseconds(env.BOT_READING_MAX_MS, 4000)];
  const receipt = range(env, 'BOT_READ_RECEIPT', [200, 800]);
  const first = range(env, 'BOT_FIRST_TOUCH', [800, 2500]);
  return {
    outboundMin: milliseconds(env.BOT_MIN_OUTBOUND_MS, 850),
    replyDelay: (text, random) => proportional(text, ...reply, random),
    readingDelay: (text, random) => proportional(text, ...reading, random),
    receiptDelay: random => between(receipt, random),
    firstDelay: random => between(first, random),
  };
}
module.exports = {createPacing};
