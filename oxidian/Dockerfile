# ════════════════════════════════════════════════════════════════
# Oxidian — Dockerfile de producción
# Incluye Flask/Gunicorn + chatbot Node en el mismo contenedor.
# Build context esperado: raíz del workspace (directorio que contiene oxidian/ y chat/)
# Ejemplo: docker build -f oxidian/Dockerfile .
# ════════════════════════════════════════════════════════════════
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# ── Usuario no-root ───────────────────────────────────────────
RUN groupadd -r oxidian && useradd -r -g oxidian -d /app -s /sbin/nologin oxidian

WORKDIR /app

# ── Deps del sistema + pip ────────────────────────────────────
COPY oxidian/requirements.txt .
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential libpq-dev libffi-dev libssl-dev curl gosu nodejs npm postgresql-client \
    && pip install --upgrade pip \
    && pip install -r requirements.txt \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* /root/.cache

# ── Código fuente Python ──────────────────────────────────────
COPY --chown=oxidian:oxidian oxidian/ .

# ── Chatbot Node en el mismo contenedor ───────────────────────
WORKDIR /app/chat
COPY --chown=oxidian:oxidian chat/package*.json ./
RUN npm install --omit=dev --no-audit --no-fund
COPY --chown=oxidian:oxidian chat/ .

WORKDIR /app

# ── Directorios con permisos correctos ───────────────────────
RUN mkdir -p /app/static/uploads /app/images /app/bot-data \
    && chown -R oxidian:oxidian /app

# ── Entrypoint: arregla permisos de volúmenes como root,
#    luego pasa a usuario oxidian ─────────────────────────────
COPY --chown=root:root oxidian/scripts/docker-entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 5000 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5000/api/config-publica', timeout=4)"

ENTRYPOINT ["/entrypoint.sh"]
CMD ["gunicorn"]
