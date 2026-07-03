/**
 * CHATBOT CONVERSACIONAL - Cliente
 * Sistema El Parcerito
 * 
 * Chatbot amigable y conversacional para clientes
 * Entiende contexto, mantiene conversación natural
 */

const EventEmitter = require('events');
const logger = require('./utils/logger');

class ClientConversationState {
  constructor(jid) {
    this.jid = jid;
    this.estado = 'inicio';  // inicio, menu, navegando, carrito, checkout, etc
    this.contexto = {};      // Estado conversacional (qué estamos haciendo)
    this.carrito = {};       // Carrito del cliente
    this.mensajes = [];      // Historial de mensajes (últimos 10)
    this.intentos_fallidos = 0;
    this.ultima_interaccion = Date.now();
    this.preferencias = {};  // Preferencias del cliente
  }

  actualizar_estado(nuevo_estado, contexto = {}) {
    this.estado = nuevo_estado;
    this.contexto = { ...this.contexto, ...contexto };
    this.ultima_interaccion = Date.now();
  }

  agregar_mensaje(texto, tipo = 'usuario') {
    this.mensajes.push({
      timestamp: new Date(),
      tipo,
      texto: texto.substring(0, 200)  // Limitar tamaño
    });
    
    // Mantener solo últimos 10 mensajes
    if (this.mensajes.length > 10) {
      this.mensajes.shift();
    }
  }

  get_contexto_actual() {
    return {
      estado: this.estado,
      contexto: this.contexto,
      carrito_items: Object.keys(this.carrito).length,
      historial_reciente: this.mensajes.slice(-3)
    };
  }

  limpiar_sesion() {
    this.estado = 'inicio';
    this.contexto = {};
    this.carrito = {};
    this.mensajes = [];
    this.intentos_fallidos = 0;
  }
}


class ClientBotConversacional extends EventEmitter {
  constructor(services) {
    super();
    this.services = services;  // Services: api, catalog, cart, etc
    this.sesiones = new Map();  // JID -> ClientConversationState
    this.intenciones = new Map(); // Mapeo de palabras clave a intenciones
    this.timeouts = new Map();   // Cleanup timeouts
    
    this.inicializar_intenciones();
  }

  inicializar_intenciones() {
    /**
     * Mapeo de palabras clave a intenciones del usuario
     * Esto permite entender qué quiere hacer el usuario sin ser rígido
     */
    this.intenciones = {
      // Saludos
      saludos: ['hola', 'hi', 'hey', 'buenos dias', 'buenas noches', 'buenos', 'saludos'],
      
      // Compra/Catálogo
      comprar: ['quiero', 'dame', 'trae', 'agregar', 'pedir', 'producto', 'catálogo', 'menu', 'qué tienen'],
      buscar_producto: ['busca', 'trae', 'tienen', 'hay', 'disponible'],
      categorias: ['categorias', 'categorías', 'tipos', 'clase', 'qué tipo'],
      
      // Carrito
      ver_carrito: ['carrito', 'mi carrito', 'qué tengo', 'resumen', 'total'],
      agregar_carrito: ['agregar', 'dame', 'quiero', 'añade'],
      quitar_carrito: ['quitar', 'elimina', 'saca', 'borra', 'no quiero'],
      vaciar_carrito: ['vaciar', 'limpiar', 'borra todo', 'empezar de nuevo'],
      
      // Compra
      pagar: ['pagar', 'comprar', 'checkout', 'confirmar', 'proceder', 'finalizar'],
      metodo_pago: ['cómo pago', 'métodos', 'tarjeta', 'efectivo', 'bizum'],
      
      // Entrega
      delivery: ['delivery', 'envío', 'mi casa', 'a domicilio', 'enviar'],
      recogida: ['recoger', 'retiro', 'paso a buscar', 'voy a buscar'],
      horarios: ['horario', 'hora', 'cuándo', 'abierto'],
      
      // Puntos
      puntos: ['puntos', 'mis puntos', 'cuántos puntos', 'saldo', 'recompensa'],
      canjear: ['canjear', 'cambiar', 'puntos por'],
      
      // Órdenes
      mis_ordenes: ['mis pedidos', 'mis órdenes', 'historial', 'pasadas', 'anteriores', 'estado'],
      seguimiento: ['dónde está', 'mi pedido', 'seguimiento', 'cuándo llega'],
      
      // Problemas
      problema: ['error', 'no funciona', 'problema', 'falla', 'no me deja', 'no puedo'],
      cancelar: ['cancelar', 'nunca', 'olvida', 'cambio de idea'],
      
      // Info
      info_tienda: ['quiénes son', 'sobre ustedes', 'acerca de', 'contacto'],
      promociones: ['promoción', 'oferta', 'descuento', 'cupón'],
      
      // Fin
      salir: ['salir', 'adiós', 'bye', 'chao', 'hasta luego', 'listo', 'eso es todo']
    };
  }

  obtener_sesion(jid) {
    if (!this.sesiones.has(jid)) {
      const sesion = new ClientConversationState(jid);
      this.sesiones.set(jid, sesion);
      
      // Limpiar sesión después de 1 hora
      const timeout = setTimeout(() => {
        this.sesiones.delete(jid);
        logger.debug(`[BOT] Sesión expirada: ${jid}`);
      }, 3600000);
      
      this.timeouts.set(jid, timeout);
    }
    
    return this.sesiones.get(jid);
  }

  detectar_intencion(texto) {
    /**
     * Detecta la intención del usuario basada en palabras clave
     * Retorna array de intenciones encontradas (puede haber múltiples)
     */
    const texto_limpio = texto.toLowerCase()
      .normalize('NFD')
      .replace(/[\u0300-\u036f]/g, '');  // Remover acentos
    
    const intenciones_encontradas = [];
    
    for (const [intencion, palabras_clave] of Object.entries(this.intenciones)) {
      for (const palabra of palabras_clave) {
        if (texto_limpio.includes(palabra)) {
          intenciones_encontradas.push(intencion);
          break;  // Una palabra clave es suficiente
        }
      }
    }
    
    return intenciones_encontradas.length > 0 
      ? intenciones_encontradas 
      : ['no_entendido'];
  }

  async procesar_mensaje(jid, texto, nombre_usuario = null) {
    /**
     * Procesa mensaje del cliente y devuelve respuesta conversacional
     */
    const sesion = this.obtener_sesion(jid);
    sesion.agregar_mensaje(texto, 'usuario');
    
    // Detectar intención
    const intenciones = this.detectar_intencion(texto);
    
    logger.debug(`[BOT-CLIENT] JID: ${jid}, Intención: ${intenciones.join(', ')}`);
    
    try {
      // Procesar según intención principal
      const intencion_principal = intenciones[0];
      
      let respuesta;
      switch (intencion_principal) {
        case 'saludos':
          respuesta = await this.responder_saludo(sesion, nombre_usuario);
          break;
        
        case 'comprar':
        case 'buscar_producto':
          respuesta = await this.mostrar_catalogo(sesion);
          break;
        
        case 'categorias':
          respuesta = await this.mostrar_categorias(sesion);
          break;
        
        case 'ver_carrito':
          respuesta = await this.mostrar_carrito(sesion);
          break;
        
        case 'agregar_carrito':
          respuesta = this.solicitar_producto_agregar(sesion);
          break;
        
        case 'quitar_carrito':
          respuesta = this.solicitar_producto_quitar(sesion);
          break;
        
        case 'vaciar_carrito':
          respuesta = this.confirmar_vaciar_carrito(sesion);
          break;
        
        case 'pagar':
          respuesta = await this.iniciar_checkout(sesion);
          break;
        
        case 'delivery':
          respuesta = this.explicar_delivery(sesion);
          break;
        
        case 'recogida':
          respuesta = this.explicar_recogida(sesion);
          break;
        
        case 'horarios':
          respuesta = await this.obtener_horarios(sesion);
          break;
        
        case 'puntos':
          respuesta = await this.mostrar_puntos(sesion, jid);
          break;
        
        case 'canjear':
          respuesta = await this.iniciar_canje_puntos(sesion);
          break;
        
        case 'mis_ordenes':
          respuesta = await this.mostrar_ordenes_recientes(sesion, jid);
          break;
        
        case 'seguimiento':
          respuesta = this.solicitar_numero_orden(sesion);
          break;
        
        case 'problema':
          respuesta = this.ayuda_problema(sesion);
          break;
        
        case 'info_tienda':
          respuesta = await this.info_tienda(sesion);
          break;
        
        case 'salir':
          respuesta = this.despedir(sesion);
          break;
        
        default:
          respuesta = this.no_entendido(sesion, texto);
      }
      
      sesion.agregar_mensaje(respuesta, 'bot');
      return respuesta;
      
    } catch (error) {
      logger.error(`[BOT-CLIENT ERROR] ${error.message}`, error);
      return this.mensaje_error_generico();
    }
  }

  // ═══════════════════════════════════════════════════════════════════
  // RESPUESTAS CONVERSACIONALES
  // ═══════════════════════════════════════════════════════════════════

  async responder_saludo(sesion, nombre = null) {
    const hora = new Date().getHours();
    let saludo = '';
    
    if (hora < 12) saludo = '¡Buenos días!';
    else if (hora < 18) saludo = '¡Buenas tardes!';
    else saludo = '¡Buenas noches!';
    
    const nombre_cliente = nombre || 'amigo';
    
    const mensajes = [
      `${saludo} ${nombre_cliente} 👋\n\nBienvenido a El Parcerito. ¿Qué te gustaría hoy?\n\n📦 Comprar productos\n📋 Ver mis pedidos\n⭐ Mis puntos\n❓ Hacer una pregunta`,
      
      `${saludo} ${nombre_cliente}! 😊\n\nEstoy aquí para ayudarte. Puedo:\n\n🛍️ Mostrar catálogo\n🛒 Ayudarte con tu carrito\n💳 Procesar tu compra\n✨ Canjear puntos`,
      
      `¡Hola ${nombre_cliente}! 👋\n\nMe alegra verte por aquí. ¿Buscas algo especial hoy o solo exploras?`,
    ];
    
    const mensaje_random = mensajes[Math.floor(Math.random() * mensajes.length)];
    sesion.actualizar_estado('menu');
    
    return mensaje_random;
  }

  async mostrar_catalogo(sesion) {
    try {
      sesion.actualizar_estado('navegando', { seccion: 'catalogo' });
      
      const catalogo = await this.services.obtener_catalogo();
      
      if (!catalogo || catalogo.length === 0) {
        return '😅 Por el momento no tenemos productos disponibles.\n\n¿Hay algo más en lo que pueda ayudarte?';
      }
      
      // Mostrar primeros 5 productos de forma bonita
      const productos_muestra = catalogo.slice(0, 5);
      let mensaje = '📦 *Nuestros Productos*\n\n';
      
      productos_muestra.forEach((prod, idx) => {
        mensaje += `${idx + 1}️⃣ *${prod.nombre}*\n`;
        mensaje += `   💰 $${prod.precio}\n`;
        if (prod.descripcion) {
          mensaje += `   ℹ️ ${prod.descripcion.substring(0, 50)}...\n`;
        }
        mensaje += '\n';
      });
      
      mensaje += `Ver más: escribe "producto" + número o "categorías"`;
      
      return mensaje;
    } catch (error) {
      logger.error('Error mostrando catálogo:', error);
      return '❌ No pude cargar el catálogo. Intenta de nuevo.';
    }
  }

  async mostrar_categorias(sesion) {
    try {
      const categorias = await this.services.obtener_categorias();
      
      if (!categorias || categorias.length === 0) {
        return '📂 No hay categorías disponibles.';
      }
      
      let mensaje = '📂 *Categorías*\n\n';
      categorias.forEach((cat, idx) => {
        mensaje += `${idx + 1}️⃣ ${cat.nombre}\n`;
      });
      
      mensaje += '\n¿Cuál te interesa? Escribe el número o el nombre.';
      
      return mensaje;
    } catch (error) {
      return '❌ No pude cargar las categorías.';
    }
  }

  async mostrar_carrito(sesion) {
    if (Object.keys(sesion.carrito).length === 0) {
      return '🛒 Tu carrito está vacío.\n\n¿Qué te gustaría agregar?\n\nEscribe "comprar" para ver nuestros productos.';
    }
    
    let mensaje = '🛒 *Tu Carrito*\n\n';
    let total = 0;
    
    for (const [prod_id, cantidad] of Object.entries(sesion.carrito)) {
      const producto = await this.services.obtener_producto(prod_id);
      if (producto) {
        const subtotal = producto.precio * cantidad;
        total += subtotal;
        mensaje += `${producto.nombre} x${cantidad} = $${subtotal}\n`;
      }
    }
    
    mensaje += `\n💰 *Total: $${total.toFixed(2)}*\n\n`;
    mensaje += `¿Deseas:\n\n`;
    mensaje += `✅ Comprar ahora\n`;
    mensaje += `➕ Agregar más\n`;
    mensaje += `❌ Quitar algo\n`;
    mensaje += `🗑️ Vaciar carrito`;
    
    return mensaje;
  }

  solicitar_producto_agregar(sesion) {
    sesion.actualizar_estado('agregando_producto');
    return '¿Qué producto deseas agregar?\n\n📝 Puedes decirme:\n"Producto + número"\no "me das [nombre]"';
  }

  solicitar_producto_quitar(sesion) {
    if (Object.keys(sesion.carrito).length === 0) {
      return '🛒 Tu carrito está vacío, no hay nada para quitar.\n\n¿Quieres ver nuestros productos?';
    }
    
    let mensaje = '¿Qué producto deseas quitar?\n\n';
    let idx = 1;
    for (const prod_id of Object.keys(sesion.carrito)) {
      const producto = this.services.obtener_producto_sync(prod_id);
      if (producto) {
        mensaje += `${idx}. ${producto.nombre}\n`;
        idx++;
      }
    }
    
    return mensaje;
  }

  confirmar_vaciar_carrito(sesion) {
    sesion.actualizar_estado('confirmando_vaciar');
    return '⚠️ ¿Estás seguro de que quieres vaciar todo tu carrito?\n\nEscribe "sí, vacía" para confirmar o "cancelar"';
  }

  async iniciar_checkout(sesion) {
    if (Object.keys(sesion.carrito).length === 0) {
      return '🛒 Tu carrito está vacío.\n\nPrimero agrega productos, luego continuamos con la compra.';
    }
    
    sesion.actualizar_estado('checkout');
    
    return '✅ Perfecto, vamos a procesar tu compra.\n\n' +
           'Necesito algunos datos:\n\n' +
           '1️⃣ Tu nombre completo\n' +
           '2️⃣ Tu email\n' +
           '3️⃣ Tu teléfono\n' +
           '4️⃣ Dirección (si es delivery)\n\n' +
           'Por favor, cuéntame.';
  }

  explicar_delivery(sesion) {
    return '🚚 *Entrega a Domicilio*\n\n' +
           '✅ Disponible en tu zona\n' +
           '⏱️ 30-45 minutos\n' +
           '💰 Costo adicional mínimo\n' +
           '🔍 Seguimiento en tiempo real\n\n' +
           '¿Te gustaría elegir esta opción?';
  }

  explicar_recogida(sesion) {
    return '🏪 *Recoger en Tienda*\n\n' +
           '✅ Sin costo de envío\n' +
           '⏱️ Listo en 15-20 minutos\n' +
           '📍 Dirección: Calle Principal 123\n' +
           '🕐 Horario: 9am - 10pm\n\n' +
           '¿Te gustaría recoger aquí?';
  }

  async obtener_horarios(sesion) {
    try {
      const horarios = await this.services.obtener_horarios();
      
      let mensaje = '🕐 *Nuestro Horario*\n\n';
      mensaje += `Lunes a Viernes: ${horarios.lunes_viernes}\n`;
      mensaje += `Sábado: ${horarios.sabado}\n`;
      mensaje += `Domingo: ${horarios.domingo}\n`;
      
      return mensaje;
    } catch (error) {
      return '🕐 Nuestro horario:\n\nLunes a Domingo: 9am - 11pm';
    }
  }

  async mostrar_puntos(sesion, jid) {
    try {
      const puntos = await this.services.obtener_puntos_cliente(jid);
      
      return '⭐ *Tus Puntos*\n\n' +
             `💫 Saldo: *${puntos.saldo} puntos*\n\n` +
             `📊 Ganados: ${puntos.ganados}\n` +
             `💳 Canjeados: ${puntos.canjeados}\n\n` +
             '¿Quieres canjear puntos por productos?';
    } catch (error) {
      return '⭐ No pude obtener tu saldo de puntos.\n\nIntenta de nuevo.';
    }
  }

  async iniciar_canje_puntos(sesion) {
    sesion.actualizar_estado('canjeando_puntos');
    
    try {
      const productos_canje = await this.services.obtener_productos_canje();
      
      if (productos_canje.length === 0) {
        return '❌ No hay productos disponibles para canjear en este momento.';
      }
      
      let mensaje = '🎁 *Productos para Canjear*\n\n';
      productos_canje.forEach((prod, idx) => {
        mensaje += `${idx + 1}. ${prod.nombre} (${prod.puntos_canje} ⭐)\n`;
      });
      
      mensaje += '\n¿Cuál te interesa?';
      
      return mensaje;
    } catch (error) {
      return '❌ No pude cargar los productos canjeables.';
    }
  }

  async mostrar_ordenes_recientes(sesion, jid) {
    try {
      const ordenes = await this.services.obtener_ordenes_cliente(jid, 5);
      
      if (!ordenes || ordenes.length === 0) {
        return '📋 No tienes órdenes anteriores.\n\n¿Te gustaría hacer una compra ahora?';
      }
      
      let mensaje = '📋 *Tus Últimas Órdenes*\n\n';
      ordenes.forEach((orden, idx) => {
        mensaje += `${idx + 1}. Orden #${orden.id}\n`;
        mensaje += `   💰 $${orden.total}\n`;
        mensaje += `   📅 ${orden.fecha}\n`;
        mensaje += `   ✅ ${orden.estado}\n\n`;
      });
      
      mensaje += '¿Quieres ver detalles de alguna?';
      
      return mensaje;
    } catch (error) {
      return '❌ No pude cargar tus órdenes.';
    }
  }

  solicitar_numero_orden(sesion) {
    sesion.actualizar_estado('siguiendo_orden');
    return '🔍 *Seguimiento de Orden*\n\n' +
           '¿Cuál es el número de tu orden?\n\n' +
           'Ej: #1234';
  }

  ayuda_problema(sesion) {
    return '😔 Disculpa, parece que hay un problema.\n\n' +
           '¿Qué específicamente te está fallando?\n\n' +
           '1. No puedo agregar productos\n' +
           '2. El carrito no funciona\n' +
           '3. No me deja pagar\n' +
           '4. Otro problema';
  }

  async info_tienda(sesion) {
    return '🏪 *El Parcerito*\n\n' +
           '🌟 Tu tienda favorita de pedidos en línea\n\n' +
           '📍 Ubicación: Calle Principal 123\n' +
           '📞 Teléfono: +1 (555) 123-4567\n' +
           '📧 Email: info@parcerito.com\n\n' +
           '✨ Queremos lo mejor para ti';
  }

  despedir(sesion) {
    sesion.limpiar_sesion();
    const mensajes = [
      '👋 ¡Hasta luego! Gracias por visitarnos.\n\nVuelve pronto.',
      '😊 Fue un placer ayudarte.\n\n¡Que disfrutes tu compra! 🎉',
      '✨ ¡Gracias por tu compra!\n\nTe esperamos pronto.',
    ];
    
    return mensajes[Math.floor(Math.random() * mensajes.length)];
  }

  no_entendido(sesion, texto) {
    sesion.intentos_fallidos++;
    
    if (sesion.intentos_fallidos > 3) {
      return '🤔 No estoy entendiendo bien.\n\n' +
             'Intenta ser más específico o escribe:\n\n' +
             '- "comprar" para ver productos\n' +
             '- "carrito" para ver tu compra\n' +
             '- "ayuda" para más opciones';
    }
    
    return `🤔 No estoy seguro de lo que quieres decir.\n\n¿Podrías reformular?`;
  }

  mensaje_error_generico() {
    return '❌ Hubo un error. Intenta de nuevo.\n\nO escribe "ayuda" para más opciones.';
  }
}

module.exports = ClientBotConversacional;
