# Revenue Agents

Project-specific vocabulary. Use these terms exactly in code, prompts, docs, and PR descriptions. When a term is missing, add it here rather than improvising. General programming concepts (timeouts, retries, error types) don't belong here even if the project uses them.

## Language

### Agents

**Orchestrator agent**:
The single conversational agent Jacob talks to. Slug `revenue-ops`. Stays thin: its tools are workflow triggers for prescribed processes and `ask_agent` for delegating to domain agents. Does not own domain tools — domain agents do.
_Avoid_: front-door agent, chat agent, assistant.

**Domain agent**:
A specialist agent that owns tools for a specific business domain and runs autonomously when delegated a task. Has a slug, a row in `agents`, and an `allowed_tools` set. When invoked via `ask_agent`, drives a ReAct loop — decides which tools to call, in what order — and returns a final answer. Examples: `bdr`, `content-orchestrator`, `revenue-recognition`.
_Avoid_: sub-agent, worker agent, helper agent.

**Prescribed worker**:
A single-turn agent invoked from inside a LangGraph graph node. No tool loop, no autonomy — the graph node owns the reasoning about what to call and when. Used when the process is fixed and changes require explicit code changes. Examples: `voice-critic`, `accuracy-critic`.
_Avoid_: specialist agent (too broad — use domain agent or prescribed worker).

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

### Delegation

**Agentic task**:
A task delegated to a domain agent via `ask_agent` where the agent decides the approach using its tools. No prescribed steps. The domain agent drives a ReAct loop to completion and returns the result. Contrast with a prescribed workflow.
_Avoid_: autonomous workflow, agent run.

**Prescribed workflow**:
A LangGraph graph with explicit nodes defining every step. Used when the process is deterministic and should only change through deliberate code changes. Examples: revenue recognition, content critique loop.
_Avoid_: workflow (too broad — qualify as prescribed or agentic).

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

> **Dev:** Jacob wants to draft a reply to an inbound lead. Where does this live?
> **Domain:** BDR — it's an agentic task. Revenue-ops calls `ask_agent("bdr", "draft a reply for lead@example.com")`. BDR decides to call its HubSpot tool, gets the context, drafts the reply, returns it. Revenue-ops surfaces it in chat.
> **Dev:** Why not just add a `draft_reply` tool to revenue-ops?
> **Domain:** Because that's how you end up with 100 tools on the orchestrator. BDR owns the outreach domain — the tools, the voice, the logic. Revenue-ops just routes.
> **Dev:** What if I need to score a lead inside a node in the outreach graph?
> **Domain:** That's a prescribed worker, not a domain agent. Single-turn inline prompt, `SYSTEM_PROMPT` constant in the graph file, `Attribution` with purpose `"outreach.score_lead"`. The graph owns the step sequence.
> **Dev:** When does the outreach graph exist at all vs. just using the BDR agentic task?
> **Domain:** Prescribed workflow when the process is fixed and the steps should never change without a code change. Agentic task when the agent should decide the approach. Multi-step outreach sequences (compose → send → follow-up on schedule) belong in a graph. "Draft a reply right now" is an agentic task.
> **Dev:** BDR drafts an email — does it go through propose-approve-execute?
> **Domain:** Only if it's being sent. A draft surfaced in chat is not a write — it's ephemeral. The moment it would touch an external system (Gmail, HubSpot) or persist to the DB, it becomes a proposed action and needs an approval row.

## Flagged ambiguities

_None recorded yet._
