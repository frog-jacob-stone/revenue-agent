# Revenue Operations System

Project-specific vocabulary. Use these terms exactly in code, prompts, docs, and PR descriptions. When a term is missing, add it here rather than improvising. General programming concepts (timeouts, retries, error types) don't belong here even if the project uses them.

## Language

### Billing & Invoicing

The deterministic core of the system (`app/services/billing/`) — no agent or LLM anywhere in this vocabulary's write path. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) and [ADR-0004](docs/adr/0004-operator-initiated-writes.md).

**Billing group**:
The unit that produces exactly one Harvest invoice — one group maps to exactly one Harvest client. Harvest has no such concept; it lives entirely in `app/services/billing/groups.py`. Config is validated at write time (project↔client mismatch is a 422, not a surprise at plan time).
_Avoid_: client, account, invoice group.

**Billing run**:
A row in `billing_runs` — either `kind=monthly` (T&M/recurring, plans across every group) or `kind=draw` (a single fixed-fee milestone, billed off-cycle). Read-only pre-flight planning produces `planned` ledger rows in `billing_run_items`; nothing reaches Harvest until a human acts.
_Avoid_: workflow, invoice batch, billing cycle. (`workflows` is a retired table name — do not reintroduce it.)

**Draw**:
One scheduled payment on a `fixed_fee_schedule` group's contract (e.g. "30% on signing, 40% at UAT, 30% at go-live"), tracked in `fixed_fee_schedule_items`. A draw's date is a schedule commitment; dates never bill anything — a human must **release** a draw (confirm delivery) before it can be invoiced, and it bills one at a time from the Draws tab, never on the monthly run.
_Avoid_: milestone (fine in prose, not in code/schema), installment.

**T&M estimate**:
The Time & Materials estimate computed from uninvoiced Harvest time and expenses (`app/services/billing/estimator.py`). Computed independently of Harvest's own invoice generation, so it will not always match to the penny — time rounding, rate resolution order, and mid-period rate changes all diverge. Expected and acceptable; not a bug to chase.
_Avoid_: quote, projection.

**Reconciliation**:
The check that every active billable project maps to exactly one billing group (`app/services/billing/reconcile.py`) — the highest-value check in the pipeline, because a project belonging to no group accrues time nobody will ever invoice, and nothing downstream would notice.
_Avoid_: validation (too generic — reconciliation is specifically the project↔group mapping check), matching.

**Duplicate guard**:
Two-layer defense against double-billing (`app/services/billing/duplicate_guard.py`): an unresolved `in_flight` ledger row blocks planning outright (unknown whether Harvest already created the invoice); partial-unique indexes on the ledger make a second live row for the same group/period or same draw structurally impossible to insert.
_Avoid_: idempotency check (too generic), lock.

**Placeholder resolution**:
The operator's per-month decision about a `recurring_line_items` placeholder — an entered amount, or an explicit omit (`recurring_line_item_resolutions`, `app/services/billing/placeholders.py`). Keyed on the line and the run month, not the ledger row, so it survives a Re-plan. An undecided placeholder blocks approval and is **not** overridable: the point of a placeholder is to be impossible to forget, and an override is a way to forget it with a click. Omitting is a decision, not an absence — the line leaves this month's payload, stays on screen struck through, and returns next month.
_Avoid_: override, line-item edit, adjustment, "filling it in in Harvest" (the retired workflow).

**In-flight resolution**:
Human-only recovery for a ledger row where a `POST` to Harvest never returned a verdict (`app/services/billing/inflight.py`) — timeout or 5xx, so the system does not know whether the invoice was created. No retry, no inference, no timeout-means-failure: it escalates to a person, who links the row to the real Harvest invoice (or confirms none was created) before the group unlocks.
_Avoid_: retry, cleanup, auto-resolve.

### Agents

**Orchestrator agent**:
The single conversational agent Jacob talks to. Slug `chief-of-staff`. Stays thin: its tools are workflow triggers for prescribed processes and `ask_agent` for delegating to domain agents. Does not own domain tools — domain agents do.
_Avoid_: front-door agent, chat agent, assistant.

**Domain agent**:
A specialist agent that owns tools for a specific business domain and runs autonomously when delegated a task. Has a slug, a row in `agents`, and an `allowed_tools` set. When invoked via `ask_agent`, drives a ReAct loop — decides which tools to call, in what order — and returns a final answer. Examples: `bdr`, `linkedin`, `revenue-ops`.
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
The agent that called a tool (typically `chief-of-staff` from a chat turn, or another agent via `ask_agent`; cron-triggered tools have no invoking agent). Distinct from the **owning agent**: when `chief-of-staff` delegates to `linkedin` via `ask_agent`, the invoking agent of `ask_agent` is `chief-of-staff`, but every LLM call inside the delegated ReAct loop is attributed to `linkedin` (the owning agent of `create_post` and the other content tools). The relationship lives in the audit trail and `agent_messages` thread, not on `llm_calls` rows.
_Avoid_: caller agent, triggering agent.

## Example dialogue

> **Dev:** Jacob wants to draft a reply to an inbound lead. Where does this live?
> **Domain:** BDR. Revenue-ops calls `ask_agent("bdr", "draft a reply for Sarah Chen, VP Eng at Acme — they asked about our modernization work")`. BDR drafts in the outreach voice and returns it; revenue-ops surfaces it in chat. Note the caller supplies the context: BDR has had no tools since HubSpot was removed, so a bare email address gets you a draft with nothing specific in it.
> **Dev:** Why not just add a `draft_reply` tool to chief-of-staff?
> **Domain:** Because that's how you end up with 100 tools on the orchestrator. BDR owns the outreach domain — the tools, the voice, the logic. Revenue-ops just routes.
> **Dev:** What if I need to score a lead as a sub-step inside an outreach tool?
> **Domain:** Inline prompt. Fixed `SYSTEM_PROMPT` constant in the tool's module, `Attribution` with purpose `"outreach.score_lead"`. The tool owns the step sequence; the inline prompt has no agent identity.
> **Dev:** When is something a prescribed workflow vs. an agentic task?
> **Domain:** Prescribed workflow when the process is fixed and the steps should never change without a code change — that's a tool returning `Done | AwaitingApproval | Blocked`. Agentic task when the agent should decide the approach. "Compose this email and propose sending it" is a prescribed workflow. "Draft a reply right now" is an agentic task.
> **Dev:** BDR drafts an email — does it go through propose-approve-execute?
> **Domain:** Only if it's being sent. A draft surfaced in chat is not a write — it's ephemeral. The moment it would touch an external system (Gmail, Harvest) or persist to the DB, it becomes a write — and per [ADR-0004](docs/adr/0004-operator-initiated-writes.md) how it gets authorized depends on who initiated it. Agent-initiated: the tool returns `AwaitingApproval` and an executor runs after a human approves. Operator-initiated: the human is already looking at the payload in the UI, and the click is the authorization.
> **Dev:** Why can't the LLM just call the Gmail-send tool directly once the user said yes in chat?
> **Domain:** Trust boundary — Rule #3. The LLM never holds the write capability. It proposes by returning `AwaitingApproval`; the executor that actually sends lives in a registry the LLM cannot reach. The user approves via the inbox UI, not by talking to the agent.

## Flagged ambiguities

_None recorded yet._
