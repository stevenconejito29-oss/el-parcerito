# Estructura actual del proyecto

El Parcerito es un monorepo con una aplicación Flask y un proceso Node para
WhatsApp. PostgreSQL conserva el negocio; Redis se usa para estado efímero y
Evolution API conecta WhatsApp.

## Mapa del repositorio

```text
el-parcerito/
├── AGENTS.md              reglas de trabajo para futuras sesiones
├── README.md              inicio rápido
├── docs/                  documentación vigente y auditorías generadas
├── oxidian/               aplicación web, API, PWA y despliegue
│   ├── app.py             factoría Flask, blueprints y arranque
│   ├── models.py          esquema ORM, estados y roles
│   ├── services.py        reglas de negocio transversales
│   ├── *_service.py       servicios de dominio específicos
│   ├── config*.py         configuración de entorno y valores iniciales
│   ├── routes/            controladores HTTP por área
│   ├── templates/         vistas Jinja por área o rol
│   ├── static/            CSS, JavaScript, imágenes y service worker
│   ├── tests/             pruebas Python de flujos y permisos
│   └── scripts/           migración, mantenimiento, QA y operación
├── chat/                  chatbot WhatsApp
│   ├── bot.js             orquestación y máquina conversacional
│   ├── texts.js           textos y opciones compartidas
│   ├── handlers/          manejadores extraídos por responsabilidad
│   ├── utils/             contexto y utilidades conversacionales
│   └── test/              pruebas Node del chatbot
└── scripts/               respaldo, restauración y autodespliegue del stack
```

Los directorios complejos tienen índices propios:

- [`oxidian/routes/README.md`](../oxidian/routes/README.md)
- [`chat/README.md`](../chat/README.md)

## Capas y dependencias

El flujo normal es:

```text
Navegador/PWA o WhatsApp
        ↓
routes/public.py, routes/admin.py o routes/api_bot.py
        ↓
services.py y servicios de dominio
        ↓
models.py / PostgreSQL
        ↓
outbox, push o respuesta HTTP/WhatsApp
```

Una ruta valida entrada, aplica autenticación/autorización, invoca una regla y
construye la respuesta. Las decisiones de estado de pedido, stock, precios,
puntos o clientes deben estar en modelos/servicios para que web y bot compartan
el mismo comportamiento.

## Roles vigentes

La fuente de verdad es `models.ROLES` y `models.ROLES_AUTENTICABLES`.

| Rol | Acceso | Responsabilidad principal |
|---|---|---|
| `super_admin` | Login + MFA en producción | Configuración global, seguridad y supervisión. |
| `admin` | Login | Operación de tienda, catálogo, pedidos, caja y clientes según permisos. |
| `cocina` | Login | Pedidos inmediatos de comida o almacén según el nicho activo. |
| `preparacion` | Login | Pedidos programados, encargos y preparación retail. |
| `repartidor` | Login | Ruta, contacto operativo, entrega y comisiones. |
| `cliente` | Sin panel autenticado | Identidad comercial asociada al teléfono y a sus pedidos. |

`staff` es un alias de compatibilidad para preparación. `marketing` y
`proveedor` conservan rutas/modelos históricos, pero no son roles autenticables
vigentes. No deben reactivarse accidentalmente desde navegación o seeds.

## Áreas HTTP

| Módulo | Responsabilidad |
|---|---|
| `public.py` | Catálogo, carrito, checkout, cuenta/identidad pública y legales. |
| `auth.py` | Inicio de sesión, MFA y cierre de sesión interno. |
| `admin.py` | CRUD y operación diaria de administradores. |
| `superadmin.py` | Controles globales reservados. |
| `preparador.py` | Flujo visual de cocina y preparación. |
| `staff.py` | Compatibilidad y herramientas de almacén para preparación. |
| `repartidor.py` | Reparto y cierre de entrega. |
| `pos.py` | Venta presencial. |
| `api_bot.py` | Contrato privado entre chatbot y negocio. |
| `push.py`, `presencia.py` | Notificaciones y presencia operativa. |
| `uploads.py` | Entrega controlada de archivos. |

## Nichos y módulos

La tienda opera en un solo nicho a la vez: `comida` o `retail`. La decisión se
obtiene mediante `store_config.py`; las vistas reciben capacidades derivadas y
no deberían inferir el nicho por textos, categorías o URLs. Los módulos activos
se controlan mediante configuración y permisos. Ocultar un botón no sustituye
la validación del servidor.

## Datos y compatibilidad

- PostgreSQL es la fuente de verdad de usuarios, clientes, pedidos y finanzas.
- El teléfono normalizado identifica al cliente entre web y WhatsApp.
- SQLite bajo `db/` es estado local del bot y no se versiona.
- Las migraciones defensivas y modelos legacy pueden ser necesarias para leer
  instalaciones anteriores. Su retirada exige auditoría de datos y migración.
- Backups, imágenes subidas, secretos, logs y dependencias son artefactos de
  entorno; no forman parte del código fuente.

## Módulos grandes

`models.py`, `services.py`, `routes/admin.py`, `routes/api_bot.py` y `chat/bot.js`
concentran bastante código por evolución histórica. Sus secciones y contratos
están activos y no deben dividirse mediante un movimiento masivo. La extracción
segura se hace por dominio completo (funciones, pruebas y consumidores en una
misma iteración), manteniendo adaptadores compatibles en el módulo original.
