# Auditoría preproducción: roles, PWA, rutas y finanzas

Fecha: 9 de agosto de 2026.

## Veredicto

El sistema todavía necesita un ensayo operativo en dispositivos reales antes
de producción. Cocina, preparación y reparto disponen de una interfaz operativa
responsive y sus acciones principales tienen contratos automatizados. El
seguimiento del repartidor que podía responder 500 quedó implementado en esta
iteración con almacenamiento efímero y consentimiento explícito.
Finanzas tiene reglas y pruebas, aunque su interfaz heredada mezclaba resumen,
tareas y contabilidad sin una jerarquía clara.

## Verificado en esta pasada

- El login limita intentos, evita enumeración evidente de usuarios, restringe
  roles autenticables, valida redirecciones y obliga MFA a admin y super_admin.
- Cocina/preparación separa pendientes y trabajo iniciado, muestra urgencia,
  alergias, modalidad, pago, responsable y conexión en vivo con fallback.
- Reparto permite disponibilidad, recogida, navegación, contacto, cobro,
  confirmación y ticket. La ruta múltiple usa Google Routes desde el servidor
  para ordenar por red vial y tráfico cuando está configurado; conserva un
  fallback explícito por cercanía si el proveedor no responde.
- Web Push tiene suscripción VAPID, estados de permiso, baja por dispositivo,
  service worker, reintentos/outbox y destinos por rol.
- Finanzas calcula ingresos, egresos, pendientes, cierres, margen y
  liquidaciones mediante rutas probadas. El rediseño de esta pasada no cambia
  ninguno de esos cálculos.

Validación dirigida: 81 pruebas de autenticación, reparto, PWA, contratos
front-end y finanzas, todas correctas. El conjunto del chatbot también queda
verde tras reparar la migración lógica del warm-up, la lectura de ubicaciones
de WhatsApp y adaptar sus contratos a la revalidación de identidad.

## Bloqueantes encontrados y estado

### P0 resuelto en código — Seguimiento del repartidor

Se añadió `RiderLocation`, su migración, endpoint exclusivo del repartidor,
validación de precisión, consentimiento visible y envío con `watchPosition`
solo mientras hay una ruta activa y la pantalla está abierta. Solo se guarda el
último punto; se elimina al detener GPS o terminar la última entrega.

Queda por certificar en dispositivos reales:

1. comportamiento de GPS con Android/iPhone bloqueados;
2. consumo de batería durante un turno;
3. pérdida y recuperación de cobertura;
4. caducidad del último punto vista desde WhatsApp.

La geolocalización web requiere contexto seguro y puede quedar bloqueada
por `Permissions-Policy`: https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Permissions-Policy/geolocation

### P0 — Ensayo real de notificaciones

Los contratos y el outbox están cubiertos, pero antes de producción hay que
probar recepción con pantalla apagada en Android y con la PWA instalada desde
Inicio en iOS, incluyendo rotación de suscripción, 410/404 del proveedor push y
apertura del pedido correcto.

## Riesgos altos y trabajo siguiente

- **Ruta vial real:** integración terminada en código. Antes de activar hay que
  definir `GOOGLE_ROUTES_API_KEY` solo en el servidor, habilitar Routes API,
  restringir la clave al servicio y vigilar cuota/facturación. El endpoint fija
  como destino la parada más alejada y permite que Google reordene las demás:
  https://developers.google.com/maps/documentation/routes/waypoint-types
- **Límites de Maps:** el panel ahora bloquea selecciones que podrían perder
  waypoints en móvil y explica que se debe dividir la ruta.
- **Notificaciones:** medir permiso aceptado, entrega, apertura y suscripciones
  inválidas. El aviso previo debe explicar valor antes de pedir permiso; es el
  patrón recomendado por web.dev:
  https://web.dev/articles/push-notifications-permissions-ux
- **Finanzas:** validar con la persona que hace el cierre diario cinco casos
  reales: efectivo con cambio, digital pendiente, devolución, liquidación de
  socio y descuadre. Añadir gráfico solo si responde una decisión; no como
  decoración.
- **Cocina:** hacer prueba de estrés con 20–30 tickets, sonido real, pérdida de
  SSE, doble toque y dos cocineros sobre el mismo pedido. Confirmar que el
  estado por ítem en `localStorage` no se interpreta como verdad compartida.
- **Catálogo/venta:** sustituir imágenes faltantes o placeholders antes de la
  demo, revisar recorte 1:1 de todas las fotos y limitar el contenido inicial a
  destacados, categorías y productos comprables. La página de muestra actual
  es excesivamente larga cuando hay muchas fichas sin imagen.
- **Observabilidad:** alerta de errores 5xx en login, checkout, cambio de estado,
  push y finanzas; métrica de pedidos atascados por estado; simulacro de backup
  y restauración antes de abrir.

## Cambios de producto incluidos

- Nueva capa visual de aplicación para cocina/preparación y reparto: cabecera
  de turno, KPIs fijos, carriles operativos, tickets con jerarquía clara,
  acciones táctiles, estados críticos y adaptación móvil/tablet/standalone.
- PWA de equipo separada del menú cliente, con nombre, identificador, inicio y
  accesos rápidos específicos por rol.
- GPS voluntario del repartidor sin historial y solo durante rutas activas.
- Finanzas reorganizadas alrededor de caja, margen, pendientes y acciones; la
  liquidación de socios muestra de forma consistente que los extravíos no son
  ventas liquidables.
- Chatbot: estado de pedido sin prometer “tiempo real” cuando no hay GPS,
  ubicación reciente cuando existe, recepción útil de ubicaciones compartidas,
  warm-up persistente corregido y revalidación de identidad mantenida antes de
  cambios administrativos.

## Criterio de salida recomendado

No lanzar hasta que los dos P0 estén cerrados, el smoke test completo pase
contra una copia de producción y cocina/reparto completen un turno simulado en
dispositivos reales sin refrescos manuales ni pedidos perdidos.
