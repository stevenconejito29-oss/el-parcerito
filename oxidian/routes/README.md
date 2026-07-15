# Rutas Flask

Los archivos de este directorio son adaptadores HTTP. Autentican, validan la
entrada, llaman modelos/servicios y construyen una respuesta. Una regla usada
por web y WhatsApp no debe vivir duplicada aquí.

## Mapa rápido

| Archivo | Área |
|---|---|
| `public.py` | Tienda pública, carrito, checkout y páginas legales. |
| `auth.py` | Login, MFA y sesión de empleados. |
| `admin.py` | Operación diaria y CRUD del administrador. |
| `superadmin.py` | Configuración y acciones globales. |
| `preparador.py` | Cocina y preparación de pedidos. |
| `staff.py` | Herramientas compatibles de almacén/preparación. |
| `repartidor.py` | Ruta, entrega y comisiones. |
| `pos.py` | Venta presencial. |
| `api_bot.py` | API privada consumida por `chat/bot.js`. |
| `presencia.py` | Estado online de roles operativos. |
| `push.py` | Suscripciones y notificaciones PWA. |
| `uploads.py` | Acceso controlado a archivos subidos. |
| `marketing.py` | Funciones de marketing accesibles por admin; no es un rol. |
| `proveedor.py` | Compatibilidad histórica; su flujo externo está desactivado. |

## Antes de añadir un endpoint

- Reutilizar el blueprint del área.
- Aplicar el decorador de autenticación/permiso correspondiente.
- Validar en servidor aunque la interfaz ya limite la entrada.
- Delegar transacciones y cambios de estado a la regla existente.
- Conservar el nombre del endpoint si ya hay plantillas o integraciones que lo
  referencian.
- Añadir una prueba de éxito y otra de autorización o entrada inválida.

El mapa completo está en [`../../docs/PROJECT_STRUCTURE.md`](../../docs/PROJECT_STRUCTURE.md).
