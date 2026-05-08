# CLAUDE.md — Revenue Agent System

AI-powered revenue operations for Frogslayer. Agents replace an entire revenue team — operational infrastructure, not a personal assistant.

Reference docs:
- `docs/ARCHITECTURE.md` — system architecture and agent pattern
- `docs/SCHEMA.md` — database (mirror of `supabase/migrations/`)

## Unbreakable Rules

1. **No write without an approved approval row.** Every create/update/delete flows through:
   ```
   graph node proposes → approval (pending) → human approves → graph resumes → executed | failed
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

## Development Flow
1. **Plan** - Create a detailed plan and save it to `.agent/plans/`
2. **Build** - Execute the plan to implement the feature
3. **Validate** - Test and verify the implementation works correctly. Use browser testing where applicable via an appropriate MCP
4. **Iterate** - Fix any issues found during validation

## Code Conventions
- Routers validate input and call services; routers contain no business logic.
- Services hold business logic; services never call routers.
- Agents propose actions only; agents never call HubSpot/Gmail/Harvest directly.
- Every state-changing service function calls `write_audit_event()`.
- Async everywhere. Pydantic v2 (`model_config`, not `class Config`).
- Schema changes go through migrations in `supabase/migrations/` — never edit the DB by hand.
- Tests use a separate test DB (default: `postgres_test` on port 54322); the conftest drops/creates it each session. Do not point `TEST_DATABASE_URL` at the live `postgres` DB.
- Run tests with plain `pytest` (or `python3 -m pytest`). `TEST_DATABASE_URL` is set automatically by `pytest-env` in `pyproject.toml` — do **not** prefix commands with `TEST_DATABASE_URL=...`.

### Orchestrator (LangGraph)
- Graphs live in `app/orchestrator/graphs/{kind}.py`. Each file exports a `build_graph() -> GraphSpec` factory; the central registry at `app/orchestrator/graphs/__init__.py::register_all` calls `runner.register(...)` for each. App startup runs `await runner.init()` then `register_all(runner)`.
- Use the audit event constants in `app/orchestrator/events.py` for any new audit calls — no string literals.
- Agent invocation from a node: `await invoke_agent(slug, input, ctx)`. Never instantiate agent classes inside nodes.
- Sub-workflows: `await spawn_workflow(kind, input, parent_workflow_id=ctx.workflow_id)`.
- Graph state TypedDicts must declare `_propose: NotRequired[dict]` (or extend `BaseGraphState` from `app/orchestrator/state.py`) for any node that requests human approval — LangGraph drops undeclared keys.
- Checkpointer is `AsyncPostgresSaver` against `settings.database_url` via its own psycopg pool (separate from the app's asyncpg pool). LangGraph's checkpoint tables are created idempotently by `setup()` at startup; migration 0012 is a marker only.
- Loop edges are a supported idiom (see `app/orchestrator/graphs/{rev_rec,outreach,content_creation}.py` and `_critique_poc.py`). Graph authors own loop-termination logic — the framework imposes no infinite-loop guard.
- LangGraph treats node names and state-key names as a single namespace; pick distinct names (e.g., the outreach graph uses `compose_email` as the node and `draft_email` as the state field).
- Production graphs: `content_publish`, `rev_rec_monthly`, `outreach_chain`, `content_creation`. Inbox UI sources solely from `/approvals`.
- `invoke_agent` is currently Anthropic-only. The outreach graph routes its three Anthropic-backed agents through `invoke_agent`; the content_creation graph keeps direct `call_openai` for its OpenAI-backed agents until a provider-aware refactor unifies dispatch.
- Agent-to-agent communication: `app/services/agent_messages.py` records turn-by-turn exchanges; `ask_agent` (in `app/tools/agent_tools.py`) is the canonical delegation tool. Both messages (outgoing prompt + incoming reply) are written under one `thread_id`. `ToolContext.workflow_id` links node-driven calls to their workflow. Demo graph: `app/orchestrator/graphs/_multi_agent_demo.py`.

## Progress
Check PROGRESS.md for current module status. Update it as you complete tasks.

## Keep These Docs in Sync

After a change, update whatever just went stale — this is not optional:

| If you... | Update... |
|---|---|
| Wrote a migration | `docs/SCHEMA.md` |
| Changed agent boundaries, layering, integration flow, or any archectural patterns | `docs/ARCHITECTURE.md` |
| Changed Product level requirements | `PRD.md` |
