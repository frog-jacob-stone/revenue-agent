# CLAUDE.md — Revenue Agent System

This file is loaded automatically by Claude Code at the start of every session.
Read it before doing anything else.

## What This Repo Is

An AI-powered revenue operations system for Frogslayer. Agents handle the work of an
entire revenue team (SDR research, outreach, proposals, content, revenue recognition).
This is not a personal assistant — it is operational infrastructure.

Full context: `docs/STACK.md`
Database schema: `docs/SCHEMA.md`
Agent definitions: `docs/AGENTS.md` (once created)

## Stack at a Glance

| Layer | Tool |
|---|---|
| API | FastAPI + Python 3.12 |
| LLM | Anthropic SDK (Claude) |
| Database | Supabase (Postgres + pgvector) |
| Secrets | Doppler (`doppler run -- uvicorn ...`) |
| Runtime | Docker Compose locally |
| Integrations | n8n → FastAPI (n8n triggers only, no business logic) |

## Project Structure

```
app/
  main.py            # FastAPI app, router mounts, lifespan
  config.py          # Settings via pydantic-settings (reads from env/Doppler)
  db.py              # Async Postgres connection pool
  models/            # Pydantic v2 request/response models
  routers/           # One file per resource (workflows, actions, agents, memories)
  agents/            # Agent implementations (base.py + one file per agent)
  services/          # Business logic: audit.py, approval.py, execution.py
  integrations/      # External API clients: hubspot.py, apollo.py, anthropic_client.py
tests/
docs/
supabase/migrations/
```

## The One Rule That Cannot Be Broken

**No agent may execute a create, update, or delete operation without a prior
`action.approved` row in the database.**

The flow is always:
```
agent proposes → action row created (status: proposed)
human approves → action row updated (status: approved)
system executes → action row updated (status: completed or failed)
```

Every state transition writes a row to `audit_log`. No exceptions.

## Code Conventions

- **Routers contain no business logic.** Routers validate input and call services.
- **Services contain business logic.** Routers call services, not the other way.
- **Agents propose; the framework executes.** Agents never call HubSpot/Gmail directly.
- **Pydantic v2 patterns.** Use `model_config`, not `class Config`.
- **Async everywhere.** All DB calls, all HTTP calls, all agent calls.
- **Every service function that changes state must call `write_audit_event()`.**

## Secrets and Environment

Secrets live in Doppler — never in `.env` files committed to git, never hardcoded.

For local development without Doppler:
```bash
cp .env.example .env   # then populate manually from `supabase status`
```

Required env vars:
- `ANTHROPIC_API_KEY`
- `SUPABASE_URL` — e.g. `http://127.0.0.1:54321`
- `SUPABASE_SECRET_KEY` — starts with `sb_secret_` (replaces old service_role key)
- `SUPABASE_PUBLISHABLE_KEY` — starts with `sb_publishable_` (replaces old anon key)
- `DATABASE_URL` — direct Postgres, e.g. `postgresql://postgres:postgres@127.0.0.1:54322/postgres`
- `HUBSPOT_TOKEN`
- `APOLLO_API_KEY`
- `LOG_LEVEL` — default `info`

## Local Dev Setup

Supabase runs separately from Docker Compose:
```bash
supabase start                  # start local Supabase
supabase db reset               # apply all migrations from scratch
docker compose up               # start FastAPI
```

To run tests (requires Supabase already running):
```bash
pytest                          # or: doppler run -- pytest
```

## Database

- Schema is defined in `supabase/migrations/0001_initial_schema.sql`
- Reference doc is `docs/SCHEMA.md` — keep them in sync
- Migrations are applied with `supabase db reset` (local) or `supabase db push` (cloud)
- Never edit the database by hand — always write a migration
- RLS is enabled on all tables; permissive `service_role` policy for v1

## What NOT to Do

- Do not put business logic in n8n — n8n triggers FastAPI endpoints and nothing more
- Do not let agents execute CUD operations directly — always go through the action queue
- Do not store secrets in code, `.env` files in git, or anywhere other than Doppler
- Do not build agent-specific UI before the shared approval inbox exists
- Do not use synchronous DB or HTTP calls — this is an async codebase
- Tests must never run against the real database (port 54322). Tests use TEST_DATABASE_URL pointing to port 54323. The transaction-rollback pattern in conftest.py ensures no test data persists.

## Current Build Status

Check `docs/STACK.md` for the current sprint and what's been completed.
When you finish a task, update `docs/STACK.md` with what changed.

## Agent Roster

| Agent | Status | Trigger |
|---|---|---|
| SDR Researcher | 🔲 Not started | New account in HubSpot / manual |
| Outreach Agent | 🔲 Not started | SDR Researcher completes |
| Content Writer | 🔲 Not started | Manual or scheduled |
| Proposal Generator | 🔲 Not started | Deal stage change in HubSpot |
| Slide Deck Agent | 🔲 Not started | Proposal Generator completes |
| Revenue Recognition | 🔲 Not started | Monthly schedule |

Update the status emoji when an agent is in progress (🔄) or complete (✅).
