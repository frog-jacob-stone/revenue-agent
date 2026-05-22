# Supabase Schema — Revenue Agent System

> Source of truth for the database. Update this file when the schema changes.
> Matches migrations: `supabase/migrations/0001_initial_schema.sql` through `0022_drop_langgraph_artifacts.sql`

## Overview

Nine core tables plus pgvector. Every table has RLS enabled from day one so policies can be added without a migration later.

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

## Design Principles

1. **Nothing executes without an approved approval row.** Every CUD operation against HubSpot, Gmail, etc. flows through `approvals` with a `pending → approved → executed | failed` lifecycle.
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
| `agent_slug` | text | Which conversational agent this chat is with. `DEFAULT 'revenue-ops'` (migration `0019`) since the single-front-door pattern means new sessions always target the same agent; old rows preserve their original slug for the audit trail. |
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

## Agent Types

**Front-door agent** — `revenue-ops`. The only conversational agent users chat with. Owns the action tools (`create_post`, `publish_post`, `trigger_revenue_recognition`, etc.). Drives an OpenAI tool-call loop inside one chat turn via `app/services/chat_turn.py`.

**Domain worker agents** — invoked single-turn via `run_agent_task` (no `allowed_tools`) or as ReAct loops (with tools). Examples: `revenue-recognition`, `content-orchestrator`, `bdr`. Reached via the `ask_agent` tool; record exchanges in `agent_messages`.

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

## RLS Status

All tables have RLS enabled with permissive `service_role` policies. When user identity is added:

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
10. `0010_create_approvals_table.sql` — creates the `approvals` table for the orchestrator's human-in-the-loop queue
11. `0011_workflows_parent_id.sql` — adds `workflows.parent_workflow_id` for sub-workflow linkage (used by `app/orchestrator/spawn.py`)
12. `0012_langgraph_checkpoint_tables.sql` — **marker migration only** (no DDL). LangGraph's checkpoint tables (`checkpoints`, `checkpoint_blobs`, `checkpoint_writes`, `checkpoint_migrations`) are created idempotently by `AsyncPostgresSaver.setup()` at app startup (called from `runner.init()`). Schema is internal to LangGraph — do not modify. If LangGraph schema needs custom changes that `setup()` doesn't cover, add a new migration that runs after this one
13. `0013_create_agent_messages.sql` — adds the `agent_messages` table for turn-by-turn agent-to-agent exchanges (powers the `ask_agent` tool)
14. `0014_drop_actions_table.sql` — drops the legacy `actions` table. The `audit_log.action_id` FK constraint is dropped via CASCADE; the column itself remains and audit_log rows are preserved
15. `0015_drop_workflow_pattern_columns.sql` — drops `workflows.pattern` and `workflows.current_step` (legacy prompt-chain progress markers, replaced by LangGraph checkpoints)
16. `0016_create_llm_calls.sql` — adds the `llm_calls` audit table for per-request LLM provider call logging
17. `0017_create_chat_tables.sql` — adds `chat_sessions` and `chat_messages` for human-to-agent chat persistence (sidebar multi-chat + durable streaming via `TurnRuntime`)
18. `0018_enable_rls_gaps.sql` — enables RLS on `approvals` and `agent_messages`
19. `0019_chat_sessions_default_slug.sql` — gives `chat_sessions.agent_slug` a `DEFAULT 'revenue-ops'`. Single front-door pattern means new sessions always target the same conversational agent; this lets the router create sessions with no body
20. `0020_drop_agents_config.sql` — drops `agents.config`. The column was a free-form jsonb knob that no app code ever read; per-agent LLM selection lives on the Python class `model` attribute. Follows the precedent of `0006_simplify_agents.sql`
21. `0021_approvals_for_tools.sql` — adds `approvals.executor` and makes `workflow_id` / `node_name` nullable. First step in the [ADR-0002](adr/0002-tools-not-graphs.md) migration from LangGraph graphs to tool-driven approvals. Both grant paths coexist until plan 19
22. `0022_drop_langgraph_artifacts.sql` — drops LangGraph checkpoint tables (`checkpoints`, `checkpoint_blobs`, `checkpoint_writes`, `checkpoint_migrations`), drops the `workflows` table, drops the workflow_id FKs on `approvals` / `audit_log` / `memories.source_workflow_id` / `llm_calls` / `agent_messages` (columns stay as plain UUIDs for historical audit lookups), and flips `approvals.executor` to NOT NULL. Final step of the ADR-0002 cutover (plan 19)

## Open Questions

- **Vector dimensions:** Currently `vector(1536)` assuming OpenAI `text-embedding-3-small` or Voyage. If switching to a different model, revisit.
- **IVFFlat vs HNSW:** IVFFlat is fine for <100k rows. Switch to HNSW when knowledge_base or memories grow past that.
- **Multi-tenant:** Not relevant yet (single company), but if Frogslayer ever runs this for clients, add `tenant_id` to every table and include in RLS.
