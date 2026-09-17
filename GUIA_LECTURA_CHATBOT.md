# GUÍA DE LECTURA - CÓMO ENTENDER EL CHATBOT
**Para usuarios que quieren estudiar el código en profundidad**

---

## 🎯 OBJETIVO

Después de esta lectura, entenderás:
1. **Qué hace cada archivo**
2. **Por dónde fluye un mensaje**
3. **Dónde están los gaps**
4. **Cómo extender el sistema**

**Tiempo estimado:** 4-6 horas de lectura activa

---

## 📚 LECTURA RECOMENDADA EN ORDEN

### FASE 1: OVERVIEW (30 min)

Empieza por entender el propósito global.

**1. Lee primero:**
- [RESUMEN_EJECUTIVO_CHATBOT.md](RESUMEN_EJECUTIVO_CHATBOT.md)
  - Sección "EN UNA FRASE"
  - Sección "LO QUE FUNCIONA BIEN"
  - Sección "ARQUITECTURA"

**2. Entiende la estructura:**
- [DIAGRAMA_FLUJOS_CHATBOT.md](DIAGRAMA_FLUJOS_CHATBOT.md)
  - Sección "FLUJO ACTUAL DE MENSAJES"
  - Sección "CAPAS DEL SISTEMA ACTUAL"

**Checkpoint:** Deberías poder responder:
- ¿Por dónde entra un mensaje?
- ¿Cuáles son las 3 capas?
- ¿Qué funciona y qué no?

---

### FASE 2: MAIN SERVER (90 min)

Ahora lee el archivo principal.

**1. Lee el inicio de bot.js:**
```
bot.js, líneas 1-100
```
**Qué aprenderás:**
- Variables de configuración (Evolution, Oxidian, rate limits)
- Imports y dependencias
- Inicialización de BD SQLite

**Código clave:**
```javascript
const EVO_URL = process.env.EVOLUTION_API_URL || 'http://localhost:8080'
const OXIDIAN_URL = process.env.OXIDIAN_URL || 'http://localhost:5000'
const db = new Database(path.join(DB_DIR, 'bot.db'))
```

---

**2. Lee las tablas SQLite:**
```
bot.js, líneas 150-250
```
**Qué aprenderás:**
- Estructura de datos
- Qué información se persiste
- Índices y constraints

**Tablas principales:**
- `sessions` - Estado de usuario
- `handoffs` - Queue de espera
- `productos_cache` - Catálogo
- `logs` - Auditoria

---

**3. Lee las funciones helper:**
```
bot.js, líneas 300-600
```
**Qué aprenderás:**
- `normalizePhone()` - Limpia números
- `cfg()` / `setCfg()` - Acceso a configuración
- `getOxidianUrl()`, `getEvolutionKey()` - Helpers de config
- `getRoleLabel()`, `isAdminJid()` - Autorización

**Código clave:**
```javascript
function cfg(key, fallback = null) {
  try { return _cfgGet.get(key)?.value ?? fallback } catch { return fallback }
}

function normalizePhone(value) {
  let digits = String(value || '').replace(/\D/g, '')
  if (digits.startsWith('00')) digits = digits.slice(2)
  if (digits.length === 9 && /^[6789]/.test(digits)) digits = `34${digits}`
  return digits
}
```

---

**4. Lee rate limiting (IMPORTANTE):**
```
bot.js, líneas 1200-1400
```
**Qué aprenderás:**
- Cómo protege el bot de baneo
- 3 capas de defensa
- Fingerprint de spam

**Código clave:**
```javascript
const MAX_INBOUND_PER_WINDOW = 18  // mensajes por minuto
const MAX_OUTBOUND_PER_TARGET = 45 // mensajes/hora por usuario
const MAX_OUTBOUND_GLOBAL_PER_MIN = 40

function outboundAllowed(target, text) {
  // (1) Por destinatario
  if (!hitWindow(outboundBuckets, target, ...)) return false
  // (2) Global
  if (_globalOutboundTimes.length >= MAX_OUTBOUND_GLOBAL_PER_MIN) return false
  // (3) Fingerprint
  if (entry.recipients.size > MAX_SAME_TEXT_RECIPIENTS) return false
  return true
}
```

---

**5. Lee comunicación WhatsApp:**
```
bot.js, líneas 1450-1650
```
**Qué aprenderás:**
- Cómo se envía mensaje a WhatsApp
- Reintentos exponenciales
- Ventana 24h (no cold messaging)

**Código clave:**
```javascript
async function sendText(jid, text, opts = {}) {
  if (!opts.force && !opts.transactional) {
    const lastIn = lastInboundAt.get(jid) || 0
    const elapsed = Date.now() - lastIn
    if (!lastIn || elapsed > 24 * 60 * 60 * 1000) {
      log('warn', 'cold_message_blocked', ...)
      return false
    }
  }
  
  for (let attempt = 1; attempt <= 3; attempt++) {
    const r = await fetch(url, { ... })
    if (r.ok) return true
    if (r.status >= 400 && r.status < 500) return false  // no reintentar
    await new Promise(res => setTimeout(res, 500 * attempt))
  }
  return false
}
```

---

**6. Lee IA integrada:**
```
bot.js, líneas 1700-2000
```
**Qué aprenderás:**
- Cómo se llama OpenAI/Groq
- Caché y memory
- Burst limiting
- JSON schema parsing

**Código clave:**
```javascript
async function aiSmartReply(jid, ses, mensajeUsuario) {
  const cfg = await getAIConfig()
  if (!cfg?.habilitado) return null
  
  // Cache LRU
  const cacheKey = 'smart: ' + normaliza(mensajeUsuario)
  const cached = aiCacheGet(cacheKey)
  if (cached) return { ...cached, fromCache: true }
  
  // Burst limit (5/min)
  if (!_smartBurstAllow(phone)) return null
  
  // Llamar IA
  const messages = [
    { role: 'system', content: _smartSystemPrompt(...) },
    ...memoria,
    { role: 'user', content: mensajeUsuario }
  ]
  
  const out = await _callAIProviderJSON(cfg, messages, 220)
  
  // Cache
  aiCacheSet(cacheKey, { action, reply, confidence, ... })
  
  return { action, reply, confidence, cliente }
}
```

---

### FASE 3: FLUJO PRINCIPAL (60 min)

La orquestación central.

**1. Lee _handleMessage:**
```
bot.js, líneas 2980-3150
```
**Qué aprenderás:**
- Punto de entrada de TODO mensaje
- Routing por rol (admin/client)
- Detección de handoff

**Código clave:**
```javascript
async function _handleMessage(jid, text, pushName) {
  const ses = getSesion(jid)  // Carga sesión de BD
  
  const lower = text.toLowerCase().trim()
  const isOwner = isAdminJid(jid)
  
  // [1] ¿Es handoff?
  if (!isOwner) {
    const handoff = getHandoff(jid)
    if (handoff?.admin_jid) {
      return forwardClientToAdmin(jid, handoff.admin_jid, text)
    }
  }
  
  // [2] ¿Es comando global?
  if (['menu', 'inicio', 'hola'].includes(lower)) {
    return startClientMenu(jid, ses.nombre)
  }
  
  // [3] ¿Es admin en chat?
  if (isOwner && ses.estado === 'admin_chat') {
    return handleAdminChat(jid, ses, text)
  }
  
  // [4] ¿Es cliente?
  return handleMainMenu(jid, ses, text)
}
```

---

**2. Lee handleMainMenu:**
```
bot.js, líneas 3861-4200
```
**Qué aprenderás:**
- Menú cliente (7 opciones)
- FAQs canned
- IA fallback
- Fuzzy matching intención

**Flujo:**
```
handleMainMenu(jid, ses, opcion)
  ├─ [1] esSaludo? → respuesta canned
  ├─ [2] esDespedida? → respuesta canned
  ├─ [3] tryCannedFAQ? → respuesta FAQ
  ├─ [4] detectClientIntent? → fuzzy match a [1-7]
  └─ [5] else → aiSmartReply (IA)
       ├─ Si confidence >= 0.55
       │  └─ switch(action) { case 'estado': handleMainMenu(jid, ses, '2') }
       └─ Else → fallback genérico
```

**Opción 1-7:**
```javascript
switch (opcion) {
  case '1': return sendText(jid, '👉 ${tiendaUrl}')  // Catálogo
  case '2': // Estado pedido
  case '3': // Puntos fidelidad
  case '4': // Cobertura delivery
  case '5': // Link tienda
  case '6': // Info negocio
  case '7': return requestHumanSupport(jid)  // Handoff
}
```

---

**3. Lee handoff:**
```
bot.js, líneas 665-1100 (helpers)
bot.js, líneas 3000-3100 (_handleMessage handoff detection)
```
**Qué aprenderás:**
- Crear queue de handoff
- Auto-asignar a admin
- Transferir mensajes
- Cerrar/liberar chat

**Helpers principales:**
```javascript
function getHandoff(clientJid)
function createHandoffRequest(clientJid, destination)
function assignHandoff(clientJid, adminJid)
function autoAssignPendingHandoff(clientJid)
function closeHumanChat(adminJid, clientJid, notifyClient)
```

---

### FASE 4: MÓDULOS DESCONECTADOS (60 min)

Ahora entiende lo que existe pero no se usa.

**1. Lee ClientConversacional:**
```
handlers/clientConversacional.js, líneas 1-100
```
**Qué aprenderás:**
- Sistema de intenciones alternativo
- Estados conversacionales
- Estructura diferente a bot.js

**Clases:**
```javascript
class ClientConversationState {
  estado: 'inicio', 'menu', 'navegando', 'carrito', 'checkout'
  contexto: {}       // Datos conversacionales
  carrito: {}        // Productos
  historial: []      // Últimos 10 mensajes
}

class ClientBotConversacional {
  intenciones: {}    // 20+ mapeos palabra clave → intención
  sesiones: Map      // JID → ClientConversationState
  
  procesar_mensaje(jid, texto)  // Punto de entrada
  detectar_intencion(texto)     // Devuelve [intención1, intención2, ...]
}
```

---

**2. Lee BotIntegracion:**
```
botIntegracion.js, líneas 1-150
```
**Qué aprenderás:**
- Intento de conexión entre contexto y bot
- Flujos específicos de compra
- Solicitud de datos cliente

**Métodos clave:**
```javascript
procesar_mensaje_cliente(jid, texto)
procesar_respuesta_pendiente(contexto, texto)
iniciar_compra(jid, productos)
agregar_carrito_conversacional(jid, producto, cantidad)
solicitar_datos_cliente(jid)
solicitar_tipo_entrega(jid)
```

---

**3. Lee ConversationContext:**
```
utils/conversationContext.js
```
**Qué aprenderás:**
- Cómo debería mantenerse contexto
- Detección de tema
- Extracción de datos

**Clases:**
```javascript
class ConversationContext {
  historial: []      // Últimos 15 mensajes
  tema_actual: null  // 'catalogo', 'carrito', 'compra', etc.
  pregunta_pendiente // Espera respuesta (timeout 5 min)
  datos_cliente: {}  // Nombre, email, etc.
}

class ContextualIntentParser {
  detectar_tema(texto)           // Retorna tema + confianza
  extraer_cantidad(texto)        // "dame 3" → 3
  extraer_email(texto)           // Email regex
  es_confirmacion(texto)         // "sí", "ok", "dale"
}
```

---

### FASE 5: SÍNTESIS (30 min)

Ahora une todo.

**1. Dibuja el flujo mental:**
Haz un diagrama en papel:
```
Usuario escribe
  ↓
_handleMessage
  ├─ ¿handoff? → forward
  ├─ ¿comando? → router
  ├─ ¿admin_chat? → adminChat
  └─ ¿client? → handleMainMenu
       ├─ saludo → respuesta
       ├─ FAQ → respuesta
       ├─ fuzzy → opción
       └─ else → IA
            ├─ confidence? → action
            └─ fallback
```

**2. Identifica los gaps:**
- Carrito: ClientConversacional existe pero no se llama
- Contexto: ConversationContext existe pero no se usa
- IA: Relegada a fallback, no detector principal

**3. Lee el análisis completo:**
- [ANALISIS_CHATBOT_COMPLETO.md](ANALISIS_CHATBOT_COMPLETO.md)

**Checkpoint:** Deberías poder responder:
- ¿Cuáles son los 3 módulos paralelos?
- ¿Por qué no funciona el carrito?
- ¿Cómo se llama IA ahora vs cómo debería?
- ¿Qué habría que cambiar para conectarlos?

---

## 🗂️ ARCHIVOS EN ORDEN DE IMPORTANCIA

| # | Archivo | Líneas | Prioridad | Tiempo |
|---|---------|--------|-----------|--------|
| 1 | RESUMEN_EJECUTIVO | - | ⭐⭐⭐ | 30 min |
| 2 | DIAGRAMA_FLUJOS | - | ⭐⭐⭐ | 20 min |
| 3 | bot.js (líneas 1-100) | 100 | ⭐⭐⭐ | 15 min |
| 4 | bot.js (líneas 150-250) | 100 | ⭐⭐⭐ | 10 min |
| 5 | bot.js (líneas 2980-3150) | 170 | ⭐⭐⭐ | 20 min |
| 6 | bot.js (líneas 3861-4200) | 340 | ⭐⭐⭐ | 30 min |
| 7 | bot.js (líneas 1700-2000) | 300 | ⭐⭐ | 20 min |
| 8 | handlers/clientConversacional.js | 800 | ⭐⭐ | 30 min |
| 9 | botIntegracion.js | 300 | ⭐⭐ | 15 min |
| 10 | utils/conversationContext.js | 500 | ⭐⭐ | 20 min |
| 11 | ANALISIS_CHATBOT_COMPLETO | - | ⭐ | 30 min |
| 12 | bot.js (resto: handoff, IA, admin) | 2200 | ⭐ | 60 min |

---

## 🎓 QUIZ DE ENTENDIMIENTO

Cuando termines, deberías poder responder:

### Básico
- [ ] ¿Dónde entra un mensaje cuando llega del cliente?
- [ ] ¿Cuál es la función orquestadora principal?
- [ ] ¿Cuántas capas de rate limiting hay?
- [ ] ¿Qué bases de datos se usan?

### Intermedio
- [ ] ¿Por qué existe ClientConversacional si no se usa?
- [ ] ¿Cuál es la diferencia entre bot.js y BotIntegracion?
- [ ] ¿Cómo se mantiene el contexto actualmente?
- [ ] ¿En qué orden se ejecutan los checks en handleMainMenu?

### Avanzado
- [ ] ¿Qué habría que cambiar para que el carrito funcione?
- [ ] ¿Cómo se llamaría IA PRIMERO en vez de fallback?
- [ ] ¿Qué problemas tiene el rate limiting actual?
- [ ] ¿Cómo se sincroniza el estado entre instancias?

---

## 💾 ARCHIVOS QUE NECESITAS

Descargar/revisar estos recursos:
1. `bot.js` - Servidor principal
2. `handlers/clientConversacional.js` - Bot desconectado
3. `botIntegracion.js` - Integrador teorico
4. `utils/conversationContext.js` - Contexto desconectado
5. `.env.example` - Variables de configuración

---

## 🚀 SIGUIENTE PASO

Después de terminar esta lectura:

**Opción A (Entender mejor):**
- Ejecuta el bot localmente
- Envía mensajes por WhatsApp
- Observa qué archivos se llaman
- Lee los logs

**Opción B (Proponer mejoras):**
- Identifica 3 cambios que harías
- Dibuja el flujo mejorado
- Estima tiempo de implementación
- Prepara propuesta

**Opción C (Implementar):**
- Conecta ClientBotConversacional a handleMainMenu
- Implementa servicios reales (BD)
- Testa carrito conversacional
- Haz PR

---

**FIN DE GUÍA DE LECTURA**
