# Architecture

The durable shape of the system. Update this when boundaries, layering, or integration flow change.

## Stack

| Layer | Tool |
|---|---|
| API / agent logic | FastAPI + Python 3.12 + OpenAI |
| Memory & state | Supabase (Postgres + pgvector) — agent memory, approval queue, audit log |
| UI | React + TypeScript + Vite (`ui/`) |
| Secrets | Doppler (local and cloud, same config) |
| Runtime | Docker Compose locally → Railway/Render later |

## Authentication

- The FastAPI app sits behind a single auth gate: `app/auth.py::get_current_user` is wired as `dependencies=[…]` on every router in `app/main.py`. The only public endpoint is `/healthz`. Anything that can't produce a valid bearer token gets a 401.
- Tokens are Supabase-issued JWTs. Verification uses the project's JWKS (`{SUPABASE_URL}/auth/v1/.well-known/jwks.json`) with `ES256`/`RS256`. A legacy `SUPABASE_JWT_SECRET` HS256 fallback exists for older projects and for tests.
- The UI (`ui/src/`) uses `@supabase/supabase-js` for sign-in (email + password). Every API call goes through `authedFetch` in `ui/src/api.ts`, which attaches `Authorization: Bearer <access_token>`. A 401 response signs the user out and bounces them to `/login`.
- DB access from FastAPI stays on the asyncpg service-role connection — no per-request user switching at the DB layer. RLS is on for every `public` table with a `service_role`-only policy. Defense-in-depth against accidental anon-key exposure; user-scoped policies are deferred until multi-user.

## The Propose / Approve / Execute Pattern

No agent may execute a create, update, or delete without a prior approved approval row. Per [ADR-0002](adr/0002-tools-not-graphs.md):

```
tool returns AwaitingApproval(executor, payload, …)
  → approvals row (pending, executor set)
  → human approves (UI inbox only)
  → grant handler invokes the named executor in a background task
  → approvals row → executed | failed
```

- The `approvals` table is the queue of pending and historical work.
- The `audit_log` table receives a row at every state transition.
- The approval inbox in the UI is the canonical (and only) surface for grant.
- Agents and tools never call HubSpot, Gmail, Harvest, etc. directly. The boundary is enforced structurally — executors live in their own registry and are **never** added to any agent's `allowed_tools`. This is [Unbreakable Rule #3](../CLAUDE.md): approvals are human-only.

## Layering

```
router  →  service  →  (agent | integration client | db)
                   ↘  tool (Done | AwaitingApproval | Blocked)
                   ↘  executor (post-approval side effects only)
```

- **Routers** validate input and call services. No business logic.
- **Services** hold business logic. Never call routers. Every state-changing service function calls `write_audit_event()`.
- **Agents** are services that drive LLM tool-call loops. They propose actions by returning `AwaitingApproval` from a tool. They never call third-party systems on their own.
- **Tools** (`app/tools/`) are the unit of agent capability. A tool returns one of `Done | AwaitingApproval | Blocked`. The runtime — `app/orchestrator/dispatch.py::dispatch_tool` — pattern-matches the return shape, writes audit + (for `AwaitingApproval`) approval rows, and hands a status dict back to the LLM.
- **Executors** (`app/executors/`) are the post-approval side-effect performers. They live in a registry separate from tools, indexed by name, and are invoked only by the approval grant handler. Never added to any agent's `allowed_tools`.
- **Integration clients** (HubSpot, Gmail, Harvest, Airtable) are called only by executors and by read-only services that an agent legitimately needs.
- All DB, HTTP, and agent calls are async.

## LLM dispatch

Every LLM call in the system flows through the dispatcher at `app/integrations/llm.py`. Callers reach for `dispatch()` (non-streaming) or `dispatch_stream()` (streaming with `StreamDelta` chunks + a terminal `LlmResponse`). Provider details, the OpenAI SDK types, request snapshotting, latency timing, and the `llm_calls` row write all live inside the dispatcher. Nothing outside `app/integrations/` imports from the `openai` package.

Attribution is structural, not ambient: every call passes `Attribution(agent_slug, purpose, workflow_id?, thread_id?)` as a required argument. `purpose` is a dotted free-form label that lands on `llm_calls.purpose` and is how telemetry gets sliced (`"chat"`, `"agent:bdr"`, `"create_post.voice_review"`, etc.). There is no contextvar form — forgetting attribution is a type error, not a silent NULL.

Tests scope a fake provider with `use_provider(FakeProvider(...))` from `tests/fakes/llm.py`. The `LlmProvider` Protocol is the internal seam — production adapter is `_OpenAiProvider` (`app/integrations/_openai_provider.py`). A second provider lands as another adapter behind the same seam; no caller changes.

## Agent Scoping Principles

An agent has a single coherent identity: one job, one audit trail, one approval context. To decide whether a capability belongs in an existing agent or a new one, ask:

1. **Same trigger schedule?** Different cadence or trigger source → likely a separate agent.
2. **Same approval context?** Different approver, risk level, or stakes → separate it.
3. **Same action type categorically?** Sending emails and generating invoices are categorically different even within one business domain.
4. **Does it propose actions, or just answer?** Read-only "answer questions" agents are separate from write-proposing "do work" agents, even on the same data.

**Read-only vs. write-proposing is a hard split.** An analytics agent and an operations agent for the same domain are distinct agents — different audit trails, different inbox behavior, different UI presentation.

**Agents vs. inline LLM calls.** Classes in `app/agents/` represent identity-bearing things: the conversational front door (`RevenueOpsAgent`) or worker personas meant to be invoked via `ask_agent` / `run_agent_task` and accountable in the audit trail (`BDRAgent`, `RevenueRecognitionAgent`, `ContentOrchestratorAgent`). Single-turn, fixed-prompt LLM calls made inline inside a tool are NOT agents — they live in the tool file (or a sibling `_prompts.py`) as `MODEL` + `SYSTEM_PROMPT` constants, attributed via `Attribution(agent_slug=..., purpose=...)` on the dispatch call. Rule of thumb: if a "thing" has no identity, no autonomy, and one caller, it's a prompt, not an agent.

**Chat is an interface, not an agent type.** Chat is one of several trigger sources (webhook, schedule, chat). All paths that propose writes flow through the approval inbox.

**Single front-door pattern.** Exactly one agent is conversational: `RevenueOpsAgent` (slug `revenue-ops`), defined in `app/agents/revenue_ops_agent.py`. The user only ever chats with this agent. Specialist agents (`revenue-recognition`, `content-orchestrator`, etc.) are plain `BaseAgent` workers invoked via `ask_agent` from the front door, which routes through `run_agent_task`. The front-door slug is hardcoded as `FRONT_DOOR_SLUG` in `app/services/chat_turn.py` — there is no agent picker.

## Orchestrator runtime (`app/orchestrator/`)

Post-ADR-0002 the orchestrator is small. Its surface:

- **`dispatch_tool(tool, ctx, args)` — `app/orchestrator/dispatch.py`.** The runtime that pattern-matches a tool's return value. On `Done(payload)` it writes a `tool.completed` audit row and hands `payload` back to the LLM. On `AwaitingApproval(executor, payload, summary, action_type, risk_level)` it creates the approval row (status `pending`, `executor` populated) and hands `{"status": "awaiting_approval", "approval_id", "summary"}` back to the LLM. On `Blocked(reason, hint)` it writes a `tool.blocked` audit row and tells the LLM what's missing so the next assistant turn can explain to the user.
- **`run_agent_task(slug, task, ctx, *, progress=None)` — `app/orchestrator/agent_invoke.py`.** The single agent-invocation primitive. Brackets the call with `agent.invoked` / `agent.completed` / `agent.failed` audit events. Drives a ReAct loop when the target has `allowed_tools` (re-entering `dispatch_tool` for each tool call) and falls back to a single-turn dispatch when it doesn't. `ask_agent` is the canonical caller; tests also use it directly.
- **`events` — `app/orchestrator/events.py`.** Audit-event constants. Imported everywhere; never written as string literals.

What's intentionally *not* here:
- **No graph engine.** Conditional branches, retry loops, and critique-and-rewrite live as inline Python (`for`, `if`, `while`) inside the tool that owns the workflow. The old LangGraph runner / state / spawn / critique_loop machinery was removed in plan 19. See [ADR-0002](adr/0002-tools-not-graphs.md).
- **No multi-gate approvals.** Every production workflow today is single-gate (or zero-gate). If a future workflow needs more than one approval, revisit; do not pre-build the abstraction.
- **No persistent workflow state.** Audit log + approvals carry enough state to reconstruct what happened. The `workflows` table was dropped in migration `0022`.

## Tools (`app/tools/`)

A tool exports a `ToolDefinition` constant — name, description, OpenAI input schema, async `execute` callable. The `execute` returns `Done | AwaitingApproval | Blocked` (the `ToolReturn` union, defined in `app/tools/base.py`). Helpers:

- **`ProgressEmitter`** — tools may emit `tool_step_started` / `tool_step_completed` events for in-tool observability. These bubble up to the chat UI and appear as nested activity lines under the tool's call.
- **`ToolContext`** — carries `agent_id`, `agent_slug`, optional `workflow_id`, and the optional `ProgressEmitter`. Passed by `dispatch_tool`.

Today's production tools include: read-only HubSpot lookups (`get_contact_by_email`, `get_company_by_id`, `get_form_submission`), social-content tools (`create_post`, `publish_post`), revenue tools (`trigger_revenue_recognition`, `get_revenue_data`, `get_revenue_data_slim`), and the agent-delegation tool (`ask_agent`).

## Executors (`app/executors/`)

Executors are the only code path that performs side effects against external systems on behalf of an approval. Each executor has a name (matching the `executor` column on its approval rows), a description, and an async `execute(ctx, payload)` callable. They are registered in `app/executors/registry.py::EXECUTORS_BY_NAME`. Today: `post_to_linkedin`, `write_rev_rec_entries`. They are **never** exposed to an LLM — that's the structural enforcement of Unbreakable Rule #3.

## Approval Inbox

The inbox UI sources from `/approvals` only. `Approval` rows discriminate by status: `pending` is the queue; `approved/rejected/executed/failed` are history. Editing the `executed_payload` before approving is supported — the executor runs with the edited shape if present.

## Multi-Agent Orchestration

The system is a **single front door + specialist workers**. There is one conversational agent (`revenue-ops`); every other agent is a worker.

Dispatch shape:

- **User → front door (chat).** `app/services/chat_turn.py::start_turn` drives the OpenAI tool-call loop against `RevenueOpsAgent`. The front door has the action tools (`trigger_revenue_recognition`, `create_post`, `publish_post`, etc.) directly.
- **Front door → specialist (LLM delegation).** The front door calls `ask_agent(target_slug, prompt)` for domain-specific explanation or reasoning. `ask_agent` writes the outbound prompt to `agent_messages`, calls `run_agent_task` (which is either single-turn or a ReAct loop depending on whether the target has `allowed_tools`), writes the inbound reply, and returns `{answer, thread_id}`. The specialist's `system_prompt` is what's load-bearing.

Specialists never appear in the chat surface. To iterate on a specialist's prompt, drive `run_agent_task` from a test or shell — there is intentionally no admin chat endpoint for them.

## Anti-Patterns

- Agents or tools mutating an approval's status (calling `approve()` / `reject()` / `mark_executed`). Approvals are human-only — Unbreakable Rule #3.
- Adding an executor to any agent's `allowed_tools`. This collapses the trust boundary.
- Calling integration clients (HubSpot, Gmail, Airtable, etc.) from a tool. Reads are fine; writes belong in an executor that runs after approval.
- Secrets anywhere outside Doppler.
- Re-introducing a graph engine for what could be a `for` loop. See [ADR-0002](adr/0002-tools-not-graphs.md).
