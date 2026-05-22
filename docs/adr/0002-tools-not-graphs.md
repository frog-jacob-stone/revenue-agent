# Prescribed workflows are tools, not LangGraph graphs

Prescribed workflows are implemented as tools the orchestrator agent calls, not as LangGraph graphs. A tool returns one of three outcomes: `Done(payload)`, `AwaitingApproval(executor, payload, …)`, or `Blocked(reason, hint)`. The approval router invokes a registered **executor** after a human approves an `AwaitingApproval` row. Executors are a separate registry from tools and are not LLM-callable, which makes the trust boundary structural.

This supersedes the "prescribed workflow" part of [ADR-0001](0001-supervisor-worker-multi-agent-architecture.md). The supervisor-worker hierarchy (orchestrator agent → domain agents via `ask_agent`, ReAct loops driven by `run_agent_task`) is unchanged.

## Why not LangGraph

LangGraph was earning its keep on exactly one feature: pause-and-resume across processes for **multi-gate** workflows — `interrupt_before` plus `AsyncPostgresSaver` checkpointing so a paused workflow can resume hours later from a fresh process.

Auditing the production graphs:

| Graph | Approval gates | Notes |
|---|---|---|
| `content_creation` | 0 | Pure sequence + an in-loop voice review. |
| `content_publish` | 1 | Before LinkedIn post. |
| `outreach_chain` | 1 | Before Gmail send. Retired during this change (not in active use). |
| `rev_rec_monthly` | 2 (today); **1 after reframe** | "Configure projects" was modelled as a gate but is actually a precondition failure — the user fixes Airtable, then re-runs. |

After the rev_rec reframe, every workflow we care about is single-gate. None of the multi-gate machinery is load-bearing. Walking the roadmap (outreach + follow-up, proposal generation, invoice operations, SDR enrichment, slide decks, contract negotiation), every apparent multi-gate case decomposes either into (a) single-gate sequences linked by orchestrator decisions, or (b) separate triggers linked by external events. True multi-gate is rare in revenue ops and shows up mainly in compliance / financial-close / regulatory flows — none in scope here.

LangGraph also charges a steady tax for cases that don't need it: a TypedDict per graph, `GraphSpec` + `build_graph()` boilerplate, the runner's `_propose` unpack, state-key namespacing, the central registry, the checkpointer pool, the workflow-event tail, and the `_owning_agent_slug` threading. For 0-gate and 1-gate flows that's pure ceremony.

## What a tool returns

```python
class ToolResult:  # one of:
    Done(payload: dict)
    AwaitingApproval(
        executor: str,             # registered executor name
        payload: dict,             # input passed to the executor after approval
        summary: str,
        reasoning: str | None = None,
        risk_level: str | None = None,
        action_type: str = "other",
    )
    Blocked(reason: str, hint: dict | None = None)
```

- **`Done`** — the work completed; the LLM sees the payload as the tool result.
- **`AwaitingApproval`** — the tool computed a proposed change but the write is gated. The runtime writes an `approvals` row, the LLM sees `{"status": "awaiting_approval", "approval_id": "…", "summary": "…"}` and is expected to surface that to the user. The LLM cannot act further on it.
- **`Blocked`** — a precondition isn't met (e.g., rev_rec needs configured projects). The runtime records the block; the LLM sees `{"status": "blocked", "reason": "…", "hint": {…}}` and tells the user what to fix.

Tools may emit progress events via the existing `ProgressEmitter` for in-tool observability (e.g., "drafting", "running voice review attempt 2/3"). Critique loops, retries, and conditional branches live inline as Python — `while`, `for`, `if`. The vanished `critique_loop.py` graph helper is not replaced; if a future tool needs the multi-critic monotonic-counter pattern, extract a helper then.

## Executors

An executor is a function the approval router invokes after a human approves an `AwaitingApproval`. Executors live in a registry separate from tools:

- Tools are LLM-callable; their schema is exposed via `allowed_tools`.
- Executors are **never** in `allowed_tools` and have no LLM-facing schema. They are referenced only by the `executor` field of an `AwaitingApproval` and looked up by the approval-grant handler.

The structural separation is the point: an LLM cannot reach an executor through any tool surface. This makes [Unbreakable Rule #3](../../CLAUDE.md) (approvals are human-only) enforced by the type system, not by convention.

## Audit and observability

The workflow-level audit vocabulary (`WORKFLOW_STARTED`, `WORKFLOW_PAUSED`, `WORKFLOW_COMPLETED`, node-level events) is replaced by tool-level events. The activity tree loses one nesting level — `tool → step (ProgressEmitter) → maybe approval → maybe executor` instead of `tool → workflow → node → subagent_call`. `activity_builder.py` loses its `_NODE_LABELS` / `_WORKFLOW_LABELS` maps; `audit_tail.py` tails tool runs instead of workflow rows.

## Considered options

**Option discarded: keep LangGraph for `rev_rec_monthly` only.** Pragmatic but leaves a small graph engine, runner, checkpointer, state convention, and event vocabulary alive to serve one workflow. The maintenance cost of a tier-2 mechanism with one user is higher than the cost of one re-implementation when a true multi-gate case appears.

**Option discarded: build a generic "tool that yields approval" coroutine abstraction.** Risks reinventing a worse LangGraph. With single-gate as the design point, the explicit return-shape contract (`Done | AwaitingApproval | Blocked`) is cleaner than implicit yield semantics.

**Option discarded: model executors as ordinary tools.** Smaller surface, but it would mean an LLM could call a writer directly if it ever ended up in `allowed_tools`. The trust boundary becomes a convention rather than a type. Rejected for [Rule #3](../../CLAUDE.md).

**Option discarded: every approval-gated step a separate orchestrator tool call (n8n's recommended pattern).** Considered for `rev_rec_monthly`. With rev_rec reframed to single-gate, the question is moot. Revisit if a true multi-gate use case lands.

## Consequences

- `app/orchestrator/runner.py`, `state.py`, `spawn.py`, `critique_loop.py`, `graphs/__init__.py`, and the `graphs/*.py` files are removed. The `langgraph` and `langgraph-checkpoint-postgres` dependencies go with them.
- `app/orchestrator/agent_invoke.py` keeps `run_agent_task` (the ReAct loop for domain agents, ADR-0001). `invoke_agent` is deleted — its only non-test callers were the multi-agent demo (also deleted) and aspirational "demoted worker" docstrings.
- The `workflows` table is repurposed or retired in favour of recording tool runs in `audit_log`. (Decided in implementation.)
- The `approvals` table gains an `executor` column (or stores it on `proposed_payload` — decided in implementation).
- `app/services/audit_tail.py` and `app/services/activity_builder.py` simplify; the node/kind label maps are removed.
- `_owning_agent_slug` threading through graph state disappears. A tool's `Attribution.agent_slug` comes from its calling agent's `ToolContext.agent_slug`. Tools that internally make LLM calls (the former graph-node inline prompts) attribute to the calling agent by default and may override per-call via the `purpose` field.
- `CONTEXT.md` is updated: `Graph`, `Critique loop` retired; `Prescribed workflow` redefined; `Done`, `AwaitingApproval`, `Blocked`, `Executor` added.
- The migration scope is `rev_rec_monthly`, `content_creation`, and `content_publish`. `outreach_chain`, `_multi_agent_demo`, and the `POST /workflows/outreach` endpoint are deleted, not migrated.
