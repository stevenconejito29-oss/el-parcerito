# Revisión de acceso privado, catálogo y delivery

Fecha: 20 de septiembre de 2026.

## Alcance y límites de esta revisión

La revisión utiliza pruebas aisladas y una auditoría de lectura del catálogo real.
El combo Mixxxx tiene nueve componentes y dos grupos; sus relaciones son válidas,
pero anuncia recogida aunque algunos componentes solo permiten delivery. La
corrección operativa limita la modalidad del combo a delivery, sin cambiar precios
ni snapshots de pedidos. El respaldo previo está verificado en el servidor.

## Recorrido del cliente

1. Superadmin registra clientes en **Clientes registrados**, con nombre y teléfono
   internacional, o autoriza explícitamente los existentes. Estar registrado por
   una compra anterior no concede acceso privado.
2. En **Configuración → Operación → Acceso de clientes**, activa
   `ACCESO_CLIENTES_REGISTRADOS`. Su valor inicial es `0`. Admin no puede cambiarlo.
3. El visitante ve una pantalla con un único campo: teléfono. La solicitud nunca
   crea cuentas. Los números desconocidos e inactivos reciben el mismo mensaje.
4. El cliente recibe un código de un solo uso por WhatsApp y lo introduce en la
   siguiente pantalla. Se reutiliza el emisor transaccional de OTP, sus límites de
   intentos, caducidad y espera entre reenvíos, también si los puntos están apagados.
5. Después de verificar puede consultar catálogo, añadir productos e instalar la
   PWA. El checkout muestra el teléfono verificado y rechaza sustituirlo por otro.
6. El primer acceso verificado vincula el teléfono a ese navegador. Otro
   dispositivo requiere que superadmin use **Restablecer dispositivo** y una
   nueva verificación. El cliente puede cerrar su acceso. Superadmin puede bloquearlo; la versión de
   sesión impide que una cookie anterior vuelva a servir después de reactivarlo.
7. Apagar la opción recupera el acceso público. Los paneles internos conservan sus
   permisos. El seguimiento y cancelación de pedidos existentes conservan su
   autorización propia para no dejar pedidos activos sin atención.

La protección importante está en el servidor, incluido el chat web y las APIs
públicas del catálogo. No se puede garantizar que el navegador impida crear un
acceso directo manual; instalar un icono no concede permiso para pedir. Las
respuestas privadas no se guardan en la caché HTML de la PWA. La sesión sigue
sujeta a la duración de cookies configurada en la aplicación.

WhatsApp debe estar operativo antes de encender la opción. No se incorporan
mensajes promocionales ni un asistente de pedidos por WhatsApp. La verificación
del primer pedido conserva su flujo existente; esta iteración no elimina ese
control ni modifica las reglas del canje de puntos.

## Flujo operativo revisado

| Paso | Regla que debe mantenerse | Resultado de esta iteración |
| --- | --- | --- |
| Elegir catálogo y combo | Un origen de inventario, opciones coherentes y modalidades compatibles | Validación del constructor reforzada y diagnóstico de composiciones guardadas. |
| Carrito | Stock, cantidades, tamaños y sabores se verifican en servidor | Las cantidades inválidas del constructor ya no se convierten silenciosamente en una unidad. |
| Identificación | Teléfono normalizado y verificación separada del cobro | Acceso privado y teléfono vinculado al checkout. |
| Entrega | Inmediato, franjas y recogida mantienen sus procesos propios | El constructor rechaza mezclar tipos de entrega o anunciar modalidades que los componentes no admiten. |
| Preparación | No empezar el primer pedido antes de la verificación requerida | El panel administrativo indica «verificar cliente» cuando corresponde. |
| Pedido listo | Distinguir recogida de reparto | La siguiente acción dice «entregar recogida» o «salir a reparto». |
| Reparto y entrega | Asignación, ruta, confirmación y cobro conservan sus permisos | Cobertura de la suite existente y revisión visual de cocina, preparación y repartidor. |
| Fidelización | Otorgar al entregar; no generar puntos por cancelaciones | Se conservan sus reglas y pruebas; no se cambia el saldo de clientes. |

Los paneles ya contienen herramientas secundarias desplegables. No se ha hecho
una reescritura global: ocultar información operativa necesaria podría aumentar
errores. La revisión visual incluye móvil de 320 y 375 px, horizontal, escritorio
y tema oscuro. Las acciones de clientes se distribuyen sin cortar sus rótulos.

## Banners

- Un archivo fallido ya no puede causar el borrado de una URL local aportada en
  el formulario como si acabara de subirse.
- Una subida inválida se informa; no se presenta como actualización correcta.
- Reemplazar o eliminar un banner conserva imágenes compartidas con otros
  banners o productos. La limpieza se limita a archivos del directorio de banners.
- Un fallo al limpiar la imagen antigua no borra la nueva después de un commit.
- Se conservan los destinos Inicio, Menú y Checkout, y el orden existente.

## Combos y relaciones

- La reconstrucción usa el ORM para limpiar relaciones de sabores y tamaños,
  incluso cuando el motor no ejecuta cascadas. No borra productos base ni sabores.
- No persiste grupos declarados que se hayan quedado sin componentes.
- Rechaza referencias a grupos inexistentes y grupos de elección distintos con
  el mismo nombre, que antes podían mezclar selecciones en la tienda.
- Rechaza IDs inválidos y cantidades cero o no numéricas.
- Admin y socio utilizan la misma comprobación de modalidad y tipo de entrega.
- La ficha del combo señala grupos de otro combo, discrepancias de selección,
  componentes no disponibles, orígenes incompatibles y tamaños/sabores inválidos.
- Guardar desde el editor completo reconstruye la composición validada. No se
  reparan automáticamente los registros de producción ni los snapshots históricos.

## Puntos: configuración existente confirmada

Superadmin dispone de activación del módulo, unidades ganadas por euro y compra
mínima; también nombre del club, rótulo de navegación, singular/plural, mensaje,
icono y emoji. Los productos definen si admiten canje y su coste en puntos.
Los puntos se canjean por productos, no por descuentos monetarios. Los combos
con selección no admiten canje directo, para evitar recompensas sin configuración
completa. Estas restricciones no se han eliminado.

## Tickets y validación

Los tickets HTML y ESC/POS usan los datos guardados al comprar, con tamaños,
sabores, cantidades, composición, estado del cobro y nombre configurable de puntos.
La impresión USB/BLE se inicia al marcar Listo y conserva la conexión durante la
confirmación. Un reintento de la transición no vuelve a solicitar impresión.
La vinculación inicial requiere intervención del operador; Bluetooth debe ser
compatible con BLE y el navegador debe ofrecer la API correspondiente.

La suite estándar con PostgreSQL de pruebas ha aprobado 955 pruebas Python y
228 Node antes del último test de impresión al marcar Listo. También pasan las
simulaciones USB/BLE y se revisaron 70 escenarios visuales. La prueba física de
impresora, la recepción real del OTP y la instalación en el teléfono del cliente
requieren sus dispositivos. El modo privado no se activa automáticamente durante
el despliegue; superadmin debe autorizar clientes y habilitar la opción.
