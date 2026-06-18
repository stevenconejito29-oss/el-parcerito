# El Parcerito

Monorepo del sistema de pedidos:

- `oxidian/`: aplicacion web Flask, roles operativos, PWA y despliegue.
- `chat/`: chatbot de WhatsApp integrado con Evolution API.
- `scripts/`: respaldo y restauracion del stack de produccion.

## Despliegue

El Dockerfile usa la raiz de este repositorio como contexto:

```bash
docker compose \
  --env-file oxidian/.env.cosmos.local \
  -f oxidian/cosmos-compose.yml \
  up -d --build
```

Crea `oxidian/.env.cosmos.local` a partir de
`oxidian/.env.production.example`. Nunca publiques ese archivo.

La documentacion operativa esta en `oxidian/OPERACIONES.md` y
`oxidian/COSMOS_DEPLOY.md`.
