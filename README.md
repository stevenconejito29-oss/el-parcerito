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

## Actualizaciones de produccion

Cada `push` a `main` ejecuta el workflow `CI`. El servidor consulta GitHub
periodicamente y solo despliega commits cuyo CI haya finalizado correctamente.
Antes de reconstruir los contenedores crea un backup y, si los healthchecks
fallan, vuelve al commit anterior.

El servidor no expone SSH ni Docker a GitHub Actions. La instalacion se realiza
una vez desde el clon de produccion:

```bash
bash scripts/install-auto-deploy.sh
```

El estado y los logs se guardan en
`~/.local/state/oxidian-deploy/`.
