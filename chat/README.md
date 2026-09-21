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
  → cliente: verificación o confirmación; otras consultas orientan a la app
  → empleado online: modo, permisos y estado operativo pendiente
  → llamada a la API Flask cuando consulta/modifica negocio
  → respuesta limitada, sanitizada y persistencia de sesión
```

En las herramientas operativas, los estados pendientes tienen prioridad sobre una intención genérica. Por eso
una respuesta `SI`, `NO`, un número o `0` debe resolverse dentro del formulario
actual antes de pasar al menú general. `MENU`/`0` son salidas explícitas y las
acciones destructivas requieren confirmación con caducidad.

## Atención humana

La atención se realiza en la app: el cliente abre `/ayuda` y el equipo usa
`/admin/chats`. Los comandos antiguos de WhatsApp orientan a esa bandeja; no
asignan nuevas conversaciones. Al salir de una sesión heredada se libera su
asignación y se conserva el historial local. Los helpers legacy siguen
presentes por compatibilidad, pero no definen el recorrido público vigente.

## Contrato del canal

WhatsApp solo se utiliza para verificaciones y confirmaciones de clientes,
además de las herramientas de admin y superadmin. No ofrece catálogo, preguntas
frecuentes, carrito ni atención humana conversacional. Las consultas reciben
una orientación a `/ayuda` de la PWA, limitada a una por diez minutos para
clientes. Esa espera no bloquea las confirmaciones explícitas.

Los mensajes salientes al cliente se limitan a `order_confirmation`
(primera compra sin identidad verificada), `delivery_code`, `points_otp` y
`canje_codigo`. Los códigos de canje se introducen en la web; el de entrega se
comparte con el repartidor según el flujo de entrega. Un saldo, campaña, reseña,
aviso de pago o seguimiento ordinario no abre una conversación de WhatsApp.
El transporte Flask también rechaza eventos antiguos fuera de este contrato.

`web_chat_handoff` es exclusivamente un aviso interno: exige un teléfono de
admin/superadmin con permiso de atención. Las rutas legacy `/api/bot/broadcast`
y `/api/bot/review-request` responden 410 sin enviar mensajes. Las dudas del
cliente se redirigen sin consultar su perfil, saldo, catálogo ni pedidos.

El teléfono normalizado del perfil activo sincronizado desde Flask determina
el menú y los permisos. Solo `admin` y `super_admin` son perfiles operativos
del bot. `OWNER_NUMBER`, `SUPERADMINS` y la antigua opción `BOT_STRICT_DB_ROLE=0`
no conceden acceso sin perfil. Las operaciones sensibles conservan además
la comprobación del actor en Flask y el PIN/confirmación cuando corresponde.

Los estados antiguos de compra del cliente se reinician sin modificar su
carrito. Un empleado en modo cliente puede volver con `/online`. La ayuda
pública añadida en la iteración anterior se retiró antes del despliegue, por
indicación expresa del usuario; no existe `/api/bot/ayuda`.

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
