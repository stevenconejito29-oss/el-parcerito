# Revisión del delivery y las interfaces — 6 de septiembre de 2026

Primera iteración aplicada localmente. El objetivo es mostrar la decisión del
momento, conservar el trabajo al reintentar y permitir consultar el resto sin
mezclar todas las tareas. No se ha desplegado.

## Recorrido revisado

| Paso | Responsable y decisión | Resultado de esta iteración |
|---|---|---|
| Consulta del pedido | Cliente: consultar o solicitar ayuda desde su sesión | El chat devuelve los pedidos autorizados también después de enviar o reintentar un mensaje; un token caducado no da acceso. |
| Recepción y supervisión | Administración: encontrar y desbloquear el pedido | Estado y cliente visibles; número, fechas, canal, zona y verificación en un desplegable. Los filtros se combinan en un único formulario. |
| Inicio | Cocina / preparación: tomar el pedido dentro de su ventana | Se conserva el inicio autorizado y la separación de inmediatos, encargos y franjas. |
| Comprobación y empaque | Preparador: verificar todas las líneas y dejar listo | Marcas por ID de línea y cantidad, sin depender del orden visual. Cambiar cantidad exige nueva comprobación. Un fallo de almacenamiento no impide completar la lista. |
| Recogida | Repartidor: revisar y tomar la carga | Selector «Recoger», con contador y acceso explícito a la cola. |
| Reparto | Repartidor: continuar las entregas activas | Selector «Entregar» como vista inicial si existe ruta; «Ver todo» permite consultar ambas etapas. Se sustituyen contadores pasivos repetidos por estos controles. |
| Llegada, código y cobro | Repartidor: avisar, verificar y cerrar | Se conservan las barreras de llegada, código y cobro y el tratamiento separado de incidencias. |
| Cierre y liquidación | Administración / socio de producto | Se conserva la separación entre logística de tienda y consulta de existencias, ventas y liquidaciones del socio. |

La lógica de preparación sale de la plantilla a
`static/js/preparation-checklist.js`. El checklist es una ayuda local del
dispositivo, no una confirmación compartida entre empleados. Las marcas no se
borran al enviar el formulario: un rechazo o fallo de red permite reintentar.
El salto al siguiente pedido ignora tarjetas filtradas y envíos bloqueados.

Las nuevas interfaces reutilizan las rutas y permisos existentes. La huella
automática de CSS y JavaScript invalida los assets modificados en la PWA.

## Validación

- 823 pruebas Python aprobadas con la configuración PostgreSQL de CI en una
  base temporal aislada; compilación Python y sintaxis JavaScript correctas.
- Prueba de interacción `scripts/test_operational_focus.mjs`: etapas,
  navegación con teclado, reordenación de líneas, cambio de cantidad,
  reintento y almacenamiento bloqueado. Incluye las hojas de estilo de reparto.
- Plantillas reales renderizadas con datos ficticios: administración, cocina,
  preparación y reparto a 375 × 812, en claro y oscuro. Sin desbordamiento
  horizontal ni errores JavaScript en las ocho capturas. Se comprobaron los
  cambios de etapa y la combinación de filtros administrativos.
- Esta revisión visual usa respuestas simuladas para servicios externos; no
  certifica GPS real, impresión, notificaciones ni navegación vial. Las
  capturas de esta ejecución están en `/tmp/parcerito-ux-pages`.
- WhatsApp: 185 de 225 pruebas aprobadas; 40 fallos en el código del bot sin
  cambios en esta iteración. Afectan, entre otros, al menú administrativo,
  PIN y transferencia de chats. Este era el bloqueo al cerrar esta iteración;
  quedó resuelto en la [revisión posterior de PWA y WhatsApp](CUSTOMER_EXPERIENCE_REVIEW.md),
  con 829 pruebas Python y 229 del bot aprobadas.

Para repetir las interacciones desde `oxidian/`:

```bash
PLAYWRIGHT_CHROMIUM_EXECUTABLE=/ruta/a/chromium node scripts/test_operational_focus.mjs
```

## Siguientes iteraciones

1. Validar en el entorno de destino el traspaso de cliente a atención humana
   junto con su pedido; los fallos automatizados del bot ya están resueltos.
2. Recorrer pedidos programados y tandas con varios pedidos, mezclando zonas,
   pendientes y cancelaciones, para evaluar la carga visual con volumen realista.
3. Evaluar el cierre de entrega con efectivo, Bizum, código incorrecto y
   cliente ausente en dispositivo físico; mantener explícita la acción que
   modifica cobro o estado.
4. Revisar el panel del socio con propuestas rechazadas, incidencias y
   liquidaciones pendientes; priorizar tareas que requieren acción.
5. Medir tiempo y número de acciones por tarea con usuarios de cada rol antes
   de afirmar que la simplificación reduce tiempos de operación.
