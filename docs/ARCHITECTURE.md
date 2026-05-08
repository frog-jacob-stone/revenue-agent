# Architecture

The durable shape of the system. Update this when boundaries, layering, or integration flow change.

## Stack

| Layer | Tool |
|---|---|
| API / agent logic | FastAPI + Python 3.12 + Anthropic SDK |
| Memory & state | Supabase (Postgres + pgvector) — agent memory, action queue, audit log |
| UI | React + TypeScript + Vite (`ui/`) |
| Integration bus | n8n — triggers and third-party calls only, no business logic |
| Secrets | Doppler (local and cloud, same config) |
| Runtime | Docker Compose locally → Railway/Render later |

## The Propose / Approve / Execute Pattern

No agent may execute a create, update, or delete without a prior approved action row.

```
agent proposes → action (proposed) → human approves → system executes → completed | failed
```

- The `actions` table is the queue of pending and historical work.
- The `audit_log` table receives a row at every state transition.
- The approval inbox in the UI is the canonical surface for review; Slack is a fallback notifier.
- Agents never call HubSpot, Gmail, Harvest, etc. directly. They emit a proposed action; the framework executes after approval.

## Layering

```
router  →  service  →  (agent | integration client | db)
                  ↘  orchestrator  →  chain → step (per-step agent or integration)
```

- **Routers** validate input and call services. No business logic.
- **Services** hold business logic. Never call routers. Every state-changing service function calls `write_audit_event()`.
- **Agents** are services that propose actions via Anthropic SDK. They never reach out to third-party systems on their own.
- **Integration clients** (HubSpot, Gmail, Harvest) are called only by services executing approved actions.
- **Orchestrator** drives multi-step chains for workflows whose `pattern` is set. See "Prompt Chain Orchestrator" below.
- All DB, HTTP, and agent calls are async.

## Integration Model

- n8n handles **triggers** (webhooks, schedules) and **third-party I/O** only.
- All complex logic lives in FastAPI. n8n calls FastAPI; FastAPI does not call n8n.
- New integrations: build the client in `app/`, expose a service, then wire the n8n trigger last.

## Agent Scoping Principles

An agent has a single coherent identity: one job, one audit trail, one approval context. To decide whether a capability belongs in an existing agent or a new one, ask:

1. **Same trigger schedule?** Different cadence or trigger source → likely a separate agent.
2. **Same approval context?** Different approver, risk level, or stakes → separate it.
3. **Same action type categorically?** Sending emails and generating invoices are categorically different even within one business domain.
4. **Does it propose actions, or just answer?** Read-only "answer questions" agents are separate from write-proposing "do work" agents, even on the same data.

**Read-only vs. write-proposing is a hard split.** An analytics agent and an operations agent for the same domain are distinct agents — different audit trails, different inbox behavior, different UI presentation.

**Chat is an interface, not an agent type.** The same agent can be triggered by webhook, schedule, or chat. All paths that propose writes flow through the approval inbox. Chat-only read-only agents never touch the inbox.

## Orchestrator (LangGraph)

Lives in `app/orchestrator/`. Workflows are LangGraph `StateGraph`s; checkpoint state is persisted by `AsyncPostgresSaver`. The `actions` table and the prompt-chain `Step` hierarchy are gone — graph state replaces them.

- **Graphs.** Each workflow kind is a `StateGraph` in `app/orchestrator/graphs/{kind}.py` exporting `build_graph() -> GraphSpec`. Approval gates are declared via `interrupt_before=("node_name",)` on the GraphSpec. The central registry at `app/orchestrator/graphs/__init__.py::register_all` calls `runner.register(...)` for each. App startup runs `await runner.init()` then `register_all(runner)`.
- **Approval bridge.** Nodes that need human review return `{"_propose": ProposeApproval(...)}` in their state. The runner reads `_propose` on pause, writes a row to the `approvals` table, and sets the workflow to `awaiting_approval`. The router (`POST /approvals/{id}/approve`) schedules `runner.resume(workflow_id)` via `BackgroundTasks`. On approve, the (possibly edited) `executed_payload` is pushed onto graph state via `aupdate_state` so the next node reads it. On reject, the workflow is marked `failed`.
- **Audit-event taxonomy.** Constants in `app/orchestrator/events.py` cover workflow lifecycle, node entry/exit, approval transitions, agent invocations, and sub-workflow spawn/complete. Call sites must import these constants — no string literals.
- **Persistence.** `AsyncPostgresSaver` (langgraph-checkpoint-postgres) runs against `settings.database_url` via its own psycopg pool, separate from the app's asyncpg pool. Its tables (`checkpoints`, `checkpoint_blobs`, `checkpoint_writes`, `checkpoint_migrations`) are created idempotently by `setup()` on first `runner.init()`. Migration 0012 is a marker only.
- **Multi-agent primitives.** `invoke_agent(slug, input, ctx)` in `app/orchestrator/agent_invoke.py` is the uniform agent entry point usable from any node, chat, or sub-agent. `spawn_workflow(kind, input, parent_workflow_id=...)` in `app/orchestrator/spawn.py` starts a child workflow linked to the parent for nested traces.
- **Loop pattern (iterate on human input).** A node may have an outgoing edge that points back to an earlier node, with an interrupt gate sitting on the loop edge. The canonical example is `rev_rec_monthly`: `validate_and_sync` → (incomplete) → `propose_configure` → [interrupt_before `apply_configure_or_loop`] → human approves → `apply_configure_or_loop` → loops back to `validate_and_sync`. One workflow_id covers the full iteration cycle. The graph author owns loop-termination logic; the framework imposes no infinite-loop guard.
- **Critique loop with shared draft.** A draft node feeds one or more critic nodes; critics on fail loop back to the draft. State carries per-critic `*_attempts` counters and a single `last_critique_feedback` slot that the next draft consumes and clears. The canonical example is `outreach_chain`: `compose_email` → `voice_critique` (max 3) → `accuracy_critique` (max 2); either critic loops back to `compose_email` on fail with budget remaining. Counters are independent (voice's attempts accumulate across the same draft sequence even when accuracy was the failure that caused the loop). Budget exhaustion routes to a terminal `failed_terminal` node. `content_creation` is the simpler single-critic version. Node names must not collide with state field names.
- **Production graphs.** `content_publish`, `rev_rec_monthly`, `outreach_chain`, `content_creation`. The `_multi_agent_demo` and `_critique_poc` graphs are not registered at startup — tests register them explicitly.
- **Provider dispatch.** `invoke_agent` is currently Anthropic-only. The outreach graph (Anthropic) uses it. The content_creation graph (OpenAI) calls `call_openai` directly; a provider-aware refactor is on the backlog.
- **Multi-agent messaging.** `app/services/agent_messages.py` (backed by the `agent_messages` table) records every agent-to-agent turn with a `thread_id` correlation key and an optional `workflow_id` link. The `ask_agent` tool (`app/tools/agent_tools.py`) is the canonical primitive: it writes the outbound prompt, invokes the target agent via `invoke_agent` (single-turn), writes the inbound reply, and returns `{answer, thread_id}`. `ToolContext` carries `workflow_id` so node-driven `ask_agent` calls automatically link their messages to the workflow. Threads are recorded but not yet *used* as conversational context — receivers see only the current question. The `_multi_agent_demo` graph is the canonical end-to-end example.

## Approval Inbox

The inbox UI sources from `/approvals` only. `Approval` rows discriminate by status: `pending` is the inbox queue; `approved/rejected/executed/failed` are history. Workflow trace renders the `audit_log` event timeline for the workflow.

## Multi-Agent Orchestration

Three patterns, in order of planned adoption:

1. **Explicit agent selection (v1).** User picks the agent in the chat UI. Simple, no magic.
2. **Router agent.** Reads the user's message, determines intent, hands off to a specialist. Build only after 4–6 specialists exist and real routing patterns are visible. Specialists remain directly selectable — the router is a convenience front door, not a gatekeeper.
3. **Full orchestrator.** Decomposes multi-step requests across multiple agents and coordinates execution.

## Anti-Patterns

- Business logic in n8n function nodes
- Agents executing CUD actions without an approved action row
- Secrets anywhere outside Doppler
- Agent-specific UI before the shared approval inbox exists
- Direct integration calls from agents (must route through services)
