ifeq ($(OS),Windows_NT)
SHELL := cmd.exe
.SHELLFLAGS := /C
endif

COMPOSE=docker compose -f infrastructure/docker-compose.yml

.PHONY: dev test test-api test-web lint format migrate seed ingest-sample ingest-proxy-sample ingest-live sync-proxy-live ingest-backfill poll-live recompute-signals

dev:
	$(COMPOSE) up --build

test: test-api test-web

test-api:
	$(COMPOSE) run --rm api pytest

test-web:
	$(COMPOSE) run --rm web npm run test -- --run

lint:
	$(COMPOSE) run --rm api ruff check .
	$(COMPOSE) run --rm web npm run lint

format:
	$(COMPOSE) run --rm api ruff format .
	$(COMPOSE) run --rm web npm run format

migrate:
	$(COMPOSE) run --rm api alembic upgrade head

seed: ingest-sample recompute-signals

ingest-sample:
	$(COMPOSE) run --rm api python -m app.cli.main ingest-sample

ingest-proxy-sample:
	$(COMPOSE) run --rm api python -m app.cli.main ingest-proxy-sample

ingest-live:
	$(COMPOSE) run --rm api python -m app.cli.main ingest-live

sync-proxy-live:
	$(COMPOSE) run --rm api python -m app.cli.main sync-proxy-live

ingest-backfill:
	$(COMPOSE) run --rm api python -m app.cli.main ingest-backfill

poll-live:
	$(COMPOSE) run --rm api python -m app.cli.main poll-live

recompute-signals:
	$(COMPOSE) run --rm api python -m app.cli.main recompute-signals
