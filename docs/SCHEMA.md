# Supabase Schema — Revenue Operations System

> Source of truth for the database. Update this file when the schema changes.
> Matches migrations: `supabase/migrations/20250101000001_initial_schema.sql` through `20250101000029_billing_settings.sql`

## Overview

Two groups of tables, plus pgvector. Every table has RLS enabled from day one so policies can be added without a migration later.

**Agent framework** (nine core tables):

```
agents           → registry of agent definitions (slug-keyed identity rows; metadata lives on the Python class)
approvals        → human-in-the-loop queue for tool-proposed actions
memories         → unified agent memory (facts, summaries, embeddings)
audit_log        → append-only record of everything that happened
knowledge_base   → vector-searchable reference content (playbooks, past deals)
social_posts     → draft and approval queue for chat-driven content creation
agent_messages   → turn-by-turn record of agent-to-agent exchanges
llm_calls        → per-request audit log of LLM provider calls
chat_sessions    → human-to-agent conversation containers (multi-chat sidebar)
chat_messages    → turn-by-turn log of human chat with assistant placeholders
```

**Revenue operations automation** (billing/invoicing, migrations `0024`–`0029` — no agent in the write path to any of these):

```
harvest_clients                 → cached Harvest client list (read-through)
harvest_projects                → cached Harvest project list, incl. billing_type
harvest_invoice_item_categories → cached Harvest invoice line-item categories
harvest_task_assignments        → cached Harvest task/rate assignments
billing_groups                  → config: one group → exactly one Harvest invoice per run
billing_group_projects          → project↔group membership (a project belongs to at most one active group)
fixed_fee_schedule_items        → a fixed-fee group's draws — schedule, release state
recurring_line_items            → effective-dated recurring/retainer line items
billing_runs                    → a monthly or draw billing run
billing_run_items               → per-group/per-draw ledger row: planned → approved → in_flight → created | failed
billing_run_flags               → error/warning/info catalog surfaced on a run
billing_settings                → account-level billing preferences (e.g. default invoice notes)
```

## Design Principles

1. **No write without a human authorizing that specific payload.** Agent-initiated writes flow through `approvals` with a `pending → approved → executed | failed` lifecycle. Operator-initiated writes — the human is in the UI looking at the payload — skip the approval row under the conditions in [ADR-0004](adr/0004-operator-initiated-writes.md). Either way the transition writes `audit_log`.
2. **Prescribed workflows are tools.** Per [ADR-0002](adr/0002-tools-not-graphs.md), a prescribed workflow is a tool that returns one of `Done | AwaitingApproval | Blocked`. `AwaitingApproval` carries an executor name that runs after human grant. There is no graph engine — conditional branches and retry loops are inline Python inside the tool.
3. **Audit log is append-only.** Enforced at the database role level, not in application code.
4. **Memory and knowledge are separate.** Memory is what agents learned (emergent). Knowledge base is what we gave them (curated).
5. **RLS on from day one.** Every `public` table has RLS enabled with a `service_role`-only policy. The FastAPI backend uses asyncpg as `service_role` (RLS-bypassing), so backend access is unaffected; the policies exist to block accidental anon/PostgREST exposure. Migration `0018` patched two gaps (`approvals`, `agent_messages`) that were created without RLS. User-scoped policies are deferred until multi-user.

## Tables

### `agents`

Stores only runtime-mutable state. Static metadata (`name`, `description`, `requires_approval`, `allowed_tools`, system prompts) is owned exclusively by the Python class in `app/agents/` — the DB is never the source of truth for those.

| Column | Type | Notes |
|---|---|---|
| `id` | uuid | PK |
| `slug` | text | Unique identifier matching the class `slug`, e.g. `outreach-agent` |
| `is_active` | boolean | Soft disable |
| `created_at` / `updated_at` | timestamptz | |

### `approvals`

Lifecycle-only queue for human-in-the-loop pauses. Originally added (migration `0010`) for LangGraph node-driven pauses; reshaped in migration `0021` for tool-driven approvals (per [ADR-0002](adr/0002-tools-not-graphs.md)) and finalized in `0022` (graph machinery removed).

| Column | Type | Notes |
|---|---|---|
| `id` | uuid | PK |
| `workflow_id` | uuid | **Historical only since `0022`** — FK dropped; column kept as plain UUID for legacy audit lookups. NULL for all approvals created post-ADR-0002. |
| `node_name` | text | Historical only since `0022`. NULL for all new approvals. |
| `executor` | text | Registered executor name (per `app/executors/registry.py`) the approval-grant handler invokes on grant. **NOT NULL since `0022`.** |
| `agent_slug` | text | The agent acting (display + future ACL) |
| `action_type` | text | Free text describing the proposed action (e.g. `post_to_linkedin`, `write_rev_rec`) |
| `status` | text | One of `pending`, `approved`, `rejected`, `executed`, `failed` (CHECK constraint enforces) |
| `risk_level` | text | `low`, `medium`, `high` |
| `summary` | text | Human-readable description |
| `reasoning` | text | Agent's explanation |
| `proposed_payload` | jsonb | What the tool proposed |
| `executed_payload` | jsonb | What actually ran (may differ if human edited) |
| `assigned_to` | text | Reserved for multi-user routing; ignored today |
| `approved_by` / `approved_at` | — | Set on approval |
| `rejected_by` / `rejection_reason` | — | Set on rejection |
| `executed_at` | timestamptz | Set when the executor completes |
| `error` | text | Set if the executor fails after approval |
| `created_at` | timestamptz | |

**Lifecycle:** `pending → approved → executed | failed`, or `pending → rejected`. Audit events emitted at every transition (see "Event Types" below).

**Two payload columns by design:** `proposed_payload` preserves the agent's draft; `executed_payload` captures what actually went out the door. If a human edits the payload before approving, both are preserved for the audit trail.

**Grant path:** `POST /approvals/{id}/approve` looks up `executor` in `app/executors/registry.py` and invokes it with `executed_payload ?? proposed_payload`. Executors live in their own registry and are never callable by an LLM — that's the structural enforcement of [Unbreakable Rule #3](../CLAUDE.md).

### `memories`

Single table, typed by kind. pgvector enabled for embedding rows.

| Column | Type | Notes |
|---|---|---|
| `id` | uuid | PK |
| `agent_id` | uuid | FK → `agents.id`, nullable (null = shared across agents) |
| `kind` | enum | `fact`, `summary`, `embedding`, `preference` |
| `scope` | text | `company:123`, `deal:456`, `global` — convention-based |
| `content` | text | The memory itself |
| `embedding` | vector(1536) | Null for non-embedding kinds |
| `source_workflow_id` | uuid | Historical only — FK dropped in `0022`. Nullable. |
| `source_action_id` | uuid | FK, nullable |
| `metadata` | jsonb | |
| `expires_at` | timestamptz | Optional TTL for short-term context |
| `created_at` | timestamptz | |

**Scope convention:** `{entity_type}:{external_id}` for entity-scoped memories; `global` for shared. Query patterns: `WHERE scope LIKE 'company:%'` or `WHERE scope = 'global'`.

### `audit_log`

Append-only. INSERT-only at the database role level.

| Column | Type | Notes |
|---|---|---|
| `id` | bigserial | PK |
| `occurred_at` | timestamptz | |
| `event_type` | text | `action.proposed`, `action.approved`, `memory.written`, etc. |
| `agent_id` | uuid | FK, nullable |
| `workflow_id` | uuid | Historical only — FK dropped in `0022`. Nullable. |
| `action_id` | uuid | FK, nullable |
| `actor` | text | `system:sdr_researcher` or user id |
| `payload` | jsonb | |
| `ip_address` | inet | |
| `user_agent` | text | |

### `knowledge_base`

Curated reference content. Separate from `memories` because the lifecycle and access pattern differ.

| Column | Type | Notes |
|---|---|---|
| `id` | uuid | PK |
| `title` | text | |
| `content` | text | |
| `kind` | text | `playbook`, `case_study`, `proposal_template`, `icp_doc` |
| `tags` | text[] | |
| `embedding` | vector(1536) | |
| `source_url` | text | |
| `version` | int | Increment on content change |
| `is_active` | boolean | Soft disable |
| `created_at` / `updated_at` | timestamptz | |

### `social_posts`

Draft and approval queue for the LinkedIn agent. Separate from `workflows`/`actions` because content creation has no external writes — approval is conversational, not inbox-based.

| Column | Type | Notes |
|---|---|---|
| `id` | uuid | PK |
| `topic` | text | What the post is about |
| `idea_title` | text | Short label for the user (e.g. "Why AI agents fail in sales") |
| `core_angle` | text | The specific take generated by the Content Strategy Agent |
| `post_text` | text | The current post content — updated in place on revision |
| `status` | text | See status values below |
| `created_at` / `updated_at` | timestamptz | |

**Status values:**

| Status | Meaning |
|---|---|
| `draft` | Text exists, not yet voice-reviewed (first status a row ever has) |
| `needs_revision` | Voice critique failed; rewrite → back to `draft` |
| `ready` | Passed voice review, available for publishing |
| `rejected` | User rejected via chat |
| `published` | Went through `content_publish` chain and was approved |

**Revision cycle:** `draft` → voice review → `needs_revision` → rewrite → `draft` → voice review → `ready`

The `rewrite_post` tool accepts posts in any status and resets to `draft`. User can publish directly after rewriting without a forced re-review.

### `agent_messages`

Turn-by-turn record of every agent-to-agent exchange. Powers the `ask_agent` tool. Migration `0013`.

| Column | Type | Notes |
|---|---|---|
| `id` | bigserial | PK; monotonic insert order |
| `thread_id` | uuid | Correlates messages within one delegation; sender generates a fresh UUID for the first turn |
| `workflow_id` | uuid \| null | Historical only — FK dropped in `0022`. Plain UUID; NULL for all new messages. |
| `from_agent_slug` | text | Sender's slug |
| `to_agent_slug` | text | Recipient's slug (may equal sender for supervisor self-talk) |
| `content` | text | The message body |
| `metadata` | jsonb | Free-form annotations |
| `created_at` | timestamptz | |

Indexes: `(thread_id, created_at)`, partial `(workflow_id) where workflow_id is not null`, `(to_agent_slug)`.

The table is the audit; service-layer functions in `app/services/agent_messages.py` do **not** write `audit_log` rows for individual messages (volume would dominate the audit log). The runner's `node.exited` events provide enough granularity. Add `AGENT_MESSAGE_SENT` to `app/orchestrator/events.py` if per-turn audit visibility is needed later.

### `llm_calls`

Per-request audit log of every LLM provider call (OpenAI today). Captures full request/response payloads, model, token usage, latency, agent context. Written by `app/services/llm_logging.py::write_llm_call`. Migration `0016`.

Key columns: `started_at`, `latency_ms`, `model`, `agent_slug`, `workflow_id`, `purpose`, `status` (ok/error), `streamed`, `request` (jsonb), `response` (jsonb), `prompt_tokens`, `completion_tokens`, `total_tokens`.

### `chat_sessions`

Human-to-agent conversation containers. Each row is one chat that the user can return to from the sidebar. Migration `0017`.

| Column | Type | Notes |
|---|---|---|
| `id` | uuid | PK |
| `agent_slug` | text | Which conversational agent this chat is with. `DEFAULT 'chief-of-staff'` (migration `0019` first set it to `'revenue-ops'`; migration `0023` renamed default + historical rows after the front-door rename). The single-front-door pattern means new sessions always target the same agent. |
| `title` | text | Auto-titled from the first user message (~60 chars), default `'New chat'` |
| `created_at` | timestamptz | |
| `updated_at` | timestamptz | Bumped on every turn |
| `last_message_at` | timestamptz | Drives sidebar sort order |

Index: `(agent_slug, last_message_at desc nulls last)`.

### `chat_messages`

Turn-by-turn log of one chat session. User messages are inserted complete; assistant messages are inserted as `status='streaming'` placeholders inside `start_turn`, then updated by the detached `TurnRuntime` in `app/services/chat_turn.py` when the turn finishes. Migration `0017`.

| Column | Type | Notes |
|---|---|---|
| `id` | bigserial | PK; insertion order |
| `session_id` | uuid | FK to `chat_sessions.id` (CASCADE) |
| `turn_id` | uuid \| null | Shared with the runtime; addresses an in-flight assistant turn |
| `role` | text | `user` \| `assistant` |
| `content` | text | Final answer text (empty until the turn completes for assistant rows) |
| `activity` | jsonb | `ActivityLine[]` tree (tool / node (= tool-step) / subagent / error), built by `app/services/activity_builder.py` and the frontend mirror in `ChatWindow.tsx::onEvent` |
| `status` | text | `streaming` \| `complete` \| `failed` |
| `tool_used` | text \| null | Top-level tool the agent called this turn |
| `error` | text \| null | Failure reason |
| `created_at` | timestamptz | |
| `completed_at` | timestamptz \| null | Set when status leaves `streaming` |

Indexes: `(session_id, id)`, partial `(session_id) where status = 'streaming'` (cheap "any turn in flight?" check).

**Durability:** the chat router persists the user message + placeholder, then `detach_turn` spawns an `asyncio.create_task` that runs the OpenAI loop. The task is held in `_ACTIVE_TURNS` so it isn't GC'd; cancellation of the originating HTTP request does NOT cancel the task. On app startup, `mark_orphaned_streaming_failed` flips any leftover `streaming` rows to `failed` (the upstream LLM stream from a prior process is unrecoverable).

### Billing / invoicing tables

Added by `20250101000024_billing_invoicing.sql`. Three groups: a Harvest read-through
cache, billing configuration, and the invoice ledger. Full requirements live in
`docs/prd/harvest-invoicing-requirements.md`.

**Harvest snapshot cache** — never authoritative; safe to truncate and re-sync.
Each carries `synced_at` and is upserted by Harvest id.

| Table | Contents |
|---|---|
| `harvest_clients` | id, name, currency, is_active |
| `harvest_projects` | id, name, code, client, currency, `is_billable`, `is_fixed_fee`, `bill_by`, `hourly_rate`, fee/budget fields, is_active, `starts_on`, `ends_on` |
| `harvest_invoice_item_categories` | valid `kind` values for free-form line items |
| `harvest_task_assignments` | per-project task rates — the last rung of the rate-resolution ladder |

**Owned, not synced** — sits alongside the cache but is not part of it.

`forecast_project_schedule` — the delivery forecast, one row per project:
`last_scheduled_on`, the last day a **person** is booked in Forecast (placeholder
bookings are capacity, not someone, and do not count). Keyed on the *Harvest*
project id — Forecast projects carry a `harvest_id`, so the join is resolved once
at sync time. Derived rather than raw: Forecast returns ~7,700 assignment rows
for a five-year window and the Projects tab needs one date. A cache, safe to
truncate and re-sync. `assignment_count` distinguishes "synced, nobody booked"
(hosting, retainers) from "never synced", which a null date alone cannot.

`excluded_harvest_clients` — Harvest clients this system treats as not-a-client,
keyed on `harvest_client_id` with a `reason` and who set it. One row hides every
project under that client, present and future, from the Projects roster and from
config reconciliation. Deliberately *not* a flag on `harvest_clients`: that table
is safe to truncate and re-sync, and operator intent must survive that. No FK to
it either, so an exclusion outlives a cache rebuild or a client deleted in
Harvest.

**Configuration**

`billing_groups` — the unit that produces exactly one Harvest invoice. Harvest
has no such concept. One group → one client, one or more projects. A client may
own any number of groups; the uniqueness rule is on the **project**, not the
client. Enums: `billing_type` (`time_and_materials` | `fixed_fee_schedule` |
`recurring_monthly` | `manual`), `billing_timing` (`arrears` | `advance`),
`payment_term`, `time_summary_type`, `expense_summary_type`.

> `time_summary_type` and `expense_summary_type` are **separate enums**. Harvest
> uses `task` for time and `category` for expenses; sharing one enum would let an
> invalid pairing through.

`billing_group_projects` — join table, PK `(billing_group_id, harvest_project_id)`.
Carries a denormalized `group_is_active`, maintained by triggers on both insert
and `billing_groups` update. That column exists solely so the double-billing
guard can be a real constraint:

```sql
create unique index billing_group_projects_one_active_group
    on billing_group_projects(harvest_project_id) where group_is_active;
```

Postgres cannot reference `billing_groups.is_active` from an index on the child
table, so the flag is denormalized rather than the rule being left to
application code.

`recurring_line_items` — the literal lines a `recurring_monthly` group bills
every month. Each carries its own `harvest_project_id`, so one invoice can span
several projects (hosting against the hosting project, a service fee against
another). `kind` is the Harvest invoice item category — validated against
`harvest_invoice_item_categories` both at save time and at plan time, so an
invalid value can never become a 422 mid-execution. `is_placeholder` marks a
line whose amount is only knowable after the fact (hosting pass-through, a
percentage-based tooling fee, a retainer overage): the operator decides it per
month on the pre-flight — see `recurring_line_item_resolutions` below — and
until they do, the line plans at $0, is excluded from `planned_amount`, and
blocks approval. `effective_from` / `effective_to` let a fee change
without erasing history — supersede the old row rather than editing it. Both are
compared **month-granular** (`date_trunc('month', …)`): any day within a month
means that whole month, because billing is monthly and the UI presents these as
"first / last month billed". Storing a mid-month date must not skip the month it
names.

`recurring_line_item_resolutions` — one operator decision about one placeholder
line for one run month: an entered amount (`resolution = 'amount'`, with
`unit_price` and an optional `quantity` override) or an explicit omit
(`resolution = 'omitted'`). Named for its parent so it sorts beside it; there is
no constraint expressing "only for a placeholder line", because a foreign key
cannot see a column on the row it points at and the flag can be toggled
afterwards — `app/services/billing/placeholders.py` enforces it, and a
resolution on a line that is no longer a placeholder is ignored rather than
rejected.

Keyed on `(recurring_line_item_id, run_month)` rather than the ledger row, which
is the whole design: a resolution is a fact about a month ("hosting for August
2026 was $1,240"), so it survives Re-plan. Keyed on `billing_run_items` instead,
the ordinary act of fixing a config problem and re-planning would silently
discard every amount already entered. That is also why `recurring_line_items.id`
must be stable across a group save — see migration `33`.

`omitted` is a first-class resolution, not an absence. A retainer overage is
configured precisely so it comes up every month; most months there is none, and
"no overage in August" is a decision worth recording rather than a question left
undecided. An omitted line leaves the Harvest payload but stays on the
pre-flight, struck through.

`fixed_fee_schedule_items` — config for the one billing type whose planning
logic is still unbuilt (PRD Phase 4). The table exists so config can be entered
ahead of the code.

**The ledger**

`billing_runs` — one operator-initiated execution against a single `run_month`
(constrained to the first of a month). Lifecycle `planning` →
`awaiting_approval` → `executing` → `completed` / `failed` / `abandoned`.
`plan_snapshot` freezes the full pre-flight at plan time. `approval_id`
references the single `approvals` row that will gate execution — null until
Phase 3.

`billing_run_items` — one row per group per run. Carries a **denormalized
`run_month`** so constraint C6 can be a real index:

```sql
create unique index billing_run_items_one_live_per_month
    on billing_run_items(billing_group_id, run_month)
    where status not in ('failed', 'skipped', 'abandoned');
```

The PRD put `run_month` on `billing_runs`, where an index on `billing_run_items`
cannot see it. Terminal-but-unsuccessful states are excluded so a failed or
abandoned attempt can be re-planned; `in_flight` is *not* excluded, which is
what makes an unresolved in-flight row a hard block until a human clears it.

`actual_amount` is the amount **at creation**, not what the client was billed —
drafts are edited in Harvest before sending. Do not build reporting that assumes
otherwise. (Placeholder resolution narrows that gap without closing it: the
amounts that used to be typed into the draft are now settled before it exists,
but a draft remains freely editable afterwards.)

`estimated_line_items` is not purely display. For a `recurring_monthly` group
each entry also carries `recurring_line_item_id`, `harvest_project_id`, `kind`,
`is_placeholder`, and `placeholder_state`, which makes it a complete description
of every line — enough that `planned_payload["line_items"]` can be rebuilt from
it. That is what lets a placeholder be resolved against the plan the operator
reviewed rather than against config as it stands now; re-deriving from live
config would fold in any edit made since planning. All five are null for T&M and
draw entries, which have no config row behind them. `placeholder_state` is also
the live approval gate: `review.py` counts `unresolved` entries in this array
rather than reading a flag, because flags are frozen at plan time and this
changes as the operator works.

**Draws hold their own index, not the month's** (`0028`). C6 above is scoped to
non-draw rows (`fixed_fee_schedule_item_id is null`), and draws get an analogous
partial unique index on `fixed_fee_schedule_item_id`. The reason is that the unit
of double-billing risk differs: for T&M and recurring it is the *period* — billing
August twice is the error — while for a draw it is the *draw*, and two milestones
landing in one calendar month is ordinary. Sharing one index would have blocked
the second milestone. The FK is `ON DELETE RESTRICT`, so a draw with a live ledger
row cannot be deleted out from under it.

A draw's state is derived, never stored: `pending` (no `released_at`), `ready`
(released, no live ledger row), `in_flight` (a live `billing_run_items` row
exists — execution has begun), `invoiced` (`invoiced_run_id` set). There is no
status column to drift out of sync with those facts. `released_at` is set only
by a human confirming delivery — it is the entire billing trigger for a
fixed-fee contract, and nothing in the system sets it.

**Nothing is written between `ready` and `in_flight`.** A draw's invoice is a
pure function of the group config and the draw, so it is computed on demand
(`preview_draw_invoice`) and never staged. The ledger row is written by the
execution path immediately before the POST, which is what the §8 in-flight
protocol requires anyway.

**`billing_settings`** (`0029`) — key/value, account-level billing config a human
edits at Settings → Billing. One key so far, `default_invoice_notes`, which exists
because Harvest's own default invoice notes reach only invoices created through
Harvest's UI; its API neither applies them nor exposes them for reading, so an
API-created invoice arrives blank unless we send them. A group's `notes_template`
overrides this value rather than appending to it. Writes are audited
(`billing.settings.updated`) *with the new value*, since it is text a client
reads. Unknown keys are refused by the allowlist in
`app/services/billing/settings_store.py`, so a typo cannot become a row nothing
reads. Secrets and deployment identity stay in environment variables and are never
stored here or served by `GET /billing/settings`.

`in_flight` reads through the partial index above rather than a column of its
own. It locks the draw's billable fields (`description`, `amount`, `kind`,
`harvest_project_id`), because execution has begun against those exact values;
`scheduled_date` stays editable, since a locked draw's date is a historical note
rather than something anyone still works against. A `failed` row is excluded
from the index, so a create that failed outright returns the draw to `ready`.
That does *not* free it for deletion: the failed row still references the draw
under `ON DELETE RESTRICT`, so a draw that has ever been billed against can no
longer be removed from a schedule.

Per-group approval (`0026`) is a status transition, not a parallel boolean: the
planner writes `planned`, and only an explicit human action moves a row to
`approved`, stamping `approved_at` / `approved_by`. Both states count as live
under the C6 index above, so the existing `status in ('planned','approved')`
predicates in the planner already cover approved rows. `error_override` records
that a human accepted an error-severity flag; it is deliberately **sticky**, so
un-approving does not withdraw the judgement. Flags in
`app/services/billing/flags.NON_OVERRIDABLE` (today `UNRESOLVED_IN_FLIGHT`) are
refused at the service layer no matter what that column says.

`billing_run_flags` — flags from the §7 catalog. `billing_run_item_id` is null
for run-level flags such as `UNMAPPED_PROJECT`, which belong to no group.

## Agent Types

**Front-door agent** — `chief-of-staff`. The only conversational agent users chat with. Owns no domain tools; delegates revenue, BDR, and LinkedIn content work to domain agents via `ask_agent`. Drives an OpenAI tool-call loop inside one chat turn via `app/services/chat_turn.py`.

**Domain worker agents** — invoked single-turn via `run_agent_task` (no `allowed_tools`) or as ReAct loops (with tools). Examples: `revenue-ops`, `linkedin`, `bdr`. Reached via the `ask_agent` tool; record exchanges in `agent_messages`.

## Event Types (Audit Log Vocabulary)

Keep this list stable; it becomes grep-able forensics. Constants live in `app/orchestrator/events.py` — call sites must import and use them, never string literals.

**Tool lifecycle (ADR-0002):**
- `tool.called`, `tool.completed`, `tool.failed`, `tool.blocked`

**Approval lifecycle:**
- `approval.requested`, `approval.granted`, `approval.rejected`, `approval.executed`, `approval.failed`

**Agent invocation:**
- `agent.invoked`, `agent.completed`, `agent.failed`

**Chat turn lifecycle:**
- `chat.turn.started`, `chat.turn.completed`, `chat.turn.failed`

**Memory and knowledge:**
- `memory.written`, `memory.expired`
- `knowledge.created`, `knowledge.updated`

**Content workflows:**
- `content.post_created`, `content.post_drafted`, `content.post_approved`, `content.post_rejected`, `content.post_updated`

**Billing / invoicing:**
- `billing.snapshot.refreshed`
- `billing.group.created`, `billing.group.updated`, `billing.group.deactivated`
- `billing.run.planned`, `billing.run.abandoned`

These cover human-initiated operations on our own store. The Harvest invoice
write (Phase 3) reuses the `approval.*` vocabulary above.

Historical audit_log rows may carry retired vocabulary (`workflow.*`, `node.*`, `subworkflow.*`, `agent.queried`, `agent.routed`, plus pre-migration `action.*` strings); these remain queryable. New code emits only the constants listed above.

## API Surface (Maps to FastAPI)

| Endpoint | Purpose |
|---|---|
| `GET /approvals?status=pending` | The approval inbox query |
| `GET /approvals/{id}` | Approval detail |
| `POST /approvals/{id}/approve` | Human approves → grant handler invokes the registered executor in a background task |
| `POST /approvals/{id}/reject` | Human rejects with reason |
| `POST /chat/sessions` | Create a chat session (auto-targets the front-door agent) |
| `POST /chat/sessions/{id}/messages` | Post user message; streams the assistant turn over SSE |
| `GET /audit-log` | Filterable audit timeline |
| `GET /llm-calls` | LLM telemetry |
| `GET/POST /billing/groups`, `GET/PATCH /billing/groups/{id}`, `POST /billing/groups/{id}/deactivate` | Billing-group configuration |
| `GET /billing/health` | Config reconciliation — unmapped projects, config flags, snapshot freshness |
| `POST /billing/snapshot/refresh` | Refresh the Harvest read-through cache |
| `GET /billing/harvest/clients`, `/billing/harvest/projects`, `/billing/harvest/item-categories` | Snapshot catalog backing the group-config form |
| `GET/POST /billing/runs`, `GET /billing/runs/{id}`, `POST /billing/runs/{id}/abandon` | Pre-flight planning. Read-only against Harvest |

## RLS Status

All tables have RLS enabled with permissive `service_role` policies. When user identity is added:

1. Replace permissive policies with scoped ones
2. Map `approvals.approved_by`, `workflows.initiated_by`, `audit_log.actor` to `auth.uid()`
3. Add role-based approval rules (who can approve what `action_type`)

No schema migration required for this step.

## Migration Order

Migrations run in filename order; each is idempotent.

> **Naming.** Files are `202501010000NN_<name>.sql`. The Supabase CLI treats the
> leading digit run as the version and `supabase migration new` generates a
> 14-digit `YYYYMMDDHHMMSS`; mixing that with the short `NNNN_` form this repo
> used originally permanently breaks `supabase db push`, so all 29 were renamed
> to 14 digits before the first remote push. The date is synthetic — these
> migrations predate it — and only the ordering matters.
>
> Prose elsewhere refers to migrations by the trailing ordinal alone
> (“migration `0022`”), which is the `NN` in the filename and the item number in
> the list below.

1. `20250101000001_initial_schema.sql` — extensions, enums, six core tables, indexes, RLS, audit_log append-only trigger

   > **Two dead values in the `action_type` enum.** `create_hubspot_record` and
   > `update_hubspot_record` are orphaned — HubSpot was removed from the system on
   > 2026-08-10 and nothing writes them any more. They are deliberately *not*
   > dropped: Postgres has no `ALTER TYPE ... DROP VALUE`, so removing them means
   > creating a replacement type, rewriting the `actions.action_type` column, and
   > dropping the old type — a table rewrite in exchange for two unused labels.
   > Any historical `actions` row still carrying one renders its raw value in the
   > inbox UI, which falls back to the enum string when a label is missing.
2. `20250101000002_agents_allowed_tools.sql` — adds `agents.allowed_tools`
3. `20250101000003_configure_rev_rec_projects_action_type.sql` — adds `configure_rev_rec_projects` to `action_type` enum
4. `20250101000004_invoice_action_types.sql` — adds invoice-related values to `action_type` enum
5. `20250101000005_agentic_patterns.sql` — adds `step_kind`, parent/retry tracking, `critique_result` to `actions`; adds `pattern`, `current_step` to `workflows`
6. `20250101000006_simplify_agents.sql` — drops static metadata columns from `agents` (`name`, `description`, `requires_approval`, `approval_scope`, `system_prompt`, `allowed_tools`); these are now owned exclusively by the Python class registry
7. `20250101000007_social_posts.sql` — adds `social_posts` table for the LinkedIn agent's draft and approval queue
8. `20250101000008_content_action_type.sql` — adds `post_to_linkedin` to `action_type` enum for the `content_publish` chain
9. `20250101000009_rename_tool_call_to_task.sql` — renames `actions.step_kind` value `tool_call` → `task`; updates the CHECK constraint to match the Python `StepKind` enum
10. `20250101000010_create_approvals_table.sql` — creates the `approvals` table for the orchestrator's human-in-the-loop queue
11. `20250101000011_workflows_parent_id.sql` — adds `workflows.parent_workflow_id` for sub-workflow linkage (used by `app/orchestrator/spawn.py`)
12. `20250101000012_langgraph_checkpoint_tables.sql` — **marker migration only** (no DDL). LangGraph's checkpoint tables (`checkpoints`, `checkpoint_blobs`, `checkpoint_writes`, `checkpoint_migrations`) are created idempotently by `AsyncPostgresSaver.setup()` at app startup (called from `runner.init()`). Schema is internal to LangGraph — do not modify. If LangGraph schema needs custom changes that `setup()` doesn't cover, add a new migration that runs after this one
13. `20250101000013_create_agent_messages.sql` — adds the `agent_messages` table for turn-by-turn agent-to-agent exchanges (powers the `ask_agent` tool)
14. `20250101000014_drop_actions_table.sql` — drops the legacy `actions` table. The `audit_log.action_id` FK constraint is dropped via CASCADE; the column itself remains and audit_log rows are preserved
15. `20250101000015_drop_workflow_pattern_columns.sql` — drops `workflows.pattern` and `workflows.current_step` (legacy prompt-chain progress markers, replaced by LangGraph checkpoints)
16. `20250101000016_create_llm_calls.sql` — adds the `llm_calls` audit table for per-request LLM provider call logging
17. `20250101000017_create_chat_tables.sql` — adds `chat_sessions` and `chat_messages` for human-to-agent chat persistence (sidebar multi-chat + durable streaming via `TurnRuntime`)
18. `20250101000018_enable_rls_gaps.sql` — enables RLS on `approvals` and `agent_messages`
19. `20250101000019_chat_sessions_default_slug.sql` — gives `chat_sessions.agent_slug` a `DEFAULT 'revenue-ops'` (later changed to `'chief-of-staff'` by migration `0023`). Single front-door pattern means new sessions always target the same conversational agent; this lets the router create sessions with no body
20. `20250101000020_drop_agents_config.sql` — drops `agents.config`. The column was a free-form jsonb knob that no app code ever read; per-agent LLM selection lives on the Python class `model` attribute. Follows the precedent of `20250101000006_simplify_agents.sql`
21. `20250101000021_approvals_for_tools.sql` — adds `approvals.executor` and makes `workflow_id` / `node_name` nullable. First step in the [ADR-0002](adr/0002-tools-not-graphs.md) migration from LangGraph graphs to tool-driven approvals. Both grant paths coexist until plan 19
22. `20250101000022_drop_langgraph_artifacts.sql` — drops LangGraph checkpoint tables (`checkpoints`, `checkpoint_blobs`, `checkpoint_writes`, `checkpoint_migrations`), drops the `workflows` table, drops the workflow_id FKs on `approvals` / `audit_log` / `memories.source_workflow_id` / `llm_calls` / `agent_messages` (columns stay as plain UUIDs for historical audit lookups), and flips `approvals.executor` to NOT NULL. Final step of the ADR-0002 cutover (plan 19)
23. `20250101000023_rename_front_door_to_chief_of_staff.sql` — renames `agents.slug` `'revenue-ops'` → `'chief-of-staff'` (the old orchestrator becomes the chief-of-staff coordinator) and `'revenue-recognition'` → `'revenue-ops'` (the rev-rec domain agent becomes the true RevOps agent owning revenue tools). Rewrites text `agent_slug` references in `chat_sessions` / `approvals` / `llm_calls` / `agent_messages` accordingly. Updates the `chat_sessions.agent_slug` default to `'chief-of-staff'`. UUIDs are preserved through the slug rename, so audit history stays intact
24. `20250101000024_billing_invoicing.sql` — the Harvest invoicing data model: snapshot cache (`harvest_clients`, `harvest_projects`, `harvest_invoice_item_categories`, `harvest_task_assignments`), configuration (`billing_groups`, `billing_group_projects`, `fixed_fee_schedule_items`, `recurring_line_items`), and the ledger (`billing_runs`, `billing_run_items`, `billing_run_flags`). Two denormalized columns (`billing_group_projects.group_is_active`, `billing_run_items.run_month`) exist purely so the double-billing guards can be real partial unique indexes rather than application convention. See the billing tables section above
25. `20250101000025_recurring_line_item_kind.sql` — adds `kind` to both line-item tables and `is_placeholder` to `recurring_line_items`. Migration `0024` created them without a Harvest invoice item category, which would have been a 422 at invoice creation with no way to fix it from config (PRD §4.7)
26. `20250101000026_billing_item_approval.sql` — adds `approved_at`, `approved_by`, and `error_override` to `billing_run_items`, persisting the operator's per-group review. Approval had been component state in the pre-flight screen: every planned group started checked and a reload wiped the review — backwards for a screen whose job is deciding which invoices may exist
27. `20250101000027_draw_scheduled_date.sql` — renames `fixed_fee_schedule_items.scheduled_month` to `scheduled_date` and drops the first-of-month check. A contract payment schedule commits to dates ("30% on 15 Sep"), and draws are billed individually on the day delivery is confirmed rather than swept up by a monthly run, so month granularity was modelling a billing shape that doesn't exist. Deliberately the opposite call to `recurring_line_items.effective_from` / `effective_to`, which stay month-granular because a recurring line is billed *for* a month
28. `20250101000028_fixed_fee_draws.sql` — makes fixed-fee draws billable: `released_at` / `released_by` on `fixed_fee_schedule_items`, `billing_run_kind` (`monthly` | `draw`) on `billing_runs`, and `fixed_fee_schedule_item_id` on `billing_run_items`. Splits the C6 index in two — see the billing tables section
29. `20250101000029_billing_settings.sql` — adds `billing_settings`, a key/value table for account-level billing config a human edits in the UI, seeded with `default_invoice_notes`. Exists because Harvest's account-level default invoice notes are applied only to invoices created through Harvest's own UI: the API neither applies them nor exposes them for reading (`GET /v2/company` has no such field), so the first live draw invoice went to a client with blank notes and no remit-to instructions. Key/value rather than one column per setting, to avoid a migration per setting; the known-key allowlist lives in `app/services/billing/settings_store.py` and an unknown key is refused rather than stored. Deliberately *not* env config — this is copy a human edits and reads back, and it is audited (`billing.settings.updated`) with the new value, since it is text a client will read
30. `20250101000030_harvest_project_dates.sql` — adds `starts_on` and `ends_on` (both nullable `date`) to `harvest_projects`. Harvest has always returned these on `/v2/projects` and `list_projects_detailed` has always fetched them; the snapshot upsert simply discarded both, so the Projects tab had no dates to show and ran on a fixture. Nullable because Harvest treats them as optional and plenty of projects leave one or both blank — a missing date is not a zero date. No backfill is possible (nothing local held the values); they populate on the next snapshot refresh. Note `ends_on` is freely editable in Harvest and moves when a project slips, so it is the *current* end date, not a contractual commitment — a committed end needs a project record this system owns

31. `20250101000031_excluded_harvest_clients.sql` — adds `excluded_harvest_clients`, the account-wide "this Harvest client is not a client" list. Frogslayer is the motivating case: our own company is a Harvest client, and some of its internal projects (Olympus, Trident) are flagged *billable*, so no automatic rule catches them. This had been handled by hand with a `manual` billing group named "Frogslayer - Exclusion" whose only job was to stop reconciliation flagging two internal projects as unmapped — project-level, so it needed upkeep every time an internal project was created. Keyed on the client instead, so one row covers every present and future project underneath. A table this system owns rather than a column on `harvest_clients`, because that cache is documented as safe to truncate and re-sync and operator intent must outlive that; no FK for the same reason. Not seeded — which clients are "us" is account-specific, and a hardcoded id or name in a migration is the thing this replaces

32. `20250101000032_forecast_project_schedule.sql` — adds `forecast_project_schedule`, the per-project delivery forecast from Forecast: the last day a person is booked. This is the "projected end" the Projects tab mockup always had and could not source — Harvest's `ends_on` is the *planned* end and goes stale, while Forecast knows who is actually on the calendar. The gap between them is the point: 8 of 29 live projects are booked past their Harvest end date, one by five months. Stores a derivation rather than the ~7,700 raw assignment rows, since one date per project is what is read; the raw endpoint remains if staffing analytics ever wants it. Refreshed by `POST /projects/refresh`, which pulls Harvest *and* Forecast in one action — nothing schedules either

33. `20250101000033_recurring_line_item_resolutions.sql` — adds `recurring_line_item_resolutions`, the operator's per-month decision about a placeholder line: an amount, or an explicit omit. Migration `0025` introduced `is_placeholder` on the understanding that the operator would complete the line by hand in the Harvest draft (PRD §10, §13). That put the last step of an invoice in a system this one cannot read, so nothing could notice when it was skipped — the invoice went out short while `planned_amount` still read as correct, precisely because placeholders were excluded from it. Keyed on `(recurring_line_item_id, run_month)` rather than the ledger row, so a decision survives Re-plan; that in turn is why `groups._save_recurring_items` became an upsert-by-id in the same change, since the previous delete-and-reinsert re-minted the ids these rows point at and would have discarded the month's amounts as a side effect of editing an unrelated fee. Also rewrites `0025`'s `is_placeholder` column comment, which described the workflow this replaces

## Open Questions

- **Vector dimensions:** Currently `vector(1536)` assuming OpenAI `text-embedding-3-small` or Voyage. If switching to a different model, revisit.
- **IVFFlat vs HNSW:** IVFFlat is fine for <100k rows. Switch to HNSW when knowledge_base or memories grow past that.
- **Multi-tenant:** Not relevant yet (single company), but if Frogslayer ever runs this for clients, add `tenant_id` to every table and include in RLS.
