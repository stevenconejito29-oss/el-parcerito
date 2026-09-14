# Pulido responsive — septiembre de 2026

## Cambios

- `storefront-polish.css` unifica el acabado público después de las hojas de
  cada vista. Mantiene los colores editables, imágenes configuradas y ornamentos
  de identidad; reduce texturas, sombras y desenfoques en las superficies de uso.
- Categorías sin desplazamiento residual sobre el título del catálogo. Cuadrícula
  de dos productos en móviles de 361–699 px, adaptable en tablet y escritorio.
- Tipografía de tarjetas más legible, búsqueda con botón accesible de 44 px,
  total del carrito contrastado y cabecera del chat legible en horizontal.
- Admin y superadmin: navegación con objetivos táctiles de 44 px, texto largo
  adaptable, foco visible en categorías y formularios móviles de 16 px para
  evitar el zoom automático de campos en iOS.
- Cocina y reparto: KPI opacos para evitar el desenfoque del listado durante el
  desplazamiento; estados de disponibilidad y GPS sin pulsos decorativos continuos.
- Transiciones de configuración limitadas a colores, en lugar de `all`.
- El service worker precarga el acabado nuevo y deja de descargar por adelantado
  la textura de 236 KiB. El archivo antiguo se conserva por compatibilidad con CSS
  y páginas cacheadas. La huella automática incluye CSS y service worker.

No se modifican rutas, estados, permisos, cantidades, reglas financieras ni
contratos de WhatsApp. Tampoco se eliminan componentes o datos de negocio.

## Comprobación local

Se renderizaron plantillas reales de Flask con una base SQLite efímera y datos
ficticios; Chromium recibió esos HTML y los recursos locales. APIs externas,
sesiones de navegador y notificaciones se simularon.

- 68 contratos de frontend y 14 pruebas de endurecimiento de PWA.
- Tres recorridos de navegador: carrito/chat, controles operativos y preparación
  con persistencia y redirección nativa.
- Público: menú con seis productos y nombres largos, carrito, checkout y chat;
  320, 375, 768 y 1280 px, orientación horizontal, navegador y PWA simulada.
  También preferencia oscura del sistema, manteniendo el tema público configurado.
- Paneles: dashboard de superadmin, finanzas, administradores, franjas de admin,
  cocina y reparto en móvil y escritorio, claro y oscuro. Revisión adicional de
  pedidos de admin y colas inmediatas de cocina, preparación y reparto.
- Se revisan desbordamientos, errores de JavaScript, navegación activa,
  desplegables y posición del compositor del chat; el total del carrito se
  comprueba con contraste mínimo 4.5:1.
- Sintaxis del service worker y `git diff --check`.

Las capturas y reportes de esta revisión están en `/tmp/parcerito-customer-pages`,
`/tmp/parcerito-admin-pages`, `/tmp/parcerito-delivery-pages` y
`/tmp/parcerito-ux-pages`. Son artefactos temporales locales.

Esta revisión no mide Core Web Vitals ni sustituye una prueba en Safari/iPhone
físico. No se ha desplegado: tras publicar corresponde comprobar actualización
de la PWA instalada y los servicios reales según `OPERATIONS.md`.

## Componentes visuales y herramientas

Se mantiene Flask/Jinja y Tailwind existente. La integración de esta iteración
usa ocho SVG oficiales de [Lucide](https://lucide.dev/), renderizados por
`partials/ui_icons.html`. La revisión exacta y licencia ISC están en
`docs/licenses/LUCIDE.txt`. No se necesita un paquete React, CDN ni JavaScript
de inicialización para los iconos. Se conserva la iconografía colombiana de marca.

Las categorías del superadmin y las tarjetas informativas de la tienda comparten
jerarquía de icono, título, descripción y accesos. Cocina y reparto incorporan
iconos operativos en su título. Foco, hover y respuesta al pulsar se implementan
con el CSS compartido y los tokens de marca.

Se toman como referencia criterios de composición de
[shadcn/ui](https://ui.shadcn.com/docs/installation), sin instalar sus componentes
React ni afirmar una migración de framework. Motion tiene una
[API JavaScript](https://motion.dev/docs/quick-start), pero las transiciones
necesarias quedan cubiertas por `motion.css`, ya existente. No se incorporan
Aceternity, Magic UI ni escenas 3D a los flujos de trabajo. No se han utilizado
los servicios externos v0, Bolt o Spline.

Validación adicional: 82 pruebas Python de frontend/PWA, tres recorridos de
interacción existentes, 28 escenarios públicos, 16 administrativos y ocho de
colas operativas. El nuevo `scripts/test_ui_motion.mjs` verifica sobre el CSS real
que skeleton, HTMX, estados y desplegables respetan movimiento reducido y
mantienen visible su contenido:

```bash
cd oxidian
PLAYWRIGHT_CHROMIUM_EXECUTABLE=/ruta/a/chromium node scripts/test_ui_motion.mjs
```

## Organización común del sistema

- El recorrido público queda en este orden: presentación compacta de la tienda,
  búsqueda, categorías, productos, recomendaciones/promociones y servicios.
  El cambio es de orden real del HTML, también para teclado y lectores de pantalla.
  Se conservan contenidos configurados, IDs, enlaces y contratos del carrito.
- `system-foundation.css`, cargado en ambos layouts, concentra radios de tarjetas
  y controles, tipografía de títulos, foco, espaciado y navegación. Las superficies
  y colores de estado conservan los tokens de cada tema y rol.
- El menú interno usa nombres funcionales: operación diaria, catálogo e inventario,
  finanzas, personas y accesos, promoción y contenido, tienda y reparto, control
  del sistema. Todos los endpoints y condiciones de permisos siguen presentes.
- Admin y superadmin disponen de búsqueda local por herramienta o categoría,
  tolerante a acentos. Al limpiar se restablecen los grupos abiertos anteriormente.
  Las categorías sin accesos autorizados se ocultan. Sin JavaScript el menú
  completo sigue disponible y no se muestra un buscador inoperante.
- Los roles operativos mantienen su navegación específica de turno/ruta.
  El script de búsqueda solo se carga en admin y superadmin.
- Los iconos Lucide de navegación usan un sprite inline único por documento,
  compartido con las tarjetas. No se hacen solicitudes externas para los SVG.

El nuevo `scripts/test_admin_tool_search.mjs` comprueba coincidencias, acentos,
resultado vacío, restauración de grupos, teclado y conservación de enlaces.
La auditoría visual incluye 28 escenarios públicos, 16 administrativos, ocho
colas operativas y 12 paneles de reparto por franjas. Se añaden pruebas sobre
las páginas renderizadas de filtros, ficha rápida y buscador real del panel.
Estos recorridos siguen usando datos sintéticos; no equivalen a una auditoría
exhaustiva de cada CRUD ni a un despliegue.

## Recorrido del cliente y seguimiento — 10 de septiembre

Se completa la revisión pendiente de las pantallas públicas con 70 escenarios:
menú, carrito, checkout, chat, producto, club, información legal, seguimiento,
vista previa de franjas y carrito vacío; cada pantalla se revisa en móvil de
320/375 px, tablet, escritorio, horizontal, navegador y preferencia oscura.
Se utilizan las plantillas reales renderizadas con datos sintéticos y APIs
simuladas. No se detectaron desbordamientos horizontales ni errores JavaScript.

- Navegación compartida para volver a la tienda o pedir ayuda desde club, legal,
  consulta de puntos, seguimiento y vista previa de reparto.
- Mensajes del chat con remitente visible y hora cuando existe una fecha válida;
  el contenido sigue insertándose como texto, sin interpretar HTML. El nombre
  del programa de recompensas respeta la configuración de la tienda.
- Carrito vacío, detalle de producto, tarjetas legales y fidelización comparten
  superficies y controles con el resto de la PWA. Se conservan navegación inferior,
  barras de compra y espacios para las áreas seguras del dispositivo.
- `order_presentation.py` proporciona los mismos textos al HTML inicial y al
  refresco autorizado del seguimiento: confirmación pendiente, preparación,
  recogida o reparto. El mensaje principal ya no queda atrasado respecto al estado.
- El método y la confirmación del pago proceden del pedido. Se elimina el texto
  fijo «Contra entrega» y se ocultan las instrucciones de Bizum cuando el pago
  está confirmado. La modalidad usa el campo explícito del pedido.
- Las consultas de seguimiento se serializan, tienen timeout y se pausan al
  ocultar la página. Un fallo conserva el último estado e informa que no se pudo
  actualizar, sin mantener el indicador de conexión exitosa.
- Los importes del seguimiento usan tinta con contraste mínimo 4.5:1 en las
  siete variantes de pantalla revisadas; se retiran los confetis continuos.

Validación: **852 pruebas Python y 226 del bot**, todas correctas con PostgreSQL
efímero; la base de pruebas se eliminó al terminar. Los recorridos de navegador
verifican historial, reintentos, borrador, carrito, remitente/hora y contenido de
chat seguro, además de actualización de confirmación/pago y consultas de
seguimiento sin solapamientos. Se comprueba que el endpoint de estado conserva
la autorización por token del pedido.

Evidencia local: `/tmp/parcerito-customer-unified-tests.log` y los reportes
`report.json`, `extended-report.json` y `tracking-final-report.json` de
`/tmp/parcerito-customer-pages`. La ejecución del navegador, bloqueada el día
anterior por límite de uso, se completó al reanudar la sesión.

Los cambios siguen sin desplegar. Los resultados no sustituyen pruebas en un
iPhone físico, medidas de rendimiento real ni comprobación de WhatsApp/push en
producción.
