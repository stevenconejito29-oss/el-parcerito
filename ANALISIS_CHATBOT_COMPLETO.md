# ANÁLISIS COMPLETO - SISTEMA DE CHATBOT EL PARCERITO
**Fecha:** 2026-07-02 | **Estado:** NO se hacen cambios, solo reporte

---

## 📋 RESUMEN EJECUTIVO

El sistema es una arquitectura **híbrida de 3 capas**:
1. **Capa Rígida**: Bot de menú numerado + FAQs canned (lo que funciona ahora)
2. **Capa Conversacional Desacoplada**: Clases para IA conversacional (NO INTEGRADA)
3. **Capa IA**: Modelo LLM (OpenAI/Groq) para respuestas naturales (INTEGRADA EN bot.js)

**Hallazgo crítico:** El sistema tiene **DOS implementaciones de conversacional** que NO hablan entre ellas:
- Una en `bot.js` (activa): IA integrada + menú rígido
- Otra en `handlers/botIntegracion.js` (huérfana): Clases conversacionales completas pero desconectadas

---

## 🏗️ ARQUITECTURA ACTUAL

```
CLIENTE WhatsApp
    ↓
Evolution API (webhook)
    ↓
bot.js:/webhook/evolution
    ↓
_handleMessage(jid, text)
    ├─ [1] ¿Es handoff? → forwardClientToAdmin()
    ├─ [2] ¿Es comando global? (menu, admin, cliente)
    ├─ [3] ¿Es admin? → handleAdminMenu()
    └─ [4] ¿Es cliente? → handleMainMenu()
         ├─ [4a] Saludo/despedida → respuesta canned
         ├─ [4b] FAQ canned → tryCannedFAQ()
         ├─ [4c] Fuzzy match intención → detectClientIntent()
         └─ [4d] IA fallback → aiSmartReply() [OpenAI/Groq]
                 ├─ Cache LRU (30 min)
                 ├─ Burst limiter (5 req/min por teléfono)
                 ├─ Rate limit backend
                 └─ JSON schema parsing
```

**BD SQLite Local:**
- `sessions`: Estado de cada usuario (rol, estado, carrito, datos)
- `handoffs`: Cola de transferencia a humanos
- `handoff_messages`: Historial durante handoff
- `productos_cache`: Catálogo sincronizado desde Oxidian
- `logs`: Auditoria

---

## 📁 ARCHIVO 1: bot.js (5800+ líneas)

### ✅ QUÉ ESTÁ BIEN IMPLEMENTADO

#### A. Configuración y Seguridad
- **60+ variables de env** documentadas (Evolution, Oxidian, rate limits)
- **WEBHOOK_SECRET** validado con time-safe comparison
- **Timestamp anti-replay** (±5 min window)
- **PIN admin** (hash SHA256) con TTL
- **Rate limiting** en 3 capas (inbound, outbound, API)

#### B. Comunicación WhatsApp
- **sendText()** con reintentos exponenciales (3 intentos)
- **Throttling por destinatario** (45 msg/hora por defecto)
- **Burst protection global** (40 msg/min)
- **Fingerprint de spam** (max 8 destinatarios con mismo texto)
- **Ventana 24h** (no envía en frío, solo en respuesta)

#### C. Integración Oxidian
- **oxidianGet/Post()** con timeout 8-10s
- **Sincronización cada 5 min**: Branding, catálogo, zonas, PIN admin
- **Cache local** de configuración + productos

#### D. Sistema IA Integrado
- **aiSmartReply()** con flujo completo:
  - Detección de prompt injection
  - Cache LRU de respuestas (100 máx, TTL 30 min)
  - Burst limiter (5 llamadas/min por teléfono)
  - Rate limit backend
  - Memoria compartida (últimos 4 turnos)
  - Soporta OpenAI y Groq
- **_smartSystemPrompt()** personalizado por negocio (nombre, dirección, etc.)
- **aiCacheGet/Set()** implementado correctamente
- **_sanitizeReply()** filtra filtraciones de prompt

#### E. Manejo de Handoff (Transferencia a Humanos)
- **Queue de pedidos** sin asignar
- **Auto-asignación** a admin disponible
- **Notificaciones a admins** cuando hay espera
- **Transcripción de mensajes** pendientes entregados después
- **Release/cierre de chat** con confirmación
- **Sesión activa** por admin (un chat por vez)
- **Lease TTL** (default 30 min)

#### F. Sesiones y Estado
- **TTL de sesión**: 45 min (configurable)
- **getSesion()** con:
  - Rol auto-detectado (admin/client)
  - Enriquecimiento de cliente (puntos, pedidos recientes)
  - Limpieza automática si expirada
- **saveSesion()** para persistencia

#### G. FAQs Canned (Conocimiento Estático)
- **tryCannedFAQ()** con fuzzy matching
- ~15 preguntas frecuentes:
  - Horario, dirección, formas de pago
  - Delivery, recogida, tiempo entrega
  - Combos, alérgenos, envío
  - Bizum, tarjeta, "cómo pedir"
  - Link tienda online

#### H. Menú Principal Cliente
- **Opción 1**: Link a tienda online
- **Opción 2**: Estado de pedido (último o por número)
- **Opción 3**: Club de fidelidad (puntos)
- **Opción 4**: Verificar cobertura delivery (dirección)
- **Opción 5**: Link tienda online
- **Opción 6**: Información negocio (horario, dirección, teléfono, métodos pago)
- **Opción 7**: Hablar con agente (handoff)

#### I. Estadísticas
- **MSG_STATS**: Contador de eventos
  - Saludos, FAQs, intenciones, IA (cache vs fresh), fallos, fallbacks
  - Métrica de ahorro: cuántos mensajes se resuelven SIN IA

#### J. Admin Panel
- **!status**: Ver estado bot
- **!take [numero]**: Tomar handoff
- **!list**: Listar handoffs pendientes
- **!broadcast**: Enviar a múltiples (con límite)
- Modo test cliente desde admin
- Bloqueo/desbloqueo de usuarios
- Sync manual de config

---

### ⚠️ QUÉ ESTÁ RÍGIDO / LIMITADO

#### A. Intención del Cliente - Fuzzy Match Limitado
```javascript
// detectClientIntent() usa fuzzy matching SIMPE:
// "catalogo" ~ "catálogo" (±1 typo)
// "pedido" ~ "estado" (solo si longitud similar)
// Solo 7 opciones hardcoded (1-7)
// Si no matchea exacto + fuzzy, va a IA directamente
```

**Limitación:** No detecta sinonimos naturales:
- "¿qué tienen?" → no mapea a opción 1 (catálogo)
- "mi compra" → no mapea a opción 2 (estado)
- "regálame un café" → no mapea a nada (va a IA)

#### B. Flujo Conversacional - Muy Lineal
```javascript
handleMainMenu(jid, ses, opcion)
// 1. Si saludo → respuesta
// 2. Si despedida → respuesta
// 3. Si FAQ canned → respuesta
// 4. Si fuzzy match → opción numérica
// 5. Else → IA
```

**Limitación:** No mantiene contexto entre mensajes
- Usuario: "¿qué tienen?"
- Bot: "Ver en tienda online"
- Usuario: "¿y entregas aquí?" (pregunta NEW en contexto de anterior)
- Bot: No entiende que es seguimiento, empieza de 0

#### C. IA Solo en Default
- **IA se invoca** solo si nada matchea (último fallback)
- **IA no se usa** para enriquecer FAQs
- **IA no mantiene** memoria de conversación dentro de misma sesión
- Cada llamada a IA es aislada (aunque hay memoria backend)

#### D. Carrito Conversacional - NO IMPLEMENTADO
- `bot.js` tiene tabla `carrito` en BD
- **Pero** NO hay flujo para agregar productos por chat
- Usuario no puede decir "dame 2 cafés" y que se agreguen
- El carrito existe solo como data que se sincroniza con tienda online

#### E. Órdenes de Checkout - NO CONVERSACIONAL
- Usuario no puede dar datos (nombre, email, dirección) por WhatsApp
- Tiene que ir a tienda online para checkout
- Solo se capturan datos en handoff humano (si entra en queue)

#### F. Handoff Queue - Primitivo
- No hay priorización por urgencia
- No hay SLA (tiempo máximo de espera)
- No hay escalamiento (si admin no responde)
- No hay routing por especialización (ej: problemas técnicos → admin tech)

---

## 📁 ARCHIVO 2: handlers/clientConversacional.js (800+ líneas)

### 📌 ESTADO ACTUAL: **HUÉRFANO**
Este archivo es una implementación completa de un bot conversacional pero **NO está conectado a bot.js**

### ✅ QUÉ TIENE IMPLEMENTADO

#### A. Clase ClientConversationState
- **Estado**: `{inicio, menu, navegando, carrito, checkout, ...}`
- **Contexto**: Datos almacenados (qué estamos haciendo)
- **Carrito**: Productos agregados con cantidad
- **Historial**: Últimos 10 mensajes
- **Preferencias**: Datos del cliente

```javascript
class ClientConversationState {
  actualizar_estado(nuevo_estado, contexto)
  agregar_mensaje(texto, tipo)  // "usuario" | "bot"
  get_contexto_actual()          // para debugging/logging
  limpiar_sesion()
}
```

#### B. Clase ClientBotConversacional
- **20+ intenciones mapeadas** por palabras clave:
  ```
  saludos: ['hola', 'hi', 'buenos dias', ...]
  comprar: ['quiero', 'dame', 'pedir', ...]
  categorias: ['categorías', 'tipos', 'qué tipo', ...]
  ver_carrito: ['carrito', 'mi carrito', 'qué tengo', ...]
  agregar_carrito: ['agregar', 'dame', 'quiero', ...]
  quitar_carrito: ['quitar', 'elimina', 'saca', ...]
  pagar: ['pagar', 'comprar', 'checkout', ...]
  delivery: ['delivery', 'envío', 'a domicilio', ...]
  puntos: ['puntos', 'mis puntos', 'saldo', ...]
  mis_ordenes: ['mis pedidos', 'historial', ...]
  problema: ['error', 'no funciona', 'falla', ...]
  promociones: ['promoción', 'oferta', 'descuento', ...]
  ... (8 más)
  ```

- **Método procesar_mensaje()**: Switch por intención
  ```
  Usuario: "Dame un café"
  → detectar_intencion() → ['comprar', 'buscar_producto']
  → procesar_mensaje() → mostrar_catalogo()
  → "📦 Nuestros Productos..."
  ```

#### C. Respuestas Implementadas
- `responder_saludo()` - con hora del día (buenos días/tardes/noches)
- `mostrar_catalogo()` - primeros 5 productos con precio
- `mostrar_categorias()` - lista de categorías
- `mostrar_carrito()` - items, cantidades, total
- `solicitar_producto_agregar()` - pide al usuario qué producto
- `solicitar_producto_quitar()` - lista items del carrito
- `confirmar_vaciar_carrito()` - pide confirmación
- `iniciar_checkout()` - solicita nombre, email, teléfono, dirección
- `mostrar_puntos()` - saldo de fidelidad
- `iniciar_canje_puntos()` - productos disponibles para canjear
- `mostrar_ordenes_recientes()` - últimas 5 órdenes
- `solicitar_numero_orden()` - para seguimiento
- `info_tienda()` - ubicación, teléfono, email
- `despedir()` - 3 variantes aleatorias

#### D. Manejo de Errores
- Try-catch en cada método
- Fallback a "No pude..."
- Contador de intentos fallidos (`intentos_fallidos++`)
- Después de 3 fallos: ayuda explícita con opciones

### ❌ PROBLEMAS - POR QUÉ NO SE USA

#### 1. No Está Integrado en bot.js
- `botIntegracion.js` importa y crea instancia
- Pero `bot.js` **NUNCA crea BotIntegracion**
- En `handleMainMenu()` se usa **IA de bot.js** (OpenAI/Groq)
- No se llama a `ClientBotConversacional.procesar_mensaje()`

#### 2. Arquitectura Incompatible
- **ClientConversacional**: Mantiene su propio mapa de sesiones (`Map<jid, ClientConversationState>`)
- **bot.js**: Mantiene sesiones en SQLite
- No pueden compartir estado simultáneamente

#### 3. Servicios Mock
- Constructor espera `services` objeto: `{ obtener_catalogo, obtener_producto, obtener_puntos, ... }`
- Estos métodos NO están implementados
- Solo hace `try-catch` que retorna error genérico

```javascript
async mostrar_catalogo(sesion) {
  const catalogo = await this.services.obtener_catalogo();
  // ^ Lanza "No pude cargar el catálogo"
}
```

#### 4. Respuestas Son Templates Simples
- Generan mensajes bonitos pero sin lógica real
- No consulta BD ni API
- Supone que `services` lo hace (que no existe)

---

## 📁 ARCHIVO 3: utils/conversationContext.js (500+ líneas)

### ✅ QUÉ TIENE

#### A. Clase ConversationContext
- Historial de **últimos 15 mensajes** (rol + texto + timestamp)
- **Tema actual**: Qué está discutiendo ("catalogo", "carrito", "compra", etc.)
- **Pregunta pendiente**: Espera respuesta (con timeout de 5 min)
- **Datos del cliente**: Captura durante conversación
- **TTL de sesión**: 2 horas

```javascript
class ConversationContext {
  agregar_mensaje(rol, texto, metadatos)
  establecer_tema(nuevo_tema)
  establecer_pregunta_pendiente(pregunta, tipo)
  responder_pregunta(respuesta)
  tiene_pregunta_pendiente()  // con timeout check
  get_historial_resumen()     // últimos 5
  obtener_contexto_para_ia()  // formatea para LLM
}
```

#### B. Clase ContextualIntentParser
- **Detección de tema** en 6 categorías:
  - catalogo, carrito, compra, entrega, puntos, pedidos
- Cada tema tiene **palabras clave + preguntas de seguimiento**
- `normalizar_texto()` - quita acentos, caracteres especiales
- `extraer_cantidad()` - "dame 3 cafés" → 3
- `extraer_email()` - regex de email
- `extraer_telefono()` - regex de teléfono
- `es_confirmacion()` - detecta "sí", "ok", "dale", etc.
- `es_negacion()` - detecta "no", "nunca", "olvida", etc.

#### C. Clase ConversationalResponseGenerator
- **Templates de respuestas** para 10+ tipos:
  - saludo (3 variantes)
  - confirmacion_recibida (3 variantes)
  - cargando (3 variantes)
  - no_encontrado (3 variantes)
  - ayuda_general (2 variantes)

- Métodos generadores:
  - `crear_lista_producto()` - formatea productos con precio
  - `crear_resumen_carrito()` - items x cantidad = subtotal
  - `crear_opciones_botones()` - lista de opciones numerada

### ❌ PROBLEMAS

#### 1. Desconectado de bot.js
- bot.js **NO usa estas clases**
- Tiene su propia lógica de detección de intención
- Tiene su propio generator de respuestas

#### 2. Temas Muy Limitados
- Solo 6 temas hardcoded
- No cubre: reportar problema, cancelar, promociones, horario, etc.

#### 3. Parser Demasiado Simple
- Busca palabras clave exactas (case-insensitive + acentos)
- No entiende sinonimos semánticos
- "¿qué venden?" no matchea "catalogo"

#### 4. Templates Genéricos
- Respuestas no personalizadas por negocio
- Hardcodeadas: "El Parcerito", "Calle Principal 123", "+1 (555) 123-4567"
- No usa config de Oxidian

---

## 📁 ARCHIVO 4: botIntegracion.js (300+ líneas)

### 📌 ESTADO ACTUAL: **PUENTE NO USADO**

Es una clase que **intenta conectar** ClientBotConversacional con ConversationContext, pero **bot.js nunca la instancia**.

### ✅ QUÉ PROPONE

```javascript
class BotIntegracion {
  procesar_mensaje_cliente(jid, texto, datos_usuario)
    → obtener_contexto(jid)
    → intent_parser.detectar_tema()
    → bot_conversacional.procesar_mensaje()
    → agregar a historial
    → retorna {ok, respuesta, contexto}

  procesar_respuesta_pendiente(contexto, texto)
    → Valida respuesta según tipo (nombre, email, teléfono, dirección)
    → Extrae datos
    → Actualiza contexto
    → Retorna respuesta de confirmación

  Flujos específicos:
    - iniciar_compra()
    - agregar_carrito_conversacional()
    - solicitar_datos_cliente()
    - solicitar_tipo_entrega()
    - solicitar_puntos_canje()
```

### ❌ POR QUÉ NO SE USA

1. **Nunca instanciado** en bot.js
2. **Supone servicios mock** que no existen
3. **Incompatible con BD SQLite** de bot.js (usa Map en memoria)
4. **Métodos incompletos** - algunos retornan "Listo, anotado"

---

## 🔄 CÓMO SE CONECTAN AHORA (REALIDAD)

```
Usuario escribe por WhatsApp
     ↓
Evolution webhook → /webhook/evolution
     ↓
_handleMessage(jid, texto, nombre)
     ↓
getSesion(jid)  ← Carga sesión de SQLite
     ↓
¿Es admin?  → handleAdminMenu()
¿Es handoff? → forwardClientToAdmin() o queueHandoffMessage()
¿Es cliente?  → handleMainMenu()
     ↓
[1] Saludo → respuesta canned (bot.js)
[2] FAQ → fuzzy match → respuesta canned (bot.js)
[3] Fuzzy match intención [1-7] → opción numérica
[4] Else → aiSmartReply() (OpenAI/Groq)
     ↓
Respuesta + update sesión
     ↓
sendText(jid, respuesta)
     ↓
Evolution API → WhatsApp
```

**NOTA:** `botIntegracion.js`, `clientConversacional.js`, `conversationContext.js` **NO participan en este flujo**.

---

## 🎯 QUÉ ES CONVERSACIONAL VS RÍGIDO

### ✅ CONVERSACIONAL

| Feature | Implementación | Ubicación |
|---------|---|---|
| IA naturaleza libre | Sí | `aiSmartReply()` en bot.js |
| Detecta tema | Sí | `aiSmartReply()` + sistema prompt |
| Mantiene contexto | Parcial | Memory backend (Oxidian) |
| Responde fuera-de-menú | Sí | IA fallback |
| Entiende sinonimos | Sí | LLM OpenAI/Groq |

### ❌ RÍGIDO

| Feature | Ubicación |
|---------|---|
| Menú numerado [1-7] | `handleMainMenu()` |
| Opciones hardcodeadas | bot.js linea 3920-3990 |
| Fuzzy match 7 opciones | `detectClientIntent()` |
| FAQs estáticas canned | ~200 líneas bot.js |
| No hay carrito conversacional | ClientConversacional tiene pero NO se usa |
| No hay checkout por chat | Solo tienda online |
| No hay captura datos (name, email, etc) conversacional | ClientBotConversacional tiene pero NO se usa |
| Handoff es lo único "conversacional" a humano | Pero IA no interviene ahí |

---

## 🔴 GAPS Y PROBLEMAS IDENTIFICADOS

### CRÍTICOS

#### 1. **Arquitectura Fragmentada - 2 Sistemas Conversacionales**
- Bot.js tiene su propia IA + menú
- botIntegracion.js tiene otro bot completamente
- **Resultado**: Code duplication, confusión, mantenimiento difícil

**Síntoma:** Si quieres agregar una intención nueva:
- Opción A: Editar `handleMainMenu()` en bot.js (90 líneas de switch)
- Opción B: Editar `ClientBotConversacional` (que NO se usa)

#### 2. **Carrito Conversacional No Funciona**
- Usuario **no puede** agregar productos por chat
- `ClientConversacional.agregar_carrito()` existe pero:
  - No se llama desde bot.js
  - Llama a `this.services.obtener_catalogo()` que no existe
  - No sincroniza con SQLite de bot.js
- **Impacto**: Usuario debe ir a tienda online, pierde experiencia WhatsApp

#### 3. **Datos de Checkout No Se Capturan en Chat**
- Usuario no puede dar nombre, email, teléfono, dirección por WhatsApp
- `botIntegracion.solicitar_datos_cliente()` existe pero no se llama
- **Impacto**: Flujo incompleto, mala UX

#### 4. **IA Solo Es Fallback**
- IA se invoca cuando nada más matchea
- No se usa para enriquecer FAQs
- No se usa para aclarar intención fuzzy
- **Impacto**: Gasta tokens innecesariamente

#### 5. **No Hay SLA en Handoff**
- Si admin no responde, cliente espera indefinidamente
- No hay escalamiento (ej: pasar a otro admin)
- No hay timeout de cola
- **Impacto**: Clientes frustrados, no se resuelve

### ALTOS

#### 6. **Intención Fuzzy Match Es Muy Simple**
```javascript
// detectClientIntent() solo busca palabras del nombre de opción
// "Dame el estado de mi pedido" no mapea a opción 2 ("estado")
// Requiere palabras clave muy específicas
```

#### 7. **Context Entre Mensajes Débil**
```javascript
// Usuario: "¿Qué tienen?"
// Bot: "Ver en tienda online"
// Usuario: "¿Y si no tengo acceso a internet?"
// Bot: Empieza de 0, no sabe que hablaba de catálogo
// Debería usar ConversationContext pero no lo hace
```

#### 8. **BD SQLite - Problema de Escalabilidad**
- Toda sesión va a BD
- `sessions` es PRIMARY KEY por JID
- Con 10k usuarios concurrentes: contención de locks
- **Solución**: Memcache/Redis pero no implementado

#### 9. **Rate Limiting Distribuido Incompleto**
- Funciona para UNA instancia bot
- Si hay 2 instancias de bot.js: cada una tiene su propio mapa en memoria
- `_globalOutboundTimes`, `inboundBuckets`, `messageQueues` son locales
- **Problema**: Si escala horizontalmente, rate limit se elude

#### 10. **Memoria de IA Tiene Límite**
- Solo guarda últimos 4 turnos (2 user + 2 assistant)
- Con conversación larga, pierde contexto
- TTL de cache IA es 30 min (pueda obsoleto si cliente espera)

### MEDIOS

#### 11. **FAQs Son Hardcodeados**
- Ubicados en bot.js en un diccionario large
- No vienen de BD ni config
- Para agregar FAQ nueva: editar bot.js

#### 12. **No Hay Multi-idioma**
- TODO es español hardcodeado
- Si negocio necesita catalán/inglés: rediseño completo

#### 13. **Admin Panel Muy Básico**
- Comandos por texto (!status, !take, etc.)
- No hay interfaz web (ese es panel Oxidian)
- No hay métricas en tiempo real
- No hay dashboard de conversaciones

#### 14. **Logging Débil Para Debugging**
- Logs van a DB (tabla `logs`)
- Solo últimas 500 líneas guardadas
- No hay nivel de debug/verbose
- No hay tracing de conversación completa

#### 15. **Bot No Entiende Correcciones**
- Usuario: "Ese no"
- Bot: "¿Qué?"
- No hay forma de volver atrás sin empezar menú

---

## 📊 MATRIZ FUNCIONAL

| Funcionalidad | ✅ Implementado | ✅ Conversacional | ⚠️ Detalles |
|---|---|---|---|
| **Menú cliente** | Sí | No | Numerado [1-7] |
| **Catálogo** | Sí | No | Link a tienda, no en chat |
| **Carrito** | Parcial | Parcial | Existe en ClientConversacional pero no se usa |
| **Checkout** | No | No | Solo en tienda online |
| **Datos cliente** | Parcial | Parcial | Solo en handoff a humano |
| **Estado pedido** | Sí | Parcial | Por número, no por contexto |
| **Cancelación** | Sí | Parcial | Requiere número exacto |
| **Puntos** | Sí | Parcial | Consulta, no canje conversacional |
| **Horario** | Sí | No | FAQ canned |
| **Dirección** | Sí | No | FAQ canned |
| **Pago** | No | No | N/A |
| **Delivery** | Parcial | Parcial | Opción en menú, no conversacional |
| **Recogida** | Parcial | Parcial | Opción en menú |
| **Handoff a humano** | Sí | Sí | Queue + auto-assign + transcrip |
| **IA fallback** | Sí | Sí | OpenAI/Groq, solo si no matchea |

---

## 🎓 CONCLUSIONES

### FORTALEZAS
1. **Sistema IA integrado** completo (OpenAI/Groq)
2. **Handoff robusto** con queue, auto-assign, transcripción
3. **Rate limiting sofisticado** anti-baneo WhatsApp
4. **Seguridad bien pensada** (PIN admin, webhook secret, anti-replay)
5. **Sync de config** desde Oxidian (branding, catálogo, zonas)
6. **Cache inteligente** (LRU + TTL)

### DEBILIDADES
1. **Dos implementaciones conversacionales que no hablan**
2. **Carrito no funciona en chat**
3. **Checkout no conversacional**
4. **IA relegada a fallback (gasta tokens)**
5. **Intención fuzzy match muy simple**
6. **Contexto entre mensajes débil**
7. **No escala horizontalmente** (rate limit local)
8. **FAQs hardcodeados**

### RECOMENDACIÓN ESTRATÉGICA
**NO refactorizar todo.** Propuesta:

**Fase 1 (Integración Rápida):**
- Conectar `botIntegracion.procesar_mensaje_cliente()` a `handleMainMenu()` como alternativa a IA
- Usar conversationContext para mantener estado
- Resultado: Conversación mejor sin refactorizar bot.js completo

**Fase 2 (Mejora de IA):**
- Llamar IA ANTES (no solo fallback) para detectar intención
- Si IA dice "estado_pedido", route a opción 2 con contexto
- Guardar respuesta IA en cache de conversación

**Fase 3 (Carrito):**
- Integrar `ClientBotConversacional.agregar_carrito()` con API real
- Capturar en SQLite, no en Map
- Sincronizar con tienda online

---

## 🚀 PRÓXIMOS PASOS SUGERIDOS

1. **Juntar las dos implementaciones conversacionales** en una sola clase
2. **Implementar servicios reales** en ClientConversacional (acceso a BD + API Oxidian)
3. **Carrito funcional en WhatsApp** desde cero
4. **Checkout conversacional** (captura datos, valida, enruta a tienda)
5. **Mejorar fuzzy match** intención
6. **Multi-idioma** como configuración

---

**FIN DEL ANÁLISIS**
