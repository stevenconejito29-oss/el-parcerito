"use strict";
/**
 * Textos de cara al cliente y al operador del bot.
 *
 * Módulo puro: cada función recibe un contexto explícito (nombres, flags de
 * features) y devuelve la cadena final. No lee `cfg()` ni `getNegocioNombre()`
 * directamente — eso queda en el llamador. Esto permite:
 *   - Cambiar el copy sin buscar entre 300+ callsites de `sendText()`.
 *   - Testear el rendering sin arrancar el bot ni la BD.
 *   - Ajustar branding/idioma de forma coordinada tocando un solo archivo.
 *
 * Cualquier texto que el cliente vea repetido dos veces o más debe vivir aquí.
 * Un mensaje inline en un `sendText()` puntual (ej. confirmación específica de
 * un endpoint) puede quedarse local; los menús, submenús, fallbacks y frases
 * transversales viven en este módulo.
 */

// ─── Cadenas comunes reutilizables ──────────────────────────────────────

// Pista de salida universal en submenús. Referenciar en cualquier estado
// donde el cliente puede quedar atascado esperando un input concreto. Los
// disparadores reales (`menu`, `0`, `inicio`, `hola`, `hi`, `start`) están
// centralizados en el catch global de `_handleMessage` — este texto solo
// documenta los dos más memorables para el cliente final.
const ESCAPE_HINT = "_Escribe *MENU* o *0* para volver al inicio._";

// Texto que se muestra cuando el bot no entiende la intención del cliente
// dentro de un submenú donde SÍ acepta texto libre. No aplicar en menú
// principal (allí detectClientIntent captura y responde con menú).
const FALLBACK_HINT = "No estoy seguro de qué necesitas. " + ESCAPE_HINT;

/**
 * Ensambla un prompt de submenú añadiendo la pista de escape al final si
 * no está ya presente. Los llamadores pasan el cuerpo del prompt libre y
 * este helper garantiza consistencia visual sin duplicar strings.
 */
function withEscapeHint(body) {
  const text = String(body || "").trimEnd();
  if (text.includes("*MENU*") || text.includes("*0*")) return text;
  return `${text}\n\n${ESCAPE_HINT}`;
}

// ─── Menús para el cliente WhatsApp ─────────────────────────────────────

/**
 * Presentación de arranque del bot para clientes.
 *
 * @param {{
 *   nombreNegocio: string,
 *   loyaltyEnabled: boolean,
 *   deliveryEnabled: boolean,
 * }} ctx
 */
function menuPrincipal(ctx) {
  const extras = [
    ctx.loyaltyEnabled ? "consultar tus puntos" : null,
    ctx.deliveryEnabled ? "comprobar cobertura" : null,
  ].filter(Boolean);
  const extraText = extras.length ? `, ${extras.join(" o ")}` : "";
  return (
    `🤝 *Asistente de ${ctx.nombreNegocio}*\n\n` +
    `Te ayudo sin tomar compras por WhatsApp: la compra se completa en la web para validar stock, combos, horarios y módulos activos.\n\n` +
    `Puedes preguntarme por el estado o cancelación de un pedido, horario, ubicación o pagos${extraText}. ` +
    `También puedes decir *Abrir tienda online* o *quiero hablar con una persona*.\n\n` +
    `_Cuéntame con tus palabras qué necesitas._`
  );
}

/**
 * Menú numerado del cliente. Las opciones 3 y 4 se ocultan si el feature
 * está desactivado para no confundir al cliente con acciones que fallan.
 *
 * @param {{
 *   verticalLabel: string,
 *   loyaltyEnabled: boolean,
 *   deliveryEnabled: boolean,
 * }} ctx
 */
function clientMenuLines(ctx) {
  const catalogo = String(ctx.verticalLabel || "Menú").toLowerCase();
  const lines = [
    `*1* — 🛒 Ver el ${catalogo} en la web`,
    `*2* — 📦 Estado de mi pedido`,
  ];
  if (ctx.loyaltyEnabled) lines.push("*3* — ⭐ Mis puntos");
  if (ctx.deliveryEnabled) lines.push("*4* — 📍 Zona de entrega");
  lines.push("*6* — ⏰ Horario / contacto");
  lines.push("*7* — 👤 Hablar con una persona");
  return lines.join("\n");
}

/**
 * Enumera las capacidades del bot en una sola línea, para usarla en frases
 * donde ya explicamos qué podemos hacer sin necesidad de listar el menú.
 * Ej: "Puedo ayudarte con: estado de pedidos, información general, ..."
 */
function clientCapabilityText(ctx) {
  const caps = ["estado de pedidos", "información general"];
  if (ctx.loyaltyEnabled) caps.push("puntos");
  if (ctx.deliveryEnabled) caps.push("cobertura");
  caps.push("horario");
  return caps.join(", ");
}

// ─── Menú del operador del bar (modo bar_servicio) ──────────────────────

/**
 * Panel que ve el WhatsApp del bar cuando escribe al número principal.
 *
 * @param {{ nombreBar: string }} ctx
 */
function barMenu(ctx) {
  return (
    `🏪 *Panel de ${ctx.nombreBar}*\n\n` +
    `Estás conectado como operador de tu bar. Desde aquí puedes:\n\n` +
    `1️⃣  📋 Ver mis pedidos pendientes\n` +
    `2️⃣  ✅ Marcar un pedido como preparado\n` +
    `3️⃣  📨 Ver incidencias de clientes\n` +
    `4️⃣  🌐 Abrir mi inventario en la web\n` +
    `5️⃣  💬 Contactar con el administrador general\n` +
    `6️⃣  🔓 Abrir / cerrar mi tienda\n` +
    `7️⃣  🛑 Marcar producto agotado / disponible\n` +
    `8️⃣  💶 Cambiar precio de un producto\n\n` +
    `_Responde con el número o con palabras (pedidos, abrir, agotado, precio…)_`
  );
}

// ─── Estados en cola de handoff ─────────────────────────────────────────

/**
 * Mensaje que ve el cliente cuando queda en cola de atención humana y no
 * hay agentes libres. Debe transmitir tranquilidad y opción de salida.
 */
const HANDOFF_QUEUED = (
  `💬 *Te he puesto en cola para hablar con una persona.*\n\n` +
  `Ahora mismo no hay agentes libres, pero guardo todos tus mensajes ` +
  `y la primera persona disponible recibirá tu historial completo. ` +
  `No te preocupes, no se pierde nada. 😊\n\n` +
  `Mientras tanto, puedes seguir escribiendo lo que necesites. ` +
  `Si prefieres volver al asistente automático escribe */volver bot*.`
);

/**
 * Mensaje que ve el cliente al cerrarse formalmente un chat humano. Debe
 * incluir el menú principal para que no quede sin siguiente paso.
 */
function handoffClosedMessage(menuText) {
  return (
    `✅ *La conversación con el agente ha finalizado.*\n\n` +
    `El asistente automático vuelve a estar disponible.\n\n${menuText}`
  );
}

/** Mensaje al liberar el chat de vuelta a la cola. */
const HANDOFF_REQUEUED = (
  `🕐 *Tu chat volvió a la cola.*\n\n` +
  `Conservamos el historial y otro agente podrá continuar la conversación.`
);

module.exports = {
  ESCAPE_HINT,
  FALLBACK_HINT,
  HANDOFF_QUEUED,
  HANDOFF_REQUEUED,
  menuPrincipal,
  clientMenuLines,
  clientCapabilityText,
  barMenu,
  handoffClosedMessage,
  withEscapeHint,
};
