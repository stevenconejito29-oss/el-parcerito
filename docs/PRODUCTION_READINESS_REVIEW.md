# Verificación de entrega — 15 de septiembre de 2026

## Estado

Candidato integrado y validado; publicación pendiente de comprobación posterior.
Se conciliaron los cambios locales con la historia del servidor. No se reemplazó
la configuración comercial ni se eliminaron columnas existentes.

## Flujos corregidos

- WhatsApp mantiene verificaciones del primer pedido, código de entrega y OTP de
  puntos. Consultas y seguimiento pertenecen al chat web/PWA; los menús internos
  requieren el teléfono de un perfil activo autorizado.
- El chat web muestra pedidos de la sesión y acceso limpio al ticket. Los tokens
  no aparecen en URLs, mensajes serializados ni respuestas al asistente. Un enlace
  copiado a otro navegador no autoriza el pedido. El ticket persiste al terminar.
- Recogida tiene preparación, listo para recoger y entrega en mostrador, con Maps.
  No utiliza dirección del cliente, franja, repartidor ni código de entrega.
  Cocina/admin confirman cobro y recogida con bloqueo transaccional; reintentos no
  duplican caja ni puntos. Se sigue pagando al recibir o recoger.
- Pedidos, conversaciones y suscripciones guardan una identidad privada de
  navegador. Los avisos del cliente se filtran por ese dispositivo. No se deduce
  autorización por teléfono. Un pedido antiguo solo se vincula con prueba de su
  sesión. Cerrar sesión no desactiva otros dispositivos identificados.
- El chat conserva su scroll al recibir mensajes/cargar historial. El compositor
  se adapta al teclado y a la navegación inferior en web y PWA.

## Datos reales y reparación ensayada

Backup del 15/09 restaurado en PostgreSQL aislado. Se encontraron 19 enlaces
huérfanos: 8 en combo_item_allowed_flavors y 11 en product_presentation_flavors.
La restauración inicial fallaba al crear cuatro claves foráneas por esos datos.

`oxidian/scripts/audit_data_integrity.py` es de solo lectura por defecto. Su opción
`--repair-catalog-links` archiva las filas originales en catalog_link_quarantine,
retira solo enlaces a padres inexistentes y restaura restricciones ausentes, en
una transacción con bloqueos y FKs activas. No elimina pedidos, productos ni
clientes, y no reinicia secuencias. En la copia: cero referencias huérfanas,
segunda ejecución sin cambios y backup completo restaurado sin errores.

La entrada antigua prep_produccion_datos.py ya rechaza escrituras: desactivaba
FKs y omitía tablas relacionadas. No se ha ejecutado esa limpieza.

No se detectaron duplicados de número de pedido, ingreso por pedido ni endpoint
push, ni pedidos/items sin padre. Los avisos activos de clientes no tenían
propietarios inexistentes/inactivos. Los mensajes históricos con parámetros de
acceso se ocultan también en las vistas del equipo.

Configuración observada: recogida activa; puntos y pedidos programados
inactivos. Cocina y reparto carecen de suscripciones push activas: el personal
necesita abrir su sesión y activar avisos en cada dispositivo.

## Validación

- 929 pruebas Python y 228 del bot aprobadas.
- Recogida: checkout real con campos de reparto falsificados, permisos, ticket,
  confirmación del primer pedido, cobro y reintento del cierre.
- Seis pruebas de aislamiento de dispositivos y suscripciones repetidas.
- Chat real: 12 escenarios Chromium/WebKit, web/PWA, 320/375/852 px; lectura,
  historial, teclado, enlaces y ausencia de desbordamiento horizontal.
- Recorridos de recogida y roles en Chromium/WebKit; carrito, canje y consulta OTP
  verificados con red simulada. Vistas de roles revisadas con copia de config real.
- Migración de tres columnas opcionales aplicada en copia; compatible con el
  código anterior. Ninguna prueba envía WhatsApp/push a clientes reales.

## Límites

Las pruebas de navegador no sustituyen la recepción push en teléfonos físicos
ni el emparejamiento de una impresora. En iPhone los avisos requieren la PWA
instalada; USB/BLE dependen del navegador y hardware. No se promete compatibilidad
universal de impresión. La salud posterior al despliegue se registrará aquí.
