# Chatbot de WhatsApp

Este proceso recibe webhooks de Evolution API y conversa por el mismo número de
la tienda. La lógica comercial permanece en Flask; el bot mantiene contexto,
presenta opciones y llama `oxidian/routes/api_bot.py`.

## Responsabilidades

| Archivo | Responsabilidad |
|---|---|
| `bot.js` | Entrada HTTP, seguridad, persistencia local, sesiones, handoff y router conversacional. |
| `texts.js` | Textos reutilizables, menús y ayudas de navegación. |
| `evolution.js` | Normalización de payloads de Evolution. |
| `handlers/clientConversacional.js` | Ayudas del diálogo determinista del cliente. |
| `utils/conversationContext.js` | Contexto corto y seguro de conversación. |
| `test/` | Regresiones de menús, pedidos, PIN, modos y atención humana. |

## Orden del flujo

```text
Webhook autenticado
  → deduplicación y límites
  → identificación del teléfono y rol
  → modo operativo online o modo cliente offline
  → chat humano activo, estado pendiente o intención nueva
  → llamada a la API Flask cuando consulta/modifica negocio
  → respuesta limitada, sanitizada y persistencia de sesión
```

Los estados pendientes tienen prioridad sobre una intención genérica. Por eso
una respuesta `SI`, `NO`, un número o `0` debe resolverse dentro del formulario
actual antes de pasar al menú general. `MENU`/`0` son salidas explícitas y las
acciones destructivas requieren confirmación con caducidad.

## Atención humana

- Un cliente sólo puede tener una asignación vigente.
- Un agente sólo puede mantener un chat activo a la vez.
- Tomar, soltar y cerrar conservan el historial necesario y verifican la
  asignación antes de entregar cada mensaje.
- Un admin en modo cliente no puede autoasignarse ni recibir su propia alerta.
- La identidad se compara por teléfono normalizado, no por nombre visible.

## Límites técnicos

- SQLite bajo `../db/` guarda estado runtime y nunca se versiona.
- Claves, URLs y números autorizados provienen del entorno o del sync de Flask.
- Stock, precios, estados, puntos, permisos y clientes no se deciden localmente.
- Las respuestas deterministas funcionan sin proveedor de IA. La asistencia IA
  opcional está limitada por configuración y no debe ejecutar acciones de negocio.

## Validación

```bash
node --check bot.js
npm test
```

Al cambiar un diálogo, actualizar `texts.js` cuando el texto sea compartido y
añadir una regresión al archivo de prueba del flujo correspondiente.
