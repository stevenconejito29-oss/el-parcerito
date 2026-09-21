# Validación de reparto para apertura — 8 de septiembre de 2026

## Modalidades y responsabilidades

| Modalidad | Superadmin | Cocina | Repartidor |
|---|---|---|---|
| Inmediato | Activa inmediato y configura zonas/tarifas. | Recibe la cola inmediata, prepara y marca listo. | Toma el pedido disponible, sale, verifica código y registra cobro. |
| Franjas | Crea planificación/cupos y habilita franjas. | Trabaja por salida con los detalles originales del pedido. | Toma la franja y selecciona una tanda durante su ventana, cuando la preparación está completa. |
| Mixto | Habilita ambas opciones. | Conserva la separación entre cola inmediata y salidas. | Cada pedido sigue el recorrido correspondiente a su `slot_id`. |

Checkout comparte `resolver_plan_delivery` con las reglas de modalidad. La
reserva de franja utiliza `reservar_franja`, con bloqueo de fila y control de
cierre/cupo. El cambio de modalidad no convierte los pedidos existentes y no
permite apagar un flujo con pedidos activos.

## Correcciones de esta revisión

- Las rutas de salida inmediata individual y múltiple rechazan pedidos de
  franja. Completa la protección previa de las rutas de asignación e impide
  saltarse ventana, reserva de rider y selección de tanda.
- El diagnóstico de apertura cuenta franjas **abiertas con cupo**, aplicando
  la misma política que la lista de checkout. Una franja iniciada o llena ya
  no basta para indicar disponibilidad. En solo franjas bloquea el diagnóstico;
  en mixto se muestra aviso porque inmediato sigue habilitado.
- El diagnóstico consulta sin generar planificación recurrente ni confirmar
  transacciones. La lista pública conserva su materialización habitual.
- Administración, cocina y reparto comparten el contador `sin_confirmar`.
  Una verificación pendiente se distingue de trabajo que cocina está
  preparando. No se permite preparar ni despachar ese pedido por ocultar el
  bloqueo en otra pantalla.

## Recorridos comprobados

Pruebas HTTP de inmediato, solo franjas y las dos alternativas del modo mixto:

1. El superadmin configura el modo por su ruta autorizada.
2. Un pedido aceptado utiliza el selector compartido y, si corresponde, reserva
   un cupo con el servicio utilizado por checkout.
3. La confirmación pendiente bloquea el inicio en cocina. En la prueba se
   establece después el estado confirmado; no se contacta con WhatsApp real.
4. Cocina asume y prepara el pedido; la franja no recibe asignación inmediata.
5. El rider toma el recorrido correcto. La franja rechaza salir antes de su
   ventana y permite despachar dentro de ella.
6. El cierre rechaza falta de cobro y código incorrecto. Con ambos correctos
   entrega y registra el pago; repetir el cierre no duplica el ingreso.

Estos recorridos comienzan en un pedido aceptado y no sustituyen una compra
real completa desde un teléfono. Las suites de checkout, reservas, pedidos,
finanzas y WhatsApp se ejecutan además con el resto del proyecto.

## Estado previo a apertura

La suite completa terminó con **846 pruebas Python y 226 del bot aprobadas**.
Las doce revisiones visuales de administración, cocina y reparto (móvil,
escritorio y oscuro) verificaron los avisos compartidos, sin desbordamiento ni
errores JavaScript. Se corrigió también el contraste del encabezado y las
acciones de las franjas. Capturas: `/tmp/parcerito-delivery-pages`.

La comprobación estática `predeploy_check.py --env-file
oxidian/.env.cosmos.local --deployment cosmos` terminó sin bloqueos. Valida
la configuración local y los artefactos, no la conectividad del servidor.
No había contenedores ejecutándose en el Docker local al revisar el entorno.

**No equivale a tener la tienda publicada y recibiendo pedidos.** Antes de
abrir ventas en la instancia de destino es necesario:

- Publicar esta versión siguiendo `OPERATIONS.md` y comprobar salud real.
- Revisar el diagnóstico de superadmin con el catálogo, zonas, pagos y
  equipo reales; para franjas, disponer de salidas abiertas con cupo.
- Comprobar que los empleados acceden con sus perfiles y están disponibles.
- Realizar una compra de prueba en cada modalidad habilitada, incluida la
  confirmación real por WhatsApp y el cierre de entrega/cobro.

Las pruebas utilizan usuarios, pedidos y bases temporales. No se activaron
ventas ni se alteraron datos de producción.
