# Experiencia del cliente — 7 de septiembre de 2026

Mejoras aplicadas localmente sobre la PWA y WhatsApp.
Actualizado con la corrección de alcance del canal del 7 de septiembre. Complementan la
[revisión de delivery y roles](DELIVERY_UX_REVIEW.md). No se ha desplegado.

## Recorrido y cambios

| Momento | Problema encontrado | Comportamiento aplicado |
|---|---|---|
| Explorar | Búsqueda estrecha en móvil, acción del hero con poco contraste y navegación poco explícita. | Búsqueda apilada, contraste del botón corregido, página y categoría activas accesibles. Buscar conserva la categoría elegida. |
| Ajustar carrito | El guardado automático interrumpía la edición; continuar podía usar cantidades anteriores. | Edición sin envío por temporizador, aviso de cambios y guardado al continuar. Cantidades inválidas conservan el producto y piden revisión. |
| Conversar | Respuestas antiguas en chats largos, saltos de scroll y reintentos poco claros. | Historial paginado, mensajes ordenados sin duplicados, reintento con el mismo identificador y conservación del siguiente borrador mientras se envía. |
| Pedir ayuda | Preguntas frecuentes y conversación competían por espacio. | Ayuda plegable, controles de historial y mensajes nuevos, compositor visible y estado de atención explicado. |
| Consultar por WhatsApp | Redirección genérica que impedía resolver dudas sencillas y estados heredados de compra/atención. | Solo verificaciones y confirmaciones, más menús administrativos por teléfono de perfil. Las consultas se orientan al chat web. |

## Estructura y límites del canal

- `web_chat.py` centraliza mensajes y metadatos de pedidos en todas las
  respuestas. Los cursores consultan únicamente la conversación del visitante.
  La interfaz pausa el polling cuando la pestaña no está visible y espacia
  reintentos tras fallos de red.
- `storefront-cart.js` concentra la interacción antes incrustada en la
  plantilla. El servidor valida cantidades, disponibilidad y el destino del
  formulario; un carrito que requiere revisión no avanza al checkout.
- WhatsApp se limita a verificaciones, confirmaciones y menús de admin y
  superadmin. Se retiraron el manejador de ayuda pública y `/api/bot/ayuda`
  añadidos localmente en la iteración anterior, antes de desplegar.
- Las consultas se orientan al chat web. La confirmación explícita del primer
  pedido conserva su prioridad y no queda bloqueada por el límite de avisos.
- Solo el teléfono de un perfil activo admin/superadmin concede acceso a las
  herramientas operativas; ni el entorno ni una opción legacy elevan el rol.
- Los comandos de atención heredados dirigen al panel web. Se libera la
  asignación anterior al migrar una sesión, sin borrar el historial. Un número
  sin perfil verificado no recibe capacidades administrativas por estar en
  una variable de entorno.

Se conservan rutas, modelos y funciones legacy por compatibilidad. Su presencia
no significa que el router público vuelva a activar esos diálogos.

## Validación de la iteración inicial

Los resultados posteriores a la corrección del canal están en la
[revisión de administración y reparto](ADMIN_STRUCTURE_REVIEW.md).

- `scripts/test-project.sh`: compilación Python, 829 pruebas Python, sintaxis
  del bot y 229 pruebas Node aprobadas. PostgreSQL temporal aislado.
- Se actualizaron pruebas que todavía esperaban compra y atención por el
  router antiguo de WhatsApp. Se mantienen pruebas de helpers heredados y se
  comprueba que las consultas genéricas no activan operaciones de pedidos.
- `oxidian/scripts/test_customer_journeys.mjs`: fallo de envío, reintento sin
  duplicar, siguiente borrador, lectura sin salto de scroll, historial y
  guardado de cantidades antes del checkout.
- Doce capturas de plantillas reales con datos ficticios: menú, carrito, chat
  y checkout en 320 × 740, 375 × 812 y 1280 × 900. Sin desbordamiento horizontal
  ni errores JavaScript. Informe local: `/tmp/parcerito-customer-pages/report.json`.

Para repetir las interacciones desde `oxidian/`:

```bash
PLAYWRIGHT_CHROMIUM_EXECUTABLE=/ruta/a/chromium node scripts/test_customer_journeys.mjs
```

La revisión de navegador usa red simulada y Chromium. No certifica recepción
en Evolution/WhatsApp real, teclado de iOS, instalación en dispositivo físico
ni tiempos de respuesta de producción.

## Próximas validaciones de producto

1. Recorrer una compra real de prueba, su confirmación por WhatsApp y la
   atención en la bandeja web con la configuración del entorno de destino.
2. Probar teclado, instalación, reconexión y notificaciones en Android/iOS.
3. Revisar las preguntas sin respuesta del chat web y completar su información
   desde la configuración de tienda.
4. Medir con clientes y empleados las acciones necesarias para comprar,
   resolver una duda y cerrar una entrega, antes de afirmar mejoras de tiempo.
