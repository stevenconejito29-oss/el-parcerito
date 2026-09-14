# Coherencia de PWA, pedidos y operación — 8 de septiembre de 2026

Revisión local del recorrido del cliente y del empleado. Complementa las
revisiones de experiencia del cliente y administración. No se ha desplegado
ni se han modificado datos de producción.

## Contrato entre módulos

| Etapa | Fuente de verdad | Comprobación |
|---|---|---|
| Menú y carrito | Catálogo actual y validación del servidor | La edición conserva productos ante cantidades inválidas y guarda antes del checkout. |
| Compra confirmada | `Order`, `OrderItem` y metadata congelada | Editar el catálogo no sustituye valores vacíos explícitos del pedido. |
| Chat web | Sesión del navegador, tokens autorizados y estados del pedido | Historial y pedidos privados aislados; cancelación explícita y respuestas coherentes con recogida/reparto. |
| Verificación WhatsApp | Pedido pendiente de confirmación y perfil registrado | No hay asistente público. Los avisos de estado genéricos no sustituyen al seguimiento de la PWA. |
| Preparación | Transiciones de servicios y líneas originales de compra | Una compra sin confirmar no entra en cocina. Ambas vistas muestran opciones, notas y alérgenos guardados. |
| Reparto | Modalidad y asignación del pedido | Inmediato y franjas conservan sus barreras; la comprobación del empaque no equivale a entrega/cobro. |
| Finanzas | Ventas entregadas, costes históricos y movimientos de caja | Se conserva el cálculo compartido y la separación entre rentabilidad y caja. |

## Errores corregidos

1. **Herencia del catálogo:** una fecha, imagen, origen o lista de alérgenos
   vacíos en el snapshot podían tomar el valor del producto editado. Ahora la
   presencia de la clave manda, incluso si su valor está vacío. Solo una
   clave ausente conserva la compatibilidad con pedidos legacy.
2. **Origen mostrado:** el detalle visual podía etiquetar como producto de un
   proveedor una compra cuyo origen congelado era propio. Se alinea con la
   regla de distribución del servicio.
3. **Metadata inválida:** se comprueba que la raíz y el snapshot de producto
   sean diccionarios antes de leerlos. Esto evita errores de lectura por esas
   formas inválidas; no reconstruye metadata histórica dañada.
4. **Preparación por franjas:** la tarjeta solo mostraba nombre y cantidad,
   omitiendo tamaños, sabores, extras, notas por línea y alérgenos. Reutiliza
   ahora el mismo detalle que la cola habitual y su checklist persistente.
5. **Respuesta al empleado:** el envío mediante fetch seguía redirecciones,
   consumiendo avisos del servidor y perdiendo el destino de impresión. Los
   formularios conservan ahora la navegación nativa y sus mensajes.
6. **Chat web:** entradas JSON con formas inválidas se rechazan; un error de
   integridad distinto de una colisión idempotente devuelve un fallo
   recuperable, no una confirmación de guardado. El fallo del registro de
   aprendizaje no convierte en fallido un mensaje ya confirmado.
7. **Modalidad en el chat:** una recogida en local ya no se describe como
   reparto inmediato. Se presenta también la fecha programada cuando existe.
8. **Entrada por rol:** preparación de encargos abre su cola por fecha, sin
   pasar por una agenda de cocina que no corresponde a su rol.

## Claridad visual y de interacción

- Una salida seleccionada cada vez, con estado accesible y selección recordada.
- Una salida vacía se presenta como vacía, no como preparada.
- En móvil se conservan «Elige salida», «Prepara» y «Entrega», en lugar de
  mostrar únicamente números.
- Se corrige el contraste del título y del detalle de opciones en franjas.
- Las notas y alérgenos permanecen visibles al comprobar el artículo.
- Sin JavaScript los paneles de las salidas siguen disponibles; las acciones
  que necesitan checklist conservan ese requisito.

## Validación realizada

- `scripts/test-project.sh`: **840 pruebas Python y 226 del bot aprobadas**,
  compilación Python y sintaxis del bot. PostgreSQL temporal aislado.
- `test_customer_journeys.mjs`: historial, lectura sin saltos, siguiente
  borrador, reintento idempotente y guardado del carrito antes de continuar.
- `test_operational_focus.mjs`: etapas, teclado, reordenación de líneas,
  cantidades, reintentos y almacenamiento bloqueado.
- `test_preparation_synergy.mjs`: selección accesible de salida, persistencia
  del checklist y navegación POST/redirección con un servidor HTTP temporal.
- Cocina habitual y preparación por franjas renderizadas con compra ficticia
  con tamaño, sabor, extra, nota y alérgenos: 320 px, 375 px, 1280 px y móvil en
  oscuro. Informe y capturas en `/tmp/parcerito-synergy-pages`.

Las pruebas del chat aíslan su cuota por escenario; no se han desactivado ni
ampliado los límites de mensajes de producción.

Desde `oxidian/`, las pruebas de interacción se ejecutan con:

```bash
PLAYWRIGHT_CHROMIUM_EXECUTABLE=/ruta/a/chromium node scripts/test_preparation_synergy.mjs
```

## Pendiente de validación real

La suite y las capturas no certifican la instalación de producción. Queda
recorrer una compra de prueba con Evolution/WhatsApp real y dispositivos de
clientes y empleados, incluyendo teclado móvil, instalación PWA, pérdida de
red, impresión y entrega. La caché mantiene carrito, checkout y superficies
internas fuera del catálogo HTML reutilizable; esto no convierte las compras
ni las transiciones operativas en acciones disponibles sin conexión.

No se han migrado pedidos históricos ni alterado los cálculos financieros.
Conviene auditar por separado los registros legacy sin snapshot completo
antes de plantear una reparación de datos: ausencia histórica no equivale a
un valor vacío guardado intencionalmente.
