# Administración, WhatsApp y reparto — 7 de septiembre de 2026

Cambios locales siguiendo la aclaración del alcance de WhatsApp y la petición
de ordenar superadmin, CRUD, reparto y finanzas. No se ha desplegado.

## WhatsApp y perfiles

- Cliente: solo verificaciones y confirmaciones. Las consultas se orientan al
  chat web, con límite de frecuencia que no bloquea la confirmación explícita.
- Admin y superadmin: el teléfono normalizado del perfil activo determina el
  menú y las capacidades. Las listas del entorno y `BOT_STRICT_DB_ROLE=0` ya
  no conceden acceso sin perfil; otros roles no se convierten en admin.
- Se retiró la ayuda pública añadida localmente en la iteración anterior,
  incluidos su endpoint y manejador nuevos. Los estados antiguos no reactivan
  compras, cancelaciones ni atención humana por WhatsApp.
- Se mantienen comprobación del actor en Flask, PIN y confirmaciones de las
  operaciones sensibles. El modo cliente del empleado explica el límite del
  canal y permite volver con `/online`.
- Este contrato queda también en `AGENTS.md` y `chat/README.md`.

## Separación de modalidades de reparto

La configuración ya distinguía inmediato, franjas y mixto. La revisión
encontró fallos en las barreras y en la pantalla de gestión:

| Caso | Corrección |
|---|---|
| Abrir el panel de franjas | Consultaba `ZonaEntrega.activa`, que no existe. Ahora usa `activo` y carga las tarifas solo de zonas activas. |
| Cambiar a solo franjas | Antes podía dejar pedidos inmediatos activos sin su flujo de trabajo. El cambio se rechaza hasta resolverlos. |
| Tomar pedidos inmediatos en modo mixto | Las rutas individual y múltiple rechazan pedidos con `slot_id`; estos deben pasar por su salida programada. |
| Planificar con franjas apagadas | Se conserva la planificación sin obligar a habilitar el módulo. |

`delivery_mode_service.py` sigue siendo la política compartida. No se cambia
la modalidad de pedidos existentes. La misma protección que ya tenían las
franjas se aplica ahora al reparto inmediato. Los pedidos de recogida no
bloquean un cambio de modalidad de delivery.

## Organización del superadmin

El inicio prioriza bloqueos y avisos, métricas y seis categorías de gestión:

1. Operación y reparto: pedidos, chat web, modalidades, franjas y zonas.
2. Catálogo e inventario: productos, categorías y stock.
3. Personas y accesos: administradores, permisos, equipo y clientes.
4. Finanzas: resultado, caja, cierres, pagos y liquidaciones.
5. Tienda y canales: configuración, contenido de la PWA y automatizaciones.
6. Datos y mantenimiento: auditoría, respaldos y restauración.

Los accesos abren los CRUD existentes, con sus permisos y validaciones.
No se añade un editor genérico de tablas que eluda las reglas de negocio.
Comprobaciones correctas, módulos y estado técnico quedan desplegables.
El menú lateral de admin/superadmin agrupa categorías y abre la de la página
actual. Solo marca un enlace activo, el más específico; sin JavaScript todas
las categorías siguen accesibles.

## Finanzas

El informe conserva `services.calcular_pl` como fuente de cálculo. La
interfaz distingue ventas entregadas, rentabilidad, caja y obligaciones
pendientes. Los detalles de productos quedan plegados y las alertas de costes
incompletos permanecen visibles. Se añaden accesos a movimientos, cierres,
cobros pendientes y pagos al equipo respetando los permisos existentes.

El período inicial del superadmin usa la fecha civil del negocio, igual que
los límites del cálculo. El estilo compartido sale de la plantilla a
`static/css/financial-control.css`. No hay migración ni recálculo de datos
históricos, ni cambios en importes, impuestos o liquidaciones.

## Comprobaciones y límites

- `scripts/test-project.sh`: 833 pruebas Python y 226 del bot aprobadas,
  además de compilación Python y sintaxis del bot. PostgreSQL temporal aislado.
- Pruebas de cambio de teléfono/rol, entorno sin perfil y modo cliente.
- Pruebas HTTP de asignación inmediata frente a franjas; protección de ambos
  tipos de pedido al cambiar de modalidad.
- Prueba del panel de reparto con franjas desactivadas y tarifas de zonas
  activas; prueba del período financiero según fecha del negocio.
- Plantillas reales con datos ficticios: inicio superadmin, finanzas, reparto
  y administradores a 320 px, 375 px y 1280 px, más móvil en oscuro. Revisión de
  navegación activa y desplegables; las 16 capturas no presentan errores
  JavaScript ni desbordamiento horizontal.
- Capturas e informe de esta ejecución: `/tmp/parcerito-admin-pages`.

Falta validar la conexión real con Evolution/WhatsApp y realizar recorridos
con empleados en dispositivos físicos. Esta iteración organiza los accesos
a CRUD y preserva sus operaciones; no constituye un rediseño de cada formulario
del sistema. El siguiente paso de producto es revisar los formularios de mayor
uso con datos representativos y medir acciones por tarea.
