# Scripts operacionales del stack Oxidian

## backup.sh

Genera un directorio con timestamp dentro de `$OXIDIAN_BACKUP_DIR`
(default `~/oxidian-backups/`) conteniendo:

| Archivo | Origen |
|---|---|
| `oxidian.dump` | `pg_dump -Fc` de la BD principal |
| `evolution.dump` | `pg_dump -Fc` de la BD de Evolution API |
| `images.tar.gz` | volumen Docker `oxidian_oxidian_images` |
| `chatbot_data.tar.gz` | volumen Docker `oxidian_chatbot_data` (SQLite del bot) |
| `manifest.txt` | tamaños + metadata del backup |
| `SHA256SUMS` | hashes de todos los archivos |

Conserva los últimos **7 días** y purga el resto. Configurable con
`OXIDIAN_BACKUP_RETENTION` (en días).

Ejecutar manualmente:

```bash
bash /home/panzeta/Documentos/scripts/backup.sh
```

Programado en cron diariamente a las **03:30** (ver `crontab -l`).
Log de cron: `~/oxidian-backups/backup.cron.log`.

## restore.sh

Restaura un backup completo. Pide confirmación interactiva antes de
sobreescribir, soporta `--dry-run` para verificar integridad sin tocar nada.

```bash
# Solo verificar
bash /home/panzeta/Documentos/scripts/restore.sh ~/oxidian-backups/20260617-174041 --dry-run

# Restaurar de verdad
bash /home/panzeta/Documentos/scripts/restore.sh ~/oxidian-backups/20260617-174041
```

Pasos del restore:
1. Verifica SHA256SUMS.
2. Detiene contenedores `oxidian` y `gateway` (mantiene PG levantado).
3. `pg_restore --clean --if-exists` sobre las dos BDs.
4. Sobrescribe los dos volúmenes Docker con el contenido de los `.tar.gz`.
5. Re-levanta oxidian + gateway.
6. Espera healthy y hace smoke test HTTP en `:5070`.

## Variables de entorno admitidas

| Variable | Default |
|---|---|
| `OXIDIAN_BACKUP_DIR` | `$HOME/oxidian-backups` |
| `OXIDIAN_BACKUP_RETENTION` | `7` |
| `OXIDIAN_DB_USER` / `OXIDIAN_DB_NAME` | `oxidian` |
| `EVOLUTION_DB_USER` / `EVOLUTION_DB_NAME` | `evolution` |
| `OXIDIAN_IMAGES_VOLUME` | `oxidian_oxidian_images` |
| `OXIDIAN_CHATBOT_VOLUME` | `oxidian_chatbot_data` |
| `OXIDIAN_COMPOSE_FILE` | `~/Documentos/oxidian/docker-compose.cosmos-local.yml` |
| `OXIDIAN_ENV_FILE` | `~/Documentos/oxidian/.env.cosmos.local` |

## Verificación periódica recomendada

Mensualmente, ejecutar el dry-run sobre el backup más reciente:

```bash
LATEST=$(ls -1d ~/oxidian-backups/*/ | tail -1)
bash /home/panzeta/Documentos/scripts/restore.sh "$LATEST" --dry-run
```

Si tienes capacidad, hacer un restore real en un PG temporal (como en el test
inicial) garantiza que los dumps siguen siendo restaurables.
