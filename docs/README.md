# Documentación de El Parcerito

Este directorio es la entrada oficial y vigente del proyecto. Su objetivo es
separar claramente la operación actual de notas históricas y capturas generadas.

## Documentos vigentes

| Documento | Para qué sirve |
|---|---|
| [Estructura del proyecto](PROJECT_STRUCTURE.md) | Componentes, capas, roles y recorrido de una petición. |
| [Desarrollo seguro](DEVELOPMENT.md) | Dónde implementar cada cambio, convenciones y pruebas. |
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
