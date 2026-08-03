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
const ESCAPE_HINT = "_Escribe *ATRÁS* para volver un paso o *MENU* / *0* para ir al inicio._";

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
  if (text.includes("*ATRÁS*") || text.includes("*MENU*") || text.includes("*0*")) return text;
  return `${text}\n\n${ESCAPE_HINT}`;
}

// ─── Menús para el cliente WhatsApp ─────────────────────────────────────

/**
 * Presentación de arranque del bot para clientes. Menciona en la línea de
 * capabilities solo las features que están ACTIVAS en la tienda — así el
 * cliente nunca lee "consultar tus puntos" en una tienda que no maneja
 * fidelidad, ni "comprobar cobertura" si no hay delivery. Cambios de
 * feature en el panel se propagan aquí en <5s vía config push (PR #38).
 *
 * @param {{
 *   nombreNegocio: string,
 *   loyaltyEnabled: boolean,
 *   deliveryEnabled: boolean,
 *   scheduledEnabled?: boolean,
 * }} ctx
 */
function menuPrincipal(ctx) {
  const lines = clientMenuLines(ctx);
  const scheduledHint = ctx.scheduledEnabled
    ? "\n📅 Consulta en la tienda los productos disponibles con fecha de entrega."
    : "";
  return (
    `🤝 *Asistente de ${ctx.nombreNegocio}*\n\n` +
    `Elige una opción respondiendo con su número:\n\n` +
    `${lines}${scheduledHint}\n\n` +
    `_También puedes escribir tu pregunta con tus palabras._`
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
  lines.push("*6* — 📖 Información y ayuda");
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
  if (ctx.scheduledEnabled) caps.push("pedidos programados");
  caps.push("horario");
  return caps.join(", ");
}

/** Opciones posteriores a consultar un pedido, según su estado real. */
function orderFollowupActions(ctx = {}) {
  const lines = [
    "*1* — 🔄 Actualizar este pedido",
    "*2* — 🔎 Consultar otro pedido",
  ];
  if (ctx.cancelable) lines.push("*3* — ❌ Cancelar este pedido");
  lines.push(`*${ctx.cancelable ? 4 : 3}* — 📝 Reportar un problema`);
  lines.push(`*${ctx.cancelable ? 5 : 4}* — 👤 Hablar con una persona`);
  lines.push("*0* — 🏠 Volver al inicio");
  return lines.join("\n");
}

/** Fecha canónica del pedido sin convertirla a UTC ni cambiar el día. */
function scheduledOrderLine(fechaEntrega) {
  const match = String(fechaEntrega || "").match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (!match) return "";
  return `📅 Entrega programada: *${match[3]}/${match[2]}/${match[1]}*`;
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

// ─── Menú admin / super_admin ──────────────────────────────────────────

/**
 * Renderiza el panel operativo reducido de admin/super_admin. WhatsApp se
 * reserva para acciones del turno y atención humana; la configuración del
 * negocio permanece en el panel web, con formularios y auditoría.
 *
 * Recibe `sections` y varias capabilities booleanas — no consulta ni cfg()
 * ni la BD. El llamador (bot.js) resuelve las capabilities y arma el ctx.
 *
 * @param {{
 *   rolLabel: string,             // "admin" o "super_admin"
 *   nombreNegocio: string,
 *   barServicio: boolean,
 *   isSuperAdmin: boolean,
 *   sections: Array<{n: string|number, label: string}>,  // 1-11
 *   can: {                        // permisos ya resueltos por adminCan()
 *     status: boolean,
 *     store: boolean,
 *     products: boolean,
 *     points: boolean,
 *     handoff: boolean,
 *     sync: boolean,
 *     ai: boolean,
 *   },
 * }} ctx
 */
function adminMenu(ctx) {
  const header = (
    `🔐 *Panel ${ctx.rolLabel} — ${ctx.nombreNegocio}*\n` +
    `_🟢 Modo operativo · solo acciones inmediatas y seguras._`
  );

  const sectionsBlock = ctx.sections.length
    ? `📂 *Secciones* _(responde con el número)_\n${
        ctx.sections.map(s => `${s.n} ${s.label}`).join("\n")
      }`
    : "";

  const parts = [header];
  if (sectionsBlock) parts.push(sectionsBlock);
  if (ctx.can.handoff) {
    parts.push(
      `💬 *Atajos de atención*\n` +
      `Escribe *TOMAR* para atender al primero\n` +
      `Escribe *COLA* para elegir un cliente`,
    );
  }
  parts.push(
    `🔁 */offline* — comprar como cliente\n` +
    `🌐 _Productos, clientes, puntos, roles, finanzas y configuración: panel web._`,
  );
  return parts.join("\n\n");
}

/**
 * Submenús específicos que se muestran al elegir una sección del panel.
 * Cada submenú es corto (3-5 opciones), auto-explicativo y siempre incluye
 * la salida "0 · volver al menú principal" para no atascar al operador.
 */
const ADMIN_SUB_MENUS = {
  store: (
    `🏪 *Gestión de tienda*\n\n` +
    `1️⃣  Ver estado actual\n` +
    `2️⃣  Cerrar tienda (con mensaje)\n` +
    `3️⃣  Abrir tienda\n\n` +
    `_0 · volver al menú principal_`
  ),
  products: (
    `🧾 *Productos y precios*\n\n` +
    `1️⃣  Buscar producto por nombre o ID\n` +
    `2️⃣  Cambiar precio\n` +
    `3️⃣  Activar / desactivar producto\n\n` +
    `_0 · volver al menú principal_`
  ),
  points: (
    `⭐ *Clientes y fidelidad*\n\n` +
    `1️⃣  Buscar cliente por teléfono\n` +
    `2️⃣  Añadir puntos\n` +
    `3️⃣  Quitar puntos\n` +
    `4️⃣  Historial de puntos\n\n` +
    `_0 · volver al menú principal_`
  ),
  admins: (
    `👥 *Administradores WhatsApp*\n\n` +
    `1️⃣  Ver lista de admins\n` +
    `2️⃣  Agregar admin\n` +
    `3️⃣  Eliminar admin\n\n` +
    `_0 · volver al menú principal_`
  ),
  handoff: (
    `💬 *Atención a clientes*\n\n` +
    `1️⃣  Tomar el primero en espera\n` +
    `2️⃣  Elegir cliente de la cola\n` +
    `3️⃣  Ver mi chat activo\n\n` +
    `_0 · volver al menú principal_`
  ),
  security: (
    `🛡️ *Seguridad y protección*\n\n` +
    `1️⃣  Estado anti-ban y reputación\n` +
    `2️⃣  Silenciar cliente 1 hora\n` +
    `3️⃣  Silenciar cliente 24 horas\n` +
    `4️⃣  Desbloquear cliente\n` +
    `5️⃣  Ver lista de silenciados\n\n` +
    `_0 · volver al menú principal_`
  ),
  emergency: (
    `🚨 *Modo emergencia*\n\n` +
    `1️⃣  🔴 Activar emergencia (cierra tienda + pausa bot)\n` +
    `2️⃣  ✅ Volver a normalidad\n` +
    `3️⃣  🔍 Ver estado actual\n\n` +
    `_0 · volver al menú principal_`
  ),
};

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
  ADMIN_SUB_MENUS,
  menuPrincipal,
  clientMenuLines,
  clientCapabilityText,
  orderFollowupActions,
  scheduledOrderLine,
  barMenu,
  adminMenu,
  handoffClosedMessage,
  withEscapeHint,
};
