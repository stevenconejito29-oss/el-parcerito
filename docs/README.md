# Documentación de El Parcerito

Este directorio es la entrada oficial y vigente del proyecto. Su objetivo es
separar claramente la operación actual de notas históricas y capturas generadas.

## Documentos vigentes

| Documento | Para qué sirve |
|---|---|
| [Estructura del proyecto](PROJECT_STRUCTURE.md) | Componentes, capas, roles y recorrido de una petición. |
| [Flujo vigente de pedidos](ORDER_FLOW.md) | Estados, destino por rol y barreras operativas. |
| [Revisión de delivery e interfaces](DELIVERY_UX_REVIEW.md) | Mejoras aplicadas, validación y siguientes iteraciones por etapa. |
| [Experiencia del cliente: PWA y WhatsApp](CUSTOMER_EXPERIENCE_REVIEW.md) | Menú, carrito, chat, responsabilidades de cada canal y comprobaciones realizadas. |
| [Administración, WhatsApp y reparto](ADMIN_STRUCTURE_REVIEW.md) | Alcance del bot, acceso por perfiles, modalidades separadas y categorías de gestión y finanzas. |
| [Coherencia del recorrido completo](WORKFLOW_COHERENCE_REVIEW.md) | Datos históricos, chat, opciones de preparación, roles y validación entre módulos. |
| [Validación de reparto para apertura](DELIVERY_READINESS.md) | Recorridos por modalidad y rol, disponibilidad real de franjas y estado previo a recibir pedidos. |
| [Pulido responsive](RESPONSIVE_POLISH.md) | Acabado público y de roles, reducción de efectos y validación visual local. |
| [Control financiero](FINANCIAL_CONTROL.md) | Criterios de ganancias, costes, gastos y caja. |
| [Finanzas, productos y seguridad](MANAGEMENT_SECURITY_REVIEW.md) | Correcciones de importes, permisos, integridad histórica, contraseñas y doble factor. |
| [Recogida y modelo comercial](PICKUP_BUSINESS_MODEL_REVIEW.md) | Capacidades existentes, aviso con Maps y decisiones de cuota fija y canal pendientes. |
| [Robustez del flujo de datos](FUNCTIONAL_DATA_FLOW_REVIEW.md) | Reintentos vigentes, códigos caducados, duplicados, retención y validación funcional. |
| [Personalización y cancelaciones](CUSTOMIZATION_CANCELLATION_REVIEW.md) | Vista previa de paleta, cancelación segura en chat web y acciones de cocina. |
| [Comprobación de apertura](PRODUCTION_READINESS_REVIEW.md) | Cobro al recibir, pruebas de personalización/tienda y estado de la verificación del servidor. |
| [Identidad visual colombiana](VISUAL_IDENTITY.md) | Tokens, componentes y criterios culturales del escaparate. |
| [Desarrollo seguro](DEVELOPMENT.md) | Dónde implementar cada cambio, convenciones y pruebas. |
| [Versiones publicadas e impresión](DEPLOYMENT_PRINTER_REVIEW.md) | Divergencia local/servidor, identidad real de WhatsApp y compatibilidad de impresión USB/BLE/AirPrint. |
| [Operación y despliegue](OPERATIONS.md) | Entornos, despliegue, salud, rollback y datos sensibles. |
| [Claves configurables](../oxidian/docs/CONFIG_KEYS.md) | Catálogo de claves editables en `SiteConfig`. |
| [QA de lanzamiento](../oxidian/docs/QA_LANZAMIENTO.md) | Controles funcionales y visuales previos a producción. |

## Referencias históricas

Los siguientes documentos explican etapas anteriores. Se conservan para
entender decisiones y compatibilidad, pero pueden mencionar roles o flujos ya
desactivados:

- `oxidian/ARQUITECTURA.md`
- `oxidian/FLUJOS.md`
- `oxidian/CLAUDE.md`
- `oxidian/OPERACIONES.md`
- `oxidian/AUDITORIA_SISTEMA_2026-06-15.md`
- `oxidian/docs/FASES_PROYECTO.md`

Si una referencia histórica contradice el código, prevalecen en este orden:

1. modelos, permisos y pruebas automatizadas;
2. esta documentación vigente;
3. documentos históricos.

## Artefactos generados

Las carpetas `docs/auditoria_*` y `oxidian/docs/auditoria_*` contienen capturas
y reportes de ejecuciones concretas. No describen necesariamente el estado
actual y no deben editarse a mano. Las nuevas ejecuciones quedan ignoradas por
Git para evitar ruido; los archivos ya versionados se conservan como evidencia.
