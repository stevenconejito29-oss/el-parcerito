# Comprobación de apertura — 13 de septiembre de 2026

## Resultado

Validación local aprobada. **Publicación y recorrido real en producción pendientes**.
En una comprobación anterior no había conexión. En la última revisión del
13 de septiembre, la IP LAN respondió HTTP 200 y SSH permitió consultar el
estado: aplicación, PostgreSQL y Evolution aparecen saludables en Docker.
Esto no acredita el recorrido de compra ni la recepción real de WhatsApp.
El checkout del servidor corresponde a una revisión distinta; los cambios
locales de esta ronda no se han publicado.

## Modelo de cobro confirmado

Se paga al recibir a domicilio o recoger en el negocio. Efectivo, Bizum y
tarjeta con datáfono son opciones de cobro al entregar, no pagos anticipados.
La confirmación de WhatsApp del primer pedido verifica el teléfono.

Se corrigieron instrucciones del checkout que hablaban de repartidor también
para recogida. El seguimiento ahora indica expresamente que el Bizum se envía
cuando se recibe o recoge y que no se paga por adelantado. Chat y seguimiento
mantienen criterios coherentes de cancelación y recarga de estado bajo bloqueo.

Una regresión HTTP prueba las seis combinaciones de modalidad y pago: cocina
empieza la preparación, `pago_confirmado` sigue falso y no se crea un ingreso
en caja. Se conservan las defensas sobre un cobro que realmente figure
registrado; no se fabrican cobros ni se reclasifican datos históricos.

## Personalización y publicación

La vista previa de paleta, contraste, cambios pendientes y restauración pasó
la prueba en Chromium. El diagnóstico comercial ahora detecta recogida sin
dirección y Bizum sin teléfono; este último no cuenta como medio disponible.
El diagnóstico no sustituye probar la operativa real con el personal.

## Evidencias locales

- `scripts/test-project.sh`: **891 pruebas Python y 228 del bot aprobadas**.
- Nueve pruebas finales de cancelación y cobro al recibir aprobadas después
  de incorporar el caso de las seis combinaciones.
- Menú, carrito, chat y checkout: **28 escenarios** en 320/375/768/1280 px,
  horizontal, navegador y modo oscuro; sin desbordamientos ni errores JS.
- Renderizado de producto, club, información legal, seguimiento y vista de
  franjas con respuestas HTTP 200 en la aplicación aislada.
- `predeploy_check.py --env-file oxidian/.env.cosmos.local --deployment cosmos`:
  sin errores bloqueantes. Valida la configuración local, no el runtime remoto.
- `git diff --check` aprobado; bases temporales retiradas al finalizar.

## Pendiente en el servidor

Verificar conectividad HTTPS, `/health/live` y `/health/ready`, base de datos,
Redis y conexión de WhatsApp. Comprobar configuración real, horarios, productos
vendibles, stock, personal, zonas/franjas y dirección de recogida. Después,
realizar un recorrido controlado de compra, primera confirmación, preparación
y entrega/recogida, registrando el cobro únicamente al recibir.

No se desplegó, abrió la tienda, envió WhatsApp ni creó pedidos en producción.

## Continuación: canje, chats y pantallas de trabajo

- Reenvío de OTP sincronizado con `OTP_MIN_RESEND_SECONDS`; respuesta sin caché
  y mensaje neutral para números desconocidos. JSON malformado no provoca 500.
- Solicitud, verificación y selección de recompensa bloquean dobles pulsaciones.
  Se descartan respuestas al cambiar el teléfono. La compra espera a que termine
  la operación de canje; errores de conexión permiten reintentar y no borran la
  última selección confirmada. El canje tiene mensajes visibles y timeout.
- Cocina/preparación: controles agrupados en móvil y textos de tema/impresora
  con contraste en modo claro. Se mantiene el acceso a los dispositivos.
- Chat web: pruebas de historial, borrador concurrente, reintento con el mismo
  identificador y carrito. WhatsApp conserva las restricciones de canal previas.

Validación: 894 pruebas Python y 228 del bot aprobadas; 50 escenarios de roles
sin desbordamientos ni errores JS, incluyendo contraste de los controles de
cocina; 28 escenarios públicos de menú, carrito, chat y checkout aprobados.
Pruebas de interacción de canje y de operación con teclado aprobadas.
El predeploy de la configuración local no detecta bloqueos; es una simulación.
No se han enviado mensajes reales ni creado pedidos en producción.

Para repetir las comprobaciones nuevas desde `oxidian/` (entorno `.venv` y
Chromium instalado; definir `PLAYWRIGHT_CHROMIUM_EXECUTABLE`):

```bash
../.venv/bin/python scripts/review_customer_views.py
node scripts/test_checkout_rewards.mjs
../.venv/bin/python scripts/review_role_views.py
node scripts/review_role_views.mjs
```

Los renderizadores usan SQLite aislado y datos ficticios. Capturas e informes
quedan en `/tmp/parcerito-role-review` y `/tmp/parcerito-customer-pages`.
