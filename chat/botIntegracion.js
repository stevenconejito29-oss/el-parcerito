/**
 * INTEGRACION BOT CONVERSACIONAL
 * Sistema El Parcerito
 * 
 * Integra el bot conversacional con la API del sistema
 */

const ClientBotConversacional = require('./handlers/clientConversacional');
const { ConversationContext, ContextualIntentParser, ConversationalResponseGenerator } = require('./utils/conversationContext');
const logger = require('./utils/logger');

class BotIntegracion {
  constructor(config = {}) {
    this.config = config;
    this.sesiones = new Map();  // JID -> ConversationContext
    this.bot_conversacional = null;
    this.intent_parser = new ContextualIntentParser();
    this.response_generator = new ConversationalResponseGenerator();
    this.servicios = null;  // Se asigna después de inicializar
  }

  inicializar(servicios) {
    /**
     * Inicializa el bot con los servicios disponibles
     */
    this.servicios = servicios;
    
    // Crear bot conversacional con servicios
    this.bot_conversacional = new ClientBotConversacional(servicios);
    
    logger.info('✓ Bot Conversacional Inicializado');
  }

  obtener_contexto(jid) {
    if (!this.sesiones.has(jid)) {
      this.sesiones.set(jid, new ConversationContext(jid));
      
      // Limpiar sesión después de 2 horas
      setTimeout(() => {
        if (this.sesiones.has(jid)) {
          this.sesiones.delete(jid);
          logger.debug(`[BOT] Sesión expirada: ${jid}`);
        }
      }, 2 * 60 * 60 * 1000);
    }
    
    return this.sesiones.get(jid);
  }

  async procesar_mensaje_cliente(jid, texto, datos_usuario = {}) {
    /**
     * Procesa mensaje del cliente de forma conversacional
     */
    try {
      const contexto = this.obtener_contexto(jid);
      
      // Agregar mensaje del usuario al historial
      contexto.agregar_mensaje('usuario', texto);
      
      // Detectar tema basado en contexto
      const deteccion_tema = this.intent_parser.detectar_tema(texto, contexto);
      
      if (deteccion_tema.confianza > 0.5) {
        contexto.establecer_tema(deteccion_tema.tema);
      }

      logger.debug(`[BOT-CLIENTE] JID: ${jid}, Tema: ${deteccion_tema.tema}, Confianza: ${deteccion_tema.confianza}`);
      
      // Procesar respuesta
      let respuesta;
      
      if (contexto.tiene_pregunta_pendiente()) {
        // Si hay pregunta pendiente, procesar respuesta
        respuesta = await this.procesar_respuesta_pendiente(contexto, texto);
      } else {
        // Si no, procesar como mensaje normal
        respuesta = await this.bot_conversacional.procesar_mensaje(
          jid,
          texto,
          datos_usuario.nombre
        );
      }
      
      // Agregar respuesta al historial
      contexto.agregar_mensaje('bot', respuesta);
      
      return {
        ok: true,
        respuesta,
        contexto: {
          tema: contexto.tema_actual,
          datos_cliente: contexto.datos_cliente
        }
      };
      
    } catch (error) {
      logger.error(`[BOT-ERROR] ${error.message}`, error);
      
      return {
        ok: false,
        respuesta: this.response_generator.generar_no_encontrado(),
        error: error.message
      };
    }
  }

  async procesar_respuesta_pendiente(contexto, texto) {
    /**
     * Procesa la respuesta a una pregunta pendiente
     */
    const respuesta_datos = contexto.responder_pregunta(texto);
    
    if (!respuesta_datos) {
      return '¿Qué querías decir?';
    }

    const { tipo, respuesta } = respuesta_datos;
    
    switch (tipo) {
      case 'nombre':
        contexto.datos_cliente.nombre = respuesta;
        return this.response_generator.generar_confirmacion() + 
               '\n\nAhora, ¿tu email?';
      
      case 'email':
        const email = this.intent_parser.extraer_email(respuesta);
        if (!email) {
          contexto.establecer_pregunta_pendiente('¿Cuál es tu email válido?', 'email');
          return '❌ Eso no parece un email válido.\n\nIntenta de nuevo. Ej: juan@gmail.com';
        }
        contexto.datos_cliente.email = email;
        return this.response_generator.generar_confirmacion() + 
               '\n\nTu teléfono, por favor:';
      
      case 'telefono':
        const telefono = this.intent_parser.extraer_telefono(respuesta);
        if (!telefono) {
          contexto.establecer_pregunta_pendiente('¿Cuál es tu teléfono?', 'telefono');
          return '❌ No reconozco ese teléfono.\n\nDame un número válido.';
        }
        contexto.datos_cliente.telefono = telefono;
        return this.response_generator.generar_confirmacion() + 
               '\n\n¿Y tu dirección?';
      
      case 'direccion':
        contexto.datos_cliente.direccion = respuesta;
        return this.response_generator.generar_confirmacion() + 
               '\n\nPerfecto, tengo todos tus datos. ¿Confirmas la compra?';
      
      case 'confirmacion':
        if (this.intent_parser.es_confirmacion(respuesta)) {
          return '✅ ¡Excelente! Tu orden está siendo procesada.\n\nRespuesta en poco tiempo.';
        } else {
          contexto.datos_cliente = {};
          return 'Entendido. ¿Hay algo más en lo que pueda ayudarte?';
        }
      
      case 'cantidad':
        const cantidad = this.intent_parser.extraer_cantidad(respuesta);
        return `✓ ${cantidad} anotado(s). ¿Algo más?`;
      
      default:
        return 'Listo, anotado.';
    }
  }

  // ═══════════════════════════════════════════════════════════════════
  // MÉTODOS DE UTILIDAD PARA FLUJOS ESPECÍFICOS
  // ═══════════════════════════════════════════════════════════════════

  async iniciar_compra(jid, productos = []) {
    /**
     * Inicia flujo de compra
     */
    const contexto = this.obtener_contexto(jid);
    contexto.establecer_tema('compra');
    
    let mensaje = '🛍️ *¡Empecemos tu compra!*\n\n';
    
    if (productos.length > 0) {
      mensaje += this.response_generator.crear_lista_producto(productos);
    } else {
      mensaje += 'Aquí están nuestros productos destacados:\n\n';
    }
    
    contexto.establecer_pregunta_pendiente('¿Qué te gustaría?', 'seleccion_producto');
    
    return mensaje;
  }

  async agregar_carrito_conversacional(jid, producto, cantidad = 1) {
    /**
     * Agrega producto al carrito de forma conversacional
     */
    const contexto = this.obtener_contexto(jid);
    
    // Guardar en contexto
    if (!contexto.datos_cliente.carrito) {
      contexto.datos_cliente.carrito = {};
    }
    
    contexto.datos_cliente.carrito[producto.id] = cantidad;
    
    return `✅ Agregué *${producto.nombre}* (x${cantidad}) a tu carrito.\n\n` +
           `💰 Subtotal: $${(producto.precio * cantidad).toFixed(2)}\n\n` +
           `¿Quieres:\n` +
           `✅ Seguir comprando\n` +
           `🛒 Ver carrito\n` +
           `💳 Ir al pago`;
  }

  async solicitar_datos_cliente(jid) {
    /**
     * Solicita datos del cliente para completar compra
     */
    const contexto = this.obtener_contexto(jid);
    
    if (!contexto.datos_cliente.nombre) {
      contexto.establecer_pregunta_pendiente('¿Cuál es tu nombre?', 'nombre');
      return '👤 ¿Cuál es tu nombre?';
    }
    
    if (!contexto.datos_cliente.email) {
      contexto.establecer_pregunta_pendiente('¿Cuál es tu email?', 'email');
      return '📧 ¿Tu email?';
    }
    
    if (!contexto.datos_cliente.telefono) {
      contexto.establecer_pregunta_pendiente('¿Tu teléfono?', 'telefono');
      return '📞 ¿Tu teléfono?';
    }
    
    if (!contexto.datos_cliente.direccion) {
      contexto.establecer_pregunta_pendiente('¿Tu dirección?', 'direccion');
      return '📍 ¿Tu dirección?';
    }
    
    // Todos los datos completos
    return this.generar_resumen_cliente(contexto.datos_cliente);
  }

  generar_resumen_cliente(datos) {
    /**
     * Genera resumen de datos del cliente
     */
    let mensaje = '✓ *Resumen de Datos*\n\n';
    
    if (datos.nombre) mensaje += `👤 Nombre: ${datos.nombre}\n`;
    if (datos.email) mensaje += `📧 Email: ${datos.email}\n`;
    if (datos.telefono) mensaje += `📞 Teléfono: ${datos.telefono}\n`;
    if (datos.direccion) mensaje += `📍 Dirección: ${datos.direccion}\n`;
    
    mensaje += `\n¿Todo correcto?`;
    
    return mensaje;
  }

  async solicitar_tipo_entrega(jid) {
    /**
     * Pregunta por tipo de entrega
     */
    const contexto = this.obtener_contexto(jid);
    contexto.establecer_tema('entrega');
    contexto.establecer_pregunta_pendiente('¿Delivery o recogida?', 'tipo_entrega');
    
    return '🚚 *¿Cómo prefieres recibir tu orden?*\n\n' +
           '1️⃣ 🚗 Delivery a domicilio (30-45 min)\n' +
           '2️⃣ 🏪 Recoger en tienda (15-20 min)\n\n' +
           'Escribe 1 o 2';
  }

  async solicitar_puntos_canje(jid, saldo_puntos) {
    /**
     * Pregunta si desea canjear puntos
     */
    const contexto = this.obtener_contexto(jid);
    contexto.establecer_tema('puntos');
    contexto.establecer_pregunta_pendiente('¿Cuántos puntos canjear?', 'cantidad_puntos');
    
    return `⭐ *Tienes ${saldo_puntos} puntos*\n\n` +
           `¿Cuántos deseas canjear?\n` +
           `(O escribe "no" para continuar sin canjear)`;
  }

  limpiar_sesion(jid) {
    /**
     * Limpia sesión de usuario
     */
    if (this.sesiones.has(jid)) {
      this.sesiones.get(jid).limpiar();
      this.sesiones.delete(jid);
      logger.debug(`[BOT] Sesión limpiada: ${jid}`);
    }
  }

  obtener_estadisticas() {
    /**
     * Retorna estadísticas del bot
     */
    const sesiones_activas = this.sesiones.size;
    
    let total_mensajes = 0;
    for (const contexto of this.sesiones.values()) {
      total_mensajes += contexto.historial.length;
    }
    
    return {
      sesiones_activas,
      total_mensajes,
      promedio_mensajes_por_sesion: sesiones_activas > 0 
        ? (total_mensajes / sesiones_activas).toFixed(1)
        : 0
    };
  }
}

module.exports = BotIntegracion;
