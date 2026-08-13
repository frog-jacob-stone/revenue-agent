# Revenue Operations System

FastAPI service for Frogslayer's revenue operations: Harvest billing/invoicing automation, revenue recognition, and an audit trail over every state change — plus an approval-gated agent framework for conversational and judgment-requiring tasks. The billing/invoicing engine, the system's largest subsystem, is deliberately deterministic and has no agent in its write path; every write an agent *does* propose flows through a human-in-the-loop approval queue before execution.

## Architecture

- **FastAPI + asyncpg** — async Python API backed directly by Postgres
- **Supabase** — local Postgres (via Docker), RLS, and migrations
- **Two write paths, one audit trail** — operator-initiated writes (billing/invoicing: a human reads the exact payload and clicks) and agent-initiated writes (proposed by a tool, gated behind human approval). See [ADR-0004](docs/adr/0004-operator-initiated-writes.md) for which applies when.
- **Agents are scoped by coherent identity** — separate agents for write-proposing operations vs. read-only analytics, even within the same domain. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the scoping principles.

## Prerequisites

- Python 3.12+
- [Supabase CLI](https://supabase.com/docs/guides/cli) (`brew install supabase/tap/supabase`)
- Docker Desktop

---

## Setup

### 1. Start Supabase locally

```bash
supabase start
```

This starts Postgres on port 54322 and the Supabase stack on 54321. Once running, grab the connection details:

```bash
supabase status
```

Note the `DB URL`, `API URL`, `service_role key`, and `anon key`.

### 2. Configure environment

There are three env files in this repo. Each lives next to whatever reads it, so none of them is interchangeable with the others:

| File | Read by | Purpose |
|---|---|---|
| `app/.env` | `app/config.py`, which anchors the path to its own module | Local backend config |
| `ui/.env` | Vite, via `loadEnv(mode, process.cwd(), …)` from `ui/` | Local frontend config |
| `.env.production` | `scripts/*.sh` only — no running program | Production deploys ([DEPLOY.md](DEPLOY.md)) |

`.env.production` sits at the root because it spans all three deploy targets — Supabase, Azure, and the Netlify build — so it belongs to no single directory. It must not be moved into `ui/`: Vite auto-loads a file by that name during a production build.

Each has a tracked `*.example` template beside it; the real files are gitignored. For local development you need the first two.

```bash
cp app/.env.example app/.env
```

`app/.env` is the only env file the backend reads — `docker-compose.yml` points `env_file` there. Populate it; the Supabase values come from `supabase status`:

| Variable | Where to find it |
|---|---|
| `DATABASE_URL` | DB URL from `supabase status` (port 54322) |
| `SUPABASE_URL` | API URL from `supabase status` (port 54321) |
| `SUPABASE_PUBLISHABLE_KEY` | `anon key` — starts with `sb_publishable_` in new CLI |
| `OPENAI_API_KEY` | From [platform.openai.com](https://platform.openai.com) |
| `HARVEST_TOKEN` | Harvest personal access token |
| `HARVEST_ACCOUNT_ID` | Harvest account ID that pairs with the token |
| `AIRTABLE_API_KEY` | Airtable personal access token |

### 3. Run migrations

Apply the schema from scratch:

```bash
supabase db reset
```

Or apply just the migration:

```bash
supabase migration up
```

### 4. Start the Approval Inbox UI

```bash
cd ui
cp .env.example .env
npm install
npm run dev
```

UI runs at http://localhost:3000. Set `VITE_API_URL` in `ui/.env` if the API is on a different host.

### 5. Start the API

#### With Docker Compose (recommended)

```bash
docker compose up --build
```

> **Note:** Supabase runs separately via `supabase start`. The API container reaches it at `host.docker.internal`, so that is what `DATABASE_URL` and `SUPABASE_URL` in `app/.env` are set to. Compose reads that file itself and injects the values as environment variables — it is not `Settings.env_file` that supplies them in this path.

#### Without Docker (development)

```bash
pip install -e ".[test]"

# app/.env is written for Docker, and `host.docker.internal` does not resolve on
# the host — override the two host-shaped values at launch. Environment variables
# outrank the env file, so everything else still comes from app/.env.
DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:54322/postgres \
SUPABASE_URL=http://127.0.0.1:54321 \
uvicorn app.main:app --reload
```

`Settings` reads `app/.env` regardless of the directory you launch from — the path is anchored to `app/config.py`, not to the working directory. Credentials load either way; only the two `host.docker.internal` URLs need replacing.

API available at `http://localhost:8000`. Docs at `http://localhost:8000/docs`.

---

## Running Tests

Tests run against the local Supabase instance — make sure `supabase start` is running first.

```bash
pip install -e ".[test]"
pytest -v
```

Tests create real rows in the local DB. No cleanup is performed between runs (each run creates unique test agents), so the DB remains clean enough for repeated local runs. Use `supabase db reset` to wipe and reapply the schema if needed.

---

## Deploying

Everything above is local development and is unaffected by deployment. For production — hosted Supabase, Azure Container Apps, and Netlify, deployed by hand with three commands — see [DEPLOY.md](DEPLOY.md).

---

## API Overview

| Method | Path | Description |
|---|---|---|
| `GET` | `/agents` | List agents; per-agent detail and tools |
| `GET` | `/approvals` | Approval inbox — defaults to `status=pending` |
| `GET` | `/approvals/{id}` | Approval detail |
| `POST` | `/approvals/{id}/approve` | Approve → invoke the registered executor → execute (writes full audit trail) |
| `POST` | `/approvals/{id}/reject` | Reject with reason |
| `GET` | `/audit_log` | Read-only audit feed |
| `GET`/`POST` | `/billing/*` | The invoicing surface — billing groups, Harvest snapshot, billing runs, draws, settings, created invoices. Operator-initiated; see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). |
| `POST` | `/chat/{agent_slug}` | Chat with an agent (SSE streaming); always routes through `chief-of-staff` |
| `GET` | `/llm_calls` | Read-only inspector over every LLM call (summary/detail/aggregates) |

---

## Project Structure

```
app/
  main.py              # FastAPI app + lifespan
  config.py            # Pydantic settings (reads .env)
  db.py                # asyncpg connection pool
  auth.py              # JWT verification against Supabase JWKS
  models/              # Pydantic v2 models (one file per table)
  routers/             # FastAPI routers (thin — business logic in services/)
    billing.py         # The invoicing surface — operator-initiated, no agent in the path
    approvals.py       # The human-in-the-loop inbox
    agents.py / audit_log.py / chat.py / llm_calls.py
  services/
    audit.py           # write_audit_event() — called on every state transition
    approvals.py       # approve_approval(), reject_approval()
    agent_messages.py  # turn-by-turn record of agent-to-agent exchanges
    revenue.py          # billing-type-aware revenue recognition math
    airtable_sync.py    # Harvest -> Airtable client/project sync
    billing/             # 18 modules: Harvest snapshot, groups, reconcile, estimator,
                          # payload, draws, recurring, planner, settings_store, etc.
  orchestrator/
    agent_invoke.py     # run_agent_task() — ReAct loop for agents with tools; no graph engine
    dispatch.py         # tool return (Done | AwaitingApproval | Blocked) -> audit + approval row
    events.py           # audit event constants
  agents/
    registry.py         # chief-of-staff, bdr, revenue-ops, linkedin (single agent class, ADR-0003)
    tools/               # ask_agent, content/*, revenue/* — tools an LLM may call
  executors/             # post_to_linkedin, write_rev_rec_entries — invoked after approval, never by an LLM
  integrations/          # Harvest, Airtable, Forecast, LLM dispatcher clients
docs/
  SCHEMA.md            # Source of truth for the DB schema
  ARCHITECTURE.md      # System architecture: RevOps automation + agent-framework pattern
  adr/                 # Architecture Decision Records
  prd/                 # Feature-level requirements docs (e.g. Harvest invoicing)
supabase/
  migrations/          # SQL migrations (apply via supabase db reset)
tests/
  conftest.py          # Pool + client + test_agent_id fixtures
  test_billing_*.py    # Billing/invoicing engine coverage
  test_approval_flow.py / test_agent_invoke.py / test_no_agent_approval_tools.py
```
