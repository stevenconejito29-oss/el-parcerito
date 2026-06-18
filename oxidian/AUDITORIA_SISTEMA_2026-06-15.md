# Auditoria funcional de Oxidian

Fecha: 15 de junio de 2026

## Arquitectura real

- Oxidian: Flask + PostgreSQL. Catalogo, carrito, pedidos, roles, caja, puntos y paneles.
- Chatbot: Node.js + SQLite. Conversacion, handoff humano, cola de administradores y cache.
- Evolution API: transporte de WhatsApp, con PostgreSQL y Redis propios.
- Redis Oxidian: rate limiting y estado efimero.
- Outbox PostgreSQL: entrega asincrona de WhatsApp y Web Push despues del commit.
- Gateway Nginx: unico acceso publico. La API interna `/api/bot/*` queda bloqueada.

## Flujo de pedido

```text
pendiente -> armando -> listo -> en_ruta -> entregado
     \           \         \          \
      ----------------------------> cancelado
```

- `pendiente`: pago Bizum confirmado cuando aplica y responsable asignado.
- `armando`: cocina, preparacion o almacen trabajan segun snapshots de cada linea.
- `listo`: todos los proveedores y el almacen de un pedido mixto deben confirmar.
- `en_ruta`: repartidor asignado y codigo de entrega generado.
- `entregado`: codigo validado, caja, puntos y comision se registran de forma idempotente.
- `cancelado`: restaura stock y puntos; la devolucion de caja no puede duplicarse.

## Roles

- `super_admin`: configuracion global, administradores e integraciones.
- `admin`: operacion segun features habilitados.
- `cocina`: productos inmediatos de cocina.
- `preparacion`: pedidos programados o por encargo.
- `staff`: empaque y productos de almacen.
- `proveedor`: confirma su parte de cocina de forma independiente.
- `repartidor`: toma pedidos listos, sale a ruta y confirma entrega.
- `cliente`: compra, consulta pedidos, puntos y resenas.

## Correcciones aplicadas

- Barreras de estado centralizadas para admin, preparacion, proveedores y almacen.
- Reasignaciones limitadas por estado, con bloqueo de fila y evento de auditoria.
- Redistribucion concurrente con `FOR UPDATE SKIP LOCKED` y savepoints.
- Logout apaga la disponibilidad operativa y ahora exige POST + CSRF.
- OTP y saldo de puntos se confirman junto con su job de WhatsApp.
- Canje de puntos bloquea la fila del cliente para evitar doble gasto concurrente.
- Pricing deja de aceptar silenciosamente cupones o afiliados invalidos.
- Limite de usos de afiliado atomico y uso unico por pedido.
- Comisiones de afiliado y delivery separadas; pago enlazado para evitar doble salida.
- Devolucion unica por pedido y cancelaciones POS/admin con bloqueo.
- Push protegido por CSRF y dirigido segun el rol actual, no un snapshot antiguo.
- XSS almacenado eliminado del modal publico de combos y alergenos.
- API interna del bot bloqueada en Nginx y secretos retirados del simulador.
- Bot inbound con reintentos, dead letter y persistencia completa de lotes grandes.
- Clave de panel separada de la clave operativa; `/api/status` queda autenticado.
- Longitud de campañas y bot alineada a 4096 caracteres.
- Secretos en cambios de configuracion ya no se copian al detalle de auditoria.
- Contrasenas de administradores elevadas a un minimo de 12 caracteres.
- Service worker solo elimina caches propios de Oxidian.
- El formulario rapido de productos conserva canal de preparacion y proveedor al editar.
- Cancelar devuelve los puntos canjeados como `devuelto`, sin colisionar con puntos ganados.
- Reversion de usos de cupon y afiliado atomica; comisiones afiliadas quedan desvinculadas antes de borrarse.
- `Makefile` fijado a la composicion local para no mezclar la topologia local con la de Cosmos.

## Segunda pasada estructural

- La separacion por blueprints y servicios de dominio es coherente, y los tres puntos de entrada
  de pedidos (web, chatbot y POS) terminan usando los mismos modelos, pricing y servicios operativos.
- Los cambios de estado y cancelaciones estan centralizados. Preparacion y almacen ya no mantienen
  barreras paralelas que puedan divergir.
- Web y chatbot bloquean deliberadamente pedidos que mezclen cocina/almacen o entrega
  inmediata/programada. Las colas internas soportan pedidos mixtos como defensa y para operacion
  administrativa, pero habilitarlos al publico requiere una decision comercial explicita.
- La principal deuda de estructura no es el flujo, sino el tamano de `routes/admin.py`,
  `routes/api_bot.py`, `models.py`, `routes/public.py` y `services.py`. Conviene dividirlos por
  catalogo, pedidos, pagos, fidelizacion y notificaciones, manteniendo las APIs actuales.
- No se recomienda una reescritura antes de operar: primero deben cerrarse los pendientes de
  produccion y añadirse pruebas por modulo; despues puede hacerse la separacion incremental.

## Pendientes antes de produccion publica

1. Configurar dominio HTTPS final. Sin HTTPS no hay PWA instalable completa, Web Push ni HSTS.
2. Automatizar backups cifrados externos de Oxidian, Evolution, imagenes y SQLite del bot; probar restauracion.
3. Adoptar Alembic y bloqueo exclusivo de migraciones. El runner actual es idempotente, pero limitado.
4. Definir zonas por geografia real. Hoy el checkout web usa la primera zona activa tras validar el radio.
5. Añadir idempotency key persistente a checkout web, POS y API bot para retries simultaneos.
6. Congelar coste y proveedor de cada componente de combo para COGS y liquidaciones exactas.
7. Registrar movimientos por lote de stock para devolver exactamente el lote FIFO consumido.
8. Separar permisos de POS: vender, descuento manual y movimiento de caja.
9. Incorporar MFA y version de sesion para administradores.
10. Separar web, chatbot y worker en procesos/contenedores independientes y endurecer capacidades.

## Verificacion ejecutada

- Compilacion Python y sintaxis Node.
- 11/11 pruebas del chatbot.
- Predeploy sin errores bloqueantes.
- Smoke outbox transaccional.
- Smoke telefono, saldo y OTP.
- Smoke integral: menu, carrito, checkout, POS, canje, preparacion, reparto, caja y notificaciones.
- Pruebas dirigidas de pricing minimo, OTP persistente, pedido mixto y limite de afiliado.
- Prueba dirigida de cancelacion con puntos, cupon, afiliado y `StaffPayment`.
- Prueba HTTP autenticada del formulario de productos con proveedor y canal.
- Migracion `20260615_01_affiliate_payment_integrity` aplicada.
- `/health/ready` correcto y Evolution/WhatsApp conectado.
- Gateway devuelve 404 para `/api/bot/*`.
