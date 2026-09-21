# Recogida y modelo comercial — 12 de septiembre de 2026

## Contexto revisado antes de los cambios

- `FEATURE_RECOGIDA` ya permite activar/desactivar recogida desde superadmin.
  Checkout y las pruebas de módulos respetan esta capacidad. Si delivery está
  apagado, el sistema conserva al menos una modalidad de entrega.
- Cocina marca el pedido listo mediante una transición bajo bloqueo de fila.
  El aviso push existente se envía después del commit. El cliente necesita
  haber activado las notificaciones en su dispositivo.
- `MODO_TIENDA=propia` corresponde al negocio propio. `bar_servicio` corresponde
  a ofrecer una instalación a un negocio cliente; el nombre interno es legacy.
  No es un sistema multiempresa dentro de la misma instalación.
- `SERVICE_COMMISSION_PCT` ya se configura por superadmin y se congela por
  pedido mediante `service_commission_pct`, `service_commission_amount` y el
  neto del comerciante. Web, POS y API usan el servicio compartido. El resultado
  financiero considera la comisión de pedidos entregados.
- No se encontró cuota fija periódica ni su libro de liquidaciones. La
  comisión calculada no equivale a un cobro automático del negocio cliente.

## Integración realizada

El aviso push distingue recogida de reparto y confirma que el cliente ya puede
pasar. Abre el seguimiento protegido del pedido, donde se añadió la dirección
pública configurada y una ruta de Google Maps. No se usa el centro geográfico
de cobertura de reparto como ubicación del local. Si falta dirección, se
ofrece el chat web para consultarla, sin inventar una ubicación.

No se condiciona la vista de una recogida ya comprada al estado actual del
módulo: apagar nuevas recogidas no debe ocultar los datos de pedidos existentes.

## Decisiones pendientes consultadas

1. Ingreso fijo mensual o por pedido. Una mensualidad requiere periodos,
   importe acordado, estados pendiente/pagado/anulado y separación entre
   ingresos del titular del sistema y ventas del negocio. Un fijo por pedido
   requiere conservar ese importe en cada venta y sus devoluciones.
2. Aviso de recogida solo por PWA o también por WhatsApp. La segunda opción
   requiere una excepción explícita `pickup_ready` al contrato transaccional,
   una clave de deduplicación por pedido y comprobación de estado antes de
   reintentar. No debe habilitar avisos genéricos, reseñas ni campañas.

Mientras estas decisiones están pendientes no se han creado cobros ni ampliado
los propósitos permitidos de WhatsApp.

## Validación

4 pruebas nuevas de ubicación y aviso, 13 de módulos y flujos, y 7 de
notificaciones aprobadas. `git diff --check` sin errores. Validación local con
mocks; no se enviaron mensajes ni se desplegó.
