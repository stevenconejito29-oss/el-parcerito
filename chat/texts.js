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
  // Opción 7 ("Hablar con una persona") oculta del menú principal.
  // Sigue funcionando como keyword (cliente que escribe AGENTE / persona /
  // ayuda es detectado por `CLIENT_INTENT_KEYWORDS['7']` en bot.js y va a
  // handoff), pero el bot no la ofrece como primera opción — se reserva
  // para cuando el cliente demuestre que no encontró la info que buscaba
  // (detección de loop de intents no reconocidos → derivación silenciosa).
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

// ─── Presencia pública, confianza y Mini App ────────────────────────────
//
// La "Mini App" es cómo llamamos internamente a la PWA de la tienda —
// deliberadamente evitamos las palabras "PWA", "webapp" o "atajo" en
// mensajes al cliente. El cliente medio no reconoce esos términos y
// baja la confianza. "Mini App" transmite algo ligero, oficial y
// familiar (mismo naming que WhatsApp, Telegram y Google usan hoy).
//
// Estas funciones son puras: reciben el `ctx` ya resuelto por el bot
// desde `/branding` (`presencia.miniapp`, `presencia.redes`,
// `presencia.confianza`) y devuelven la cadena final. Los campos
// opcionales (redes vacías, sin descripción) se omiten limpiamente en
// vez de mostrar líneas huérfanas — así el mismo texto sirve para
// tiendas que aún no configuraron su Instagram.

/**
 * Invitación a instalar la Mini App con tutorial paso a paso Android/iOS.
 * Se ofrece al final de respuestas donde el cliente pregunta por la app,
 * la web o cómo hacer un pedido más cómodo.
 *
 * @param {{
 *   nombreNegocio: string,
 *   miniappNombre: string,    // "Mini App" por defecto
 *   miniappUrl: string,       // Fallback a tienda_url ya resuelto en el backend
 * }} ctx
 */
function miniAppInvite(ctx) {
  const nombreApp = String(ctx.miniappNombre || "Mini App").trim() || "Mini App";
  return (
    `📱 *La ${nombreApp} de ${ctx.nombreNegocio}*\n\n` +
    `Es una versión ligera de nuestra tienda que se instala en tu móvil ` +
    `en 10 segundos. No pasa por Google Play ni App Store, no ocupa espacio ` +
    `y no pide permisos raros.\n\n` +
    `👉 Ábrela primero aquí: ${ctx.miniappUrl}\n\n` +
    `*Cómo instalarla:*\n` +
    `📱 *Android (Chrome):* toca los ⋮ arriba a la derecha → *"Añadir a pantalla ` +
    `de inicio"* o *"Instalar app"*.\n` +
    `🍎 *iPhone (Safari):* toca el botón compartir ⬆️ abajo → *"Añadir a ` +
    `pantalla de inicio"*.\n\n` +
    `Aparecerá como un icono más en tu móvil. Al abrirlo verás el catálogo ` +
    `completo, tus puntos y podrás pedir en dos toques.`
  );
}

/**
 * Bloque de confianza / "quiénes somos". Combina la descripción configurada
 * en admin con la frase corporativa de confianza y la cobertura, evitando
 * repetir información ya visible en otras respuestas.
 *
 * @param {{
 *   nombreNegocio: string,
 *   descripcion?: string,     // DESCRIPCION_NEGOCIO desde SiteConfig
 *   mensajeConfianza?: string,// MENSAJE_CONFIANZA
 *   cobertura?: string,       // ZONA_COBERTURA_RESUMEN
 *   telefono?: string,
 *   ciudad?: string,
 * }} ctx
 */
function sobreNosotros(ctx) {
  const bloques = [`🤝 *Sobre ${ctx.nombreNegocio}*`];
  const desc = String(ctx.descripcion || "").trim();
  if (desc) bloques.push(desc);
  const confianza = String(ctx.mensajeConfianza || "").trim();
  if (confianza) bloques.push(confianza);
  const detalles = [];
  const cobertura = String(ctx.cobertura || "").trim();
  if (cobertura) detalles.push(`🛵 *Cobertura:* ${cobertura}`);
  if (ctx.telefono) detalles.push(`📞 *Teléfono:* ${ctx.telefono}`);
  if (ctx.ciudad) detalles.push(`📍 *Base:* ${ctx.ciudad}`);
  if (detalles.length) bloques.push(detalles.join("\n"));
  bloques.push(
    `✅ *Pago 100% contra entrega.* No pedimos datos de tarjeta ni por WhatsApp ni por la web.`
  );
  return bloques.join("\n\n");
}

/**
 * Enlaces a redes sociales. Se omiten silenciosamente las redes sin URL
 * configurada. Devuelve `null` si no hay ni una red — el llamador decide
 * qué mostrar en ese caso (típicamente un fallback a "aún no publicamos
 * en redes").
 *
 * @param {{
 *   nombreNegocio: string,
 *   instagram?: string,
 *   facebook?: string,
 *   tiktok?: string,
 * }} ctx
 * @returns {string|null}
 */
function redesSociales(ctx) {
  const items = [];
  if (ctx.instagram) items.push(`📸 *Instagram:* ${ctx.instagram}`);
  if (ctx.facebook)  items.push(`👥 *Facebook:* ${ctx.facebook}`);
  if (ctx.tiktok)    items.push(`🎵 *TikTok:* ${ctx.tiktok}`);
  if (!items.length) return null;
  return (
    `🌐 *Síguenos en redes*\n\n` +
    `Novedades, promociones y platos del día:\n\n` +
    `${items.join("\n")}\n\n` +
    `Un mensaje directo por aquí siempre es la vía más rápida para tu pedido.`
  );
}

/**
 * Refuerzo de confianza específico para el mensaje de pago. Se usa como
 * complemento a la FAQ existente `metodos_pago`, no la sustituye — el
 * objetivo es reducir la ansiedad del cliente que nunca ha comprado con
 * nosotros dejando explícito que NO se pide tarjeta anticipada.
 *
 * @param {{ tiendaUrl?: string }} ctx
 */
function pagoContraEntregaTrust(ctx) {
  return (
    `🔒 *Cómo se paga*\n\n` +
    `Pago *100% contra entrega* — cuando el pedido llega a tu puerta.\n` +
    `• Nunca pedimos número de tarjeta por WhatsApp.\n` +
    `• Nunca pedimos datos anticipados por la web.\n` +
    `• Si quieres tarjeta al recibir, dilo al confirmar el pedido y el ` +
    `repartidor lleva datáfono.\n\n` +
    (ctx.tiendaUrl ? `Empieza tu pedido aquí 👉 ${ctx.tiendaUrl}` : "")
  ).trimEnd();
}

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
  miniAppInvite,
  sobreNosotros,
  redesSociales,
  pagoContraEntregaTrust,
};
