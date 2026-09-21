# Integración e impresión — 15 de septiembre de 2026

La divergencia local/servidor se resolvió conservando ambas historias y los
cambios legítimos pendientes. El Compose mantiene las redes reales del servidor;
la imagen se construye desde la raíz e incluye Flask y el bot Node.
El resultado de validación y publicación está en PRODUCTION_READINESS_REVIEW.md.

La cocina dispone de impresión del sistema y conexión directa USB/BLE cuando el
navegador y la impresora lo permiten. Los permisos requieren gesto del usuario y
contexto seguro HTTPS. iPhone utiliza la impresión del sistema/red compatible;
no se garantiza WebUSB o Web Bluetooth en Safari.

Se probaron selección, reconexión y rutas de error con hardware simulado. Sigue
pendiente vincular y probar la impresora física del negocio. El ticket conserva
los datos del pedido confirmado y distingue recogida de reparto.
