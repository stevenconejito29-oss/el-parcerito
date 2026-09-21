# DIAGRAMA DE FLUJO - SISTEMA CHATBOT EL PARCERITO

## 1. FLUJO ACTUAL DE MENSAJES (LO QUE SUCEDE REALMENTE)

```
┌─────────────────────────────────────────────────────────────┐
│                    CLIENTE WHATSAPP                         │
│              (escribe: "¿Qué tienen?")                      │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
         ┌───────────────────────────────────┐
         │   Evolution API Webhook           │
         │  POST /webhook/evolution          │
         │  (recibe payload de mensaje)      │
         └────────────┬──────────────────────┘
                      │
                      ▼
         ┌───────────────────────────────────┐
         │   bot.js: /webhook/evolution      │
         │  - Valida WEBHOOK_SECRET          │
         │  - Verifica timestamp (anti-replay)
         │  - Persiste en tabla inbound_messages
         └────────────┬──────────────────────┘
                      │
                      ▼
      ┌──────────────────────────────────────────┐
      │  _handleMessage(jid, "¿Qué tienen?")     │
      │                                          │
      │  [1] ¿Es handoff activo?                 │
      │      → forwardClientToAdmin()            │
      │      [NO en este caso]                   │
      │                                          │
      │  [2] ¿Es comando global?                 │
      │      ("menu", "admin", "cliente")        │
      │      [NO en este caso]                   │
      │                                          │
      │  [3] ¿Es admin?                          │
      │      → handleAdminMenu()                 │
      │      [NO en este caso]                   │
      │                                          │
      │  [4] ¿Es cliente?                        │
      │      → handleMainMenu()                  │
      │      [SÍ → continúa]                     │
      └────────┬─────────────────────────────────┘
               │
               ▼
      ┌──────────────────────────────────────────────┐
      │  handleMainMenu(jid, ses, "¿Qué tienen?")   │
      │                                             │
      │  [1] ¿Es saludo?                            │
      │      esSaludo("¿Qué tienen?") → FALSE       │
      │                                             │
      │  [2] ¿Es despedida?                         │
      │      esDespedida("¿Qué tienen?") → FALSE    │
      │                                             │
      │  [3] ¿Hay FAQ canned?                       │
      │      tryCannedFAQ("¿Qué tienen?")           │
      │      [Busca en diccionario]                 │
      │      [NO matchea exacto]                    │
      │                                             │
      │  [4] ¿Fuzzy match intención?                │
      │      detectClientIntent("¿Qué tienen?")     │
      │      [Busca palabras: "qué", "tienen"]      │
      │      Opción 1? (catalogo)                   │
      │      [NO matchea → opcion = null]           │
      │                                             │
      │  [5] DEFAULT → aiSmartReply()               │
      │      [Va a IA]                              │
      └────────┬─────────────────────────────────────┘
               │
               ▼
      ┌──────────────────────────────────────────────┐
      │  aiSmartReply(jid, ses, "¿Qué tienen?")     │
      │                                             │
      │  [1] ¿IA habilitada?                        │
      │      cfg('habilitado') → true               │
      │                                             │
      │  [2] ¿Ya está en cache?                      │
      │      Key = "smart: qué tienen"              │
      │      aiCache.get(key) → null                │
      │      [Primera vez]                          │
      │                                             │
      │  [3] ¿Burst limiter?                        │
      │      5 llamadas/min por teléfono             │
      │      Este cliente: 1era llamada              │
      │      [PERMITE]                              │
      │                                             │
      │  [4] ¿Rate limit backend?                   │
      │      POST /api/bot/ai/usage                 │
      │      → Oxidian verifica límite global       │
      │      [OK]                                   │
      │                                             │
      │  [5] Obtener contexto cliente:              │
      │      GET /ai/cliente-context?telefono=...   │
      │      ↓ Oxidian retorna:                     │
      │      {nombre, puntos, pedidos_recientes}    │
      │                                             │
      │  [6] Obtener memoria de IA:                 │
      │      GET /ai/memory?telefono=...            │
      │      ↓ Últimos 4 turnos                     │
      │      [Vacío: primera vez]                   │
      │                                             │
      │  [7] Armar prompt al LLM:                   │
      │      system: _smartSystemPrompt()           │
      │      + memoria anterior                     │
      │      + {role:user, content:"¿Qué tienen?"} │
      │                                             │
      │  [8] Llamar LLM (OpenAI/Groq):              │
      │      POST https://api.openai.com/v1/...    │
      │      JSON schema → {action, reply, conf}   │
      │      ↓ Retorna:                             │
      │      {                                      │
      │        action: "menu",                      │
      │        reply: "Puedo ayudarte con...",      │
      │        confidence: 0.89                     │
      │      }                                      │
      │                                             │
      │  [9] Sanitizar reply:                       │
      │      _sanitizeReply() → quita fences,etc    │
      │      Max 1200 chars                         │
      │                                             │
      │  [10] Cache + Memoria:                      │
      │       aiCacheSet(key, result)               │
      │       POST /ai/memory (guardar usuario)     │
      │       POST /ai/memory (guardar assistant)   │
      │                                             │
      │  [11] Registrar tokens:                     │
      │       POST /ai/usage (con tokens_in/out)   │
      │                                             │
      │  Retorna:                                   │
      │  {                                          │
      │    action: "menu",                          │
      │    reply: "Puedo ayudarte con...",          │
      │    confidence: 0.89,                        │
      │    fromCache: false                         │
      │  }                                          │
      └────────┬─────────────────────────────────────┘
               │
               ▼
      ┌──────────────────────────────────────────────┐
      │  handleMainMenu() recibe resultado IA        │
      │                                             │
      │  confidence >= 0.55? → true (0.89)          │
      │  action === "menu"? → true                  │
      │                                             │
      │  switch(action) {                           │
      │    case "menu":                             │
      │      → handleMainMenu(jid, ses, "1")        │
      │      [Route a opción 1]                     │
      │  }                                          │
      └────────┬─────────────────────────────────────┘
               │
               ▼
      ┌──────────────────────────────────────────────┐
      │  handleMainMenu() - Opción 1                 │
      │  (mostrar catálogo)                         │
      │                                             │
      │  case "1": {                                │
      │    sendText(jid,                            │
      │      "La disponibilidad... está en:         │
      │       👉 https://tienda.com                 │
      │       Por WhatsApp no mostramos..."         │
      │    )                                        │
      │  }                                          │
      └────────┬─────────────────────────────────────┘
               │
               ▼
      ┌──────────────────────────────────────────────┐
      │  sendText(jid, mensaje)                     │
      │                                             │
      │  [1] Valida número (6-15 dígitos)           │
      │  [2] ¿Ventana 24h?                          │
      │      lastInboundAt[jid] existe? → true      │
      │      elapsed < 24h? → true                  │
      │      [PERMITE]                              │
      │  [3] ¿Rate limit outbound?                  │
      │      outboundAllowed(target, text)          │
      │      - Por destinatario: 45/hora             │
      │      - Global: 40/min                       │
      │      - Fingerprint: 8 destinatarios         │
      │      [PERMITE]                              │
      │  [4] Llamar Evolution API:                  │
      │      POST /message/sendText/oxidian         │
      │      {number: "34600123456", text: "..."}  │
      │  [5] Reintentos (max 3):                    │
      │      r.ok? → return true                    │
      │      4xx? → return false (no reintentar)    │
      │      5xx? → wait 500ms, retry               │
      │  [6] Registrar log                          │
      │      log("info", "send_ok", ...)            │
      └────────┬─────────────────────────────────────┘
               │
               ▼
      ┌──────────────────────────────────────────────┐
      │  Evolution API enruta WhatsApp               │
      │  ↓                                           │
      │  Servidor WhatsApp Business API              │
      │  ↓                                           │
      │  Cliente recibe mensaje en su teléfono      │
      │                                             │
      │  "La disponibilidad... está en:             │
      │   👉 https://tienda.com                     │
      │   Por WhatsApp no mostramos..."             │
      └──────────────────────────────────────────────┘
```

---

## 2. ARQUITECTURA MODULAR TEÓRICA (LO QUE DEBERÍA EXISTIR)

```
┌─────────────────────────────────────────────────────────────┐
│                  bot.js (COORDINADOR)                       │
│                                                             │
│  Webhook → _handleMessage()                                │
│     ├─ Detecta rol (admin/client)                          │
│     ├─ Detecta contexto (handoff, estado)                  │
│     └─ Delega a procesador correspondiente                 │
└────────────────┬────────────────────────────────────────────┘
                 │
    ┌────────────┼────────────┬──────────────┐
    │            │            │              │
    ▼            ▼            ▼              ▼
┌────────┐  ┌────────┐  ┌────────┐  ┌──────────┐
│ Admin  │  │Client  │  │Handoff │  │   IA     │
│Menu    │  │Bot     │  │Manager │  │Provider  │
│Handler │  │Conv.   │  │        │  │          │
└────────┘  └───┬────┘  └────────┘  └──────────┘
               │
       ┌───────┴───────┐
       │               │
       ▼               ▼
    ┌──────────┐   ┌──────────┐
    │Context   │   │Intent    │
    │Manager   │   │Parser    │
    │          │   │          │
    │• Historial   │• Detectar│
    │• Tema actual │• Extraer │
    │• Pregunta    │• Validar │
    └──────────┘   └──────────┘
       │
       └─→ BD SQLite (sesiones)
       └─→ API Oxidian (datos reales)
```

---

## 3. CAPAS DEL SISTEMA ACTUAL

```
┌──────────────────────────────────────────────┐
│  LAYER 1: BOT RÍGIDO (ACTIVO)                │
│  ────────────────────────────────────────────│
│  • Menú numerado [1-7]                       │
│  • FAQs canned (hardcodeados)                │
│  • Fuzzy match simple (7 opciones)           │
│  • Rate limiting, seguridad, logging         │
│  • Admin panel de texto                      │
│  • Handoff a humano (ROBUSTO)                │
├──────────────────────────────────────────────┤
│  LAYER 2: IA OPENAI/GROQ (PARCIAL)          │
│  ────────────────────────────────────────────│
│  • Solo fallback (si nada matchea)           │
│  • Cache LRU (30 min)                        │
│  • Memoria backend (4 turnos)                │
│  • Burst limit (5/min)                       │
│  • JSON schema parsing                       │
│  • Gasta tokens innecesariamente             │
├──────────────────────────────────────────────┤
│  LAYER 3: BOT CONVERSACIONAL (HUÉRFANO)     │
│  ────────────────────────────────────────────│
│  • Intenciones detalladas (20+)              │
│  • Contexto conversacional                   │
│  • Manejo de carrito                         │
│  • Captura de datos                          │
│  • NO CONECTADO A bot.js                     │
│  • Servicios mock sin implementar            │
└──────────────────────────────────────────────┘
```

---

## 4. FLUJO DE HANDOFF (LO QUE SÍ FUNCIONA)

```
Cliente escribe: "AGENTE"
        ↓
handleMainMenu() case '7' (opción 7: hablar con agente)
        ↓
requestHumanSupport(jid, initialText)
        ├─ createHandoffRequest(jid)
        │  └─ INSERT INTO handoffs (client_jid, admin_jid=NULL)
        │
        ├─ queueHandoffMessage(jid, 'client', text)
        │  └─ INSERT INTO handoff_messages (body, sender='client')
        │
        ├─ autoAssignPendingHandoff(jid)
        │  ├─ availableAdminJids()
        │  │  [Busca admins sin chat activo, activos en últimos 15min]
        │  ├─ assignHandoff(clientJid, adminJid)
        │  │  └─ UPDATE handoffs SET admin_jid=?, assigned_at=now()
        │  └─ deliverQueuedTranscript(clientJid, adminJid)
        │     └─ Envía mensajes pendientes al admin
        │
        └─ notifyAdminsHandoffQueued(jid)
           └─ Envía mensaje a TODOS los admins disponibles

Admin recibe: "📨 Cliente [número] necesita atención. Escribe *!take [numero]*"

Admin escribe: "!take 34600123456"
        ↓
handleAdminMenu() → takeHandoff(adminJid, clientJid)
        ├─ UPDATE handoffs SET admin_jid = adminJid, assigned_at = now()
        ├─ UPDATE sessions SET estado = 'admin_chat', active_client_jid = clientJid
        └─ sendText(adminJid, "Chateo iniciado. Escribe lo que quieras.")

Admin escribe: "Hola! ¿En qué puedo ayudarte?"
        ↓
_handleMessage() detecta admin_chat
        ↓
handleAdminChat(adminJid, ses, texto)
        ├─ forwardClientToAdmin(clientJid, adminJid, texto)
        │  ├─ queueAssignedHandoffMessage(clientJid, adminJid, 'admin', texto)
        │  └─ sendText(clientJid, texto)
        │
        └─ UPDATE sessions SET updated_at = now()

Cliente recibe: "Hola! ¿En qué puedo ayudarte?"
        ↓
Cliente escribe: "Quiero cambiar mi pedido"
        ↓
_handleMessage(clientJid) detecta handoff activo
        ├─ forwardClientToAdmin(adminJid, "Quiero cambiar mi pedido")
        └─ sendText(clientJid, "Tu mensaje fue recibido 👍")

Admin recibe: "Quiero cambiar mi pedido"
        ↓
[Conversación humana normal]

Admin escribe: "!soltar chat"
        ↓
releaseHumanChat(adminJid, clientJid)
        ├─ UPDATE handoffs SET admin_jid = NULL, assigned_at = NULL
        ├─ Reopen a queue para otro admin
        └─ sendText(clientJid, "Tu chat está en cola de nuevo")

O Admin escribe: "!cerrar chat"
        ↓
closeHumanChat(adminJid, clientJid)
        ├─ DELETE FROM handoffs
        ├─ DELETE FROM handoff_messages
        ├─ UPDATE sessions SET estado = 'idle', active_client_jid = NULL
        ├─ sendText(clientJid, "Chat finalizado. Vuelve al menú:")
        └─ sendText(adminJid, "Chat cerrado ✓")
```

---

## 5. MATRIZ DE RESPONSABILIDADES

| Módulo | Responsabilidad | Activo | Funciona |
|--------|---|---|---|
| bot.js (main) | Orquestación, routing | ✅ | ✅ |
| bot.js (aiSmartReply) | IA fallback | ✅ | ✅ |
| bot.js (handleMainMenu) | Menú cliente | ✅ | ✅ |
| bot.js (FAQs) | Respuestas canned | ✅ | ✅ |
| bot.js (handoff) | Transferencia a humano | ✅ | ✅ |
| bot.js (rate limit) | Anti-baneo, security | ✅ | ✅ |
| ClientBotConversacional | Procesamiento conversacional | ❌ | ⚠️ |
| ClientBotConversacional | Carrito conversacional | ❌ | ⚠️ |
| ClientBotConversacional | Captura datos | ❌ | ⚠️ |
| ConversationContext | Mantener estado | ❌ | ⚠️ |
| BotIntegracion | Integrador | ❌ | ❌ |

---

## 6. PUNTOS DE ROTURA ACTUALES

```
1. CARRITO CONVERSACIONAL
   └─ ClientConversacional.mostrar_carrito() → this.services.obtener_catalogo()
      └─ services.obtener_catalogo() NO EXISTE
      └─ Resultado: "❌ No pude cargar el catálogo"

2. DATOS CLIENTE
   └─ ClientConversacional.iniciar_checkout() espera que usuario diga nombre
      └─ Pero este flujo NUNCA SE INVOCA de bot.js
      └─ Resultado: No se capturan datos en chat (solo en handoff)

3. CONTEXTO
   └─ bot.js NO LLAMA a ConversationContext.agregar_mensaje()
      └─ Cada mensaje es aislado
      └─ Resultado: Usuario: "Dame dos" → Bot: "¿Qué dos?"

4. INTENCIÓN
   └─ detectClientIntent() solo matchea 7 opciones hardcodeadas
      └─ "Quiero dos cafés" no matchea nada
      └─ Resultado: Va a IA que cuesta tokens

5. ESCALABILIDAD
   └─ Rate limiting vive en memoria (Map local)
      └─ No sincroniza entre instancias
      └─ Resultado: Con 2+ instancias, se elude rate limit
```

---

## 7. FLUJO IDEAL PROPUESTO (FUTURO)

```
Usuario: "Dame dos cafés"
        ↓
_handleMessage() → getSesion() → cargar contexto
        ↓
[1] Saludo? NO
[2] FAQ? NO
[3] Fuzzy? NO
[4] IA PRIMERO (no fallback):
        ├─ Llamar IA
        ├─ Parse JSON
        ├─ action: "agregar_carrito"
        ├─ reply: "Perfecto, agregué 2 cafés a tu carrito"
        └─ confidence: 0.92
        ↓
[5] Si confidence >= 0.55 Y action en SMART_ACTIONS:
        ├─ action="agregar_carrito" → BotIntegracion.agregar_carrito()
        │  ├─ Valida cantidad (2)
        │  ├─ Busca producto "café" en BD
        │  ├─ Agrega a sesión.carrito
        │  └─ Retorna {ok, precio_total}
        │
        └─ Envía reply IA:
           "Perfecto, agregué 2 cafés a tu carrito 🛒
            Subtotal: $8.50
            ¿Quieres más o proceder al pago?"
           ↓
Usuario: "Una cerveza más"
        ↓
_handleMessage() + ConversationContext.tiene_pregunta_pendiente()? NO
        ↓
[Mismo flujo]
        ├─ IA detecta: action="agregar_carrito", cantidad=1, producto="cerveza"
        └─ Respuesta con contexto de carrito anterior

Usuario: "Pagar"
        ↓
IA detecta: action="checkout"
        ↓
BotIntegracion.solicitar_datos_cliente()
        ├─ ConversationContext.establecer_pregunta_pendiente("Nombre", "nombre")
        └─ "¿Cuál es tu nombre?"

Usuario: "Juan"
        ↓
_handleMessage() + ses.tiene_pregunta_pendiente()? SÍ
        ↓
BotIntegracion.procesar_respuesta_pendiente()
        ├─ contexto.datos_cliente.nombre = "Juan"
        ├─ contexto.establecer_pregunta_pendiente("Email", "email")
        └─ "¿Tu email?"

[Idem para email, teléfono, dirección]

Todos datos capturados
        ↓
BotIntegracion.generar_resumen_cliente()
        ├─ Muestra: Nombre, Email, Teléfono, Dirección
        └─ "¿Confirmas?"

Usuario: "Sí"
        ↓
IA o parser detecta: es_confirmacion("Sí") = true
        ↓
Llamar Oxidian POST /pedidos
        ├─ {carrito, cliente_datos, delivery_type, ...}
        └─ Retorna pedido_id, total, tiempo_entrega

Enviar:
"✅ Pedido confirmado! 🎉
 Número: #1234
 Total: $25.50
 Entrega: 30-45 minutos
 ¡Gracias por tu compra!"
```

---

**FIN DE DIAGRAMA TÉCNICO**
