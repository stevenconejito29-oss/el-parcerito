# Revisión de versiones, roles e impresión — 14 de septiembre de 2026

## Por qué no se ven todas las mejoras

La copia local parte de `2c115a6`; el checkout del servidor en
`/opt/oxidian-workspace` está limpio y parte de `53feb33`. Comparten el ancestro
`a219cb0`. Hay 60 commits exclusivos del lado local y 38 del servidor: no es
un simple retraso de caché ni una actualización que se pueda resolver con
`git pull` sin revisar conflictos.

Se compararon diez archivos del contenedor en ejecución con los locales:
bot, aplicación, controladores de cocina/reparto/admin/identidad, personalización,
impresora, estilos de empleados y service worker. Ninguno coincide. Por tanto,
las pruebas de la versión local no acreditan que esas mejoras estén publicadas.

La simulación de integración, con copia de los cambios locales pendientes,
produce conflictos en 51 archivos. Incluyen modelos, migraciones, finanzas y
pedidos, franjas, WhatsApp, chat web y plantillas. Está aislada en
`/tmp/parcerito-deploy-review-20260914`; **no es una versión publicable**.
El informe de conflictos está en `/tmp/parcerito-merge-review.log` y la
comparación de archivos activos en `/tmp/parcerito-live-version-review.json`.
No se ha modificado el código activo, las cuentas ni los pedidos del servidor.

Para publicar es necesario conciliar esas dos líneas, conservar las reglas
vigentes de cobro al recibir y reparto modular, ejecutar las pruebas sobre el
resultado, respaldar los datos, reconstruir la imagen con el Compose real y
validar salud/recorridos después. No copiar archivos aislados sobre producción:
la divergencia afecta también a controladores y modelo de datos.

## Identificación de WhatsApp comprobada en el servidor

- Dos perfiles activos de superadmin, ambos con teléfono y sin duplicidad entre
  teléfonos privilegiados en la comprobación realizada.
- La API interna de identidad devuelve correctamente `super_admin` para ambos.
  La firma/identidad almacenada en la caché del bot coincide con ambos perfiles.
- Dos clientes activos: la API interna devuelve correctamente `cliente`.
- No existe ningún perfil con rol `admin`; no se debe conceder ese menú a un
  teléfono ajeno a un perfil activo de ese rol.
- Estas son consultas internas de lectura; no prueban la recepción de un
  mensaje real de Evolution/WhatsApp ni envían mensajes a personas.

El dominio HTTPS responde y las comprobaciones de Docker muestran la aplicación,
PostgreSQL y Evolution saludables. La IP por HTTP no es una URL adecuada para
PWA operativa: USB/Bluetooth y GPS dependen de contexto seguro y permisos.

## Correcciones locales de impresión

El botón USB estaba presente, pero no existía `pairUSB()` ni un listener para
su acción; además, `Permissions-Policy` denegaba USB en todas las rutas.
El ticket independiente tenía un botón de impresión sin listener.

Ahora se implementa:

- Selección explícita USB de impresora ESC/POS compatible, apertura y reclamación
  de interfaz, salida bulk y verificación de transferencia completa.
- Bluetooth BLE con escritura compatible con características con/sin respuesta,
  bloques de 20 bytes y restauración únicamente de la impresora elegida.
- Preferencia local preservada frente a preferencias de otro dispositivo del
  mismo operador; la preferencia del servidor nunca concede permiso al hardware.
- Exclusión de envíos locales simultáneos para no intercalar tickets. No se
  envía HTML de login a la impresora como si fuera un ticket.
- Permisos USB/Bluetooth para el propio origen en rutas operativas de cocina/POS;
  permanecen deshabilitados en rutas públicas. Los endpoints de ticket conservan
  autorización por usuario y pedido.
- Opciones del modal según las capacidades del navegador: USB, BLE, impresora
  del negocio si está configurada, e impresión del sistema/AirPrint.
- Botón del ticket independiente funcional, modal con teclado/foco, mensajes
  visibles, controles de conexión deshabilitados cuando no están soportados.

## Compatibilidad real

| Entorno | Opción |
|---|---|
| Android con navegador WebUSB y USB host/OTG | USB directo si la impresora expone interfaz ESC/POS compatible y el sistema permite reclamarla. |
| Navegador con Web Bluetooth | BLE compatible; Bluetooth clásico/SPP no funciona mediante esta API. |
| Safari/PWA en iPhone | Diálogo del sistema con impresora AirPrint compatible, o envío a la impresora de red configurada en el negocio. |
| Impresora USB/clásica sin soporte web | Requiere un equipo/servicio de impresión puente o integración del fabricante. |

Fuentes de plataforma: [WebUSB en Chrome](https://developer.chrome.com/docs/capabilities/usb),
[posición de WebKit sobre APIs de periféricos](https://webkit.org/tracking-prevention/),
[AirPrint de Apple](https://support.apple.com/es-es/109349).
No se promete compatibilidad con todos los modelos de móvil e impresora.
Falta conocer y probar el modelo físico de la impresora del negocio.

## Validación

- Suite general: 895 pruebas Python y 228 del bot aprobadas.
- Después de añadir las pruebas de preferencias USB: 18 pruebas de preferencias
  de impresora/cabeceras aprobadas (incluyen dos regresiones nuevas).
- Navegador con periféricos simulados: USB, BLE, transferencia incompleta,
  exclusión de tickets simultáneos, reconexión exacta, rechazo de HTML de login,
  alternativa de red y botón de impresión del ticket independiente.
- 50 escenarios visuales de roles en Chromium y 50 en WebKit 18.4: sin
  desbordamientos ni errores JS; contraste de controles de cocina comprobado.
  Las vistas incluyen superadmin, configuración, admin, productos, personal,
  finanzas, cocina, preparación y repartidor, en móvil/escritorio y claro/oscuro.
- Es una comprobación de motores y tamaños de pantalla, no una prueba física
  de todos los teléfonos Android/iPhone. No hubo impresión de papel real.

Desde `oxidian/`, con `PLAYWRIGHT_CHROMIUM_EXECUTABLE` definido:

```bash
../.venv/bin/python scripts/review_role_views.py
node scripts/test_thermal_printer.mjs
node scripts/review_role_views.mjs
REVIEW_BROWSER=webkit node scripts/review_role_views.mjs
```

WebKit necesita estar instalado con las dependencias de Playwright.
Los renderizadores usan SQLite en memoria y datos ficticios.
