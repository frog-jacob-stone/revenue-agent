# Revenue Agents

Project-specific vocabulary. Use these terms exactly in code, prompts, docs, and PR descriptions. When a term is missing, add it here rather than improvising. General programming concepts (timeouts, retries, error types) don't belong here even if the project uses them.

## Language

### Agents

**Front-door agent**:
The single conversational agent users chat with. Slug `revenue-ops`. The only agent with action tools attached and the only one that drives a tool-use loop.
_Avoid_: chat agent, assistant.

**Specialist agent** _(synonym: worker agent)_:
A single-turn agent invoked via `invoke_agent` or `ask_agent`. No autonomy, no tool loop. Has identity, a slug, and a row in the `agents` table. Examples: `bdr`, `revenue-recognition`, `content-orchestrator`, `voice-critic`, `accuracy-critic`.
_Avoid_: sub-agent, helper agent.

**Inline prompt**:
A single-turn LLM call made by a graph node with a fixed `SYSTEM_PROMPT` constant, attributed via `Attribution` but with no agent class. Not an agent — has no identity, no autonomy, one caller. Lives in `app/orchestrator/graphs/{kind}.py` or a sibling `_prompts.py`.
_Avoid_: anonymous agent, prompt-only agent.

### Approval flow

**Propose-Approve-Execute**:
The unbreakable rule: every create/update/delete flows through `agent proposes → approval (pending) → human approves → executed | failed`. No write without an approved approval row.
_Avoid_: approval pattern, action queue, hitl flow.

**Approval**:
A row in the `approvals` table representing a pending or historical write. The inbox UI sources from this table only.
_Avoid_: action, task, pending write. (The `actions` table is gone — do not reintroduce that word.)

**Audit event**:
A row in `audit_log` representing one state transition. Every state-changing service function writes one via `write_audit_event()`. Event names are constants in `app/orchestrator/events.py` — never string literals.
_Avoid_: log entry, trace event.

### Orchestrator

**Graph**:
A LangGraph `StateGraph` defined in `app/orchestrator/graphs/{kind}.py`, exporting `build_graph() -> GraphSpec`. The static definition.
_Avoid_: workflow definition, chain, pipeline.

**Workflow**:
A runtime instance of a graph. Has a `workflow_id`, a checkpoint state, and rows in `workflows` + `audit_log`.
_Avoid_: run, execution.

**Critique loop**:
The reusable draft → critic → loop/fail pattern in `app/orchestrator/critique_loop.py`. Attached via `add_critique_loop(...)`.
_Avoid_: review loop, validation loop.

### LLM dispatch

**LLM dispatcher** _(synonym: dispatcher)_:
The single module that absorbs the LLM call. Provider-agnostic interface; provider knowledge stays inside. Every LLM call in the system goes through it. Lives at `app/integrations/llm.py`.
_Avoid_: LLM client, OpenAI wrapper, chat client.

**Attribution**:
The frozen dataclass that names *who* is calling the LLM and *why*. Required argument on every dispatch — there is no ambient/context-var form. Fields: `agent_slug`, `purpose`, `workflow_id?`, `thread_id?`. Lands on every `llm_calls` row. `agent_slug` is `None` for inline calls that have no owning agent (rare).
_Avoid_: LLM context, call context, trace context.

**LLM provider**:
A Protocol/port implemented by adapters that know one model API. OpenAI is the production adapter. Test adapters supply scripted responses. The dispatcher routes to a provider; callers never see one.
_Avoid_: LLM backend, model client.

**Owning agent**:
The agent identity a workflow's work attributes to. Declared as `GraphSpec.owning_agent` (the default) and overridable per invocation via `runner.start(owning_agent=...)`. The runner seeds it into graph state as `_owning_agent_slug`; every LLM call inside the workflow inherits it via the graph's `_attribution(state, purpose)` helper, so `llm_calls.agent_slug` reflects the work's owner — not a per-node persona. Voice/accuracy critics and other sub-step LLM calls share the owning agent's slug; their distinct role is captured by `purpose`.
_Avoid_: workflow agent, runner agent.

**Invoking agent**:
The agent that triggered a workflow to start (a chat tool call, an `ask_agent`, a cron job — though cron has no agent). Distinct from the **owning agent**. The relationship is captured by the workflow's spawn link (`parent_workflow_id`) and audit trail, not by `agent_slug` on `llm_calls` rows. Example: when `revenue-ops` calls `create_post`, the spawned `content_creation` workflow's owning agent is `content-orchestrator`; the invoking agent is `revenue-ops`.
_Avoid_: caller agent, triggering agent.

## Example dialogue

> **Dev:** I'm adding a node to the outreach graph that needs an LLM call to score a lead. Where does it go?
> **Domain:** Inline prompt — single-turn, one caller, no identity. Put the `SYSTEM_PROMPT` in `app/orchestrator/graphs/outreach.py` or a sibling `_prompts.py`. Call the dispatcher with an `Attribution` whose `purpose` is something like `"outreach.score_lead"`.
> **Dev:** Should the score be written straight to the lead row?
> **Domain:** If it's a *write*, no — propose-approve-execute. The node returns a `_propose` payload; the graph pauses; the inbox surfaces an approval; on approve the execute node writes the row and emits an audit event.
> **Dev:** Even for a lead score? That feels heavy.
> **Domain:** If you'd want a human to see it before HubSpot mutates, yes. If it's just an internal scratch value the workflow uses, then it's not a write — recompute or stash it in graph state instead. State writes through the checkpointer don't go through approvals; mutations to first-party data do.

## Flagged ambiguities

_None recorded yet._
