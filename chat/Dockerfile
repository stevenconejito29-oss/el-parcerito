FROM node:20-slim

# Evolution API no necesita Chromium; solo build tools para better-sqlite3.
RUN apt-get update && apt-get install -y \
    python3 \
    make \
    g++ \
    --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

ENV NODE_ENV=production

WORKDIR /app

# Instalar dependencias primero (mejor uso de caché de Docker)
COPY package*.json ./
RUN npm install --omit=dev

# Copiar el código
COPY . .

# Crear carpeta de datos persistente
RUN mkdir -p /app/data /app/public

EXPOSE 3000

CMD ["node", "bot.js"]
