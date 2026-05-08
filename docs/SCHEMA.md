# Supabase Schema — Revenue Agent System

> Source of truth for the database. Update this file when the schema changes.
> Matches migrations: `supabase/migrations/0001_initial_schema.sql` through `0015_drop_workflow_pattern_columns.sql`

## Overview

Eight core tables plus pgvector and LangGraph's checkpoint tables. Every table has RLS enabled from day one so policies can be added without a migration later.

```
agents           → registry of agent definitions (config, prompts, scopes)
workflows        → a business process instance (e.g. "outbound to Acme")
approvals        → human-in-the-loop queue for graph nodes that pause for review
memories         → unified agent memory (facts, summaries, embeddings)
audit_log        → append-only record of everything that happened
knowledge_base   → vector-searchable reference content (playbooks, past deals)
social_posts     → draft and approval queue for chat-driven content creation
agent_messages   → turn-by-turn record of agent-to-agent exchanges
```

## Design Principles

1. **Nothing executes without an approved approval row.** Every CUD operation against HubSpot, Gmail, etc. flows through `approvals` with a `pending → approved → executed | failed` lifecycle.
2. **Workflows are graphs.** A single business process is one workflow whose progress lives in LangGraph's checkpoint tables. A workflow groups its approvals (one row per human gate) and audit-log events.
3. **Audit log is append-only.** Enforced at the database role level, not in application code.
4. **Memory and knowledge are separate.** Memory is what agents learned (emergent). Knowledge base is what we gave them (curated).
5. **RLS on from day one.** Permissive policies for v1; scoped policies when users are added.

## Tables

### `agents`

Stores only runtime-mutable state. Static metadata (`name`, `description`, `requires_approval`, `allowed_tools`, system prompts) is owned exclusively by the Python class in `app/agents/` — the DB is never the source of truth for those.

| Column | Type | Notes |
|---|---|---|
| `id` | uuid | PK |
| `slug` | text | Unique identifier matching the class `slug`, e.g. `outreach-agent` |
| `config` | jsonb | Runtime overrides (model, temperature, etc.) |
| `is_active` | boolean | Soft disable |
| `created_at` / `updated_at` | timestamptz | |

### `workflows`

One row per business process instance.

| Column | Type | Notes |
|---|---|---|
| `id` | uuid | PK |
| `kind` | text | `outreach_chain`, `rev_rec_monthly` (chains are the source of truth — extend as new chains land) |
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
| `parent_workflow_id` | uuid | FK → `workflows.id`, on delete set null. Set when this workflow was spawned from another workflow's node (`spawn_workflow` primitive). Used to render nested traces. Added in migration `0011`. |

**Why `subject_ref` is denormalized:** When you look at a workflow three weeks later, the HubSpot record may have changed. Keep the snapshot of what the agent was looking at.

### `approvals`

Lifecycle-only queue for human-in-the-loop pauses in LangGraph workflows. Added in migration `0010`.

| Column | Type | Notes |
|---|---|---|
| `id` | uuid | PK |
| `workflow_id` | uuid | FK → `workflows.id`, cascade delete |
| `node_name` | text | Which graph node requested approval |
| `agent_slug` | text | The agent acting (display + future ACL) |
| `action_type` | text | Free text — same vocabulary as `actions.action_type` for now |
| `status` | text | One of `pending`, `approved`, `rejected`, `executed`, `failed` (CHECK constraint enforces) |
| `risk_level` | text | `low`, `medium`, `high` |
| `summary` | text | Human-readable description |
| `reasoning` | text | Agent's explanation |
| `proposed_payload` | jsonb | What the node proposed |
| `executed_payload` | jsonb | What actually ran (may differ if human edited) |
| `assigned_to` | text | Reserved for multi-user routing; ignored in v1 of v2 |
| `approved_by` / `approved_at` | — | Set on approval |
| `rejected_by` / `rejection_reason` | — | Set on rejection |
| `executed_at` | timestamptz | Set when the gated node completes |
| `error` | text | Set if the gated node fails after approval |
| `created_at` | timestamptz | |

**Lifecycle:** `pending → approved → executed | failed`, or `pending → rejected`. Audit events emitted at every transition (see "Event Types" below).

**Two payload columns by design:** `proposed_payload` preserves the agent's draft; `executed_payload` captures what actually went out the door. If a human edits the payload before approving, both are preserved for the audit trail.

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

### `social_posts`

Draft and approval queue for the Content Orchestrator. Separate from `workflows`/`actions` because content creation has no external writes — approval is conversational, not inbox-based.

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

Turn-by-turn record of every agent-to-agent exchange. Powers the `ask_agent` tool and the multi-agent demo graph. Migration `0013`.

| Column | Type | Notes |
|---|---|---|
| `id` | bigserial | PK; monotonic insert order |
| `thread_id` | uuid | Correlates messages within one delegation; sender generates a fresh UUID for the first turn |
| `workflow_id` | uuid \| null | FK to `workflows.id` (CASCADE); set when the call originated from a graph node, NULL when from chat |
| `from_agent_slug` | text | Sender's slug |
| `to_agent_slug` | text | Recipient's slug (may equal sender for supervisor self-talk) |
| `content` | text | The message body |
| `metadata` | jsonb | Free-form annotations |
| `created_at` | timestamptz | |

Indexes: `(thread_id, created_at)`, partial `(workflow_id) where workflow_id is not null`, `(to_agent_slug)`.

The table is the audit; service-layer functions in `app/services/agent_messages.py` do **not** write `audit_log` rows for individual messages (volume would dominate the audit log). The runner's `node.exited` events provide enough granularity. Add `AGENT_MESSAGE_SENT` to `app/orchestrator/events.py` if per-turn audit visibility is needed later.

## Agent Types

**Write-proposing agents** — use the full workflow → actions → approval lifecycle. Every operation creates a `workflow` row and one or more `action` rows. Examples: Outreach, Revenue Recognition.

**Read-only agents (analytics, Q&A)** — produce no `workflow` or `action` rows. Their activity is logged to `audit_log` with event type `agent.queried`. Chat history is stored separately per agent session. (None currently in the system; Invoice Analytics was retired alongside Invoice Operations and may return.)

**Router agents** — propose no actions. Their output is a handoff decision referencing which specialist agent should handle the request. Audit logged as `agent.routed`. The router never creates a workflow row — the specialist it hands off to does.

## Event Types (Audit Log Vocabulary)

Keep this list stable; it becomes grep-able forensics. Constants live in `app/orchestrator/events.py` — call sites must import and use them, never string literals.

**Workflow lifecycle:**
- `workflow.started`, `workflow.completed`, `workflow.failed`, `workflow.paused`, `workflow.resumed`, `workflow.cancelled`

**Graph execution:**
- `node.entered`, `node.exited`, `node.failed`

**Approval lifecycle:**
- `approval.requested`, `approval.granted`, `approval.rejected`, `approval.executed`, `approval.failed`

**Agent invocation:**
- `agent.invoked`, `agent.completed`, `agent.failed`
- `agent.queried` (read-only agents answering questions)
- `agent.routed` (router handing off to a specialist)

**Sub-workflows:**
- `subworkflow.spawned`, `subworkflow.completed`

**Memory and knowledge:**
- `memory.written`, `memory.expired`
- `knowledge.created`, `knowledge.updated`

**Content workflows:**
- `content.post_created`, `content.post_drafted`, `content.post_approved`, `content.post_rejected`, `content.post_updated`

Historical audit_log rows from before Phase 5 may carry pre-migration vocabulary (`action.proposed/approved/rejected/executed/failed` and `actor='orchestrator_v2:*'`); these remain queryable. New code emits only the constants listed above.

## API Surface (Maps to FastAPI)

| Endpoint | Purpose |
|---|---|
| `POST /workflows` | n8n or manual trigger creates a workflow |
| `GET /workflows/{id}` | Workflow detail |
| `GET /workflows/{id}/trace` | Audit-log event timeline for the workflow |
| `GET /approvals?status=pending` | The approval inbox query |
| `POST /approvals/{id}/approve` | Human approves → writes audit_log, resumes the graph |
| `POST /approvals/{id}/reject` | Human rejects with reason |
| `POST /memories` | Agent writes to memory (requires approval if `write` in approval_scope) |
| `GET /memories/search` | Vector + scope filter |

## RLS Status

All tables have RLS enabled with permissive `service_role` policies for v1. When user identity is added:

1. Replace permissive policies with scoped ones
2. Map `approvals.approved_by`, `workflows.initiated_by`, `audit_log.actor` to `auth.uid()`
3. Add role-based approval rules (who can approve what `action_type`)

No schema migration required for this step.

## Migration Order

Migrations run in filename order; each is idempotent.

1. `0001_initial_schema.sql` — extensions, enums, six core tables, indexes, RLS, audit_log append-only trigger
2. `0002_agents_allowed_tools.sql` — adds `agents.allowed_tools`
3. `0003_configure_rev_rec_projects_action_type.sql` — adds `configure_rev_rec_projects` to `action_type` enum
4. `0004_invoice_action_types.sql` — adds invoice-related values to `action_type` enum
5. `0005_agentic_patterns.sql` — adds `step_kind`, parent/retry tracking, `critique_result` to `actions`; adds `pattern`, `current_step` to `workflows`
6. `0006_simplify_agents.sql` — drops static metadata columns from `agents` (`name`, `description`, `requires_approval`, `approval_scope`, `system_prompt`, `allowed_tools`); these are now owned exclusively by the Python class registry
7. `0007_social_posts.sql` — adds `social_posts` table for the Content Orchestrator draft and approval queue
8. `0008_content_action_type.sql` — adds `post_to_linkedin` to `action_type` enum for the `content_publish` chain
9. `0009_rename_tool_call_to_task.sql` — renames `actions.step_kind` value `tool_call` → `task`; updates the CHECK constraint to match the Python `StepKind` enum
10. `0010_create_approvals_table.sql` — creates the `approvals` table for the v2 (LangGraph) orchestrator's human-in-the-loop queue. Coexists with `actions` until Phase 5 of the rearchitecture
11. `0011_workflows_parent_id.sql` — adds `workflows.parent_workflow_id` for sub-workflow linkage (used by `app/orchestrator_v2/spawn.py`)
12. `0012_langgraph_checkpoint_tables.sql` — **marker migration only** (no DDL). LangGraph's checkpoint tables (`checkpoints`, `checkpoint_blobs`, `checkpoint_writes`, `checkpoint_migrations`) are created idempotently by `AsyncPostgresSaver.setup()` at app startup (called from `runner.init()`). Schema is internal to LangGraph — do not modify. If LangGraph schema needs custom changes that `setup()` doesn't cover, add a new migration that runs after this one
13. `0013_create_agent_messages.sql` — adds the `agent_messages` table for turn-by-turn agent-to-agent exchanges (powers the `ask_agent` tool)
14. `0014_drop_actions_table.sql` — drops the legacy `actions` table (Phase 5 of `.agent/plans/3.langgraph-multi-agent-rearchitecture.md`). The `audit_log.action_id` FK constraint is dropped via CASCADE; the column itself remains and audit_log rows are preserved
15. `0015_drop_workflow_pattern_columns.sql` — drops `workflows.pattern` and `workflows.current_step` (the v1 prompt-chain orchestrator's progress markers, replaced by LangGraph checkpoints)

## Open Questions

- **Vector dimensions:** Currently `vector(1536)` assuming OpenAI `text-embedding-3-small` or Voyage. If switching to a different model, revisit.
- **IVFFlat vs HNSW:** IVFFlat is fine for <100k rows. Switch to HNSW when knowledge_base or memories grow past that.
- **Multi-tenant:** Not relevant yet (single company), but if Frogslayer ever runs this for clients, add `tenant_id` to every table and include in RLS.
