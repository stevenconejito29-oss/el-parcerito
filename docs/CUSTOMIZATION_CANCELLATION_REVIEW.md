# Personalización y cancelaciones — 13 de septiembre de 2026

## Contexto y alcance

El sistema ya dispone de identidad, imágenes, colores de marca, paleta de
superficies/textos y rótulos configurables por secciones. Se conservan sus
permisos y validadores. Delivery y recogida siguen siendo módulos; WhatsApp
no adquiere funciones de compra ni cancelación del cliente.

## Personalización

- Muestra local de la paleta antes de guardar, con cabecera, tarjeta, texto,
  detalles de marca y botón. El texto del botón elige el mismo contraste claro
  u oscuro que usa la tienda.
- Indicación de combinaciones de bajo contraste para texto principal,
  secundario y cabecera. Es una ayuda sobre la muestra, no una certificación
  de accesibilidad de todas las pantallas.
- Aviso de colores modificados sin guardar y botón para restaurar los valores
  guardados de cada formulario. No guarda automáticamente otras secciones.
- JavaScript separado, sin librerías adicionales. Las entradas originales
  siguen funcionando si JavaScript no está disponible.

## Cancelación por el cliente

El chat usa los pedidos autorizados por la sesión del navegador y exige
confirmación explícita. Se corrigió la lectura bajo bloqueo para recargar el
estado real: si cocina ya empezó, no puede cancelar usando una instancia ORM
antigua que todavía decía pendiente.

La cancelación automática requiere estado `pendiente` y pago sin confirmar,
para domicilio y recogida. El pedido pagado se deriva a atención para revisar
la devolución, ahora también para tarjeta, de forma coherente con Bizum.

Pedido, liberaciones y mensaje de chat se guardan en una sola transacción.
Si falla la escritura del chat, se revierte la cancelación y se devuelve un
error recuperable, sin afirmar al cliente que quedó cancelado.

## Cocina y preparación

Se añade una acción desplegable de incidencias en la cola y en las tarjetas de
franjas. La cancelación necesita motivo de 5–300 caracteres y confirmación.
El servidor verifica rol, cola/asignación, estado pendiente/en preparación y
ausencia de pago confirmado. No permite cancelar pedidos listos, en ruta o
finalizados desde esta acción; tampoco pedidos de otro trabajador.

Usa `cancelar_pedido_operativo`, que centraliza liberación de stock y reservas,
puntos, cupones/comisiones y trazabilidad. El aviso push se solicita después
del commit. Repetir el POST de un pedido ya cancelado no repite los efectos.

## Validación

- Batería completa: **888 pruebas Python y 228 del bot aprobadas**.
- Ocho pruebas específicas finales de cancelación, incluidas las peticiones
  HTTP del cliente para domicilio y recogida, concurrencia simulada de estado,
  permisos de cocina, pagos confirmados, repetición y rollback.
- Chromium: prueba de vista previa, aviso de contraste, cambios sin guardar y
  restauración. Durante esta prueba se corrigió el momento de refresco después
  del evento nativo `reset`.
- `git diff --check` sin errores. Pruebas locales y envíos simulados; sin
  despliegue ni movimientos sobre datos reales.

Archivos de regresión: `oxidian/tests/test_cancellation_workflow.py` y
`oxidian/scripts/test_theme_preview.mjs`.
