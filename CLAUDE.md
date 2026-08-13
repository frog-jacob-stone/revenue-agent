# CLAUDE.md — Revenue Operations System

Operational infrastructure for Frogslayer's revenue operations — Harvest billing/invoicing automation, revenue recognition, and (planned) revenue reporting — with an approval-gated agent framework layered in for conversational and judgment-requiring tasks. Not a personal assistant, and not primarily an agent framework: the largest, most mature subsystem (billing/invoicing) is deliberately deterministic and has no agent in its path.

Reference docs:
- `docs/ARCHITECTURE.md` — system architecture, the RevOps automation and agent-framework pattern
- `docs/SCHEMA.md` — database (mirror of `supabase/migrations/`)

## Unbreakable Rules

1. **No write without a human authorizing that specific payload.** How that is obtained depends on who initiated it — see [ADR-0004](docs/adr/0004-operator-initiated-writes.md).

   **Agent-initiated** writes flow through the approval chain:
   ```
   tool returns AwaitingApproval → approval (pending) → human approves → executor runs → executed | failed
   ```

   **Operator-initiated** writes (the human is already in the UI) need no approval row, provided all three hold: the exact payload is shown before the click; the endpoint is human-only — never in any agent's `allowed_tools`, never an executor; and the transition writes `audit_log`. `tests/test_no_agent_approval_tools.py` enforces the second condition structurally.

   Either way, every state transition writes a row to `audit_log`.

2. **Every endpoint requires auth except `/healthz`.** Routers are registered in `app/main.py` with `dependencies=[Depends(get_current_user)]`. When you add a new router, register it the same way. JWT is verified by `app/auth.py` against the Supabase JWKS; tests stub `get_current_user` via `app.dependency_overrides`.

3. **Approvals are human-only.** Agents and tools may propose approvals (by returning `AwaitingApproval`); they may not call `approve()`, `reject()`, or otherwise mutate an approval's status. The inbox UI is the only path to approval action. Executors (the functions that run *after* approval) are registered in a separate registry and are never added to any agent's `allowed_tools` — the trust boundary is structural, not conventional. See [ADR-0002](docs/adr/0002-tools-not-graphs.md).

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
- Agents propose actions only; agents never call Harvest/Gmail/Airtable directly.
- Every state-changing service function calls `write_audit_event()`.
- Async everywhere. Pydantic v2 (`model_config`, not `class Config`).
- Schema changes go through migrations in `supabase/migrations/` — never edit the DB by hand.
- Tests use a separate test DB (default: `postgres_test` on port 54322); the conftest drops/creates it each session. Do not point `TEST_DATABASE_URL` at the live `postgres` DB.
- Run tests with plain `pytest` (or `python3 -m pytest`). `TEST_DATABASE_URL` is set automatically by `pytest-env` in `pyproject.toml` — do **not** prefix commands with `TEST_DATABASE_URL=...`.

### Orchestrator
- Prescribed workflows are **tools**, not LangGraph graphs (see [ADR-0002](docs/adr/0002-tools-not-graphs.md)). A tool returns one of `Done(payload)`, `AwaitingApproval(executor, payload, …)`, or `Blocked(reason, hint)`. The runtime writes the approval row, surfaces the result to the LLM as a status dict, and — on human approval — invokes the registered executor with the (possibly edited) payload.
- Use the audit event constants in `app/orchestrator/events.py` for any new audit calls — no string literals.
- Domain agent delegation: `await run_agent_task(slug, prompt, ctx)`. Drives the ReAct loop for agents with `allowed_tools`. Never instantiate agent classes outside the registry.
- Tools may emit progress events via `ProgressEmitter` for in-tool observability (e.g., `{"type": "step_started", "name": "compose_email"}`).
- Critique loops, retries, and conditional branches are inline Python (`for`, `while`, `if`) — there is no graph helper. Extract a shared helper only when at least two tools need the same pattern.
- Executors live in their own registry and are **never** added to any agent's `allowed_tools`. They are invoked by the approval-grant handler, not by the LLM.
- Production tools (workflow-shaped): `create_post`. `trigger_revenue_recognition` and `publish_post` still exist and still return `AwaitingApproval`, but ADR-0004 removed them from every agent's `allowed_tools` — no agent can propose an approval, so the inbox is empty by construction. Inbox UI still sources solely from `/approvals`.
- Agent-to-agent communication: `app/services/agent_messages.py` records turn-by-turn exchanges; `ask_agent` (in `app/agents/tools/agent/ask_agent.py`) is the canonical delegation tool. Both messages (outgoing prompt + incoming reply) are written under one `thread_id`.

## Progress
Check PROGRESS.md for current module status. Update it as you complete tasks.

## Keep These Docs in Sync

After a change, update whatever just went stale — this is not optional:

| If you... | Update... |
|---|---|
| Wrote a migration | `docs/SCHEMA.md` |
| Changed agent boundaries, layering, integration flow, or any archectural patterns | `docs/ARCHITECTURE.md` |
| Changed Product level requirements | `PRD.md` |
