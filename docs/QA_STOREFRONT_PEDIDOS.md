# Revisión de storefront, PWA y primer pedido — 28/09/2026

Revisión local de los cambios de Claude. Se conservó el trabajo que ya estaba
sin commit. Las pruebas utilizan datos sintéticos, SQLite en memoria y
transporte simulado; no se publicaron cambios ni se enviaron mensajes reales.

## Problemas corregidos

- La regla `article.ep-card { display: block }` anulaba el layout de tarjetas:
  en móvil la imagen ocupaba el espacio y el nombre/precio quedaban recortados.
  Se recupera el componente responsive, incluida una columna a 280–350 px.
- Los botones «Armar combo», «Personalizar» y «Ver fecha» heredaban el ancho
  de 44 px del botón «+». Ahora conservan texto legible y espacio propio.
- Los pasos del combo repartían título, estado y flecha en una fila demasiado
  estrecha. El estado ocupa una segunda fila y las opciones se distribuyen
  según el ancho disponible del contenedor. Las imágenes respetan `contain`.
- Las tarjetas tienen una hoja de estilos final del componente, sin reglas
  móviles contradictorias: producto horizontal y combo apilado en pantallas
  pequeñas, etiquetas con ancho natural, títulos completos y acciones táctiles.
- El combo muestra todos sus componentes activos, cantidades, presentación y
  sabor fijo cuando corresponde, además de los límites de cada grupo elegible.
  Los componentes desactivados tampoco entran en nuevas selecciones ni en el
  cálculo del precio. Se conservan los snapshots de pedidos históricos.
- El precio fijo del combo coincide entre tarjeta, detalle y cálculo del carrito.
  Los combos calculados por porcentaje indican que el precio es orientativo.
  Los productos con presentaciones muestran «Desde» con el menor suplemento
  activo, incluidos suplementos negativos.
- El primer pedido abre WhatsApp con el texto exacto `si`; se explica que el
  cliente debe pulsar Enviar con el teléfono usado en la compra. La confirmación
  verifica identidad y no implica pago adelantado.
- La API aceptaba palabras dentro de preguntas o condiciones. Solo las
  respuestas completas explícitas pueden confirmar o cancelar. El bot informa
  de fallos y de respuestas repetidas, y devuelve al chat web sin ofrecer menús
  de seguimiento retirados de WhatsApp.
- El ticket permite consultar el chat antes de confirmar, actualiza estado y
  apariencia al volver de WhatsApp o recuperar conexión, y retira la invitación
  a activar avisos cuando termina el pedido.
- El chat reconoce también «tiket» y «tiquet», ofrece el botón real «Ver pedido
  y ticket» y mantiene el aislamiento por sesión y dispositivo. Los pedidos
  terminados no aparecen en la bandeja activa.
- Los controles de avisos reflejan activación, bloqueo y error incluso sin el
  banner global, conservan el icono del chat y orientan a instalar en iPhone.
- El acceso privado respeta autorizaciones de compra explícitas para usuarios
  activos de cualquier rol, como ya establece la sesión de tienda; no concede
  acceso sin `CustomerAccessGrant` y dispositivo autorizado.

## Validación

- Suites completas: **979 pruebas Python, una omitida; 234 del bot; sin fallos**.
- Catálogo, detalle de combo y chat: 280, 320, 375, 480, 768, 1024 y 1440 px,
  en navegador y PWA simulada. Se comprueba que nombre, precio y acción quedan
  dentro de la tarjeta y que no hay desbordamiento horizontal de página.
- Catálogo sintético ampliado: cuatro componentes incluidos, cantidades de 1 a
  4, selección entre 2 y 3 bebidas, componente desactivado, producto agotado y
  presentaciones con suplementos. Se contrasta un precio fijo de 17,50 con un
  precio genérico de 99 para detectar discrepancias. Comprobado con paletas
  clara y oscura, nombres largos y tamaño raíz de texto ampliado a 20 px.
- Recorrido real sobre servidor QA: armar combo → checkout de recogida → ticket
  pendiente → `si` por API del bot → actualización del ticket → solicitud de
  «tiket» en chat → reapertura del mismo ticket autorizado.
- Avisos con APIs simuladas: éxito, permiso denegado, error del servidor e
  instalación previa en iPhone.
- Regresiones de seguimiento, historial/reintento del chat, teclado/viewport,
  carrito y recogida. El fingerprint se comprueba para CSS, JS y plantillas.
- Se actualizaron fixtures desfasadas: app falsa sin `root_path`, motor de DB
  configurado después de inicializar SQLAlchemy y expectativas de texto antiguas.

## Repetir las comprobaciones

Desde la raíz, usando Node 20 (el módulo SQLite local estaba compilado para esa
versión; el Node 26 predeterminado no podía cargarlo):

```bash
DATABASE_URL=postgresql://test:test@127.0.0.1:15432/test \
OXIDIAN_PYTHON="$PWD/.venv/bin/python" PATH=/usr/bin:/bin \
bash scripts/test-project.sh
```

La URI anterior es una configuración sintética; estas pruebas usan sus propias
bases de datos de prueba. Para la revisión visual, arrancar en otra terminal:

```bash
.venv/bin/python oxidian/scripts/serve_flow_review.py
```

Después, desde `oxidian/`:

```bash
node scripts/test_storefront_review.mjs
node scripts/test_push_controls.mjs
node scripts/test_order_tracking_view.mjs
node scripts/test_customer_journeys.mjs
../.venv/bin/python scripts/review_customer_views.py
node scripts/test_chat_viewport.mjs
node scripts/test_pickup_journey.mjs
```

Para el primer pedido, arrancar un servidor QA nuevo en el puerto reservado:

```bash
REVIEW_PORT=5078 .venv/bin/python oxidian/scripts/serve_flow_review.py
# Desde oxidian/, en otra terminal:
node scripts/test_first_order_journey.mjs
```

Para comprobar información y tarjetas con el catálogo ampliado, arrancar un
servidor QA nuevo y ejecutar desde `oxidian/`:

```bash
# Desde la raíz:
REVIEW_PORT=5077 REVIEW_RICH_CATALOG=1 .venv/bin/python oxidian/scripts/serve_flow_review.py
# Desde oxidian/, en otra terminal:
REVIEW_BASE_URL=http://127.0.0.1:5077 node scripts/test_catalog_content.mjs
REVIEW_BASE_URL=http://127.0.0.1:5077 node scripts/test_storefront_review.mjs
```

Las capturas y logs de esta revisión se guardaron en `/tmp/parcerito-*`.
La simulación de PWA no sustituye una instalación física: falta comprobar
WhatsApp/Evolution y entrega de push en segundo plano en Android/iPhone reales.

## Segunda revisión: progreso, sabores, extras y vista rápida

Se reprodujo el problema señalado en `#pd-combo-guide`: la hoja compartida
forzaba pasos con texto a 24–32 px y la ficha limitaba su contenedor a 7,5 rem.
Se eliminó el tamaño fijo y se usa una cuadrícula que ocupa el ancho disponible,
con botones multilínea de al menos 44 px. La guía permanece en el flujo para no
cubrir opciones al desplazarse.

Las opciones de sabor admiten nombres largos en tarjetas con estado seleccionado
visible. Los extras conservan los controles de cantidad en una fila y permiten
que nombre y precio ocupen su propio espacio. En el menú, los productos con
sabores, extras o presentaciones usan una tarjeta apilada en móvil.

La imagen del producto o combo abre la vista rápida; el botón principal sigue
permitiendo ir directamente a personalizar. La modal distingue componentes
incluidos de grupos elegibles, conserva toda la lista, muestra sabores, extras,
suplementos y presentaciones, y tiene un único desplazamiento vertical. Los
precios de presentación parten del precio final, respetando descuentos.

Validación adicional: 107 pruebas de catálogo, frontend, combos, extras,
presentaciones y fingerprint. `test_customization_responsive.mjs` abre las
modales, pulsa pasos y selecciona sabores/extras a 280, 375, 852 (apaisado) y
1280 px, con texto de 20 px, en web y PWA simulada. Las capturas de la guía y
personalización se guardan en `/tmp/parcerito-*-responsive.png`.

Con el servidor ampliado activo en 5077:

```bash
# Desde oxidian/:
node scripts/test_customization_responsive.mjs
```

La prueba de vista rápida incluye el cambio de presentación (precio y valor
que enviará el formulario) y la legibilidad de los encabezados sobre una paleta
oscura en los contextos de PWA simulada.

## Regresión de la captura: combo ya en la canasta

El contador conservaba `bottom/left: 7px` de la hoja del menú y recibía
`top/right` de otra hoja. Los cuatro anclajes lo estiraban sobre la imagen.
La prueba con tres combos añadidos al carrito reprodujo un contador de
216 × 127 px a 280 px de viewport antes de corregirlo.

Se neutralizan los anclajes inferiores, se fija el alto en 32 px y se conserva
el contador en la esquina superior derecha. Se elimina la cinta duplicada
«En canasta», puesto que el estado ya aparece junto al precio. La acción vuelve
a decir «Armar combo»/«Personalizar», en una fila con ancho completo.

`test_catalog_cart_badges.mjs` añade tres unidades mediante el formulario real,
vuelve al menú y verifica contador, imagen, botón y distribución a 280, 320,
393, 430 y 768 px, en navegador y PWA simulada. Ejecutar desde `oxidian/`:

```bash
node scripts/test_catalog_cart_badges.mjs
REVIEW_BROWSER=webkit node scripts/test_catalog_cart_badges.mjs
```

WebKit local aproxima el motor de Safari; no sustituye probar una instalación
física de iOS. Las capturas usan datos de QA y ocultan las notificaciones
producidas por bloquear el service worker durante la simulación.

## Revisión posterior a Claude: carrito, privacidad y avisos desde la primera visita

Se revisó el commit `457eff1` antes de modificarlo. Se conserva la separación
por comas y el diseño compacto de las opciones simples. El selector de cantidad
ocupaba la columna de 20 px destinada al radio: sus botones se superponían al
nombre del producto e impedían pulsar «+» en móvil. Ahora tiene una fila propia.

- Botones de tarjetas con texto completo y columna explícita; las etiquetas de
  entrega siguen visibles incluso por debajo de 380 px.
- Carrito con nombres multilínea, etiquetas y sabores adaptables, controles de
  cantidad en una fila y espacio completo para los datos en móvil.
- Checkout deja de copiar claves `producto#firma` a las notas del pedido. Las
  notas por producto continúan en sus líneas. El ticket de pedidos anteriores
  oculta únicamente el sufijo automático con claves; no reescribe el histórico.
- Push se puede activar durante la primera visita pública. La suscripción se
  vincula a la identidad privada del dispositivo, sin crear usuarios ficticios,
  y al realizar checkout se asocia a ese cliente dentro de la transacción.
- Tiendas privadas conservan la verificación de acceso. Otro navegador no puede
  consultar, reemplazar ni borrar la suscripción; tampoco recibe sus pedidos.
  Se mantiene la migración de suscripciones antiguas de su propio usuario.
- La prueba de aviso propio admite dispositivos todavía sin pedido. El worker
  descarta trabajos si cambió el dispositivo, el propietario o su rol.

`test_first_visit_push_cart.mjs` verifica la activación inicial con API y CSRF
reales (solo Web Push simulado), menú y carrito a 280/320/375/393/430/768 px con
texto ampliado, checkout, conservación de la suscripción y rechazo del acceso
ajeno al ticket. Ejecutado en Chromium y WebKit. También se repite el recorrido
WhatsApp «si» → ticket actualizado → solicitud de ticket por chat.

```bash
# Servidor QA ampliado en 5077; desde oxidian/:
node scripts/test_first_visit_push_cart.mjs
REVIEW_BROWSER=webkit node scripts/test_first_visit_push_cart.mjs
REVIEW_BASE_URL=http://127.0.0.1:5077 node scripts/test_first_order_journey.mjs
```

No requiere migración de datos: `push_subscriptions.user_id` ya admite NULL.
Las pruebas de transporte no sustituyen la recepción física de push en iOS.

### Revisión de componentes y franjas — 29/09/2026

- Opciones de combo: las etiquetas auxiliares ocupan filas completas, los
  contadores quedan centrados y se adaptan a 280 px con texto ampliado. La
  cabecera comunica su altura real para no tapar controles al cambiar de paso.
- Ticket público: desglose del snapshot (cantidades por combo/unidad, tamaños,
  sabores y extras), nombres completos y ninguna serialización de IDs, costes o
  proveedores. Las nuevas compras distinguen la nota escrita por el cliente del
  resumen automático; los pedidos antiguos conservan sus notas.
- Checkout: selector de franjas adaptable, textos claros y reserva efectiva al
  enviar el pedido. El tiempo de zona se identifica como trayecto orientativo,
  adicional a preparación y espera de salida.
- Franjas existentes: reserva con capacidad/cierre, salida sólo dentro del
  horario activo y con preparación completa, límites por tanda y peso. No se
  han cambiado las modalidades de reparto ni su configuración en producción.
- Ruta existente: Google Routes cuando está configurado; alternativa local por
  cercanía con GPS. El mapa distingue ambas de un orden sin optimizar y aclara
  que las líneas dibujadas unen paradas, no describen las calles.
- Pendiente de evolución: persistir la secuencia de paradas por tanda y
  recalcular estimaciones al entregar/reordenar. Hoy la secuencia se organiza
  en el navegador del repartidor; no hay ETA individual fiable para prometer
  al cliente. El orden de entrega no equivale al orden de compra.
- Avisos previos al primer pedido: suscripción y vínculo posterior comprobados
  con API/CSRF reales y navegador simulado; la recepción con la aplicación
  cerrada necesita validación en un móvil físico.

Validación de esta revisión: 986 pruebas Python (1 omitida); recorrido completo
combo → recogida → confirmación `si` → ticket desde chat; revisión visual web/PWA
280, 375, 852 y 1280 px y carrito/checkout/ticket 280–768 px. En producción, el
endpoint VAPID valida las claves persistidas; Google Routes no está configurado
(se usa la alternativa por cercanía). Backup previo verificado:
`20260929-160246`, imagen de retorno `oxidian-release-rollback:3cf18ad`.
