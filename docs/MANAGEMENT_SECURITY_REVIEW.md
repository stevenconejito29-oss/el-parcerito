# Revisión de finanzas, productos y acceso — 10 de septiembre de 2026

Revisión del código local de El Parcerito, con correcciones y pruebas sobre
datos sintéticos. No se ha desplegado ni modificado la base de producción.

## Correcciones

| Área | Hallazgo | Resultado |
|---|---|---|
| Finanzas | El filtro de módulos no incluía `/admin/finanzas` ni sus cierres. | El admin necesita el permiso `caja`; superadmin conserva acceso. |
| Caja | El alta manual aceptaba `NaN` e infinito al convertir a `float`. | Solo admite importes finitos y positivos antes de registrar movimientos. |
| Productos | El coste aceptaba valores no finitos y convertía texto inválido en ausencia de coste. | Coste opcional, finito y no negativo; las entradas inválidas muestran error. |
| Precios | El cambio rápido admitía valores no finitos y podía poner precio a un producto exclusivo de canje. | Se valida el importe y se conserva el precio cero del canje exclusivo. Los cambios normales mantienen historial y sincronización. |
| Extras y suplementos | La conversión monetaria no rechazaba explícitamente valores no finitos. | Conversión compartida con error de validación y manejo del error al añadir suplementos a combos. |
| Combos | El validador aceptaba `NaN` como precio o porcentaje de descuento. | Precio y descuento deben ser finitos antes de calcular o guardar el combo. |
| Contraseñas | `set_password` no incrementaba la versión de sesión al reemplazar credenciales. | Cada reemplazo invalida las sesiones previas en su siguiente petición, mediante el control existente en `app.py`. |
| Login | La validación de `next` admitía rutas con barras invertidas o controles que los navegadores pueden interpretar de otra manera. | Destinos internos explícitos, sin host externo ni caracteres ambiguos; mismo criterio para el retorno por Referer. |
| Doble factor | Una intención MFA incompleta podía omitir comprobaciones de antigüedad o contraseña. | Se exige fecha válida dentro de cinco minutos y vínculo con la contraseña actual. |
| Configuración MFA | Un POST con secreto de configuración antiguo podía sustituir un MFA ya activo. | Se impide sobrescribirlo y se elimina el secreto de configuración residual. Alta y desactivación tienen límite de intentos. |

## Integridad y controles revisados

- Finanzas: separación de caja y margen, costes congelados de productos y
  combos, ingresos por envío, devoluciones y comisiones/liquidaciones de socios.
  Las pruebas existentes cubren estos criterios; no constituyen conciliación
  contable de los movimientos reales de la tienda.
- Inventario: pruebas de capacidad, reserva, liberación, agotamiento y reserva
  concurrente de la última tanda.
- Histórico: nueva prueba HTTP de eliminación de un producto vendido; se
  archiva y se conservan nombre y precio del pedido. También se mantienen las
  pruebas de snapshots que evitan heredar cambios posteriores del catálogo.
- Roles: restricciones de gestión entre admin y superadmin, protección de la
  última cuenta superadmin, matriz de permisos, exclusión del cliente del login
  interno y rechazo de cuentas inactivas en el cargador de sesión.
- Seguridad HTTP: configuración de CSRF, cookies y MFA, más las pruebas
  existentes de cabeceras y controles de las API. Los permisos de WhatsApp
  siguen dependiendo del perfil activo registrado, según las pruebas del bot.

## Validación

Las 14 regresiones nuevas están en
`oxidian/tests/test_management_security_review.py`. Usan una base aislada,
comprueban rechazo sin escrituras y conservan casos válidos de caja, precios y
MFA. La prueba de archivo activa claves foráneas en SQLite.

La batería completa se ejecuta con `scripts/test-project.sh`, usando PostgreSQL
temporal para las pruebas que requieren la factoría real de la aplicación.
El chequeo `predeploy_check.py --deployment cosmos` sobre la configuración local
terminó sin errores bloqueantes.

Resultados: **866 pruebas Python y 226 pruebas del bot aprobadas**. Tras el
último ajuste del validador de combos se repitieron las **42 pruebas de combos**
y las **14 regresiones de esta revisión**, también aprobadas. `git diff --check`
terminó sin errores. El contenedor PostgreSQL temporal se retiró al finalizar.

Esta revisión no es una prueba de penetración del servidor publicado ni una
auditoría de dependencias. Quedan fuera la comprobación en vivo de HTTPS/proxy,
la conciliación de datos reales y el comportamiento de servicios externos en
producción. No permite afirmar ausencia absoluta de vulnerabilidades.
