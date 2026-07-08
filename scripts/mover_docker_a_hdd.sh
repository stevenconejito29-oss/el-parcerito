#!/usr/bin/env bash
# Mueve el data-root de Docker del SSD (/var/lib/docker) al HDD (/mnt/hdd/docker-root).
# Al terminar, el SSD queda con solo el SO + logs; imágenes y build-cache
# vivirán en el HDD (916 GB disponibles) permitiendo levantar más proyectos
# sin llenar el disco raíz.
#
# Uso:   sudo bash scripts/mover_docker_a_hdd.sh
#
# Downtime esperado: ~5-10 min según tamaño de /var/lib/docker.
# Antes de nada: hay una FASE 1 sin parar Docker que copia todo lo grande.
# Luego para Docker, hace rsync delta (segundos) y arranca.

set -euo pipefail

SRC=/var/lib/docker
DST=/mnt/hdd/docker-root

if [[ $EUID -ne 0 ]]; then
  echo "❌  Ejecutar con sudo: sudo bash $0" >&2
  exit 1
fi

if [[ ! -d /mnt/hdd ]]; then
  echo "❌  /mnt/hdd no existe o no está montado. Aborto." >&2
  exit 1
fi

echo "▶ FASE 1: rsync inicial SIN parar Docker (copia la mayoría del contenido)"
mkdir -p "$DST"
rsync -aHAX --info=progress2 "$SRC"/ "$DST"/ || true

echo "▶ FASE 2: parando contenedores del proyecto (Cosmos y otros seguirán vivos hasta parar Docker)"
docker ps --format '{{.Names}}' | while read -r c; do
  echo "  · stop $c"; docker stop -t 10 "$c" || true
done

echo "▶ FASE 3: parando Docker daemon"
systemctl stop docker docker.socket

echo "▶ FASE 4: rsync delta final (solo cambios desde FASE 1)"
rsync -aHAX --delete --info=progress2 "$SRC"/ "$DST"/

echo "▶ FASE 5: renombrando data-root viejo por seguridad"
mv "$SRC" "${SRC}.old-$(date +%Y%m%d-%H%M%S)"

echo "▶ FASE 6: configurando /etc/docker/daemon.json con data-root en HDD"
if [[ -f /etc/docker/daemon.json ]]; then
  cp /etc/docker/daemon.json "/etc/docker/daemon.json.bak-$(date +%Y%m%d-%H%M%S)"
  # Fusiona con jq si está; si no, sobrescribe (Debian/Ubuntu suele tener jq).
  if command -v jq >/dev/null 2>&1; then
    tmp=$(mktemp)
    jq --arg dr "$DST" '. + {"data-root":$dr}' /etc/docker/daemon.json > "$tmp"
    mv "$tmp" /etc/docker/daemon.json
  else
    echo "{\"data-root\":\"$DST\"}" > /etc/docker/daemon.json
  fi
else
  echo "{\"data-root\":\"$DST\"}" > /etc/docker/daemon.json
fi

echo "▶ FASE 7: arrancando Docker daemon"
systemctl start docker

sleep 3
docker info 2>&1 | grep -iE "docker root dir|storage driver" || true

echo "▶ FASE 8: verificando que los contenedores vuelvan (restart=unless-stopped)"
sleep 5
docker ps --format 'table {{.Names}}\t{{.Status}}'

echo ""
echo "✅  Migración completada."
echo "    El data-root viejo está renombrado en $SRC.old-* (puedes borrarlo cuando compruebes"
echo "    que todo funciona bien: 'sudo rm -rf $SRC.old-*')."
echo "    Ahora el SSD sólo aloja el SO. Espacio libre:"
df -h / /mnt/hdd | tail -3
