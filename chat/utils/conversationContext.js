/**
 * CONTEXTO CONVERSACIONAL - Bot Cliente
 * Sistema El Parcerito
 * 
 * Mantiene contexto de la conversación para responder de forma más natural
 */

class ConversationContext {
  constructor(jid) {
    this.jid = jid;
    this.historial = [];        // Últimos 15 mensajes
    this.tema_actual = null;    // Tema que se está discutiendo
    this.pregunta_pendiente = null; // Pregunta esperando respuesta
    this.datos_cliente = {};    // Datos que el cliente ha proporcionado
    this.timestamp_creacion = Date.now();
  }

  agregar_mensaje(rol, texto, metadatos = {}) {
    this.historial.push({
      timestamp: Date.now(),
      rol,  // 'usuario' | 'bot'
      texto: texto.substring(0, 300),
      metadatos
    });

    // Mantener solo últimos 15 mensajes
    if (this.historial.length > 15) {
      this.historial.shift();
    }
  }

  establecer_tema(nuevo_tema) {
    this.tema_actual = nuevo_tema;
  }

  establecer_pregunta_pendiente(pregunta, tipo = 'texto') {
    this.pregunta_pendiente = { pregunta, tipo, timestamp: Date.now() };
  }

  responder_pregunta(respuesta) {
    if (!this.pregunta_pendiente) return null;
    
    const resultado = {
      pregunta: this.pregunta_pendiente.pregunta,
      respuesta,
      tipo: this.pregunta_pendiente.tipo
    };

    this.pregunta_pendiente = null;
    return resultado;
  }

  tiene_pregunta_pendiente() {
    if (!this.pregunta_pendiente) return false;
    
    // Pregunta expira después de 5 minutos
    const tiempo_transcurrido = Date.now() - this.pregunta_pendiente.timestamp;
    if (tiempo_transcurrido > 5 * 60 * 1000) {
      this.pregunta_pendiente = null;
      return false;
    }

    return true;
  }

  get_historial_resumen() {
    return this.historial.slice(-5).map(m => ({
      rol: m.rol,
      texto: m.texto
    }));
  }

  obtener_contexto_para_ia() {
    /**
     * Retorna contexto formateado para procesar con un modelo de IA
     * Útil si queremos usar NLP más avanzado
     */
    return {
      jid: this.jid,
      tema: this.tema_actual,
      datos_cliente: this.datos_cliente,
      pregunta_pendiente: this.pregunta_pendiente?.pregunta,
      historial_reciente: this.get_historial_resumen(),
      tiempo_sesion_minutos: Math.round((Date.now() - this.timestamp_creacion) / 60000)
    };
  }

  limpiar() {
    this.historial = [];
    this.tema_actual = null;
    this.pregunta_pendiente = null;
    this.datos_cliente = {};
  }
}


/**
 * Analizador de intención con contexto
 * Entiende mejor qué quiere hacer el usuario basándose en el contexto
 */
class ContextualIntentParser {
  constructor() {
    this.palabras_clave_por_tema = this.inicializar_temas();
  }

  inicializar_temas() {
    return {
      'catalogo': {
        palabras: ['producto', 'catálogo', 'que tienes', 'tiene', 'ver', 'mostrar'],
        preguntas: ['¿Qué buscas?', '¿Qué categoría?', '¿Tienes algo específico en mente?']
      },
      'carrito': {
        palabras: ['carrito', 'agregar', 'quitar', 'total', 'cantidad'],
        preguntas: ['¿Cuántos?', '¿Qué cantidad?', '¿Algo más?']
      },
      'compra': {
        palabras: ['comprar', 'pagar', 'checkout', 'proceder', 'finalizar'],
        preguntas: ['¿Dónde lo enviamos?', '¿Cómo prefieres pagar?', '¿Tienes algún cupón?']
      },
      'entrega': {
        palabras: ['delivery', 'recoger', 'envío', 'dirección', 'domicilio'],
        preguntas: ['¿Cuál es tu dirección?', '¿Prefieres entrega o recogida?']
      },
      'puntos': {
        palabras: ['puntos', 'saldo', 'recompensa', 'canjear', 'descuento'],
        preguntas: ['¿Cuántos puntos quieres canjear?', '¿Qué producto?']
      },
      'pedidos': {
        palabras: ['pedido', 'orden', 'historial', 'anteriores', 'pasadas'],
        preguntas: ['¿Cuál es el número?', '¿Qué orden?']
      }
    };
  }

  detectar_tema(texto, contexto_anterior = null) {
    /**
     * Detecta el tema principal de la conversación
     */
    const texto_limpio = this.normalizar_texto(texto);
    let tema_detectado = null;
    let confianza = 0;

    for (const [tema, config] of Object.entries(this.palabras_clave_por_tema)) {
      for (const palabra of config.palabras) {
        if (texto_limpio.includes(palabra)) {
          // Aumentar confianza si hay múltiples coincidencias
          confianza++;
        }
      }

      if (confianza > 0) {
        tema_detectado = tema;
        break;
      }
    }

    // Si no detecta tema pero hay contexto anterior, usar ese
    if (!tema_detectado && contexto_anterior) {
      tema_detectado = contexto_anterior.tema_actual;
    }

    return {
      tema: tema_detectado || 'general',
      confianza: confianza / 10  // Normalizar a 0-1
    };
  }

  obtener_pregunta_seguimiento(tema) {
    /**
     * Retorna una pregunta natural de seguimiento según el tema
     */
    const config = this.palabras_clave_por_tema[tema];
    if (!config) return '¿Algo más?';

    return config.preguntas[Math.floor(Math.random() * config.preguntas.length)];
  }

  normalizar_texto(texto) {
    return texto
      .toLowerCase()
      .normalize('NFD')
      .replace(/[\u0300-\u036f]/g, '')  // Remover acentos
      .replace(/[^\w\s]/g, '');          // Remover caracteres especiales
  }

  extraer_cantidad(texto) {
    /**
     * Extrae número de cantidad del texto
     * "dame 3 cafés" -> 3
     * "dos pizzas" -> 2
     */
    const numeros_texto = {
      'uno': 1, 'un': 1, 'una': 1,
      'dos': 2, 'dos': 2,
      'tres': 3,
      'cuatro': 4,
      'cinco': 5,
      'seis': 6,
      'siete': 7,
      'ocho': 8,
      'nueve': 9,
      'diez': 10,
      'once': 11,
      'doce': 12,
    };

    // Buscar número directo (2, 3, 10, etc)
    const match_numero = texto.match(/\d+/);
    if (match_numero) {
      return parseInt(match_numero[0]);
    }

    // Buscar número escrito
    const texto_limpio = this.normalizar_texto(texto);
    for (const [palabra, numero] of Object.entries(numeros_texto)) {
      if (texto_limpio.includes(palabra)) {
        return numero;
      }
    }

    return 1;  // Default
  }

  extraer_email(texto) {
    /**
     * Extrae email del texto
     * "mi email es juan@gmail.com" -> juan@gmail.com
     */
    const regex_email = /[^\s@]+@[^\s@]+\.[^\s@]+/g;
    const matches = texto.match(regex_email);
    return matches ? matches[0] : null;
  }

  extraer_telefono(texto) {
    /**
     * Extrae teléfono del texto
     * "mi teléfono es 1234567890" -> 1234567890
     */
    const regex_telefono = /\b\d{7,15}\b/g;
    const matches = texto.match(regex_telefono);
    return matches ? matches[0] : null;
  }

  extraer_datos_contacto(texto) {
    /**
     * Extrae múltiples datos de contacto en un mensaje
     */
    return {
      email: this.extraer_email(texto),
      telefono: this.extraer_telefono(texto)
    };
  }

  es_confirmacion(texto) {
    /**
     * Detecta si el usuario está confirmando algo
     */
    const confirmaciones = [
      'si', 'sí', 'claro', 'obvio', 'ok', 'okay', 'perfecto',
      'dale', 'ándale', 'va bien', 'está bien', 'vale',
      'yes', 'yeah', 'sure', 'yes please'
    ];

    const texto_limpio = this.normalizar_texto(texto);
    return confirmaciones.some(conf => texto_limpio.includes(conf));
  }

  es_negacion(texto) {
    /**
     * Detecta si el usuario está negando algo
     */
    const negaciones = [
      'no', 'nope', 'nada', 'nunca', 'jamás',
      'cancelar', 'olvida', 'cambie de idea', 'cambio de idea',
      'nah', 'nope', 'no thanks', 'no gracias'
    ];

    const texto_limpio = this.normalizar_texto(texto);
    return negaciones.some(neg => texto_limpio.includes(neg));
  }
}


/**
 * Generador de respuestas conversacionales
 * Crea respuestas naturales y fluidas
 */
class ConversationalResponseGenerator {
  constructor() {
    this.respuestas_template = this.inicializar_templates();
  }

  inicializar_templates() {
    return {
      saludo: [
        '¡Hola! 👋 Bienvenido a El Parcerito.',
        '¡Hola amigo! 😊 ¿Qué necesitas hoy?',
        'Saludos 👋 ¿En qué puedo ayudarte?'
      ],
      confirmacion_recibida: [
        'Perfecto, anotado. 📝',
        'Listo, entendido. ✓',
        'Gotcha. Continuemos. 👍'
      ],
      cargando: [
        '⏳ Un momento, estoy buscando...',
        '🔍 Déjame buscar eso...',
        '⌛ Cargando información...'
      ],
      no_encontrado: [
        '😅 No encontré eso. ¿Quieres intentar otra cosa?',
        '❌ Parece que no hay disponibilidad. 🤔',
        'Hmm, parece que no tenemos eso disponible.'
      ],
      ayuda_general: [
        '¿Cómo puedo ayudarte? Puedo:\n\n🛍️ Mostrar productos\n🛒 Ayudarte con el carrito\n💳 Procesar compra\n⭐ Redimir puntos',
        'Aquí está lo que puedo hacer:\n\n📦 Ver catálogo\n💰 Calcular total\n🚚 Arreglar envío\n💫 Gestionar puntos'
      ]
    };
  }

  obtener_random(array) {
    return array[Math.floor(Math.random() * array.length)];
  }

  generar_saludo(nombre = null) {
    const saludo = this.obtener_random(this.respuestas_template.saludo);
    
    if (nombre) {
      return saludo.replace('Hola', `Hola ${nombre}`);
    }
    
    return saludo;
  }

  generar_confirmacion() {
    return this.obtener_random(this.respuestas_template.confirmacion_recibida);
  }

  generar_cargando() {
    return this.obtener_random(this.respuestas_template.cargando);
  }

  generar_no_encontrado() {
    return this.obtener_random(this.respuestas_template.no_encontrado);
  }

  generar_ayuda() {
    return this.obtener_random(this.respuestas_template.ayuda_general);
  }

  crear_lista_producto(productos, mostrar_precio = true) {
    /**
     * Crea una lista bonita de productos
     */
    let mensaje = '';
    
    productos.forEach((prod, idx) => {
      mensaje += `${idx + 1}. *${prod.nombre}*\n`;
      
      if (mostrar_precio) {
        mensaje += `   💰 $${prod.precio}\n`;
      }
      
      if (prod.descripcion) {
        mensaje += `   ${prod.descripcion.substring(0, 60)}\n`;
      }
      
      mensaje += '\n';
    });

    return mensaje;
  }

  crear_resumen_carrito(items, total) {
    /**
     * Crea un bonito resumen del carrito
     */
    let mensaje = '🛒 *Resumen de Tu Compra*\n\n';
    
    items.forEach((item, idx) => {
      mensaje += `${idx + 1}. ${item.nombre}\n`;
      mensaje += `   x${item.cantidad} = $${item.subtotal}\n`;
    });

    mensaje += `\n💰 *Total: $${total.toFixed(2)}*\n`;

    return mensaje;
  }

  crear_opciones_botones(opciones) {
    /**
     * Crea opciones interactivas (si el platform lo soporta)
     * Sino, crea una lista textual
     */
    let mensaje = '';
    
    opciones.forEach((opt, idx) => {
      mensaje += `${idx + 1}. ${opt}\n`;
    });

    return mensaje;
  }
}


module.exports = {
  ConversationContext,
  ContextualIntentParser,
  ConversationalResponseGenerator
};
