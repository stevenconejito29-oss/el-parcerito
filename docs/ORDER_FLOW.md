# Flujo vigente de pedidos

Este documento describe el recorrido operativo que comparten web, WhatsApp y
POS. La máquina de estados vive en `oxidian/models.py`; las transiciones y la
distribución viven en `oxidian/services.py`.

## Recorrido por estado y rol

| Estado | Responsable principal | Vista | Acción siguiente |
|---|---|---|---|
| `pendiente` inmediato | `cocina` | `/preparador/pedidos` | Tomar e iniciar preparación. |
| `pendiente` programado | `preparacion` | `/preparador/pedidos` | Planificar por fecha; iniciar dentro de la ventana. |
| `armando` | Preparador asignado | `/preparador/pedidos` | Completar y marcar listo. |
| `listo` con delivery | `repartidor` | `/repartidor/ruta` | Tomar ruta y salir. |
| `listo` para recoger | `admin` | `/admin/pedidos` | Confirmar cobro y entrega local. |
| `en_ruta` | Repartidor asignado | `/repartidor/ruta` | Validar código, cobro y entrega. |
| `entregado` / `cancelado` | Administración | `/admin/pedidos` | Consulta, ticket y auditoría. |

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
