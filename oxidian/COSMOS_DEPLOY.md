# Oxidian → Cosmos OS — Guía de despliegue

## ¿Qué es Cosmos OS?

Cosmos Cloud es un sistema operativo para servidores personales que gestiona aplicaciones Docker con HTTPS automático, reverse proxy y panel de control.

---

## Opción A — Script automático (recomendado)

```bash
# 1. Copiar el workspace al servidor (debe incluir oxidian/ y chat/)
scp -r /ruta/local/Documentos usuario@tu-servidor:/opt/oxidian-workspace
# O via git clone si tienes remote:
# git clone <tu-repo> /opt/oxidian

# 2. Conectar al servidor
ssh usuario@tu-servidor
cd /opt/oxidian-workspace/oxidian

# 3. Ejecutar el instalador
bash scripts/cosmos_install.sh
```

El script verifica variables, construye la imagen y levanta todos los servicios.

---

## Opción B — Manual paso a paso

### 1. Transferir el proyecto

```bash
# En tu PC:
scp -r /ruta/local/oxidian usuario@IP_SERVIDOR:/opt/oxidian
```

### 2. Configurar variables de entorno

```bash
cd /opt/oxidian
cp .env.production.example .env.cosmos.local
nano .env.cosmos.local
```

**Variables obligatorias que debes cambiar:**

| Variable | Descripción | Ejemplo |
|---|---|---|
| `SECRET_KEY` | Clave Flask 64 chars | `python -c "import secrets; print(secrets.token_hex(32))"` |
| `SEED_PASSWORD` | Contraseña inicial admins | mínimo 12 chars |
| `OXIDIAN_DB_PASSWORD` | BD PostgreSQL Oxidian | password fuerte |
| `EVOLUTION_DB_PASSWORD` | BD PostgreSQL Evolution | password fuerte |
| `EVOLUTION_API_KEY` | API key de Evolution | larga y aleatoria |
| `BOT_API_KEY` | Clave entre chatbot y Oxidian | larga y aleatoria |
| `OWNER_NUMBER` | Tu número WhatsApp sin + | `34XXXXXXXXX` |
| `OXIDIAN_PUBLIC_URL` | URL completa con HTTPS | `https://tudominio.com` |
| `SIMULATE_EVO_SEND` | `0` en producción | `0` |
| `SESSION_COOKIE_SECURE` | `1` con HTTPS | `1` |
| `WEB_CONCURRENCY` | Workers web Gunicorn | `3` |
| `WEB_THREADS` | Hilos por worker | `2` |
| `DB_POOL_SIZE` | Conexiones PostgreSQL por worker | `5` |
| `DB_MAX_OVERFLOW` | Conexiones extra por worker | `10` |

### 3. Construir la imagen

```bash
docker compose --env-file .env.cosmos.local -f cosmos-compose.yml build oxidian
```

### 4. Arrancar el stack

```bash
docker compose --env-file .env.cosmos.local -f cosmos-compose.yml up -d --build
```

### 5. Verificar

```bash
# Ver logs en tiempo real
docker compose --env-file .env.cosmos.local -f cosmos-compose.yml logs -f oxidian

# Verificar que responde
curl http://localhost:${PUBLIC_PORT:-5070}/gateway-health
curl http://localhost:${PUBLIC_PORT:-5070}/health
```

---

## Opción C — Panel Cosmos (GUI)

1. Abre el panel Cosmos: `https://tu-servidor:4242`
2. Ve a **Market → Compose → Import file**
3. Sube el archivo `cosmos-compose.yml`
4. Rellena las variables cuando Cosmos las solicite
5. Cosmos configura HTTPS automático con Let's Encrypt

---

## Configurar HTTPS con Cosmos

Cosmos gestiona el HTTPS automáticamente. Solo necesitas:

```bash
# En .env.cosmos.local:
OXIDIAN_PUBLIC_URL=https://tudominio.com
TIENDA_URL=https://tudominio.com
SESSION_COOKIE_SECURE=1
```

Cosmos debe apuntar el dominio al servicio `gateway` puerto `80`. Oxidian, el bot y Evolution quedan internos.

### Escalabilidad

El stack usa:

- Gunicorn con workers e hilos configurables (`WEB_CONCURRENCY`, `WEB_THREADS`).
- Pool PostgreSQL configurable (`DB_POOL_SIZE`, `DB_MAX_OVERFLOW`).
- Redis propio de Oxidian para rate limiting (`REDIS_URL=redis://oxidian-redis:6379/0`).
- Gateway Nginx con compresión, cache para estáticos y timeouts controlados.
- `ProxyFix` para respetar HTTPS/host real detrás del proxy.

Para subir capacidad sin tocar código, aumenta primero `WEB_CONCURRENCY` y `WEB_THREADS`, vigilando que `WEB_CONCURRENCY * (DB_POOL_SIZE + DB_MAX_OVERFLOW)` no supere el límite real de PostgreSQL.

---

## Conectar WhatsApp

1. Abre `https://tudominio.com/superadmin/chatbot`
2. Usa el QR que muestra Oxidian
3. Escanea con WhatsApp del teléfono del negocio
4. Espera `CONECTADO ✓`
5. Cambia en `.env.cosmos.local`:
   ```
   SIMULATE_EVO_SEND=0
   ```
6. Reinicia el stack:
   ```bash
   docker compose --env-file .env.cosmos.local -f cosmos-compose.yml restart oxidian
   ```

---

## Gestión diaria

```bash
# Ver estado de los contenedores
docker compose --env-file .env.cosmos.local -f cosmos-compose.yml ps

# Ver logs de Oxidian
docker compose --env-file .env.cosmos.local -f cosmos-compose.yml logs -f oxidian

# Ver logs del chatbot
docker compose --env-file .env.cosmos.local -f cosmos-compose.yml exec oxidian curl -s http://127.0.0.1:3000/api/status

# Reiniciar Oxidian (tras cambios de config)
docker compose --env-file .env.cosmos.local -f cosmos-compose.yml restart oxidian

# Actualizar a nueva versión
git pull
docker compose --env-file .env.cosmos.local -f cosmos-compose.yml up -d --build oxidian gateway evolution-setup

# Parar todo
docker compose --env-file .env.cosmos.local -f cosmos-compose.yml down

# Parar y borrar datos (¡CUIDADO! irreversible)
docker compose --env-file .env.cosmos.local -f cosmos-compose.yml down -v
```

---

## Backup de la base de datos

```bash
# Crear backup
docker exec oxidian-db pg_dump -U oxidian oxidian > backup_$(date +%Y%m%d).sql

# Restaurar backup
docker exec -i oxidian-db psql -U oxidian oxidian < backup_20260523.sql
```

---

## URL Única

Los tres pilares trabajan fusionados bajo un solo dominio:

| Pilar | Visibilidad |
|---|---|
| Oxidian | Única app pública: `https://tudominio.com` |
| Chatbot Node | Interno; se administra desde `/superadmin/chatbot` |
| Evolution API | Interno; entrega webhooks y QR hacia Oxidian |
| BD Oxidian | Solo acceso interno |
| BD Evolution | Solo acceso interno |

En local, el gateway unificado queda en `http://localhost:5070`.

---

## Credenciales iniciales

- **Email admin:** valor de `ADMIN_EMAIL`
- **Email superadmin:** valor de `SUPERADMIN_EMAIL`
- **Password:** valor de `SEED_PASSWORD`

Cámbialos desde `https://tudominio.com/superadmin/usuarios` tras el primer login.

---

## Solución de problemas

**Oxidian no arranca:**
```bash
docker compose --env-file .env.cosmos.local -f cosmos-compose.yml logs oxidian | tail -30
```

**WhatsApp desconectado:**
- Ir a `/superadmin/chatbot` y reescanear el QR
- O reiniciar Evolution: `docker restart evolution-api`

**Base de datos no conecta:**
```bash
docker compose --env-file .env.cosmos.local -f cosmos-compose.yml logs oxidian-db
```

**Limpiar y empezar de cero:**
```bash
docker compose --env-file .env.cosmos.local -f cosmos-compose.yml down -v
docker compose --env-file .env.cosmos.local -f cosmos-compose.yml up -d --build
```
