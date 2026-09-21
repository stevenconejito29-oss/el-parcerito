# Revisión funcional y del flujo de datos — 12 de septiembre de 2026

## Alcance y recorrido

Se revisaron servicios de negocio, configuración comercial, cola de avisos y
su relación con los pedidos. La batería general cubre creación de pedidos,
precios, snapshots, caja y comisiones, permisos, stock y reservas, preparación,
recogida, reparto inmediato/por franjas, chat web y contrato de WhatsApp.
Estas pruebas usan datos aislados; no verifican operaciones reales del servidor.

El recorrido conservado es: configuración → cálculo de compra → pedido y
snapshots → confirmación → preparación → recogida/reparto → entrega y registro
financiero. Las notificaciones se encolan dentro de la transacción y el worker
las envía después del commit.

## Fallos reproducidos y correcciones

| Problema | Corrección |
|---|---|
| Un aviso antiguo podía pedir confirmar un pedido ya confirmado o cancelado. | El worker vuelve a comprobar estado y confirmación antes de cada envío/reintento. |
| Un código de entrega podía llegar después de entregar o cancelar el pedido. | Solo se envía si el pedido sigue en ruta, necesita reparto y el código coincide con el actual. |
| Un OTP podía reintentarse después de caducar, consumirse o ser sustituido. | La cola conserva la fecha de expiración de esa emisión y la contrasta con el cliente actual. |
| Un cambio de teléfono dejaba el aviso dirigido al destinatario anterior. | Se compara el teléfono normalizado actual antes de enviar confirmaciones y códigos. |
| Dos solicitudes iguales podían crear avisos pendientes duplicados. | Se bloquea el pedido y se reutiliza una notificación pendiente/en proceso con el mismo contenido y destinatario. |
| Los avisos fallidos sin fecha de envío no se eliminaban con la retención. | La purga usa su fecha de creación cuando no existe fecha de envío; conserva los pendientes. |
| El mensaje de OTP anunciaba siempre diez minutos aunque el plazo estuviera configurado de otra forma. | Texto y cálculo del intervalo respetan el plazo acotado entre 1 y 60 minutos que usa el modelo. |
| La edición de comisión/descuento aceptaba `NaN`. | Validación de rango que también rechaza valores no finitos. La lectura de comisiones legacy inválidas aplica el mismo cero defensivo que los valores malformados. |

Los descartes quedan registrados como terminales (`failed`) con motivo
`discarded:...`, sin contabilizarlos como enviados ni repetir intentos inútiles.
Los avisos legacy de códigos que no tienen la metadata necesaria se descartan
de forma conservadora: corresponde solicitar un código vigente desde su flujo.

## Validación

- Batería completa: **880 pruebas Python y 228 del bot aprobadas**.
- Ocho regresiones nuevas del worker, retención y duplicados incluidas en esa
  batería: `oxidian/tests/test_outbox_validity.py`.
- Tras el último ajuste de comisión, ocho pruebas de configuración aprobadas,
  incluida una nueva regresión de valores no finitos.
- Prueba adicional real en PostgreSQL: dos conexiones simultáneas intentaron
  encolar el mismo aviso; ambas obtuvieron el mismo registro y quedó una fila.
- Compilación/sintaxis y `git diff --check` sin errores.

## Límites y decisiones comerciales pendientes

La deduplicación comprobada afecta a solicitudes idénticas mientras el aviso
sigue pendiente o en proceso. No garantiza entrega externa exactamente una vez
ante una caída después de que WhatsApp acepte el mensaje y antes de guardar el
resultado. No se han añadido campañas ni nuevos propósitos de WhatsApp.

La cuota fija mensual frente a importe fijo por pedido y la posible excepción
de WhatsApp para recogida siguen pendientes de definición en la revisión
comercial anterior. No se han inventado contratos ni registrado cobros.

Cambios locales, sin despliegue, mensajes reales ni modificaciones de datos de
producción. Las bases temporales se retiran después de validar.
