# SECTOR4

SECTOR4 is a public SEC Form 4 signal scanner. It discovers newly filed ownership reports, normalizes insider purchase activity, scores clustered open-market buying, and presents the results through an API and a browser dashboard.

The project uses public SEC disclosures only. It does not use non-public information, it does not place trades, and it should not be treated as investment advice.

## What It Does

- Polls SEC daily indexes to discover new Form 4 and Form 4/A filings
- Fetches and parses ownership XML into normalized issuer, insider, filing, and transaction records
- Scores recent insider-buying clusters with deterministic 0-100 component scoring
- Enriches signals with optional SEC-backed and market-data-backed context
- Exposes ranked signals through FastAPI endpoints and a simple React dashboard
- Supports optional fact-only AI summaries and webhook alerts

## Product Rules

- Uses public SEC filings only
- Focuses on open-market non-derivative buys
- Excludes routine or non-bullish transaction types by default
- Preserves raw filing evidence and links back to the original SEC source
- Labels missing health or price inputs explicitly instead of hiding uncertainty

## Stack

- Backend: Python, FastAPI, SQLAlchemy, Alembic, PostgreSQL, httpx, pytest
- Frontend: React, TypeScript, Vite
- Local development: Docker Compose, Makefile, Windows batch launchers

## Quick Start

1. Copy `.env.example` to `.env`.
2. Set `SEC_USER_AGENT` to a real contact string before running live SEC ingestion.
3. Start Docker Desktop.
4. Start the app:

```bash
make dev
```

5. Apply migrations:

```bash
make migrate
```

6. Load sample data:

```bash
make seed
```

7. Open:

- Dashboard: `http://localhost:5180`
- API docs: `http://localhost:8000/docs`

## Windows Launchers

For Windows-first local use:

- `start-dashboard.bat`
  Starts the normal local stack and keeps existing data.
- `start-live-dashboard.bat`
  Refreshes recent live SEC filings, then opens the dashboard.
- `start-live-only.bat`
  Deletes the local Postgres volume, rebuilds a live-only database, and starts the app with no sample data.

If you changed the database name or credentials, run `start-live-only.bat` once so the local Postgres volume is recreated cleanly.

## Minimum Free Setup

The project runs with no paid APIs.

Required:

- Docker Desktop
- `make`
- SEC user-agent string in `.env`

Optional:

- `MARKET_DATA_PROVIDER=sec_companyfacts`
  Enables SEC-backed issuer enrichment without a paid market-data key.
- `MARKET_DATA_PROVIDER=alpha_vantage`
  Adds quote and price-context enrichment when you provide `MARKET_DATA_API_KEY`.
- `OPENAI_API_KEY`
  Enables optional fact-only summaries.

If optional providers are not configured, SECTOR4 still works and labels those fields as unavailable or unknown.

## Core Commands

- `make dev` starts PostgreSQL, API, and the web dashboard
- `make test` runs backend and frontend tests
- `make lint` runs backend linting and frontend type-check linting
- `make format` formats backend and frontend code
- `make migrate` applies Alembic migrations
- `make seed` loads included sample fixtures and recomputes signals
- `make ingest-sample` ingests included SEC fixtures only
- `make ingest-live` runs one live SEC ingestion pass
- `make ingest-backfill` backfills a recent date range of live filings
- `make recompute-signals` rebuilds signal windows and summaries

## Environment

Copy `.env.example` to `.env` and adjust values as needed.

```env
APP_ENV=development
DATABASE_URL=postgresql+psycopg://sector4:sector4@db:5432/sector4
SEC_USER_AGENT=SECTOR4/0.1 (your-email@example.com)
SEC_BASE_URL=https://www.sec.gov
SEC_DATA_BASE_URL=https://data.sec.gov
SEC_MAX_RPS=5
OPENAI_API_KEY=
AI_SUMMARY_MODEL=gpt-5.4-mini
MARKET_DATA_PROVIDER=
MARKET_DATA_API_KEY=
ALERT_WEBHOOK_URL=
ALERT_MIN_SIGNAL_SCORE=75
ALERT_MIN_SCORE_DELTA=5
ALERT_MIN_TOTAL_PURCHASE_DELTA_USD=50000
DEFAULT_MARKET_CAP_MAX=500000000
DEFAULT_CLUSTER_WINDOW_DAYS=30
DEFAULT_MIN_UNIQUE_BUYERS=2
DEFAULT_MIN_TOTAL_PURCHASE_USD=100000
ROUTINE_MICRO_TRANSACTION_USD=10000
RAW_FILINGS_DIR=data/raw_filings
FIXTURE_MANIFEST_PATH=tests/fixtures/sec/manifest.json
PROXY_FIXTURE_MANIFEST_PATH=tests/fixtures/sec/proxy_manifest.json
SEC_PROXY_SYNC_ENABLED=false
WEB_PORT=5180
CORS_ALLOWED_ORIGINS=http://localhost:5180,http://127.0.0.1:5180
OPS_API_TOKEN=
OPS_SCHEDULER_ENABLED=false
OPS_LIVE_INGEST_LIMIT=25
OPS_BACKFILL_DAYS=5
OPS_POLL_INTERVAL_SECONDS=900
```

Notes:

- The app should run without `OPENAI_API_KEY`.
- Keep `.env` local. The repository ignores it by default.
- Use `.env.example` as the shareable template.

## API Surface

Key routes:

- `GET /health`
- `GET /ready`
- `GET /signals`
- `GET /signals/latest`
- `GET /signals/{signal_id}`
- `GET /filings/{accession_number}`
- `GET /issuers/{ticker_or_cik}`
- `GET /issuers/{ticker_or_cik}/transactions`
- `GET /insiders/{insider_id}`
- `POST /ops/ingest/live`
- `POST /ops/ingest/backfill`
- `POST /ops/recompute-signals`

## Dashboard

The dashboard is designed around a ranked opportunity board:

- Recent opportunities in a scan-first list
- Ticker search for focused lookup
- Centered detail modal with transaction evidence, issuer context, and raw filing links
- Live-only operation supported so mock data does not mix with current filings

## Current Scope

Implemented:

- SEC daily-index discovery
- Form 4 and Form 4/A parsing
- Raw evidence retention
- Deterministic rolling-window scoring
- Ranked signals API
- React dashboard
- SEC-backed issuer enrichment
- Optional Alpha Vantage price context
- Optional AI summaries
- Webhook alerting
- Live ingest and backfill flows

Still limited:

- Proxy-derived compensation coverage is partial
- Non-SEC fundamentals are intentionally narrow
- This is an operational scanner, not a backtesting platform or execution system

## Compliance

- Uses public SEC filings only
- Not investment advice
- Public filings can lag the actual trade date
- Review the original SEC filing before acting on any signal
