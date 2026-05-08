# Revenue Agents API

FastAPI service that orchestrates AI agents for revenue operations. Every action an agent takes flows through a human-in-the-loop approval queue before execution.

## Architecture

- **FastAPI + asyncpg** — async Python API backed directly by Postgres
- **Supabase** — local Postgres (via Docker), RLS, and migrations
- **Anthropic SDK** — Claude-powered agents (next sprint)
- **n8n** — triggers and third-party integration routing only; complex logic lives here in FastAPI
- **Agents are scoped by coherent identity** — separate agents for write-proposing operations vs. read-only analytics, even within the same domain. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the scoping principles.

## Prerequisites

- Python 3.12+
- [Supabase CLI](https://supabase.com/docs/guides/cli) (`brew install supabase/tap/supabase`)
- Docker Desktop
- [Doppler CLI](https://docs.doppler.com/docs/install-cli) (`brew install dopplerhq/cli/doppler`) — agreed secret manager for both local and cloud

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

**Option A — Doppler (recommended, agreed standard):**

```bash
doppler setup   # select the revenue-agents project + dev config
doppler run -- uvicorn app.main:app --reload
```

Doppler injects all env vars at runtime; no `.env` file needed.

**Option B — local `.env` (quick start without Doppler):**

```bash
cp .env.example .env
```

Populate `.env` with the values from `supabase status`:

| Variable | Where to find it |
|---|---|
| `DATABASE_URL` | DB URL from `supabase status` (port 54322) |
| `SUPABASE_URL` | API URL from `supabase status` (port 54321) |
| `SUPABASE_SECRET_KEY` | `service_role key` — starts with `sb_secret_` in new CLI |
| `SUPABASE_PUBLISHABLE_KEY` | `anon key` — starts with `sb_publishable_` in new CLI |
| `ANTHROPIC_API_KEY` | From [console.anthropic.com](https://console.anthropic.com) |
| `HUBSPOT_TOKEN` | HubSpot private app token (optional for now) |
| `APOLLO_API_KEY` | Apollo.io API key (optional for now) |

### 3. Run migrations

Apply the schema from scratch:

```bash
supabase db reset
```

Or apply just the migration:

```bash
supabase db push
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

> **Note:** Supabase runs separately via `supabase start`. The API container connects to it via `host.docker.internal`. Update `DATABASE_URL` in `.env` to use `host.docker.internal` instead of `127.0.0.1` when running inside Docker.

#### Without Docker (development)

```bash
pip install -e ".[test]"
uvicorn app.main:app --reload
```

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

## API Overview

| Method | Path | Description |
|---|---|---|
| `POST` | `/workflows` | Create a workflow (triggers `workflow.started` audit event) |
| `GET` | `/workflows` | List workflows (filter by `status`, `kind`) |
| `GET` | `/workflows/{id}` | Workflow detail |
| `GET` | `/workflows/{id}/trace` | Audit-log event timeline for the workflow |
| `GET` | `/approvals` | Approval inbox — defaults to `status=pending` |
| `GET` | `/approvals/{id}` | Approval detail |
| `POST` | `/approvals/{id}/approve` | Approve → resume the graph → execute (writes full audit trail) |
| `POST` | `/approvals/{id}/reject` | Reject with reason |

---

## Project Structure

```
app/
  main.py              # FastAPI app + lifespan
  config.py            # Pydantic settings (reads .env)
  db.py                # asyncpg connection pool
  models/              # Pydantic v2 models (one file per table)
  routers/             # FastAPI routers (thin — business logic in services/)
  services/
    audit.py           # write_audit_event() — called on every state transition
    approvals.py       # approve_approval(), reject_approval() (v2 surface)
    agent_messages.py  # turn-by-turn record of agent-to-agent exchanges
  orchestrator/        # LangGraph runner + production graphs
    runner.py
    graphs/            # one StateGraph per workflow kind
  agents/
    base.py            # BaseAgent ABC
  integrations/        # HubSpot, Apollo, Anthropic stubs
docs/
  SCHEMA.md            # Source of truth for the DB schema
  ARCHITECTURE.md      # System architecture and agent pattern
  AGENTS.md            # Agent roster
  BACKLOG.md           # What's next, deferred, blocked
  adr/                 # Architecture Decision Records
supabase/
  migrations/          # SQL migrations (apply via supabase db reset)
tests/
  conftest.py          # Pool + client + test_agent_id fixtures
  test_workflows.py
  test_orchestrator_v2_*.py  # runner, approval flow, agent_invoke, spawn
```
