# Flujo operativo del reparto por franjas

Aplica cuando `SiteConfig.delivery_franjas_activo = 1` (mutex enforced con
delivery inmediato). Tres actores: cliente, cocina y repartidor.

## Cliente
1. En checkout elige franja (calendar picker mobile) — obligatorio si el
   mutex está activo.
2. Confirma el pedido → queda en `estado=pendiente` con `slot_id` asignado.
3. Recibe push "tu franja empezó" cuando la ventana arranca
   (idempotente vía `DeliverySlot.notif_inicio_at`).

## Cocina — vista franja-céntrica (`/preparador/franjas/hoy`)
1. Ve las franjas de HOY en orden cronológico. Cada tarjeta:
   - Cabecera con `hora_inicio → hora_fin`, countdown vivo en minutos.
   - Contadores: total, en preparación, listos, pendientes.
   - Borde rojo pulsante cuando faltan ≤ 15 min → señal de "empacar ya".
2. Por cada pedido de la franja, acción según estado:
   - `pendiente` → `POST /preparador/pedidos/<id>/empezar` → `armando`.
   - `armando` → `POST /preparador/pedidos/<id>/listo` → `listo`.
   - `listo` → chip verde "✓ Listo" (sin acción).
3. La vista general `/preparador/pedidos` mantiene un banner que
   linkea aquí cuando el módulo está encendido, además del bloque
   `df-kitchen-panel` que resume franjas dentro de la cola normal.

Nota: no se han duplicado endpoints. Se reutilizan `empezar_armar` y
`marcar_listo` que ya existen en `routes/preparador.py`.

## Repartidor — panel de franjas (`/repartidor/franjas/panel`)
1. Ve las franjas de la semana con estado (libre / mía / llena).
2. `POST /repartidor/franjas/<id>/tomar` reserva la franja.
3. `POST /repartidor/franjas/<id>/liberar` la libera si ya no quiere.
4. Botón "Iniciar ruta (N)" en el panel: batch dispatch de TODOS los
   listos → los pasa a `en_ruta` y lleva a `/repartidor/ruta`.

## Repartidor — panel de una franja (`/repartidor/franjas/<id>/pedidos-panel`)
Nuevo. Complementa el JSON `/repartidor/franjas/<id>/pedidos`. Permite:
1. Ver los pedidos de la franja con estado (listo / en_ruta / entregado…).
2. Multi-select granular con checkboxes (solo `listo` es seleccionable).
3. Contador vivo "X seleccionados de Y listos" + progress bar
   `despachados / total`.
4. Botón "Salir con seleccionados" → `POST /repartidor/franjas/<id>/iniciar-reparto`
   con `pedido_ids` (subset). El endpoint acepta lista parcial y sólo
   despacha los propios/no-asignados.
5. El rider vuelve por el resto: repite hasta terminar la franja, luego
   libera.

## Estados y transiciones
```
pendiente ──empezar──> armando ──listo──> listo ──iniciar-reparto──> en_ruta ──entregar──> entregado
```

Nada se inventa: la máquina de estados existente
(`avanzar_estado_pedido`) es la fuente única.

## Notificaciones (canal_service anti-baneo Meta)
- `en_ruta`: `notificar_en_camino` (push preferente, WhatsApp sólo si el
  cliente lo pidió). Idempotente vía `en_camino_at`.
- `en_la_puerta`: único mensaje WhatsApp del flujo (opt-in por rider).

## Archivos relevantes
- Backend: `routes/preparador.py::franjas_hoy`, `routes/repartidor.py::franjas_slot_panel`,
  y los batch dispatch endpoints ya existentes.
- Templates: `templates/preparador/franjas_hoy.html`,
  `templates/repartidor/franja_pedidos_panel.html`,
  `templates/repartidor/franjas.html` (link "Ver pedidos"),
  `templates/preparador/pedidos.html` (banner).
