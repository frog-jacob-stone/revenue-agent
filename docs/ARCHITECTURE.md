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

## Prompt Chain Orchestrator

Lives in `app/orchestrator/`. Drives workflows whose `pattern` is set (see `docs/SCHEMA.md` "Agentic Patterns") through a declarative chain of `Step`s. Each step writes one row to `actions`; tool calls, LLM calls, and critiques auto-progress, while `checkpoint` and `execution` steps pause for human approval.

- **Chain definition.** `Chain(kind, pattern, agent_slug, steps=(...))` registered at module import time via `register_chain()`. Looked up by workflow `kind`.
- **Resume model.** When a paused action is approved, the approval router schedules `orchestrator.resume(workflow_id)` via FastAPI `BackgroundTasks` so the HTTP response returns immediately. Server restart loses in-flight resumes — recovery is via re-triggering the workflow (a UI-driven "Resume" button is on the backlog along with a real worker queue).
- **State.** Reconstructed from `actions` rows on every entry; the orchestrator holds no in-memory state across calls. `WorkflowState` is a runtime view, never persisted.
- **Reflection loops.** A `CritiqueStep` declares `critiques_step_index` and `max_attempts`. On fail with budget remaining, the orchestrator rewinds `current_step` to the critiqued step and writes a retry attempt (`retry_of_action_id` chains back to the prior failed attempt). Budget exhausted → workflow `failed`.
- **Legacy approval flow** (action whose workflow has no `pattern`) is unchanged: approve → execute synchronously → complete.

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
