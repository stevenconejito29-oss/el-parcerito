"use strict";
/**
 * Adaptador para el formato de payload de la API Evolution (Baileys).
 *
 * Todo acceso a campos específicos de la API (nombres de propiedades del
 * webhook, formato del mensaje, ubicación del QR) vive AQUÍ. Cualquier
 * otro archivo del bot debe consumir las funciones exportadas — no leer
 * `msg.key.id`, `payload.data.messages`, etc directamente.
 *
 * Motivación: Evolution cambia con relativa frecuencia el envelope
 * externo del webhook (a veces `data.messages` es array, a veces `data`
 * ES el mensaje; el QR llega en `qrcode.base64` o directamente en
 * `qrcode`, etc). Concentrar el parsing aquí significa que una
 * actualización de la API se resuelve editando UN archivo, no grepping
 * 40 callsites.
 *
 * Reglas para modificar este archivo:
 *   - Cada función debe ser pura (recibe payload/msg, devuelve valor).
 *   - Documentar en el docstring qué versión de Evolution se soporta.
 *   - Añadir un test en `test/bot.evolution.test.js` por cada cambio.
 */

// ─── Eventos soportados por el webhook ──────────────────────────────────
// Nombres oficiales de Evolution API v2.x
const EVENT_MESSAGE_UPSERT     = "messages.upsert";
const EVENT_CONNECTION_UPDATE  = "connection.update";
const EVENT_QRCODE_UPDATED     = "qrcode.updated";

// ─── QR de emparejamiento ───────────────────────────────────────────────

/**
 * Normaliza un candidato a data URL de imagen PNG del QR.
 * Acepta:
 *   - Ya venir como `data:image/...` → se devuelve tal cual.
 *   - String base64 largo → se envuelve con el prefijo `data:image/png;base64,`.
 *   - Cualquier otra cosa → null.
 */
function asQrDataUrl(value) {
  if (!value || typeof value !== "string") return null;
  const raw = value.trim();
  if (!raw) return null;
  if (raw.startsWith("data:image/")) return raw;
  if (raw.length > 200 && /^[A-Za-z0-9+/=\r\n]+$/.test(raw)) {
    return `data:image/png;base64,${raw.replace(/\s+/g, "")}`;
  }
  return null;
}

/**
 * Localiza el QR en un payload que puede venir con nombres muy distintos
 * según la versión y el evento (connection.update vs qrcode.updated vs
 * llamada directa a /instance/connect). Prueba las variantes conocidas en
 * orden de probabilidad y devuelve la primera que se pueda normalizar.
 */
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

// ─── Mensajes entrantes ─────────────────────────────────────────────────

/**
 * Devuelve el texto legible de un mensaje entrante. Contempla:
 *   - Texto plano (`conversation`) y texto extendido (`extendedTextMessage`)
 *   - Captions de imagen/video/documento
 *   - Adjuntos sin texto → devuelve etiqueta descriptiva
 *     "[Adjunto recibido: <tipo> · <fileName?> · <mimetype?> · <seconds?>]"
 *     así el bot puede procesar el evento como "algo llegó" aunque no haya
 *     copy que analizar.
 *
 * Devuelve string vacío si no reconoce ningún contenido.
 */
function extractText(msg) {
  const text = (
    msg?.message?.conversation ||
    msg?.message?.extendedTextMessage?.text ||
    msg?.message?.imageMessage?.caption ||
    msg?.message?.videoMessage?.caption ||
    msg?.message?.documentMessage?.caption ||
    ""
  ).trim();
  if (text) return text;

  const media = [
    ["audio",      msg?.message?.audioMessage],
    ["imagen",     msg?.message?.imageMessage],
    ["video",      msg?.message?.videoMessage],
    ["documento",  msg?.message?.documentMessage],
    ["sticker",    msg?.message?.stickerMessage],
    ["contacto",   msg?.message?.contactMessage],
    ["ubicacion",  msg?.message?.locationMessage],
  ].find(([, value]) => value);
  if (!media) return "";

  const [type, value] = media;
  const details = [
    value.fileName,
    value.mimetype,
    value.seconds ? `${value.seconds}s` : "",
  ].filter(Boolean).join(" · ");
  return `[Adjunto recibido: ${type}${details ? ` · ${details}` : ""}]`;
}

/**
 * Devuelve el array de mensajes de un payload `messages.upsert`. Evolution
 * a veces envía `data.messages` como array y a veces `data` ES el mensaje
 * directo — normalizamos las dos variantes aquí.
 *
 * Si el evento no es `messages.upsert` devuelve array vacío para que los
 * llamadores puedan iterar sin condicional previo.
 */
function getMessagesFromPayload(payload) {
  if (!payload || payload.event !== EVENT_MESSAGE_UPSERT) return [];
  const data = payload.data;
  if (Array.isArray(data?.messages)) return data.messages.filter(Boolean);
  return data ? [data] : [];
}

/**
 * Extrae los metadatos que el bot usa para enrutar el mensaje:
 *   - jid:         identidad WhatsApp del remitente (ej. "34600...@s.whatsapp.net")
 *   - messageId:   ID único para deduplicar (`msg.key.id`)
 *   - senderName:  nombre de contacto para saludos personalizados
 *   - isFromMe:    true si el mensaje lo envió el propio bot (echo local)
 *   - isGroup:     true si viene de un grupo (jid termina en `@g.us`)
 *
 * Todos los campos son opcionales — nunca lanza si `msg` es null.
 */
function getMessageMeta(msg) {
  const remoteJid = msg?.key?.remoteJid || null;
  return {
    jid:        remoteJid,
    messageId:  msg?.key?.id || null,
    senderName: msg?.pushName || msg?.key?.participant || "",
    isFromMe:   Boolean(msg?.key?.fromMe),
    isGroup:    remoteJid ? String(remoteJid).endsWith("@g.us") : false,
  };
}

module.exports = {
  EVENT_MESSAGE_UPSERT,
  EVENT_CONNECTION_UPDATE,
  EVENT_QRCODE_UPDATED,
  asQrDataUrl,
  extractQrDataUrl,
  extractText,
  getMessagesFromPayload,
  getMessageMeta,
};
