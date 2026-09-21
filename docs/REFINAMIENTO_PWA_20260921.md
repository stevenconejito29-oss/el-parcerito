# Refinamiento de PWA y operación — 21/09/2026

## Correcciones

- Combo: el resumen busca el contenedor real de cada opción; conserva nombres,
  tamaños y sabores. El mínimo y máximo de elecciones coincide en interfaz y
  servidor. El carrito muestra las cantidades elegidas, no las del componente
  base; su composición aparece abierta y expresa las unidades por combo.
- Cocina y preparación: resumen de productos visible con la comanda cerrada.
  Paleta clara estable, sin controles ni preferencia automática de día/noche.
- Puntos y recompensas: acceso propio en navegación, con saldos, canjes e
  historial separados; búsqueda y páginas de 30 resultados. Los ajustes de
  saldo requieren motivo, permiso, cliente activo y saldo no negativo; usan
  bloqueo transaccional, auditoría y protección contra reenvío del formulario.
- Página privada: superadmin elige pública o privada en configuración. Solo
  números expresamente autorizados pueden verificar por WhatsApp y acceder al
  menú, carrito, checkout y manifiesto PWA. Restablecer acceso o cambiar teléfono
  invalida sesiones anteriores y desactiva suscripciones push antiguas.
- Notificaciones: la verificación privada permite suscribirse antes del primer
  pedido. La prueba de avisos ahora recorre servidor, cola y proveedor push hacia
  el dispositivo que la solicita; no simula el envío con un aviso local.
- Impresión: USB/OTG y BLE conservan su conexión al marcar Listo; se añade puerto
  serie para impresoras ESC/POS, incluido Bluetooth clásico cuando el sistema y
  navegador lo exponen. Por seguridad, un puerto serie se selecciona de nuevo
  tras recargar; no se deduce su identidad por marca/modelo. Se mantienen las
  alternativas de red y diálogo del sistema. No existe compatibilidad universal
  con todas las impresoras ni se ha hecho una prueba física de hardware.
- Caché: precarga de recursos con versión; los estáticos sin versión requieren
  revalidación en el gateway. El menú no se recupera de HTML offline: evita
  enseñar un catálogo anteriormente público tras cambiar el acceso a privado.

## Despliegue

Backup previo de esta iteración:
`/home/panzeta/oxidian-backups/20260921-230753`.
Se conserva la modalidad delivery del combo real Mixxxx corregida en la primera
publicación, sin modificar sus precios ni pedidos históricos. El modo privado
permanece desactivado hasta que superadmin autorice clientes y lo habilite.
La prueba física del ticket y la recepción en el teléfono requieren el dispositivo
final; las pruebas automáticas no sustituyen esa comprobación.

## Validación

960 pruebas Python aprobadas; 85 escenarios de roles y 77 de PWA sin
desbordamientos ni errores de JavaScript. El combo incluye pruebas interactivas
de cantidades, mínimo de elecciones y resumen. El bot se verifica con Node 20,
la misma versión de CI; Node 26 local requiere recompilar su módulo SQLite.
