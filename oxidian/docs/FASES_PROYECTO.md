# Flujo maestro de finalizacion de Oxidian

Fecha de inicio del registro: 2026-05-18

Este documento es la memoria operativa del proyecto. Cada fase solo se considera cerrada cuando cumple sus criterios de aceptacion, tiene pruebas o verificacion manual registrada y no introduce regresiones en la suite completa.

## Estado global

Fase actual: 10 de 10 - QA final, documentacion y lanzamiento.

Definicion de terminado del proyecto:
- Todas las rutas principales renderizan sin errores 500.
- Los datos visibles vienen de la base de datos o de configuracion, sin hard coding funcional.
- Cliente, pedido, items, pagos, puntos, stock, caja y notificaciones quedan persistidos y trazables.
- La vista publica funciona en desktop, Android e iOS con flujo completo de compra.
- La suite completa pasa y cada fase tiene al menos pruebas enfocadas o una verificacion manual documentada.
- Los roles internos solo acceden a funciones permitidas.
- Produccion tiene configuracion segura y checklist de lanzamiento.

## Fase 1 - Vista publica, carrito y checkout

Objetivo: que el cliente pueda comprar desde menu publico con carrito, modales, checkout y pedido confirmado sin bugs bloqueantes.

Alcance:
- Home/menu publico, producto detalle, modal quick-add, carrito, puntos por WhatsApp, checkout y pedido confirmado por token.
- Guardado correcto de cliente invitado o logueado.
- Guardado correcto de pedido, items, direccion, zona, metodo de pago, notas, cupones y puntos.

Criterios de aceptacion:
- El menu muestra categorias, productos, precios, promociones, stock visible, alergenos, imagenes y combos desde BD.
- El modal quick-add no rompe scroll/focus y respeta cantidad maxima configurada.
- El carrito no permite cantidades fuera de stock o fuera de limite.
- Checkout precarga cliente por telefono sin usar endpoints protegidos del bot.
- El total estimado visual coincide con el motor de pricing para cupon, puntos y envio.
- El pedido queda persistido y accesible con token para invitado.

Estado:
- Completada a nivel backend/templates.
- Pendiente de validacion visual manual exhaustiva en dispositivos reales.

Hecho:
- Endpoint publico limitado `/api/public/cliente`.
- Normalizacion de telefono en checkout.
- Actualizacion de nombre/direccion de clientes existentes cuando compran como invitados.
- Carrito corregido para evitar formularios anidados invalidos.
- Ratio de puntos y limite de carrito leidos desde configuracion.
- Total estimado con envio gratis por zona.
- Ajustes responsive para carrito y checkout.
- Tests enfocados de carrito/checkout.

Verificacion:
- `./venv/bin/pytest tests/test_integration.py::TestCarrito -q` -> 11 passed.
- `./venv/bin/pytest -q` -> 178 passed.
- `/`, `/carrito` y `/api/public/cliente` responden 200 en servidor local.

## Fase 2 - Catalogo, productos, categorias, combos e imagenes

Objetivo: que administracion de catalogo y menu controle al 100% lo que ve el cliente.

Alcance:
- CRUD de productos, categorias, combos y componentes.
- Stock inicial y lotes para productos inmediatos.
- Imagenes de productos, categorias, banners y menu config.
- Campos de tipo de entrega, horario, dias visibles, promociones rapidas, alergenos, canje de puntos y origen.
- MenuConfig para banners, destacados, secciones y checkout.

Criterios de aceptacion:
- Crear/editar/desactivar producto se refleja correctamente en la vista publica.
- Crear/editar/desactivar categoria ordena y filtra productos correctamente.
- Un combo con componentes fijos y seleccionables muestra opciones correctas al cliente y descuenta stock correcto.
- Imagenes subidas quedan guardadas en ruta relativa consistente y se muestran en admin/publico.
- No se guarda una URL `/uploads/...` como si fuera ruta relativa duplicable.
- Productos con horario/dias fuera de ventana no se muestran ni se agregan al carrito.
- Formularios admin validan minimo nombre/precio/tipo y no aceptan datos incoherentes.

Pruebas/verificacion requerida:
- Tests de CRUD producto/categoria/menu config.
- Tests de combo seleccionable y stock.
- Prueba manual de subida de imagen.
- Suite completa sin regresiones.

Estado:
- Cerrada tecnicamente.

Hecho:
- Normalizacion centralizada de rutas de imagen en admin para evitar guardar `/uploads/...` como dato local.
- Filtro Jinja `upload_url` para renderizar imagenes locales, `/uploads/...` heredadas y URLs externas sin duplicar rutas.
- Plantillas publicas y admin relevantes actualizadas para usar `upload_url`.
- El preview/upload AJAX de productos guarda ruta relativa consistente.
- Productos con horario nocturno que cruza medianoche ahora se muestran correctamente.
- Productos marcados como hipoalergenicos ya no guardan alergenos contradictorios.
- Tests de producto/menu/categoria ampliados para imagenes, horario nocturno e hipoalergenicos.

Verificacion:
- `./venv/bin/pytest tests/test_integration.py::TestProductos tests/test_integration.py::TestSeguridad tests/test_integration.py::TestCategorias -q` -> 31 passed.
- `./venv/bin/pytest -q` -> 182 passed.

## Fase 3 - Pedidos, cocina, preparacion y reparto

Objetivo: que todo pedido llegue completo a operaciones y avance por estados sin perder informacion.

Alcance:
- Pedidos admin, cola, preparador, repartidor y confirmacion de entrega.

Criterios de aceptacion:
- Pedido online/bot/POS crea items y totales correctos.
- Asignacion de preparador/repartidor es deterministicamente valida con staff disponible.
- Estados avanzan en orden permitido y bloquean transiciones invalidas.
- Cancelacion restaura stock y puntos segun reglas.
- Repartidor confirma entrega con codigo y se registra hora.

Pruebas/verificacion requerida:
- Tests de transiciones, cancelacion y restauracion.
- Tests de asignacion staff.
- Recorrido manual admin -> preparador -> repartidor -> entregado.

Estado:
- Cerrada tecnicamente.

Hecho:
- Bloqueado que un preparador inicie un pedido pendiente asignado a otro preparador.
- La vista de cocina/preparacion ya filtra pedidos pendientes por propios o sin asignar.
- Se agregaron pruebas para evitar robo de pedidos desde la accion directa `empezar`.
- Se agrego prueba para que un preparador no vea pedidos pendientes asignados a otro.

Verificacion:
- `./venv/bin/pytest tests/test_integration.py::TestFlujosPedido tests/test_integration.py::TestPreparador tests/test_integration.py::TestRepartidor -q` -> 16 passed.
- `./venv/bin/pytest -q` -> 184 passed.

## Fase 4 - Pagos, Bizum, caja y conciliacion

Objetivo: que el dinero registrado cuadre con pedidos, pagos y caja.

Alcance:
- Metodos de pago, Bizum, pagos pendientes, confirmacion digital, caja, ingresos/egresos y POS.

Criterios de aceptacion:
- Pedido online genera movimiento de caja correcto.
- Bizum muestra datos desde configuracion.
- Confirmar pago digital registra usuario y fecha.
- Cancelaciones no duplican ingresos ni dejan caja incoherente.
- POS registra ventas presenciales con stock y caja.

Pruebas/verificacion requerida:
- Tests de caja por pedido y POS.
- Tests de pago confirmado.
- Recorrido manual efectivo/Bizum.

Estado:
- Cerrada tecnicamente.

Hecho:
- Cancelar un pedido desde admin registra una reversion de caja por el ingreso neto asociado al pedido, evitando caja inflada por pedidos anulados.
- Rechazar un pago digital cancela el pedido y registra la reversion de caja correspondiente.
- Las reversiones son idempotentes a nivel de neto por pedido: no se genera egreso si el pedido ya no tiene saldo positivo en caja.
- Las ventas POS quedan como pago confirmado con usuario y fecha de confirmacion.
- Las devoluciones POS enlazan el egreso de caja con el `pedido_id`, dejando trazabilidad de la venta y su devolucion.
- `registrar_egreso` acepta `pedido_id` opcional sin romper los usos existentes de pagos a staff.
- Tests enfocados agregados para reversion admin, rechazo digital y pago confirmado en POS.

Verificacion:
- `./venv/bin/python -m py_compile app.py models.py services.py routes/public.py routes/admin.py routes/preparador.py routes/pos.py tests/test_integration.py` -> ok.
- `./venv/bin/pytest tests/test_integration.py::TestPOS tests/test_integration.py::TestCaja tests/test_integration.py::TestFlujosPedido -q` -> 15 passed.
- `./venv/bin/pytest tests/test_integration.py::TestCarrito tests/test_integration.py::TestProductos tests/test_integration.py::TestSeguridad tests/test_integration.py::TestCategorias tests/test_integration.py::TestFlujosPedido tests/test_integration.py::TestPreparador tests/test_integration.py::TestRepartidor -q` -> 59 passed.
- `./venv/bin/pytest -q` -> 186 passed.

## Fase 5 - Fidelizacion, puntos, cupones, promociones y afiliados

Objetivo: que descuentos y fidelizacion sean consistentes, auditables y sin doble aplicacion.

Alcance:
- Puntos ganados/usados, productos canjeables, cupones, promociones automaticas, afiliados y comisiones.

Criterios de aceptacion:
- El motor unico de pricing es la fuente de verdad.
- Cupones respetan vigencia, minimo, usos y caps.
- Puntos se verifican, descuentan y registran una sola vez.
- Afiliados generan descuento y comision correctos.
- Cancelaciones devuelven o revierten puntos correctamente.

Pruebas/verificacion requerida:
- Tests unitarios del motor de pricing.
- Tests de puntos/cupon/afiliado en checkout, POS y bot.

Estado:
- Cerrada tecnicamente.

Hecho:
- El motor de pricing ahora devuelve `puntos_usados` coherentes con el descuento real aplicado, incluso cuando los puntos solicitados superan el subtotal disponible.
- La API del bot limita `puntos_usar` al saldo real del cliente antes de calcular el precio.
- La cancelacion de pedidos revierte el uso de cupones para no consumir cupos en pedidos anulados.
- La cancelacion de pedidos revierte usos de afiliado y elimina comisiones pendientes no pagadas asociadas al pedido cancelado.
- Se mantuvo el canje real de puntos centralizado en `loyalty_service.aplicar_canje_en_pedido`.
- Se agregaron pruebas para cap de puntos en pricing, cap de puntos desde bot y reversion de cupon/afiliado pendiente al cancelar.

Verificacion:
- `./venv/bin/python -m py_compile pricing_service.py loyalty_service.py models.py routes/api_bot.py tests/test_pricing.py tests/test_loyalty.py tests/test_integration.py` -> ok.
- `./venv/bin/pytest tests/test_pricing.py tests/test_loyalty.py tests/test_integration.py::TestFlujosPedido tests/test_integration.py::TestPuntos tests/test_integration.py::TestCupones tests/test_integration.py::TestAfiliados tests/test_integration.py::TestPromociones tests/test_integration.py::TestApiBot tests/test_integration.py::TestPricingService tests/test_integration.py::TestLoyaltyService -q` -> 82 passed.
- `./venv/bin/pytest -q` -> 189 passed.

## Fase 6 - Bot WhatsApp e integraciones

Objetivo: que el bot consuma datos reales, cree pedidos validos y notifique estados.

Alcance:
- API bot, catalogo bot, clientes, pedidos bot, puntos bot, promociones, mensajes, broadcast y sincronizacion.

Criterios de aceptacion:
- Endpoints protegidos exigen `X-Bot-Key`.
- Catalogo del bot coincide con productos visibles y disponibles.
- Pedido bot usa el mismo motor de pricing y reglas de stock que web.
- Notificaciones fallan de forma no bloqueante.
- Broadcast respeta permisos y configuracion.

Pruebas/verificacion requerida:
- Tests API bot completos.
- Prueba manual con bot o simulador HTTP.

Estado:
- Cerrada tecnicamente.

Hecho:
- El bot ya no descuenta puntos al verificar un codigo de canje; solo confirma la identidad y devuelve `producto_canje_id` para crear el pedido.
- La creacion de pedido desde bot acepta `producto_canje_id` y aplica el canje en `loyalty_service.aplicar_canje_en_pedido`, cuando el pedido ya existe.
- La API del bot mantiene el motor de pricing unificado para cupones, afiliados, puntos, envio y promociones.
- Los endpoints extendidos del catalogo del bot usan una misma regla de disponibilidad: producto activo, visible ahora y disponible para venta.
- El detalle de producto del bot devuelve 404 si el producto esta fuera de venta.
- Promociones, catalogo por categoria y productos canjeables ya no exponen productos inactivos/no disponibles.
- Se actualizo `loyalty_service` a `db.session.get` para evitar API legacy de SQLAlchemy.
- Se agregaron pruebas para flujo de canje del bot y para no exponer productos fuera de venta en endpoints extendidos.

Verificacion:
- `./venv/bin/python -m py_compile routes/api_bot.py loyalty_service.py tests/test_integration.py` -> ok.
- `./venv/bin/pytest tests/test_integration.py::TestApiBot tests/test_integration.py::TestLoyaltyService -q` -> 19 passed.
- `./venv/bin/pytest -q` -> 192 passed.

## Fase 7 - Roles, permisos y paneles internos

Objetivo: que cada rol pueda hacer solo lo que le corresponde.

Alcance:
- Auth, registro/login, admin, superadmin, marketing, staff, preparador, repartidor y POS.

Criterios de aceptacion:
- Rutas protegidas tienen decoradores/permisos correctos.
- Cliente no accede a admin.
- Staff no accede a superadmin.
- Acciones sensibles registran auditoria cuando corresponde.
- Navegacion muestra solo funciones disponibles.

Pruebas/verificacion requerida:
- Tests de acceso por rol.
- Revision de decoradores por blueprint.

Estado:
- Cerrada tecnicamente.

Hecho:
- Se verificaron de nuevo las fases 1 a 6 con pruebas enfocadas de vista publica, catalogo, pedidos, pagos/caja, fidelizacion y bot.
- Se cerraron accesos de `admin` sin feature a los blueprints operativos de POS, marketing e inventario/staff.
- `staff` mantiene acceso a POS e inventario, pero no a admin ni superadmin.
- `super_admin` ahora se trata como operador global en preparador y repartidor, no como usuario normal sin pedidos asignados.
- Se agregaron pruebas de permisos para admin limitado, admin con feature POS, staff y superadmin operativo.
- Se mantuvo el control por `AdminFeature` para rutas admin ya cubiertas por prefijos.

Verificacion:
- `./venv/bin/python -m py_compile app.py models.py services.py pricing_service.py loyalty_service.py routes/*.py tests/test_integration.py tests/test_pricing.py tests/test_loyalty.py` -> ok.
- `./venv/bin/pytest tests/test_pricing.py tests/test_loyalty.py tests/test_integration.py::TestCarrito tests/test_integration.py::TestProductos tests/test_integration.py::TestCategorias tests/test_integration.py::TestFlujosPedido tests/test_integration.py::TestPreparador tests/test_integration.py::TestRepartidor tests/test_integration.py::TestPOS tests/test_integration.py::TestCaja tests/test_integration.py::TestPuntos tests/test_integration.py::TestCupones tests/test_integration.py::TestAfiliados tests/test_integration.py::TestPromociones tests/test_integration.py::TestApiBot tests/test_integration.py::TestLoyaltyService -q` -> 119 passed.
- `./venv/bin/pytest tests/test_integration.py::TestAuth tests/test_integration.py::TestSeguridad tests/test_integration.py::TestSuperAdmin tests/test_integration.py::TestPOS tests/test_integration.py::TestPreparador tests/test_integration.py::TestRepartidor -q` -> 40 passed.
- `./venv/bin/pytest -q` -> 196 passed.

## Fase 8 - Inventario, analitica, reportes y datos operativos

Objetivo: que stock, analitica y reportes sean confiables para operar el negocio.

Alcance:
- Stock por lotes, caducidad, productos top, ventas por categoria, analytics, dashboard y reportes.

Criterios de aceptacion:
- Stock total coincide con lotes.
- Descuentos de stock son FIFO y restauraciones coherentes.
- Reportes excluyen cancelados cuando corresponde.
- Fechas y filtros devuelven datos correctos.
- Dashboards no explotan con tablas vacias.

Pruebas/verificacion requerida:
- Tests de stock masivo y reportes.
- Prueba manual con datos demo.

Estado:
- Cerrada tecnicamente.

Hecho:
- Se verificaron nuevamente las fases 1 a 7 con suite completa y pruebas enfocadas de carrito, catalogo, pedidos, pagos/caja, fidelizacion, bot, roles, stock y analitica.
- `Product.descontar_stock` ahora prevalida cantidad y stock total antes de tocar lotes, evitando descuentos parciales si la operacion falla por stock insuficiente.
- `Product.descontar_stock_combo` acumula y prevalida todos los componentes requeridos antes de descontar, evitando que un combo deje componentes parcialmente descontados cuando otro componente no alcanza.
- Los reportes de P&L, top productos y ventas por categoria usan rangos de fecha semiabiertos (`>= inicio`, `< fin + 1 dia`) para incluir correctamente el dia final sin contar movimientos de la medianoche siguiente.
- Ventas por categoria ahora incluye productos sin categoria bajo "Sin categoria" y sigue excluyendo pedidos cancelados.
- Se agregaron pruebas enfocadas para atomicidad de stock simple, atomicidad de stock en combos, limites exactos de reportes y categoria sin asignar.

Verificacion:
- `./venv/bin/python -m py_compile models.py services.py tests/test_integration.py` -> ok.
- `./venv/bin/pytest tests/test_integration.py::TestStock tests/test_integration.py::TestAnalytics -q` -> 14 passed.
- `./venv/bin/pytest tests/test_pricing.py tests/test_loyalty.py tests/test_models.py tests/test_colombian_catalog_seed.py -q` -> 47 passed.
- `./venv/bin/pytest tests/test_integration.py::TestProductos tests/test_integration.py::TestFlujosPedido tests/test_integration.py::TestPOS tests/test_integration.py::TestApiBot -q` -> 41 passed.
- `./venv/bin/python -m py_compile app.py models.py services.py pricing_service.py loyalty_service.py routes/*.py tests/test_integration.py tests/test_pricing.py tests/test_loyalty.py tests/test_models.py tests/test_colombian_catalog_seed.py` -> ok.
- `./venv/bin/pytest -q` -> 200 passed.

## Fase 9 - Seguridad, privacidad, configuracion y produccion

Objetivo: preparar el sistema para datos reales.

Alcance:
- CSRF, sesiones, cookies, cabeceras, endpoints publicos, variables de entorno, errores, logs y base de datos.

Criterios de aceptacion:
- Produccion rechaza configuracion insegura.
- Endpoints publicos no exponen datos sensibles innecesarios.
- Formularios sensibles tienen CSRF.
- Subidas validan extension, tipo y tamano.
- Paginas 403/404/500 son correctas.
- No hay secretos hardcodeados.

Pruebas/verificacion requerida:
- Tests de seguridad basicos.
- Revision manual de endpoints publicos.
- Checklist de variables de entorno.

Estado:
- Cerrada tecnicamente.

Hecho:
- Se activo `CSRFProtect` globalmente en la aplicacion; los tokens ya presentes en formularios y fetch pasan a tener validacion real.
- El blueprint del bot queda exento de CSRF porque su frontera de seguridad es `X-Bot-Key`; se agrego prueba para confirmar que un POST sin token CSRF llega al control de API key y responde 401.
- `create_app("production")` valida configuracion al arrancar y exige `SECRET_KEY` y PostgreSQL.
- Las subidas de imagen validan extension y contenido real de imagen antes de guardar, rechazando archivos con extension valida pero payload no imagen.
- Se mantuvieron cabeceras de seguridad existentes: `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, `Permissions-Policy` y cookies seguras en produccion.
- Se agregaron pruebas enfocadas para CSRF, exencion del bot, configuracion insegura de produccion y validacion de uploads.

Verificacion:
- `./venv/bin/python -m py_compile app.py extensions.py routes/uploads.py tests/test_integration.py` -> ok.
- `./venv/bin/pytest tests/test_integration.py::TestSeguridad tests/test_integration.py::TestApiBot -q` -> 42 passed.
- `./venv/bin/python -m py_compile app.py config.py extensions.py models.py services.py pricing_service.py loyalty_service.py routes/*.py tests/test_integration.py tests/test_pricing.py tests/test_loyalty.py tests/test_models.py tests/test_colombian_catalog_seed.py` -> ok.
- `./venv/bin/pytest tests/test_integration.py::TestCarrito tests/test_integration.py::TestProductos tests/test_integration.py::TestCategorias tests/test_integration.py::TestSuperAdmin tests/test_integration.py::TestPOS tests/test_integration.py::TestStock tests/test_integration.py::TestAnalytics -q` -> 45 passed.
- `./venv/bin/pytest -q` -> 204 passed.

## Fase 10 - QA final, documentacion y lanzamiento

Objetivo: cerrar el proyecto como sistema completo listo para operar.

Alcance:
- Recorridos de usuario final, admin, cocina, reparto, marketing, bot y POS.
- Documentacion de uso y operacion.
- Checklist de despliegue.

Criterios de aceptacion:
- Suite completa en verde.
- Recorrido completo web: menu -> carrito -> checkout -> pedido -> cocina -> reparto -> entrega -> reseña.
- Recorrido bot completo.
- Recorrido POS completo.
- Pruebas responsive en Android/iOS/desktop.
- Documentacion de operacion actualizada.
- Lista de pendientes final vacia o aceptada explicitamente.

Pruebas/verificacion requerida:
- Suite completa.
- Checklist manual firmado en este documento.

Estado:
- Cerrada tecnicamente. Pendiente de firma manual en dispositivos reales, bot WhatsApp real y POS real antes de abrir produccion.

Hecho:
- Se agrego `docs/QA_LANZAMIENTO.md` con verificacion automatizada obligatoria, checklist de produccion y checklist manual por canal.
- Se agrego un recorrido automatizado de QA completo: cliente web crea pedido desde carrito/checkout, se registra stock/caja/puntos, cocina lo inicia y marca listo, repartidor sale a entrega y confirma codigo/cobro, cliente deja resena.
- Se verificaron de nuevo los recorridos de bot, POS, seguridad, preparador y repartidor con pruebas enfocadas.
- La suite completa queda en verde tras fases 8, 9 y 10.

Verificacion:
- `./venv/bin/python -m py_compile tests/test_integration.py` -> ok.
- `./venv/bin/pytest tests/test_integration.py::TestFlujosPedido::test_qa_recorrido_web_operativo_completo_hasta_resena -q` -> 1 passed.
- `./venv/bin/pytest tests/test_integration.py::TestFlujosPedido tests/test_integration.py::TestPreparador tests/test_integration.py::TestRepartidor tests/test_integration.py::TestPOS tests/test_integration.py::TestApiBot tests/test_integration.py::TestSeguridad -q` -> 63 passed.
- `./venv/bin/pytest -q` -> 205 passed.

Checklist manual pendiente de firma:
- Desktop real: menu -> carrito -> checkout -> pedido confirmado.
- Android real: menu, modal quick-add, carrito, checkout y confirmacion.
- iOS real: menu, modal quick-add, carrito, checkout y confirmacion.
- Bot WhatsApp real: catalogo, creacion de pedido, notificaciones de estado y broadcast.
- POS real: cobro, ticket, devolucion y cuadre de caja.

## Auditoria 2026-05-22 - Plan de cierre en 7 fases

Objetivo: verificar que los datos de super admin/admin se aplican a menu, carrito, pedidos, areas operativas, finanzas, notificaciones, chatbot y PWA; corregir fallos por fases.

Estado de auditoria automatizada:
- Stack Cosmos local arriba: Oxidian, PostgreSQL Oxidian, chatbot, Evolution API, PostgreSQL Evolution y Redis.
- `pytest -q tests/test_models.py tests/test_pricing.py tests/test_loyalty.py tests/test_colombian_catalog_seed.py tests/test_data_entry_integrity.py tests/test_day_simulation.py` -> 49 passed.
- `pytest -q tests/test_integration.py` -> 187 passed.
- `python scripts/simulate_data_matrix.py` -> 57/57 OK.
- `pytest -q tests/test_integration.py::TestSuperAdmin tests/test_integration.py::TestApiBot tests/test_integration.py::TestCaja tests/test_integration.py::TestPagosStaff tests/test_integration.py::TestFlujosPedido` -> 57 passed.
- `pytest -q tests/test_integration.py::TestApiBot tests/test_integration.py::TestCaja tests/test_integration.py::TestPagosStaff` y verificacion enfocada posterior -> 32 passed.
- `python scripts/simulate_against_local.py --url http://127.0.0.1:5070 --days 1 --daily-base 20 --workers 6 --pool-size 24 --bot-key local-evolution-bot-key --pos-email admin@oxidian.local --pos-password oxidian-local-2026` -> 20/20 pedidos creados: web 11/11, bot 4/4, POS 5/5.

Fallas encontradas y corregidas:
- Fase 1: los combos con grupos multiopcion fallaban si el formulario/simulador enviaba lista de IDs en vez de cantidades. Corregido en `routes/public.py` y cubierto con test.
- Fase 2: el simulador local generaba falsos negativos en web/POS por no usar CSRF y por credenciales POS antiguas. Corregido en `scripts/simulate_against_local.py`.
- Fase 3: la sincronizacion manual del chatbot desde Oxidian podia fallar porque el bot no aceptaba `X-Panel-Key`. Corregido en `../chat/bot.js`.
- Fase 4: pedidos sin empleados online quedaban sin asignar, que es correcto segun la regla operativa, pero faltaba alerta visible para admin/superadmin. Agregados KPIs y aviso de pedidos sin asignar.

Pendientes / riesgos reales:
- Bot WhatsApp esta en modo `SIMULATE_EVO_SEND=1` en local; las rutas funcionan, pero el envio real requiere quitar simulacion y escanear WhatsApp Business en Evolution.
- PWA tiene manifest, service worker y flujo de instalacion; falta prueba manual en Android/iOS reales porque `beforeinstallprompt` depende del navegador/dispositivo.
- Se debe repetir simulacion de varios dias contra servidor ya reconstruido despues de cada bloque grande.
- El aviso de pedidos sin asignar resuelve visibilidad, pero todavia falta una politica configurable: pausar checkout si no hay cocina/reparto online o permitir cola sin asignar.

Plan de 7 fases:
1. Datos de producto/combo/menu/carrito: asegurar que todo producto vendible se pueda agregar, comprar y trazar.
2. Simulacion real HTTP/Cosmos local: evitar falsos positivos y falsos negativos, cubrir CSRF, sesiones, POS y concurrencia.
3. Chatbot/Evolution: sincronizacion, QR, menus cliente/superadmin, handoff humano y notificaciones de estado.
4. Operacion empleados: alertas de pedidos sin responsable, online/offline, cocina, preparacion y reparto.
5. Finanzas y puntos: caja, staff, comisiones, bizum/efectivo, puntos ganados/canjeados y cancelaciones.
6. PWA y responsive: instalacion movil, service worker, carrito/checkout/menu en Android/iOS.
7. Predeploy Cosmos: variables, healthchecks, volumes, backup DB, simulacion 7 dias y checklist manual final.

Estado de avance:
- Fases 1, 2, 3 y primera parte de 4 corregidas y probadas.
- Siguiente bloque recomendado: Fase 5 completa con reporte financiero cruzado contra pedidos entregados/cancelados y caja.

## Auditoria fuerte 2026-05-22 - cierre por 7 fases

Objetivo: revisar el proyecto completo por fases, ejecutar testeo fuerte y corregir fallas logicas detectadas en datos, stock, empleados, chatbot, PWA, menu y carrito.

Fases ejecutadas:
1. Inventario y salud del stack: se verifico el stack Cosmos local con Oxidian, PostgreSQL Oxidian, chatbot, Evolution API, PostgreSQL Evolution y Redis arriba.
2. Compilacion y sintaxis: Python, rutas, servicios, scripts, tests principales, JS, JSON/YAML y templates Jinja.
3. Backend y reglas de negocio: productos, combos, carrito, checkout, puntos, caja, cupones, afiliados, promociones, empleados, reparto y seguridad.
4. Simulacion de datos: matriz admin/superadmin, entradas y salidas de datos, canales web/bot/POS y concurrencia local.
5. Chatbot, Evolution y PWA: endpoints HTTP, manifest, service worker, estado del bot y Evolution API local.
6. Frontend visual/responsive: menu, detalle, carrito, checkout, admin, POS y repartidor en desktop y movil con Playwright.
7. Documentacion de resultados: fallos encontrados, correcciones aplicadas, pruebas verdes y riesgos pendientes.

Fallas encontradas y corregidas:
- `tests/test_flujos_completos.py` tenia errores de fixture y referencias de datos que impedian probar flujos completos. Se corrigio para recargar entidades vigentes desde la sesion activa y cubrir 56 escenarios reales.
- `loyalty_service.py` fallaba si `pedido.puntos_usados` venia ausente o no numerico en objetos transitorios. Se hizo robusta la idempotencia sin cambiar el flujo real de canje.
- `models.py` descontaba lotes antes de confirmar disponibilidad total. Ahora valida stock total primero, evitando que un intento fallido deje lotes en cero.
- `routes/preparador.py` y `routes/repartidor.py` usaban disponibilidad cacheada de `current_user`. Ahora leen disponibilidad fresca desde BD y un empleado offline no ve ni toma pedidos nuevos.
- `tests/test_integration.py` tenia dos pruebas de chatbot que seteaban configuracion sin persistirla como lo hace el formulario real. Se ajusto el test para validar la URL configurada correctamente.
- `tests/visual/oxidian.spec.js` usaba una clave antigua del bot de simulacion. Ahora usa `SIM_BOT_KEY` o `local-evolution-bot-key`.
- `playwright.config.js` corria la prueba visual en paralelo sobre la misma BD y service worker, causando abortos intermitentes. Ahora corre estable con 1 worker.

Verificacion ejecutada:
- `python -m py_compile app.py config.py extensions.py models.py services.py pricing_service.py loyalty_service.py routes/*.py scripts/*.py tests/test_integration.py tests/test_flujos_completos.py tests/test_loyalty.py` -> ok.
- Validacion JS/manifest/YAML/Jinja -> ok.
- `pytest -q tests/test_flujos_completos.py` -> 56 passed.
- `pytest -q tests/test_models.py tests/test_pricing.py tests/test_loyalty.py tests/test_colombian_catalog_seed.py tests/test_data_entry_integrity.py tests/test_day_simulation.py tests/test_massive_operations.py` -> 51 passed.
- `pytest -q tests/test_integration.py` -> 188 passed.
- `pytest -q` -> 355 passed.
- `python scripts/simulate_data_matrix.py` -> 57/57 OK.
- `python scripts/simulate_against_local.py --url http://127.0.0.1:5070 --days 1 --daily-base 20 --workers 6 --pool-size 24 --bot-key local-evolution-bot-key` -> 15/15 pedidos creados; web 6/6, bot 6/6, POS 3/3.
- `npx playwright test` -> 6 passed.
- `curl http://127.0.0.1:5070/` -> 200 OK.
- `curl http://127.0.0.1:5070/static/manifest.webmanifest` -> 200 OK.
- `curl http://127.0.0.1:5070/sw.js` -> 200 OK.
- `curl http://127.0.0.1:3000/api/status` -> 200 OK, bot conectado a Evolution con instancia `oxidian`.
- `curl http://127.0.0.1:8080/` -> 200 OK, Evolution API funcionando.

Resultado:
- No quedan fallos conocidos en la auditoria automatizada ejecutada.
- El sistema queda probado por datos, menu, carrito, checkout, empleados, finanzas, notificaciones, chatbot, PWA, simulacion local y vistas principales.

Riesgos pendientes no automatizables:
- Prueba manual en telefono real Android/iOS para instalacion PWA, porque el evento de instalacion depende del navegador y del dispositivo.
- Prueba WhatsApp real con QR escaneado en Evolution y envio real desactivando cualquier modo simulado.
- Prueba de varios dias en el servidor Cosmos real despues del despliegue, con backups y variables definitivas.
