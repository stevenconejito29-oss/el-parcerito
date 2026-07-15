# El Parcerito

Monorepo del sistema de pedidos, operación por roles, PWA y chatbot de
WhatsApp.

La entrada para entender el código es [docs/README.md](docs/README.md). El mapa
de responsabilidades está en
[docs/PROJECT_STRUCTURE.md](docs/PROJECT_STRUCTURE.md) y las reglas para cambiar
sin romper flujos en [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).

## Componentes

- `oxidian/`: aplicación Flask, API, roles, PWA y configuración del stack.
- `chat/`: chatbot de WhatsApp integrado con Evolution API.
- `scripts/`: respaldo, restauración y autodespliegue.
- `docs/`: documentación vigente y artefactos de auditoría identificados.

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

La operación vigente está resumida en
[docs/OPERATIONS.md](docs/OPERATIONS.md). Los documentos que permanecen en la
raíz de `oxidian/` son referencias históricas o guías específicas y están
clasificados en el índice.

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
