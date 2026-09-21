# Flujo vigente de pedidos

Este documento describe el recorrido operativo que comparten web, WhatsApp y
POS. La máquina de estados vive en `oxidian/models.py`; las transiciones y la
distribución viven en `oxidian/services.py`.

## Recorrido por estado y rol

| Estado | Responsable principal | Vista | Acción siguiente |
|---|---|---|---|
| `pendiente` inmediato | cocina / preparación | `/preparador/pedidos` | Tomar e iniciar preparación. |
| `pendiente` programado | preparación | `/preparador/pedidos` | Planificar por fecha; iniciar dentro de la ventana. |
| `armando` | Preparador asignado | `/preparador/pedidos` | Completar y marcar listo. |
| `listo` con delivery | repartidor | `/repartidor/ruta` | Tomar ruta y salir (`listo` → `en_ruta`). |
| `listo` para recoger | preparador / mostrador | `/preparador/pedidos` | Confirmar cobro y entrega local (`completar_recogida`). |
| `en_ruta` | Repartidor asignado | `/repartidor/ruta` | Validar código, cobro y entrega (`en_ruta` → `entregado`). |
| `entregado` / `cancelado` | Administración | `/admin/pedidos` | Consulta, ticket y auditoría. |

### Divergencia recogida vs delivery

Ambos flujos comparten `pendiente → armando → listo`. A partir de ahí:

- **Delivery** (`tipo_entrega_cliente=delivery`): se asigna repartidor, pasa a
  `en_ruta` (genera código de entrega) y se cierra en la puerta con código + cobro.
- **Recogida** (`tipo_entrega_cliente=recogida`): no entra en reparto ni genera
  código. El cierre es atómico en mostrador (`services.completar_recogida`) con
  cobro explícito → `entregado` + puntos + caja.

La máquina de estados **no** permite avanzar una recogida `listo` a `entregado`
con `avanzar_estado`; hay que usar el handoff de mostrador.

`admin` y `super_admin` supervisan todos los estados y pueden resolver
asignaciones, pero las barreras del servidor siguen aplicando: confirmación del
primer pedido, responsable de preparación, proveedor pendiente, Bizum y código
de entrega no se omiten por ocultar o mostrar un botón.

## Reglas de distribución

- Los inmediatos priorizan cocina y pueden caer en preparación si está disponible.
- Los programados solo se asignan a preparación; nunca se esconden en cocina.
- El reparto prioriza la zona del pedido y después el pool global disponible.
- Ponerse online redistribuye trabajo pendiente con bloqueos de fila para que
  dos empleados no tomen el mismo pedido.
- La lista administrativa está paginada y las cargas del equipo se calculan en
  consultas agregadas, evitando una consulta por empleado dentro de cada tarjeta.
- El correlativo visible se reserva bajo un bloqueo transaccional de PostgreSQL,
  evitando números repetidos cuando entran pedidos concurrentes.

## Avisos al cliente

- El seguimiento de estados (`armando`, `listo`, `en_ruta`, `entregado`) va por
  **PWA / chat web** (`push_service.notify_order_state`).
- WhatsApp transaccional se reserva a confirmación del primer pedido, OTP de
  canje y código de entrega en puerta.
- En recogida, al marcar `listo` el cliente recibe «Ya puedes recoger…» con
  dirección del local. En delivery, al marcar `listo` se avisa al pool de
  repartidores; al salir a ruta, el cliente recibe «va en camino».

## Hitos y métricas operativas

Cada pedido conserva columnas UTC para `creado_en`, `preparado_en`,
`repartidor_asignado_en`, `repartidor_tomado_en`, `en_ruta_en` y
`entregado_en`. La asignación y la aceptación son hitos distintos: el primero
indica cuándo el sistema eligió responsable; el segundo, cuándo el repartidor
asumió el pedido. Los reintentos no sobrescriben una marca ya registrada.

`/admin/analytics` compara promedios, medianas, P90 y cobertura de cada etapa.
La cobertura evita interpretar un dato ausente como una duración de cero. El
CSV de tiempos usa los mismos campos, no contiene datos personales del cliente
y complementa el respaldo completo de PostgreSQL, que ya incluye estas
columnas y el historial `order_events`.

## Presentación responsive

Las vistas operativas separan planificación, trabajo activo y cierre. En móvil
vertical usan una columna; en móvil horizontal pueden usar dos columnas sin
ocultar acciones. Direcciones, notas y nombres deben permitir salto de línea, y
todos los controles operativos conservan un objetivo táctil mínimo de 44 px.

## Franja horaria de entrega (opcional)

Cuando el módulo `delivery_franjas_activo` está encendido, un pedido puede
tener `slot_id` apuntando a una `DeliverySlot` con hora de entrega
comprometida y cupo limitado. Los pedidos sin `slot_id` operan igual que
antes (delivery inmediato o recogida). El `slot_id` no forma parte del
snapshot congelado del pedido: es una relación operativa que puede cambiar
excepcionalmente (por ejemplo, si admin reasigna una franja llena a otra).

Detalle completo del módulo en
[`oxidian/docs/COBERTURA_REPARTO.md`](../oxidian/docs/COBERTURA_REPARTO.md).
