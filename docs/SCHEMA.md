# Supabase Schema — Revenue Agent System

> Source of truth for the database. Update this file when the schema changes.
> Matches migrations: `supabase/migrations/0001_initial_schema.sql` through `0005_agentic_patterns.sql`

## Overview

Six core tables plus pgvector. Every table has RLS enabled from day one so policies can be added without a migration later.

```
agents           → registry of agent definitions (config, prompts, scopes)
workflows        → a business process instance (e.g. "outbound to Acme")
actions          → individual agent steps within a workflow (the approval unit)
memories         → unified agent memory (facts, summaries, embeddings)
audit_log        → append-only record of everything that happened
knowledge_base   → vector-searchable reference content (playbooks, past deals)
```

## Design Principles

1. **Nothing executes without an approved action row.** Every CUD operation against HubSpot, Gmail, etc. flows through `actions` with a `proposed → approved → executing → completed` lifecycle.
2. **Workflows group actions.** A single business process (e.g. "generate proposal for Acme") is one workflow with N actions. The workflow is what a human cares about; actions are what gets approved.
3. **Audit log is append-only.** Enforced at the database role level, not in application code.
4. **Memory and knowledge are separate.** Memory is what agents learned (emergent). Knowledge base is what we gave them (curated).
5. **RLS on from day one.** Permissive policies for v1; scoped policies when users are added.

## Tables

### `agents`

Registry of agent definitions. Stored in DB (not hardcoded) so agents can be toggled, reconfigured, and listed in the UI without a deploy.

| Column | Type | Notes |
|---|---|---|
| `id` | uuid | PK |
| `slug` | text | Unique identifier, e.g. `sdr_researcher` |
| `name` | text | Display name |
| `description` | text | |
| `requires_approval` | boolean | Master switch for this agent |
| `approval_scope` | text[] | Which op types need approval: `{create,update,delete}` |
| `config` | jsonb | Model, temperature, tool whitelist |
| `system_prompt` | text | Base prompt (may be overridden per workflow) |
| `is_active` | boolean | Soft disable |
| `created_at` / `updated_at` | timestamptz | |

### `workflows`

One row per business process instance.

| Column | Type | Notes |
|---|---|---|
| `id` | uuid | PK |
| `kind` | text | `sdr_outreach`, `proposal_generation`, `rev_rec_monthly`, `invoice_generation`, `invoice_edit`, `invoice_send`, `invoice_review_digest` |
| `status` | enum | `pending`, `running`, `awaiting_approval`, `completed`, `failed`, `cancelled` |
| `trigger_source` | text | `hubspot_webhook`, `manual`, `schedule` |
| `trigger_payload` | jsonb | Raw event that started it |
| `subject_type` | text | `company`, `deal`, `contact`, `period` |
| `subject_id` | text | External ID (HubSpot company id, etc.) |
| `subject_ref` | jsonb | **Denormalized snapshot** of the subject at workflow start |
| `initiated_by` | text | `system` or user id later |
| `started_at` / `completed_at` | timestamptz | |
| `error` | text | Populated on failure |
| `metadata` | jsonb | |
| `pattern` | text | One of `supervised_automation`, `prompt_chain_action`, `prompt_chain_artifact`. See "Agentic Patterns" below. |
| `current_step` | int | Sequence number of the active step. Set by the orchestrator on each step transition; helps resume after a checkpoint approval. |

**Why `subject_ref` is denormalized:** When you look at a workflow three weeks later, the HubSpot record may have changed. Keep the snapshot of what the agent was looking at.

### `actions`

The atomic unit of work and the approval gate. Every step in a multi-step chain — tool call, LLM step, critique, checkpoint, execution — is one row in `actions`. The full chain is reconstructible from `workflow_id` ordered by `sequence`.

| Column | Type | Notes |
|---|---|---|
| `id` | uuid | PK |
| `workflow_id` | uuid | FK → `workflows.id`, cascade delete |
| `agent_id` | uuid | FK → `agents.id` |
| `sequence` | int | Order within the workflow (unique per workflow) |
| `action_type` | enum | `research`, `send_email`, `create_hubspot_record`, `generate_invoice`, `edit_invoice`, `send_invoice`, `post_review_digest`, `write_rev_rec` |
| `status` | enum | `proposed`, `approved`, `rejected`, `executing`, `completed`, `failed` |
| `summary` | text | Human-readable: "Send intro email to Jane at Acme" |
| `proposed_payload` | jsonb | What the agent drafted |
| `executed_payload` | jsonb | What actually ran (may differ if human edited) |
| `result` | jsonb | Response from HubSpot/Gmail/etc. |
| `reasoning` | text | Agent's explanation — shown in approval UI |
| `risk_level` | text | `low`, `medium`, `high` |
| `approved_by` | text | User id later; `auto` if no approval needed |
| `approved_at` | timestamptz | |
| `rejection_reason` | text | |
| `executed_at` | timestamptz | |
| `error` | text | |
| `created_at` | timestamptz | |
| `step_kind` | text | One of `tool_call`, `llm_step`, `critique`, `checkpoint`, `execution`. Null on pre-0005 rows (treated as `execution`). See "Agentic Patterns". |
| `parent_action_id` | uuid | FK → `actions.id`. Critique points to the draft it critiqued; revision points to the critique that triggered it. |
| `retry_of_action_id` | uuid | FK → `actions.id`. Points to the failed prior attempt this row retries. Retries are siblings, not children. |
| `attempt_number` | int | 1 for the first attempt; incremented per retry within a loop. |
| `max_attempts` | int | Set on the *first* attempt of a loop only. Null = no cap. Orchestrator computes remaining budget by counting siblings via `retry_of_action_id`. |
| `critique_result` | jsonb | Populated only on `step_kind = 'critique'`. Shape: `{ passed: bool, score: float, feedback: text, issues: [] }` |

**Two payload columns by design:** `proposed_payload` preserves the agent's draft; `executed_payload` captures what actually went out the door. If a human edits the email before approving, both are preserved for the audit trail.

**Inbox filter:** `GET /actions?status=proposed` returns only rows with `step_kind IN ('checkpoint', 'execution') OR step_kind IS NULL`. Internal `tool_call`, `llm_step`, and `critique` rows auto-progress and never appear in the inbox; they are visible only via the workflow trace endpoint.

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
| `source_workflow_id` | uuid | FK, nullable |
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
| `workflow_id` | uuid | FK, nullable |
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

## Agent Types

**Write-proposing agents** — use the full workflow → actions → approval lifecycle. Every operation creates a `workflow` row and one or more `action` rows. Examples: SDR Researcher, Outreach Agent, Invoice Operations.

**Read-only agents (analytics, Q&A)** — produce no `workflow` or `action` rows. Their activity is logged to `audit_log` with event type `agent.queried`. Chat history is stored separately per agent session. Examples: Invoice Analytics, (future) Revenue Analytics.

**Router agents** — propose no actions. Their output is a handoff decision referencing which specialist agent should handle the request. Audit logged as `agent.routed`. The router never creates a workflow row — the specialist it hands off to does.

## Agentic Patterns

A workflow's `pattern` column captures the *shape* of the work, which determines how the orchestrator drives the chain. Three patterns are supported:

| Pattern | Example | Shape |
|---|---|---|
| `supervised_automation` | Revenue recognition monthly run | Deterministic pipeline of tool calls and LLM steps with a single human checkpoint near the end. |
| `prompt_chain_action` | SDR outreach | Multi-step LLM chain with reflection loops (critique → revise) ending in an external write (Gmail send, HubSpot update). |
| `prompt_chain_artifact` | Brand research synthesis (deferred) | Heavy RAG, retry caps with hard fail, source traceability, ends in a file artifact. Schema-ready; build deferred. |

### Step Kinds

Every step in a chain is an `actions` row. The `step_kind` column declares what role the step plays:

| step_kind | Approval required? | Behavior |
|---|---|---|
| `tool_call` | No | Auto-progresses. Audit trail only. Used for HubSpot lookups, web search, knowledge_base retrieval. |
| `llm_step` | No | Auto-progresses. Audit trail only. Used for context consolidation, drafting, revision. |
| `critique` | No | Auto-progresses. Emits `critique_result`. On fail with budget remaining, triggers a retry of the prior step. |
| `checkpoint` | **Yes** | Pauses the workflow for human review. Appears in the approval inbox. |
| `execution` | **Yes** | Pauses for approval before performing an external write (Gmail send, HubSpot create). Appears in the approval inbox. |

### Parent vs Retry Relationships

Two columns express two different relationships, and they should not be conflated:

- **`parent_action_id`** — the *semantic* parent. A critique points to the draft it critiqued. A revision points to the critique that triggered it. Used by the trace UI to render hierarchy.
- **`retry_of_action_id`** — a *sibling* relationship between attempts. A retry has the same `step_kind` and `parent_action_id` as its prior attempt; only `attempt_number` and `retry_of_action_id` differ. Used by the orchestrator to count remaining budget.

A retry is *not* a child of the failure it retries — it is a sibling of it.

### Loop Budget Convention

`max_attempts` is set only on the **first attempt** of a loop. Retry rows do not duplicate the value. The orchestrator computes "attempts remaining" by counting prior attempts via `retry_of_action_id` and comparing to the budget on the first attempt.

When a critique fails and budget is exhausted, the workflow is marked `failed` with structured error in `workflows.error`.

## Event Types (Audit Log Vocabulary)

Keep this list stable; it becomes grep-able forensics.

- `workflow.started`, `workflow.completed`, `workflow.failed`, `workflow.cancelled`
- `action.proposed`, `action.approved`, `action.rejected`, `action.executed`, `action.failed`
- `memory.written`, `memory.expired`
- `knowledge.created`, `knowledge.updated`
- `agent.queried` (read-only agents answering questions)
- `agent.routed` (router handing off to a specialist)

## API Surface (Maps to FastAPI)

| Endpoint | Purpose |
|---|---|
| `POST /workflows` | n8n or manual trigger creates a workflow |
| `GET /workflows/{id}` | Workflow detail with actions |
| `POST /workflows/{id}/actions` | An agent proposes an action |
| `POST /actions/{id}/approve` | Human approves → writes audit_log, triggers execution |
| `POST /actions/{id}/reject` | Human rejects with reason |
| `GET /actions?status=proposed` | The approval inbox query |
| `POST /memories` | Agent writes to memory (requires approval if `write` in approval_scope) |
| `GET /memories/search` | Vector + scope filter |

## RLS Status

All tables have RLS enabled with permissive `service_role` policies for v1. When user identity is added:

1. Replace permissive policies with scoped ones
2. Map `actions.approved_by`, `workflows.initiated_by`, `audit_log.actor` to `auth.uid()`
3. Add role-based approval rules (who can approve what `action_type`)

No schema migration required for this step.

## Migration Order

Migrations run in filename order; each is idempotent.

1. `0001_initial_schema.sql` — extensions, enums, six core tables, indexes, RLS, audit_log append-only trigger
2. `0002_agents_allowed_tools.sql` — adds `agents.allowed_tools`
3. `0003_configure_rev_rec_projects_action_type.sql` — adds `configure_rev_rec_projects` to `action_type` enum
4. `0004_invoice_action_types.sql` — adds invoice-related values to `action_type` enum
5. `0005_agentic_patterns.sql` — adds `step_kind`, parent/retry tracking, `critique_result` to `actions`; adds `pattern`, `current_step` to `workflows`

## Open Questions

- **Vector dimensions:** Currently `vector(1536)` assuming OpenAI `text-embedding-3-small` or Voyage. If switching to a different model, revisit.
- **IVFFlat vs HNSW:** IVFFlat is fine for <100k rows. Switch to HNSW when knowledge_base or memories grow past that.
- **Multi-tenant:** Not relevant yet (single company), but if Frogslayer ever runs this for clients, add `tenant_id` to every table and include in RLS.
