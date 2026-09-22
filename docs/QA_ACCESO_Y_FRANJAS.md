# Verificación de acceso privado y reparto

La tienda conserva sus clientes, pedidos y datos contables. El interruptor de
acceso aparece en el inicio de superadmin y en Ajustes → Acceso de clientes;
ambos guardan la misma configuración y exigen permiso de superadmin.

## Recorrido de operación

1. En Clientes, registrar un teléfono o autorizar un cliente existente.
2. Activar «Tienda privada» en el panel principal de superadmin.
3. Desde un navegador de cliente, solicitar el código e introducirlo. El menú,
   las APIs del catálogo y el manifiesto quedan bloqueados antes de verificar.
4. Si la sesión caduca, verificar otra vez desde el navegador vinculado. La
   identidad persistente no equivale a una sesión autorizada. Si se borran todas
   las cookies o se usa otro perfil de navegador, superadmin debe restablecer
   el vínculo en Clientes.
5. En Modalidades y franjas, crear los horarios antes de activar su venta.
   Seleccionar inmediato, franjas o mixto en esa única pantalla. En checkout,
   el cliente elige día y horario. Cocina prepara por salida y reparto revisa
   los productos y el cobro antes de salir.

## Fallos corregidos

- Scripts sin nonce que la CSP bloqueaba en checkout, cocina y reparto.
- Respuestas de archivos estáticos que renovaban sesiones anteriores y podían
  sobrescribir un login o una verificación recientes.
- Vínculo de dispositivo que desaparecía al caducar la sesión. Se migra a una
  cookie firmada, HttpOnly, SameSite=Lax y Secure en producción.
- Formulario de franjas bloqueado mientras el módulo estaba apagado.
- Selector duplicado de modalidad que no enviaba sus claves de configuración.
- Error de carga de horarios oculto y validación de franja sin aviso visible.
- Controles flotantes sobre pedidos y textos recortados en pantallas pequeñas.

## Reproducir en local

```sh
.venv/bin/python oxidian/scripts/serve_flow_review.py
# En otra terminal:
cd oxidian
node scripts/review_live_access.mjs
```

El servidor escucha únicamente en 127.0.0.1:5079, crea una base SQLite en memoria
y usa usuarios sintéticos. La entrega de WhatsApp se sustituye por una captura
local del código de prueba. La prueba de navegador usa CSRF, CSP y formularios
reales: activación/desactivación, OTP, caducidad de sesión, manifiesto, dos
franjas, extras, sabores, combo, carrito y vistas de cocina/reparto.

La recepción del WhatsApp en un teléfono real y la instalación en dispositivos
físicos requieren una comprobación operativa adicional. El navegador de QA no
certifica la entrega de mensajes ni la compatibilidad de impresoras físicas.

La auditoría PostgreSQL de solo lectura (`audit_data_integrity.py`) comprueba
relaciones huérfanas. `combo_audit.audit_combo` comprueba los grupos, componentes,
modalidades, tamaños y sabores del combo existente. La prueba de finanzas
`seed_finance_stress.py` usa exclusivamente una base QA y verifica ingresos,
comisiones, cancelaciones y snapshots de zonas en 400 pedidos sintéticos.
