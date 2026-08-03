# Control financiero

La pantalla **Ganancias y costes** separa dos lecturas que no deben mezclarse:

- **Rentabilidad:** pedidos entregados en el período, descuento distribuido
  entre sus líneas, coste congelado de lo vendido, envío y gastos pagados.
- **Caja:** movimientos `Caja` registrados en el período, sin reinterpretar su
  origen.

## Fuentes de verdad

- Venta: `Order` con estado `entregado` y `entregado_en` dentro del período.
- Precio y coste histórico: snapshot de `OrderItem.metadata_json`.
- Coste de combos: suma de sus componentes fijos y seleccionados congelados.
- Gastos e ingresos manuales: `Caja`.
- Nóminas y comisiones: `StaffPayment` pagado y con `fecha_pago` en el período.

Los períodos son días civiles de `TIMEZONE_NEGOCIO` (por defecto
`Europe/Madrid`) y se convierten a UTC antes de consultar. Así una venta hecha
después de medianoche local nunca termina en el cierre del día anterior.

Las compras de insumos son una salida de caja, pero no se vuelven a descontar
del resultado: su consumo ya se reconoce como coste de producto vendido. Esto
evita contabilizar la misma compra dos veces.

## Calidad del dato

Un producto sin coste nunca se interpreta como coste cero. Si una venta no
tiene coste congelado, el informe marca el resultado como **provisional** y
muestra la cobertura de costes. Los pedidos nuevos congelan el coste vigente;
los históricos sin snapshot pueden usar el coste actual únicamente como
estimación y lo indican en la interfaz.

La implementación compartida vive en `services.calcular_pl`; las rutas de
administración solamente validan el período y presentan el resultado.
