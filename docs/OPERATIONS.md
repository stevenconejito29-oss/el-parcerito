# Operación y despliegue

Esta guía define los principios vigentes. Los comandos exactos pueden variar
entre el clon local, Cosmos y el servidor; confirmar siempre el archivo Compose
real antes de actuar.

## Entornos

- Desarrollo mínimo: `oxidian/docker-compose.yml`.
- Pruebas: `oxidian/docker-compose.test.yml`.
- Cosmos local: `oxidian/docker-compose.cosmos-local.yml`.
- Producción de dominio único: `oxidian/cosmos-compose.yml`.

El Compose de producción construye desde la raíz del monorepo porque la imagen
incluye tanto `oxidian/` como `chat/`. Ejecutarlo con `oxidian/` como único
contexto deja fuera el chatbot.

## Configuración sensible

- Partir de `oxidian/.env.production.example` o
  `oxidian/.env.cosmos.example`.
- Guardar los valores reales únicamente en el servidor o gestor de secretos.
- Nunca copiar `.env`, bases SQLite, dumps, imágenes subidas ni logs al repositorio.
- Revisar [`oxidian/docs/CONFIG_KEYS.md`](../oxidian/docs/CONFIG_KEYS.md) antes
  de crear una clave nueva de `SiteConfig`.

## Secuencia de publicación

1. Confirmar cambios y alcance con `git status` y `git diff`.
2. Ejecutar pruebas Python, Node y el predeploy.
3. Crear backup verificable de PostgreSQL y datos persistentes.
4. Construir/publicar usando el Compose real del servidor.
5. Aplicar migraciones compatibles antes de servir tráfico si corresponde.
6. Comprobar salud, cabeceras, PWA y un recorrido por cada rol afectado.
7. Revisar logs sin exponer secretos o datos personales.

No se considera terminado un despliegue sólo porque el contenedor reinicie.

## Salud mínima posterior

```bash
curl -fsS https://elparcerito.com/health/live
curl -fsS https://elparcerito.com/health/ready
curl -fsSI https://elparcerito.com/sw.js
curl -fsSI https://elparcerito.com/manifest.webmanifest
```

También deben comprobarse base de datos, Redis, Evolution API y el canal real de
WhatsApp si el cambio toca el bot. Para frontend, verificar vertical y
horizontal en un dispositivo pequeño y que ninguna navegación cubra acciones.

## Rollback

Un rollback seguro restaura conjuntamente una versión compatible de código y
esquema. No usar comandos destructivos de Git ni borrar volúmenes. Si una
migración no es reversible, restaurar el backup correspondiente y documentar el
incidente antes de reabrir la tienda.

## Documentos específicos conservados

- `oxidian/COSMOS_DEPLOY.md`: instalación general en Cosmos.
- `oxidian/docs/COSMOS_OS_DEPLOY.md`: notas del entorno Cosmos OS.
- `oxidian/docs/QA_LANZAMIENTO.md`: checklist ampliado de lanzamiento.

Esas guías pueden contener rutas o puertos de una instalación anterior. El
Compose y las variables del servidor son la fuente final para infraestructura.
