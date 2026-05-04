# CLAUDE.md — Revenue Agent System

AI-powered revenue operations for Frogslayer. Agents replace an entire revenue team — operational infrastructure, not a personal assistant.

Reference docs:
- `docs/ARCHITECTURE.md` — system architecture and agent pattern
- `docs/SCHEMA.md` — database (mirror of `supabase/migrations/`)
- `docs/AGENTS.md` — agent roster and prompts
- `docs/BACKLOG.md` — what's next, deferred, blocked, open questions

## Unbreakable Rules

1. **No write without an approved action row.** Every create/update/delete flows through:
   ```
   agent proposes → action (proposed) → human approves → system executes → completed | failed
   ```
   Every state transition writes a row to `audit_log`.

## Planning
- Save all plans to `.agent/plans/` folder
- Naming convention: `{sequence}.{plan-name}.md` (e.g., `1.auth-setup.md`, `2.document-ingestion.md`)
- Plans should be detailed enough to execute without ambiguity
- Each task in the plan must include at least one validation test to verify it works
- Assess complexity and single-pass feasibility - can an agent realistically complete this in one go?
- Include a complexity indicator at the top of each plan:
  - ✅ **Simple** - Single-pass executable, low risk
  - ⚠️ **Medium** - May need iteration, some complexity
  - 🔴 **Complex** - Break into sub-plans before executing

## Code Conventions

- Routers validate input and call services; routers contain no business logic.
- Services hold business logic; services never call routers.
- Agents propose actions only; agents never call HubSpot/Gmail/Harvest directly.
- Every state-changing service function calls `write_audit_event()`.
- Async everywhere. Pydantic v2 (`model_config`, not `class Config`).
- Schema changes go through migrations in `supabase/migrations/` — never edit the DB by hand.
- Tests use `TEST_DATABASE_URL` (port 54323), never the prod port (54322).

## Progress
Check PROGRESS.md for current module status. Update it as you complete tasks.

## Keep These Docs in Sync

After a change, update whatever just went stale — this is not optional:

| If you... | Update... |
|---|---|
| Wrote a migration | `docs/SCHEMA.md` |
| Changed agent boundaries, layering, or integration flow | `docs/ARCHITECTURE.md` |
| Added or retired an agent | `docs/AGENTS.md` |
| Finished, started, deferred, or blocked work | `docs/BACKLOG.md` |
