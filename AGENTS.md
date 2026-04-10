# Insider Signal Scanner — AGENTS.md

This repository builds a **public insider-transactions signal scanner** based on SEC ownership filings. The product is **not** for illegal insider trading, and it must never imply access to non-public information. It tracks **public Form 4 / Form 4/A filings**, normalizes them, scores likely bullish clusters, and presents them via API and dashboard.

Codex should treat this as a **greenfield but production-minded** repo unless existing code says otherwise.

## Mission

Build an MVP that can:

1. discover newly filed SEC Form 4 ownership reports,
2. parse and normalize the filing data,
3. identify clusters of meaningful insider buying,
4. enrich signals with basic issuer health and market context,
5. expose the results through a clean API and a simple dashboard,
6. optionally generate short AI summaries from structured facts.

## What “done” looks like

A fresh developer should be able to clone the repo, add environment variables, run one command for local startup, ingest sample SEC data, and view ranked insider-buying signals in a browser.

## Non-goals for MVP

Do **not** build these in the first pass:

- brokerage integration,
- auto-trading,
- options trading workflows,
- mobile app,
- social features,
- backtesting engine beyond a very small validation script,
- speculative alpha claims.

## Product shape

The app should have four layers:

1. **Ingestion** — fetch filing indexes, filing metadata, filing XML, and issuer metadata.
2. **Normalization** — turn SEC filings into stable relational records.
3. **Scoring** — compute deterministic insider-buying signals.
4. **Presentation** — API, dashboard, alerts, and optional AI summaries.

## Ground rules for Codex

- Prefer small, reviewable changes over giant rewrites.
- Keep the system working after each milestone.
- If the repo is empty, scaffold the minimum viable structure and move in phases.
- Add tests with every meaningful parser, scorer, and API change.
- Never hardcode secrets.
- Keep `.env.example` current.
- Keep `README.md` current.
- Add database migrations for schema changes.
- Avoid brittle scraping when a structured SEC source exists.
- Do not let LLM output become source-of-truth data.
- Use deterministic scoring first; AI is only a summarization/explanation layer.

## Preferred stack

Use this stack unless the repository already clearly standardizes on something else:

### Backend

- Python
- FastAPI
- SQLAlchemy
- Alembic
- PostgreSQL
- `httpx` for HTTP
- `pytest` for tests
- a scheduler for polling jobs

### Frontend

- TypeScript
- React-based web UI
- simple table/detail workflow

### Local development

- Docker Compose for local dependencies
- Makefile with stable commands

## Required developer commands

If these commands do not exist yet, create them and keep them working:

- `make dev` — start app stack locally
- `make test` — run backend and frontend tests
- `make lint` — lint code
- `make format` — format code
- `make migrate` — apply migrations
- `make seed` — load demo/sample data
- `make ingest-sample` — ingest included SEC fixtures only
- `make ingest-live` — run live SEC ingestion once

## Suggested repo layout

If the repo is empty, scaffold roughly this structure:

```text
/apps
  /api
  /web
/packages
  /core
  /sec_ingestion
  /scoring
  /ai_summary
  /shared_types
/infrastructure
  docker-compose.yml
  migrations/
/tests
  /fixtures
  /integration
.env.example
Makefile
README.md
AGENTS.md
```

A simpler monorepo is fine if it keeps boundaries clear.

## Core business rules

The system tracks **public insider transaction disclosures**, not “insider trading” in the illegal sense.

### MVP bullish signal definition

A candidate bullish signal is an issuer where, within a rolling 30-day window:

- there are at least **2 unique insiders** buying,
- the transactions are **open-market purchases** in the **non-derivative** table,
- transaction code is **`P`**,
- the acquired/disposed flag is acquisition,
- the purchases are not obviously grants, gifts, tax withholdings, option exercises, or other routine/non-open-market events,
- the issuer passes minimum health filters or is clearly labeled when health data is missing.

### Exclude by default

Do not count these as bullish buys unless explicitly enabled later:

- transaction codes `A`, `F`, `G`, `M`, `S`, `D`, `J`, `K`, `V`,
- derivative-only events,
- pure option exercises,
- tax withholding transactions,
- gifts/transfers,
- indirect-only ownership when no direct open-market buy is present,
- amended filings that do not materially change the underlying transaction set.

### Keep raw evidence

For every normalized transaction, store:

- accession number,
- source path,
- filing timestamp,
- transaction date,
- issuer CIK,
- issuer ticker when known,
- reporting owner name,
- reporting owner relationship flags,
- transaction code,
- share count,
- price,
- post-transaction holdings,
- direct vs indirect ownership,
- raw footnotes,
- raw XML or a reference to stored raw XML.

## SEC source strategy

Use structured SEC sources in this order:

### 1. Discovery of new filings

Use EDGAR **daily/full index files** to discover new Form 4 and Form 4/A filings across the market.

Implementation notes:

- Poll `daily-index` for the current year.
- Use `full-index` as a bridge across the current quarter and to recover missed filings.
- Prefer JSON/XML index helpers when practical.
- Avoid fragile HTML scraping for discovery.

### 2. Filing document retrieval

For each discovered accession:

- fetch the filing directory index,
- locate the ownership XML document,
- parse the XML,
- persist both normalized data and a raw copy/reference.

### 3. Issuer enrichment

Use SEC issuer submissions/company metadata where possible for:

- entity name,
- CIK,
- filing history,
- ticker mapping,
- basic issuer metadata.

### 4. Historical backfill

Use the SEC quarterly Insider Transactions Data Sets for backfill and demo data loading.

### 5. Fair-access compliance

Always:

- send a declared SEC user-agent header from config,
- throttle requests well under the SEC maximum,
- cache aggressively,
- never fetch more than needed.

## Configuration

Create and document at least these environment variables:

```env
APP_ENV=development
DATABASE_URL=
SEC_USER_AGENT=
SEC_BASE_URL=https://www.sec.gov
SEC_DATA_BASE_URL=https://data.sec.gov
SEC_MAX_RPS=5
OPENAI_API_KEY=
MARKET_DATA_PROVIDER=
MARKET_DATA_API_KEY=
ALERT_WEBHOOK_URL=
DEFAULT_MARKET_CAP_MAX=500000000
DEFAULT_CLUSTER_WINDOW_DAYS=30
DEFAULT_MIN_UNIQUE_BUYERS=2
DEFAULT_MIN_TOTAL_PURCHASE_USD=100000
```

Notes:

- `SEC_MAX_RPS` should default below the SEC ceiling to leave headroom.
- The app must run without `OPENAI_API_KEY`; AI summaries are optional.
- The app must degrade gracefully if market-data enrichment is unavailable.

## Data model

Create normalized tables or equivalent models for at least:

### `issuers`

- `id`
- `cik` (unique)
- `ticker`
- `name`
- `exchange` nullable
- `sic` nullable
- `state_of_incorp` nullable
- `market_cap` nullable
- `latest_price` nullable
- timestamps

### `insiders`

- `id`
- `reporting_owner_cik` nullable
- `name`
- `is_director`
- `is_officer`
- `is_ten_percent_owner`
- `officer_title` nullable
- timestamps

### `filings`

- `id`
- `accession_number` (unique)
- `form_type`
- `issuer_id`
- `filed_at`
- `source_url`
- `xml_url`
- `is_amendment`
- `raw_xml_path` or blob reference
- `fingerprint`
- timestamps

### `transactions`

- `id`
- `filing_id`
- `insider_id`
- `transaction_date`
- `security_title`
- `is_derivative`
- `transaction_code`
- `acquired_disposed`
- `shares`
- `price_per_share`
- `value_usd`
- `shares_after`
- `ownership_type`
- `deemed_execution_date` nullable
- `footnote_text`
- `is_candidate_buy`
- `is_likely_routine`
- `routine_reason` nullable
- timestamps

### `signal_windows`

- `id`
- `issuer_id`
- `window_start`
- `window_end`
- `unique_buyers`
- `total_purchase_usd`
- `average_purchase_usd`
- `signal_score`
- `health_score` nullable
- `price_context_score` nullable
- `summary_status`
- `rationale_json`
- timestamps

### `alerts`

- `id`
- `signal_window_id`
- `channel`
- `status`
- `sent_at` nullable
- `payload_json`
- timestamps

## Parser requirements

The parser must:

- support Form 4 and Form 4/A,
- parse both non-derivative and derivative tables,
- map footnotes back to transactions where possible,
- preserve unrecognized fields in raw JSON for future use,
- be idempotent,
- handle missing price/share fields safely,
- support deduplication by accession number and fingerprint,
- include fixture-based tests from real SEC examples.

### Parser decision rules for MVP

- Only **non-derivative** rows with code `P` and acquisition flag count toward the bullish cluster score.
- Keep derivative rows in storage, but do not count them in the core signal.
- `4/A` should update/replace the latest normalized view for that accession chain.
- If a filing includes multiple owners or multiple rows, preserve row-level granularity.

## Routine vs non-routine classification

This must be mostly deterministic for MVP.

### Deterministic routine flags

Mark as likely routine if any of the following are true:

- transaction code is not `P`,
- ownership is only indirect and there is no direct buy,
- footnotes contain strong indicators of plan/award/withholding/gift language,
- value is below a configurable micro-transaction threshold,
- the row is clearly tied to compensation or option exercise mechanics.

### Optional AI assist

A model may classify ambiguous footnotes, but only as a helper that returns structured JSON like:

```json
{
  "is_likely_routine": true,
  "confidence": 0.91,
  "reason": "Mentions tax withholding tied to vesting"
}
```

Never let the model invent shares, prices, dates, roles, or transaction codes.

## Scoring model

The scoring engine must be deterministic and transparent.

Return a **0–100 score** with component breakdown.

### Required score components

#### 1. Cluster strength (0–30)

Based on:

- number of unique insiders buying in the rolling window,
- number of qualifying buy transactions,
- total purchase value.

#### 2. Conviction (0–25)

Based on:

- purchase size per insider,
- purchase value relative to prior holdings when derivable,
- concentration of buying near the same date range.

If compensation data is unavailable, do not block the score. Use holdings-based conviction instead.

#### 3. Price context (0–15)

Based on whether buying occurs near:

- 52-week lows,
- recent local lows,
- depressed drawdown zones.

If no market data provider is configured, mark this component unavailable and reweight remaining components proportionally.

#### 4. Health filter / health score (0–20)

Aim to compute:

- Altman Z-score when feasible,
- current ratio,
- obvious distress flags.

If health inputs are missing, do not silently pass. Show `health_status = unknown`.

#### 5. Event context (0–10)

Optional but useful:

- upcoming earnings,
- recent 8-Ks,
- cluster timing around catalysts.

This may be a later milestone.

### Minimum candidate threshold

A signal should not appear in the main ranked feed unless:

- `unique_buyers >= 2`,
- `total_purchase_usd >= configured threshold`,
- at least one component other than raw count shows non-trivial strength.

## Fundamentals and market context

Build provider interfaces so the core app does not depend on one vendor.

Create abstractions for:

- market cap and last price,
- 52-week high/low or similar range context,
- earnings calendar,
- financial-statement-derived health metrics.

At minimum:

- ship one SEC-backed fundamentals provider where possible,
- ship mock providers for tests,
- make third-party providers pluggable via config.

## API requirements

Expose REST endpoints at minimum:

### Health and system

- `GET /health`
- `GET /ready`

### Signals

- `GET /signals`
- `GET /signals/{signal_id}`
- `GET /signals/latest`

Support filters for:

- ticker,
- CIK,
- date range,
- market-cap max,
- minimum score,
- minimum unique buyers,
- include/exclude indirect,
- include/exclude unknown health.

### Filings and transactions

- `GET /filings/{accession_number}`
- `GET /issuers/{ticker_or_cik}`
- `GET /issuers/{ticker_or_cik}/transactions`
- `GET /insiders/{insider_id}`

### Operations

- `POST /ops/ingest/live`
- `POST /ops/ingest/backfill`
- `POST /ops/recompute-signals`

Protect ops endpoints in non-dev environments.

## Dashboard requirements

Build a simple but useful UI.

### Main page

A ranked table of current signals with columns for:

- ticker,
- issuer name,
- signal score,
- unique insiders,
- total buy value,
- latest transaction date,
- health status,
- price context,
- quick explanation.

### Detail page

Show:

- issuer info,
- all qualifying transactions in the cluster,
- insider names and roles,
- raw filing links,
- component score breakdown,
- AI summary if available,
- warnings/unknowns.

### Filters

Add filters for:

- market cap ceiling,
- score floor,
- date range,
- health known/unknown,
- direct only vs all,
- include amendments.

## Alerts

Alerts are a milestone after the ranked feed works.

Support at least one outbound channel:

- webhook

Alert only when:

- a new signal crosses a configured score threshold, or
- an existing signal materially strengthens.

Avoid duplicate alerts.

## Milestones

Implement in this order.

### Milestone 1 — Project scaffold

Deliver:

- backend app skeleton,
- database setup,
- migration setup,
- Docker Compose,
- Makefile,
- `.env.example`,
- README,
- health endpoints.

Acceptance:

- `make dev` starts the stack,
- `make test` passes,
- database migrations apply cleanly.

### Milestone 2 — SEC ingestion and parser

Deliver:

- index polling,
- accession discovery,
- filing fetch,
- XML parser,
- normalized persistence,
- fixture tests.

Acceptance:

- sample fixtures ingest correctly,
- duplicate ingest is idempotent,
- Form 4 `P` rows are parsed correctly,
- footnotes are preserved.

### Milestone 3 — Deterministic scoring

Deliver:

- rolling-window cluster builder,
- scoring engine,
- explainable score breakdown,
- `/signals` endpoints.

Acceptance:

- seeded sample data produces at least one ranked signal,
- scores are reproducible,
- business-rule tests cover exclusion cases.

### Milestone 4 — Dashboard

Deliver:

- signals table,
- detail page,
- filters,
- error/loading states.

Acceptance:

- a user can browse recent signals without using the API directly.

### Milestone 5 — Enrichment

Deliver:

- pluggable market/fundamental provider layer,
- health status,
- price context,
- optional catalyst/earnings enrichment.

Acceptance:

- app still works when provider is absent,
- unknown data is clearly labeled.

### Milestone 6 — AI summaries and alerts

Deliver:

- structured AI summary generation,
- webhook alerts,
- duplicate-alert suppression.

Acceptance:

- summaries never replace raw facts,
- alerts are traceable to a stored signal snapshot.

## Testing requirements

Write tests for:

- Form 4 XML parsing,
- amendment handling,
- transaction-code filtering,
- rolling-window cluster construction,
- score breakdown math,
- API response shape,
- idempotent ingestion,
- routine/non-routine classification logic.

Include:

- unit tests,
- a few integration tests using stored SEC fixtures,
- at least one end-to-end smoke path.

## Observability

Add:

- structured logs,
- ingest counters,
- parse error counts,
- score generation counts,
- alert outcomes.

At minimum, log enough to answer:

- what filing was processed,
- whether it parsed,
- whether it contributed to a signal,
- why it was excluded.

## UI/UX principles

- Make the ranked feed easy to scan.
- Show why a signal exists.
- Show uncertainty explicitly.
- Link back to raw SEC evidence.
- Never present a model summary without the underlying facts.

## Compliance and safety

This project must include visible disclaimers:

- Uses public SEC filings only.
- Not investment advice.
- Signals may be delayed because filings are public disclosures submitted after transactions.
- Users should review original SEC filings before acting.

## Implementation notes and tradeoffs

- Treat SEC raw filings and XML as source of truth.
- Use quarterly SEC insider datasets for backfill, not real-time discovery.
- Use daily/full indexes for live discovery.
- Prefer clean abstractions so market-data vendors can be swapped.
- If an enrichment input is missing, preserve the signal and label the missing field.
- Accuracy is more important than speed in the first pass.

## First task for Codex when starting from an empty repo

1. Scaffold Milestone 1 completely.
2. Add sample SEC fixtures.
3. Implement Milestone 2 parser for Form 4 `P` rows.
4. Stop once tests pass and summarize what remains.

## Good kickoff prompt to pair with this file

Use this as the first direct instruction in Codex if needed:

> Build Milestone 1 and Milestone 2 from AGENTS.md in small reviewable commits. Prioritize a working local stack, SEC fixture-based parsing, idempotent ingestion, and passing tests. Do not add brokerage features, auto-trading, or speculative extras.
