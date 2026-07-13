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

// ─── Menú admin / super_admin ──────────────────────────────────────────

/**
 * Renderiza el panel completo del admin/super_admin agrupando los comandos
 * por dominio funcional. Sin secciones el operador veía 30+ líneas planas
 * y buscar "¿cómo cambio el precio?" era mirar un chorro. Ahora:
 *
 *   1. Encabezado con modo (propio vs bar_servicio).
 *   2. Bloque de secciones numeradas (submenús).
 *   3. Grupos de comandos por dominio, cada uno con su emoji identificador.
 *   4. Bloque exclusivo super_admin al final.
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
    (ctx.barServicio
      ? `_🏪 Modo servicio · gestión completa desde WhatsApp._`
      : `_🏠 Modo propio · usa el panel web para gestión avanzada._`)
  );

  const sectionsBlock = ctx.sections.length
    ? `📂 *Secciones* _(responde con el número)_\n${
        ctx.sections.map(s => `${s.n} ${s.label}`).join("\n")
      }`
    : "";

  // Agrupamos comandos por dominio. Cada grupo solo aparece si el operador
  // tiene al menos un comando dentro — evita bloques vacíos con el título.
  const grupos = [];

  const consulta = [
    ctx.can.status ? "`!status` estado del bot" : null,
    ctx.can.store  ? "`!hoy` resumen del día" : null,
    "`!diag` diagnóstico completo",
  ].filter(Boolean);
  if (consulta.length) {
    grupos.push(`📊 *Consulta rápida*\n${consulta.join("\n")}`);
  }

  const clientes = [
    ctx.can.points ? "`!buscar-cliente 34XXXXXXXXX` ver perfil" : null,
    ctx.can.points ? "`!cliente Nombre 34XXXXXXXXX` registrar" : null,
    ctx.can.points ? "`!puntos 34XXXXXXXXX +50 motivo` ajustar puntos" : null,
  ].filter(Boolean);
  if (clientes.length) {
    grupos.push(`👥 *Clientes y fidelidad*\n${clientes.join("\n")}`);
  }

  const atencion = [
    (ctx.can.store || ctx.can.points) ? "`!pendientes` cola tiempo real" : null,
    ctx.can.handoff ? "`!take N` tomar chat · `!release` soltar" : null,
    ctx.can.handoff ? "`!disponible` marcar disponible/ausente" : null,
    ctx.can.handoff ? "`!send NUMERO mensaje` enviar directo" : null,
  ].filter(Boolean);
  if (atencion.length) {
    grupos.push(`💬 *Atención humana*\n${atencion.join("\n")}`);
  }

  const catalogo = [
    "`!buscar <texto>` encontrar producto",
    ctx.can.sync ? "`!sync` sincronizar catálogo" : null,
    ctx.can.ai ? "`!ia <pregunta>` análisis IA del negocio" : null,
  ].filter(Boolean);
  if (catalogo.length) {
    grupos.push(`🛍️ *Catálogo e IA*\n${catalogo.join("\n")}`);
  }

  // Comandos avanzados de tienda — solo tiene sentido exponerlos cuando el
  // admin gestiona su negocio íntegramente desde WhatsApp (bar_servicio).
  if (ctx.barServicio) {
    const tienda = [
      "`!pausar-tienda` / `!reanudar-tienda`",
      "`!producto <id> activar|desactivar`",
      "`!precio <id> <euros>` cambiar precio",
      "`!stock <id> +N | -N | =N` ajustar inventario",
      "`!crear-producto <nombre>|<precio>|<categoria>`",
      "`!ver-pedidos [estado]` listar con detalle",
      "`!horario HH:MM-HH:MM` fijar apertura/cierre",
      "`!minimo <euros>` pedido mínimo",
      "`!nombre <texto>` cambiar nombre del negocio",
      "`!nicho comida|producto` cambiar nicho",
      "`!config <CLAVE> <valor>` cualquier ajuste runtime",
      "`!ver-config <PREFIJO>` listar config",
    ];
    grupos.push(`🏪 *Gestión de tienda (modo servicio)*\n${tienda.join("\n")}`);
  }

  // Solo super_admin ve estos — corresponden a control estratégico global.
  if (ctx.isSuperAdmin) {
    const sa = [
      "`!modo-tienda` alternar propio ↔ servicio",
      "`!modulo delivery|recogida|puntos|programados on|off`",
      "`!cerrar-tienda` / `!abrir-tienda`",
      "`!salud` snapshot del sistema",
      "`!limpiar` reset sesiones clientes",
    ];
    grupos.push(`👑 *Solo Super Admin*\n${sa.join("\n")}`);
  }

  const parts = [header];
  if (sectionsBlock) parts.push(sectionsBlock);
  parts.push(...grupos);
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
    `💬 *Atención humana (handoff)*\n\n` +
    `1️⃣  Ver clientes en espera\n` +
    `2️⃣  Soltar mi chat activo\n` +
    `3️⃣  Cerrar todos mis chats\n\n` +
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
  barMenu,
  adminMenu,
  handoffClosedMessage,
  withEscapeHint,
};
