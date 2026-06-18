COMPOSE = docker compose --env-file .env.cosmos.local -f docker-compose.cosmos-local.yml

up:
	$(COMPOSE) up -d --build

start:
	$(COMPOSE) start

stop:
	$(COMPOSE) stop

down:
	$(COMPOSE) down

restart:
	$(COMPOSE) restart oxidian

build:
	$(COMPOSE) build oxidian

logs:
	$(COMPOSE) logs -f

ps:
	$(COMPOSE) ps

.PHONY: up start stop down restart build logs ps
