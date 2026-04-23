# CLAUDE.md — Revenue Agent System

AI-powered revenue operations for Frogslayer. Agents replace an entire revenue team —
this is operational infrastructure, not a personal assistant.

Docs: `docs/STACK.md` (sprint status) · `docs/SCHEMA.md` (database) · `docs/AGENTS.md` (agents)

## Stack

| Layer | Tool |
|---|---|
| API | FastAPI + Python 3.12 |
| LLM | Anthropic SDK (Claude) |
| Database | Supabase (Postgres + pgvector) |
| Secrets | Doppler (`doppler run -- uvicorn ...`) |
| Runtime | Docker Compose locally |
| UI | React + TypeScript + Vite (ui/) |
| Integrations | n8n → FastAPI (triggers only, no business logic in n8n) |

## The One Rule That Cannot Be Broken

**No agent may execute a create, update, or delete operation without a prior
`action.approved` row in the database.**

```
agent proposes → action row created (status: proposed)
human approves → action row updated (status: approved)
system executes → action row updated (status: completed or failed)
```

Every state transition writes a row to `audit_log`. No exceptions.

## Code Conventions

- **Routers contain no business logic.** Routers validate input and call services.
- **Services contain business logic.** Services never call routers.
- **Agents propose; the framework executes.** Agents never call HubSpot/Gmail directly.
- **Every service function that changes state must call `write_audit_event()`.**
- **Async everywhere.** All DB, HTTP, and agent calls must be async.
- **Pydantic v2.** Use `model_config`, not `class Config`.

## Secrets and Environment

Secrets live in Doppler — never in committed `.env` files, never hardcoded.

For local dev without Doppler:
```bash
cp app/.env.example app/.env   # populate from `supabase status`
cp ui/.env.example ui/.env     # VITE_ prefixed vars only
```

## Local Dev

```bash
supabase start && supabase db reset   # start Supabase, apply migrations
docker compose up                     # start FastAPI
cd ui && npm run dev                  # start UI on localhost:3000
pytest                                # run tests (Supabase must be running)
```

## Database

- Schema: `supabase/migrations/` — never edit the DB by hand, always write a migration
- Keep `docs/SCHEMA.md` in sync with migrations
- RLS enabled on all tables; permissive `service_role` policy for v1
- **Tests use `TEST_DATABASE_URL` (port 54323), never the real DB (port 54322).** The conftest.py transaction-rollback pattern ensures no test data persists.

## Build Status

Check `docs/STACK.md` for current sprint and completed work. Update it when you finish a task.
