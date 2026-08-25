# Socios de productos y horario semanal

## Horario de tienda

La fuente de verdad es `SiteConfig.HORARIO_SEMANAL_JSON`. Usa claves `0..6`
(`0` lunes, `6` domingo) y admite varias franjas por día:

```json
{"0":[["09:00","14:00"],["18:00","22:00"]],"1":[]}
```

Una lista vacía significa día cerrado y una franja como `20:00–02:00` cruza
medianoche. `schedule_service.py` concentra validación, textos y cálculo de
apertura para web, checkout y chatbot. Si todavía no existe esta clave, se
construye automáticamente un horario compatible a partir de
`HORARIO_APERTURA` y `HORARIO_CIERRE`.

Los cierres y aperturas manuales mantienen esta prioridad:

1. `TIENDA_FORZAR_CERRADA`
2. `TIENDA_FORZAR_ABIERTA`
3. horario semanal

## Franjas de reparto

El horario semanal también es el límite operativo de las rutas. Al crear o
clonar una franja de reparto, el sistema solo acepta horas completamente
contenidas en la apertura de esa fecha (incluida la continuación de una
apertura nocturna). Así, el equipo configura primero cuándo abre el negocio y
después planifica, por ejemplo, sus cuatro salidas diarias con su capacidad de
pedidos; no pueden aparecer rutas que prometan entregas cuando la tienda está
cerrada.

Se permiten como máximo cuatro franjas activas por fecha. Cada franja acumula
los pedidos de los clientes que la eligieron y el equipo los reparte en bloque
al comenzar esa salida.

## Socios de productos

El rol autenticable es `socio_producto`; `proveedor` queda únicamente como
alias compatible con cuentas antiguas. La entidad se mantiene en la tabla
histórica `proveedores` para evitar una migración destructiva.

Un producto del socio cumple simultáneamente:

- `Product.proveedor_despachador_id` conserva, por compatibilidad, la identidad
  de su propietario económico.
- `ProveedorProducto` guarda stock independiente.
- `Product.precio_costo` queda vacío: no es coste ni inventario de la tienda.
- `Product.stock_mostrar_en_web` permanece activo para impedir sobreventa.
- el snapshot del `OrderItem` congela socio, acuerdo y comisión.
- preparación y delivery internos reciben la línea como cualquier producto de
  la tienda; el socio no prepara ni entrega.

Las franjas de `Proveedor.horario_semanal_json` se conservan para proveedores
externos históricos. Un acuerdo `socio_porcentaje` ignora ese horario y sigue
el horario general de la tienda, porque todo sale del mismo local.

Para el acuerdo `socio_porcentaje`, `Proveedor.comision_pct` es el porcentaje
que conserva la tienda. El socio recibe `100 - comision_pct`. Tanto pérdidas y
ganancias como liquidaciones reconocen la parte del socio como obligación, no
como margen propio.

Los pedidos entregados generan esa obligación. Un pedido marcado como
extraviado se conserva en la trazabilidad financiera, pero no genera saldo
liquidable al socio: el cliente es reembolsado y la venta no se materializó.

## Flujo administrativo

1. Super Admin crea el socio en **Catálogo → Socios de productos**.
2. Crea una cuenta con rol **Socio de productos** y la vincula al socio.
3. El socio registra una propuesta desde **Mis productos → Registrar producto**.
4. El propietario se toma exclusivamente de la cuenta autenticada; nunca de
   un identificador enviado por el formulario.
5. La propuesta y su inventario se crean juntos, inicialmente inactivos.
6. Super Admin aprueba la ficha o la devuelve con una observación concreta.
7. El socio ve exclusivamente sus pedidos, inventario, ventas e incidencias.
8. Super Admin revisa y registra pagos en **Finanzas → Liquidar socios**.

El socio también puede proponer combos desde **Crear combo**. El backend
acepta únicamente componentes simples, activos y cuyo
`proveedor_despachador_id` coincide con el socio autenticado. El combo no tiene
stock duplicado: su disponibilidad y consumo se calculan desde
`ProveedorProducto` de cada componente.

Una canasta puede mezclar producto propio y mercancía de socios de capital. La
operación logística sigue siendo una sola, pero cada `OrderItem` congela su
origen real para descontar el inventario correcto y liquidar cada porcentaje
sin cruces.

Cambiar el porcentaje afecta solo a pedidos futuros: los pedidos históricos
usan el acuerdo congelado en su snapshot.

### Estados de una propuesta

- `pending`: pendiente de revisión y no visible al cliente.
- `approved`: aprobada por Super Admin y publicable.
- `rejected`: requiere cambios; el socio puede corregir y reenviar.

Editar la ficha no equivale a aprobarla. La única transición que publica está
en `POST /admin/productos/<id>/revision-socio` y exige `super_admin`. Tanto el
alta como cada ajuste de inventario validan
`producto.proveedor_despachador_id == current_user.proveedor_id`; así, alterar
IDs en el navegador no permite consultar ni modificar mercancía de otro socio.
