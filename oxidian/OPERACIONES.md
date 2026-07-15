# Guía operacional — El Parcerito de Carmona

> **Referencia histórica.** Incluye el antiguo flujo multi-proveedor, hoy
> desactivado. Para operación y despliegue vigentes consulta
> [`../docs/OPERATIONS.md`](../docs/OPERATIONS.md).

Cómo añadir bares, asignarles stock, configurar el WhatsApp para derivación
de clientes y operar el sistema de pedidos sin tocar código.

## 1. Cuentas mínimas del sistema

Tras el cleanup, los roles efectivos son:

| Rol | Para qué | Login obligatorio MFA |
|---|---|---|
| `super_admin` | Configuración global, integraciones, alta de bares | ✅ (en producción) |
| `admin` | Día a día: productos, pedidos, caja, marketing | ❌ |
| `preparacion` | Cocina + almacén unificados (rol propio único) | ❌ |
| `repartidor` | Entregas y comisiones | ❌ |
| `proveedor` | Operador de un bar — solo ve sus pedidos | ❌ |
| `cliente` | Cliente final | ❌ |

> El gate MFA se controla con `OXIDIAN_MFA_ENFORCED=1` en `.env.cosmos.local`.
> En desarrollo local está en `0` para iterar más rápido.

## 2. Añadir un bar (proveedor) nuevo

1. Login como `super_admin` → menú **🏪 Proveedores**.
2. **➕ Nuevo proveedor** → rellena:
   - **Nombre comercial**: ej. "Bar Centro".
   - **📱 WhatsApp directo del bar**: el número al que el chatbot redirige a los
     clientes cuando un pedido suyo ya no se puede cancelar automáticamente.
     Formato internacional: `+34 600 000 000`.
   - **Modelo de acuerdo**:
     - **A (`stock_proveedor`)** — el bar pone el stock. Nosotros le pagamos
       `precio_costo × unidades`. Nuestro margen = PVP − coste.
     - **B (`stock_propio_bar`)** — nosotros ponemos el stock; el bar solo
       prepara. Le pagamos `comision_pct % del PVP` por preparar.
   - **Comisión %**: solo aplica en modo B.
3. **Crear** → el sistema crea el proveedor.

## 3. Asignar productos (SKUs) al bar

Cada bar tiene **inventario independiente**. Aunque un producto exista
también en nuestro stock, el bar gestiona su propia existencia.

1. En el listado de Proveedores → **Editar** del bar.
2. Bajo "📦 Inventario del proveedor", añade SKUs:
   - **Producto**: elige del catálogo.
   - **Stock**: cantidad inicial disponible en el bar.
   - **Coste (€)**: lo que cuesta al bar producir esa unidad.
3. **Añadir SKU** — queda registrado para ese bar.

> Un SKU puede pertenecer a varios bares y al stock propio simultáneamente.
> Cada inventario es independiente: si el Bar A se queda sin Cocas, el Bar B
> sigue pudiendo vender la suya.

## 4. Crear un combo del bar

Los combos solo se pueden armar con productos del **mismo bar** (o 100%
propios). El sistema lo fuerza desde el constructor:

1. Admin → menú **🥟 Productos** → **Constructor de combo**.
2. **Origen del combo**: selecciona "Stock nuestro" o un bar concreto.
3. La grid de componentes se filtra automáticamente. Solo verás SKUs del
   origen elegido.
4. Arma las secciones (Base fija + opciones de elección).
5. Si cambias el origen a media construcción y los componentes ya añadidos
   ya no son válidos, el sistema te avisa con un alert.

## 5. Operador del bar (rol `proveedor`)

1. Super_admin → **Usuarios & Equipo** → **Nuevo usuario** con rol `proveedor`.
2. Editar ese usuario → enlazarlo al proveedor (bar) correspondiente.
3. El operador del bar entra con su email/password y solo ve:
   - **🍳 Pedidos a preparar** — los pedidos que le tocan a su bar.
   - **📦 Mi inventario** — puede ajustar stock y precio_coste de sus SKUs.

## 6. Flujo de un pedido del bar (resumen)

1. Cliente añade producto/combo del Bar X al carrito y paga.
2. Sistema descuenta del stock del Bar X (no del propio).
3. Si el pedido es 100% del Bar X → **NO se asigna preparador interno**, va
   directo a la cola del bar. Cuando el bar confirma "preparado", el pedido
   pasa automáticamente a "listo" y se asigna repartidor.
4. Si el pedido es mixto (propio + bar) → preparador interno prepara su parte
   y el bar la suya. Ambas confirmaciones son necesarias para pasar a "listo".
5. Repartidor recoge y entrega. Si el cliente cancela:
   - **Antes de empezar preparación** → cancelable vía chatbot WhatsApp.
   - **Después** → chatbot envía al cliente el WhatsApp directo del bar.

## 7. Configuración general

Super_admin → **Configuración global**. Cada campo se edita por separado.
Claves importantes:

| Clave | Qué controla |
|---|---|
| `NOMBRE_NEGOCIO` | Nombre que aparece en mensajes, tienda, etc. |
| `TELEFONO_NEGOCIO` | WhatsApp de El Parcerito (el principal del bot) |
| `DIRECCION_NEGOCIO` | Mostrado en /info del bot |
| `HORARIO_APERTURA` / `HORARIO_CIERRE` | Bloquean pedidos fuera de horario |
| `TIENDA_FORZAR_CERRADA` | "1" cierra la tienda inmediatamente |
| `CENTRO_LAT` / `CENTRO_LON` / `RADIO_ENTREGA_KM` | Cobertura geográfica básica |
| `BOT_API_KEY` | API key compartida Oxidian ↔ bot Node |
| `PUNTOS_POR_EURO` / `PUNTOS_CANJE_RATIO` | Programa de fidelidad |

## 8. Zonas de entrega

Super_admin → **Zonas**. Cada zona puede tener su propia geo (lat/lng/radio)
para asignación automática en checkout, o quedarse sin geo y el sistema usa
la primera por orden.

## 9. Backups

Programados a las **03:30 todos los días**. Mantiene los últimos 7 días en
`~/oxidian-backups/`. Para forzar uno manual:

```bash
bash /home/panzeta/Documentos/scripts/backup.sh
```

Para restaurar:

```bash
bash /home/panzeta/Documentos/scripts/restore.sh ~/oxidian-backups/<timestamp>
```

(Pide confirmación interactiva. Soporta `--dry-run` para verificar integridad
sin tocar nada.)

## 10. Cuentas de prueba (entorno local)

Password único: `test123456789`.

| Rol | Email |
|---|---|
| super_admin | `carmocream15@gmail.com` |
| admin | `admin@oxidian.com` |
| preparacion | `cocina@oxidian.com`, `staff@oxidian.com` |
| repartidor | `repartidor@oxidian.com` |
| cliente | `cliente@oxidian.com` |
| Bar El Parcerito | `bar@oxidian.com` |
| Bar Centro | `bar2@oxidian.com` |

## 11. Comandos básicos del stack

```bash
# Levantar
cd /home/panzeta/Documentos
docker compose --file oxidian/docker-compose.cosmos-local.yml \
               --env-file oxidian/.env.cosmos.local up -d

# Reconstruir oxidian tras cambios de código
docker compose --file oxidian/docker-compose.cosmos-local.yml \
               --env-file oxidian/.env.cosmos.local build oxidian

# Logs en vivo
docker logs -f oxidian-oxidian-1

# Estado
docker compose --file oxidian/docker-compose.cosmos-local.yml \
               --env-file oxidian/.env.cosmos.local ps
```

URL pública local: **http://localhost:5070** (o `http://192.168.1.41:5070`).

## 12. Chatbot — keywords reconocidas

El cliente puede escribir tanto el número de menú como palabras naturales.
Ejemplos válidos: "pedido", "cancelar", "estado", "menú", "cobertura",
"horario", "agente", "puntos", "tienda".

## 13. Modelo de derivación del chatbot

El cliente SIEMPRE habla con `+34633096706` (El Parcerito). Es nuestro único
número entrante. El bot, según el caso, deriva al cliente al WhatsApp del bar
correspondiente o lo deja en cola con nuestros agentes propios.

| Situación del cliente | El bot hace | Por qué |
|---|---|---|
| Pregunta menú, cobertura, puntos, horario… | Responde con la info y mantiene la conversación | Información estandarizada que no necesita humano |
| Pide cancelar pedido en estado `pendiente` | Cancela vía API y confirma al cliente | Acción automática segura |
| Pide cancelar pedido en estado avanzado | Envía `wa.me/<bar>` si lo despacha un bar activo; si es propio, lo pone en cola de admins | Decisión humana, debe coordinarse con quien lo está preparando |
| Reporta incidencia (`REPORTAR <texto>`) | Guarda el evento en el panel del bar (o admin); además, si es del bar, ofrece `wa.me/<bar>` para conversar directo | El bar tiene trazabilidad en su panel y puede contactar al cliente |
| Pide hablar con agente (`AGENTE`/`AYUDA`) | Si su último pedido activo es de un bar → envía `wa.me/<bar>`; si no, cola general | Conecta al cliente con quien realmente puede ayudarle |
| Cancelación falla por error inesperado | Si el pedido es del bar → `wa.me/<bar>`; si es propio → cola general | Mismo principio que arriba |

### Reglas del `bar_contacto` (responde el API `/api/bot/pedido/<id>`)

- Si TODOS los items del pedido vienen de UN bar activo con WhatsApp configurado y al menos 1 SKU activo → `{tipo: "bar", nombre, whatsapp_url}`.
- En cualquier otro caso (mixto, pedido propio, bar inactivo, bar sin SKUs activos, bar sin teléfono) → `{tipo: "propio", nombre: NEGOCIO, whatsapp_url: TELEFONO_NEGOCIO}`.

> El cliente nunca ve el nombre de un bar en el menú ni en el catálogo. El
> nombre del bar solo aparece cuando se le ofrece su WhatsApp directo para
> coordinar algo de un pedido específico.

### Comandos prácticos para el cliente

| Cliente escribe | Resultado |
|---|---|
| `MENU` / cualquier número 1-7 | Vuelve al menú principal |
| `ESTADO` o `2` | Consulta sus pedidos activos por su WhatsApp |
| `CANCELAR` o `CANCELAR 1024` | Inicia flujo de cancelación |
| `REPORTAR <texto>` | Reporta novedad sobre su último pedido activo |
| `REPORTAR #1024 <texto>` | Reporta sobre un pedido concreto |
| `AGENTE` o `7` | Pide hablar con persona; el bot deriva al bar correcto o cola general |
| `SI` / `NO` | Confirma o cancela una acción pendiente |
