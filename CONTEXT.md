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

**Inline prompt**:
A single-turn LLM call made from a tool function with a fixed `SYSTEM_PROMPT` constant, attributed via `Attribution` but with no agent class. Not an agent — has no identity, no autonomy, one caller. Lives in the tool's module or a sibling `_prompts.py`. This is the right shape for sub-steps like voice critique or accuracy critique inside a tool.
_Avoid_: anonymous agent, prompt-only agent, prescribed worker (retired term — see [ADR-0002](adr/0002-tools-not-graphs.md)).

### Approval flow

**Propose-Approve-Execute**:
The unbreakable rule: every create/update/delete flows through `tool returns AwaitingApproval → approval (pending) → human approves → executor runs → executed | failed`. No write without an approved approval row. See [ADR-0002](adr/0002-tools-not-graphs.md).
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
A tool that runs a fixed sequence of steps and returns one of `Done`, `AwaitingApproval`, or `Blocked`. Used when the process is deterministic and should only change through deliberate code changes. Examples: `trigger_revenue_recognition`, `create_post`, `publish_post`. See [ADR-0002](adr/0002-tools-not-graphs.md). Loops, retries, and conditional branches are inline Python — there is no graph engine.
_Avoid_: workflow (too broad — qualify as prescribed or agentic), graph, chain, pipeline.

### Tool return shapes

**Done**:
A tool's terminal-success return. Carries the payload the LLM sees as the tool result. No approval gate.
_Avoid_: success, ok, completed.

**AwaitingApproval**:
A tool's "I've computed a proposed change but the write is gated" return. Carries the registered **executor** name, the payload the executor will receive after approval, and human-facing fields (`summary`, `reasoning`, `risk_level`). The runtime writes an `approvals` row; the LLM sees `{"status": "awaiting_approval", "approval_id": "…", "summary": "…"}` and surfaces it to the user. The LLM cannot act further on the approval.
_Avoid_: pending, paused, gated.

**Blocked**:
A tool's "precondition not met" return. Carries a human-readable `reason` and an optional structured `hint` (e.g., which rows need configuration). The LLM sees `{"status": "blocked", "reason": "…", "hint": {…}}` and tells the user what to fix. Used when no approval is meaningful — the user must do work elsewhere and re-trigger.
_Avoid_: failed, error, halted.

### Executors

**Executor**:
A function the approval-grant handler invokes after a human approves an `AwaitingApproval`. Lives in a registry separate from tools and is **never** in any agent's `allowed_tools` — the LLM has no path to call an executor. Receives the (possibly edited) payload from the inbox; returns success or marks the approval failed. This is the structural enforcement of [Unbreakable Rule #3](../CLAUDE.md): approvals are human-only.
_Avoid_: writer, action, callback, post-approval tool.

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
The agent identity a tool's work attributes to. Declared as a class attribute on the tool (or its module), defaulting to the caller's `ToolContext.agent_slug` when not set. Every LLM call made from inside the tool — including inline prompts for sub-steps like voice or accuracy critique — uses this slug for `Attribution.agent_slug`, so `llm_calls.agent_slug` reflects the work's owner, not a per-step persona. The sub-step's distinct role is captured by `purpose` (e.g., `"outreach.compose_email"` vs `"outreach.voice_critique"`).
_Avoid_: workflow agent, runner agent.

**Invoking agent**:
The agent that called a tool (typically `revenue-ops` from a chat turn, or another agent via `ask_agent`; cron-triggered tools have no invoking agent). Distinct from the **owning agent**: when `revenue-ops` calls `create_post`, the invoking agent is `revenue-ops` and the owning agent is `content-orchestrator` (the tool's declared owner). The relationship lives in the audit trail and `agent_messages` thread, not on `llm_calls` rows.
_Avoid_: caller agent, triggering agent.

## Example dialogue

> **Dev:** Jacob wants to draft a reply to an inbound lead. Where does this live?
> **Domain:** BDR — it's an agentic task. Revenue-ops calls `ask_agent("bdr", "draft a reply for lead@example.com")`. BDR decides to call its HubSpot tool, gets the context, drafts the reply, returns it. Revenue-ops surfaces it in chat.
> **Dev:** Why not just add a `draft_reply` tool to revenue-ops?
> **Domain:** Because that's how you end up with 100 tools on the orchestrator. BDR owns the outreach domain — the tools, the voice, the logic. Revenue-ops just routes.
> **Dev:** What if I need to score a lead as a sub-step inside an outreach tool?
> **Domain:** Inline prompt. Fixed `SYSTEM_PROMPT` constant in the tool's module, `Attribution` with purpose `"outreach.score_lead"`. The tool owns the step sequence; the inline prompt has no agent identity.
> **Dev:** When is something a prescribed workflow vs. an agentic task?
> **Domain:** Prescribed workflow when the process is fixed and the steps should never change without a code change — that's a tool returning `Done | AwaitingApproval | Blocked`. Agentic task when the agent should decide the approach. "Compose this email and propose sending it" is a prescribed workflow. "Draft a reply right now" is an agentic task.
> **Dev:** BDR drafts an email — does it go through propose-approve-execute?
> **Domain:** Only if it's being sent. A draft surfaced in chat is not a write — it's ephemeral. The moment it would touch an external system (Gmail, HubSpot) or persist to the DB, it becomes a proposed action: the tool returns `AwaitingApproval` and an executor runs after a human approves.
> **Dev:** Why can't the LLM just call the Gmail-send tool directly once the user said yes in chat?
> **Domain:** Trust boundary — Rule #3. The LLM never holds the write capability. It proposes by returning `AwaitingApproval`; the executor that actually sends lives in a registry the LLM cannot reach. The user approves via the inbox UI, not by talking to the agent.

## Flagged ambiguities

_None recorded yet._
