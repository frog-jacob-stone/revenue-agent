# Progress

Track progress through implementation. Update this file as you complete modules - Claude Code reads this to understand where you are in the project.

## Convention
- `[ ]` = Not started
- `[-]` = In progress / partial
- `[x]` = Completed

## Modules

### Module 1: Approval Inbox — `[-]`
- [x] Inbox list view — pending approvals table, newest first
- [x] Inbox detail view — full agent output and JSON payload display
- [x] Per-row payload previews — action-type-specific inline previews (rev rec entries table, outreach email stub)
- [x] Approve action — real API call, status update
- [x] Reject action — free-text reason, real API call
- [x] Workflow trace — step tree with retry grouping and critique expansion
- [x] Filtering — agent/action type/status dropdowns wired to backend query params
- [x] Pending badge — nav item with live count
- [x] Empty state
- [x] Edit & Approve — inline payload editing via EditBodyModal; Modified badge; diff stored as `executed_payload`
- [-] Realtime — currently polls every 15s; Supabase Realtime subscription not implemented

### Module 2: Agent Dashboard — `[-]`
- [x] Summary cards — last run, current status, pending count, actioned today per agent
- [-] Activity feed — renders but uses hardcoded mock data (`AUDIT_ENTRIES`), not real API
- [x] Global status banner — error state indicator
- [-] Quick-trigger buttons — "Reach out" (Outreach) wired to real API with HubSpot ID prompt; all other agents log to console with StubBadge

### Module 3: Agent Detail Pages — `[-]`
- [x] Agent status indicator — idle/running/paused
- [x] Enable/disable toggle — `setAgentActive()` API call
- [x] Last run summary — timestamp and outcome
- [-] Pending approvals panel — renders filtered mini-list; approve/reject icons are stubbed (console.log, no API call)
- [x] Run history — table of last N actions with outcome and reasoning
- [x] Manual trigger button — `triggerAgent()` real API call with error handling
- [x] Agent tools list — tools registry view
- [-] Agent-specific config panels — 6 panels render inputs; no save handlers wired to `agents.config`

### Module 4: Audit Log — `[-]`
- [x] Chronological log table — timestamp, agent, action type, target, outcome, reason
- [x] Filters — agent dropdown, date input, outcome dropdown; all filter params sent to API
- [x] Expandable rows — click to reveal full JSON payload
- [x] Loading spinner and empty state
- [-] CSV export — button renders with StubBadge; handler logs to console only

### Module 5: Agent Chat Interface — `[-]`
- [x] Conversational chat — message bubbles, real `agentChat()` API call
- [x] Agent selector sidebar — filtered to conversational agents only
- [x] Message history — persisted in component state (max 20 messages)
- [x] Typing indicator — animated dots during loading
- [x] Auto-scroll to latest message
- [x] Inbox routing notice — "Actions from this chat route to your Approval Inbox"
- [-] Markdown rendering — plain `<pre>` with whitespace-pre-wrap; no markdown library
- [ ] Chat history persistence — messages reset on agent switch; not saved to Supabase
- [ ] Context attachment — no ability to paste/attach company description or deal notes

### Module 6: Knowledge Base / Memory Viewer — `[-]`
- [-] Memory list view — UI scaffold with agent tabs and entry metadata (hardcoded mock data, no API)
- [-] Search input — renders with StubBadge; no filtering logic implemented
- [-] Add memory modal — form exists (agent, content, tags); submit logs to console only, no `POST /memories`
- [-] Delete memory entry — button renders; handler logs to console only, no `DELETE /memories/{id}`
- [ ] Backend integration — no API calls wired for any read/write operations

### Module 7: Analytics — `[-]`
- [x] Agent runs per day chart — line chart with legend, real API data
- [x] Approval rate by agent chart — bar chart, real API data
- [x] Summary stat cards — accounts researched, outreach sent, proposals generated, approval rate, avg time-to-approve, most active agent; all from real API
- [-] Date range selector — 7/30/90/Custom buttons render with StubBadge; API call hardcoded to 30 days; clicking logs to console only
- [ ] Custom date range picker — not implemented

### Module 8: Settings — `[-]`
- [-] Integration status cards — HubSpot, Apollo, OpenAI, Slack connection indicators render (hardcoded static data)
- [-] Cron schedule table — 6 agent schedules with cron expressions display (hardcoded static data)
- [ ] Integration connect/edit — buttons render with StubBadge; no modal or API calls
- [ ] Cron expression editor — edit button renders with StubBadge; no editor UI
- [ ] Timezone save — selector renders; no save handler

---

## Agentic Workflows

### Workflow A: Revenue Recognition — `[x]`
- [x] `rev_rec_monthly` chain — `supervised_automation` pattern
- [x] `_sync_and_validate` — real Harvest → Airtable sync; completeness validation
- [x] `_propose_configure` checkpoint — surfaces incomplete projects; `on_approve` requeues a fresh validation cycle
- [x] `skip_if` predicate — checkpoint skipped when data is complete
- [x] `_compute_entries` — real Harvest invoice totals + Forecast scheduled hours; Fixed Fee / T&M / MSF / Hosting formulas
- [x] `_propose_write` — execution approval gate
- [x] `_write_entries` — real Airtable batch upsert
- [x] Duplicate guard — refuses to run twice for the same period

### Workflow B: Outreach — `[-]`
- [x] `outreach_chain` — v2 LangGraph (Phase 3); 10-node graph with two critique loops sharing one `compose_email` node; `interrupt_before=("gmail_send",)`
- [-] `pull_hubspot` — returns hardcoded stub data; raises `NotImplementedError` if real token present; no HubSpot integration
- [-] `web_search` — returns hardcoded fake signals; no real web search
- [x] `consolidate` — Anthropic LLM call via `invoke_agent("outreach-agent", ...)`
- [-] `retrieve_kb` — hardcoded GTM blurb stub; pgvector retrieval deferred until ingestion pipeline ships
- [x] `compose_email` — Anthropic LLM call via `invoke_agent("outreach-agent", ...)`; consumes `last_critique_feedback` on retry
- [x] `voice_critique` — Anthropic LLM call via `invoke_agent("voice-critic", ...)`; voice profile loaded from `memories` at runtime; max 3 attempts; loops to `compose_email` on fail
- [x] `accuracy_critique` — Anthropic LLM call via `invoke_agent("accuracy-critic", ...)`; max 2 attempts; loops to `compose_email` on fail
- [x] `propose_send` — execution approval gate (`action_type=send_email`)
- [ ] `gmail_send` — stub only; logs to console; no Gmail integration

### Workflow C: Content Creation & Publishing — `[-]`
- [x] `content_creation` — v2 LangGraph (Phase 3); 4-node graph; `voice_review` loops to `draft_post` on fail; no interrupt gate
- [x] `interpret_brief` — direct OpenAI call (`ContentStrategyAgent` system prompt; title, angle, target, type)
- [x] `draft_post` — direct OpenAI call (`LinkedInWritingAgent`); writes/updates `social_posts` row
- [x] `voice_review` — direct OpenAI call (`PersonalVoiceAgent`); max 3 attempts; pass → `social_posts.status=ready`; exhausted → `failed_terminal` + workflow `failed` (post stays at `status=draft`; mismatch with prior PROGRESS docs noted; revisit when inbox surfaces `needs_revision`)
- [x] `content_publish` — v2 LangGraph (Phase 1); `supervised_automation` pattern
- [x] `propose_post` — execution approval gate (`action_type=post_to_linkedin`)
- [-] `post_to_linkedin` — stub only; updates DB status to `published` but does not post; no LinkedIn integration
- [x] Content orchestrator (`content-orchestrator`) — conversational front door
- [x] Post state machine — `draft` → `ready` → `published | rejected` (`needs_revision` aspirational; not currently set on v2)

---

## Tooling

### Chain Visualizer — `[x]`
- [x] `chain_to_mermaid` / `chain_to_dict` in `app/orchestrator/diagram.py` — pure transforms over the `Chain` registry
- [x] Optional `skip_if_label` / `on_approve_label` on `Step` — populated on `rev_rec_monthly`
- [x] `GET /chains`, `GET /chains/{kind}`, `GET /chains/{kind}/diagram` — read-only API
- [x] `<MermaidDiagram>` UI component using the `mermaid` npm package
- [x] AgentDetail "Chains" section — chains filtered by agent_slug
- [x] `/chains` index page — all registered chains side-by-side; sidebar entry

## LangGraph Migration

Multi-week rearchitecture from the bespoke v1 orchestrator + `actions` table to LangGraph + `approvals` table. Master plan: `.agent/plans/3.langgraph-multi-agent-rearchitecture.md`.

### Phase 0 — Foundations — `[x]`
Sub-plan: `.agent/plans/4.path-b-phase-0-foundations.md`
- [x] Conftest fixture fix (`agents.name` rot from migration 0006); 96 tests now pass with 2 known v1 chain xfails (retired in Phases 2/5)
- [x] LangGraph + checkpoint-postgres + psycopg[binary] in `pyproject.toml`
- [x] Migration 0010 — `approvals` table (with `assigned_to nullable` for future routing)
- [x] Migration 0011 — `workflows.parent_workflow_id` for sub-workflow linkage
- [x] `app/orchestrator_v2/events.py` — canonical audit-event constants
- [x] `app/models/approvals.py` + `app/services/approvals.py` + `app/routers/approvals.py`
- [x] `app/orchestrator_v2/runner.py` — start/resume/register; emits the full event taxonomy
- [x] `app/orchestrator_v2/state.py` — `BaseGraphState`, `ProposeApproval` conventions
- [x] `app/orchestrator_v2/agent_invoke.py` + `spawn.py` — agent invocation + sub-workflow primitives
- [x] Critique-loop POC (`app/orchestrator_v2/graphs/_critique_poc.py`) — proves the cycle pattern Phase 3 needs
- [x] 5 new test files (12 tests across runner, approval flow, agent_invoke, spawn, critique POC) — all green
- [x] Doc updates: PRD, CLAUDE, SCHEMA, PROGRESS

### Phase 1 — Migrate `content_publish` — `[-]`
Sub-plan: `.agent/plans/5.path-b-phase-1-content-publish.md`
- [x] Migration 0012 — marker for LangGraph checkpoint tables (created idempotently by `AsyncPostgresSaver.setup()` at startup)
- [x] Runner swap — `MemorySaver` → `AsyncPostgresSaver` against `settings.database_url` via psycopg pool; lazy `init()` keyed off first `start`/`resume`
- [x] `app/orchestrator_v2/graphs/content_publish.py` — `propose_post` → `[interrupt_before]` → `post_to_linkedin` → END; same approval payload shape as v1
- [x] `app/orchestrator_v2/graphs/__init__.py::register_all` — wired into `app/main.py` lifespan after `runner.init()`
- [x] `app/tools/content_tools.py:_publish_post` — dispatches via `runner.start("content_publish", ...)`; v1 `CONTENT_PUBLISH_CHAIN` registration removed (chain object stays defined for Phase 5 cleanup)
- [x] `GET /workflows/{id}/trace` — returns `audit_log` events under new `events` field for v2 workflows; v1 actions list still served for non-v2 kinds
- [x] Frontend dual-source — `getInboxItems()` merges `/actions` + `/approvals`; `InboxList`/`InboxDetail`/`Sidebar` badge updated; `InboxItem = Action | Approval` discriminated by `node_name`; `?source=v2` route param routes detail fetches/approve/reject
- [x] `WorkflowTrace` — renders v2 audit-event timeline when `trace.events` is populated; v1 step tree otherwise
- [-] Tests for `content_publish` graph (`tests/test_v2_content_publish_graph.py`) — written but not yet green; circle back

### Phase 2 — Migrate `rev_rec_monthly` — `[-]`
Sub-plan: `.agent/plans/6.path-b-phase-2-rev-rec.md`
- [x] `app/orchestrator_v2/graphs/rev_rec.py` — 6 nodes (`validate_and_sync`, `propose_configure`, `apply_configure_or_loop`, `compute_entries`, `propose_write_entries`, `write_entries`); conditional edge after validate; loop edge from `apply_configure_or_loop` → `validate_and_sync`; `interrupt_before=("apply_configure_or_loop", "write_entries")`
- [x] One workflow_id covers all configure→validate iterations (replaces v1's split-workflow `on_approve = create_workflow + resume` pattern)
- [x] Payload shapes for `configure_rev_rec_projects` and `write_rev_rec` match v1 — inbox UI unchanged
- [x] `apply_configure_or_loop` is POC no-op; future propose+execute will read `state.executed_payload` and apply Airtable updates before looping (graph topology unchanged)
- [x] v1 fix surfaced naturally — v2 reads `state.entries` directly, no sequence-indexed lookup to misalign when checkpoint is skipped
- [x] Registered in `app/orchestrator_v2/graphs/__init__.py::register_all`
- [x] v1 `REV_REC_CHAIN` unregistered (`app/orchestrator/chains/rev_rec.py::register()` is a no-op stub; chain object stays defined for Phase 5 cleanup)
- [x] `app/tools/revenue_tools.py:_trigger_revenue_recognition` dispatches via `runner.start("rev_rec_monthly", ...)`
- [ ] Tests (`tests/test_v2_rev_rec_graph.py`, `tests/test_chain_diagram.py` parametrize cleanup, skip `tests/test_rev_rec_chain.py`) — deferred per "code first, tests later" preference; tracked alongside Phase 1's deferred test work

### Phase 3 — Migrate `outreach_chain` and `content_creation` — `[-]`
Sub-plan: `.agent/plans/7.path-b-phase-3-outreach-content.md`
- [x] `app/orchestrator_v2/graphs/outreach.py` — 10 nodes; two critique loops sharing one `compose_email` node; independent voice (max 3) and accuracy (max 2) budgets; `last_critique_feedback` surfaces only the most recent failed critique into the next draft; `failed_terminal` on either budget exhaustion; `interrupt_before=("gmail_send",)`
- [x] `app/orchestrator_v2/graphs/content_creation.py` — 4 nodes; `voice_review` loops back to `draft_post` on fail with budget; no interrupt gate (terminates at voice pass with `social_posts.status=ready`, or at `failed_terminal` on budget exhaustion)
- [x] LLM calls: outreach uses `invoke_agent` (all three agents are Anthropic-backed and registered in AGENTS); content_creation uses direct `call_openai` (Phase 4 will unify provider dispatch via a provider-aware `invoke_agent`)
- [x] Voice profile loaded from `memories` (same SQL as v1; seed unchanged)
- [x] Node-vs-state-key namespace conflict learned: outreach uses `compose_email` (node) + `draft_email` (state field); LangGraph rejects identical names
- [x] Registered in `app/orchestrator_v2/graphs/__init__.py::register_all` (all four kinds now on v2)
- [x] v1 chains unregistered (`outreach.py::register()` and `content.py::register()` are now no-op stubs; chain objects stay defined for Phase 5 cleanup)
- [x] `app/routers/workflows.py:trigger_outreach` dispatches via `runner.start("outreach_chain", ...)`; `BackgroundTasks` arg removed (the runner drives the graph synchronously to the first interrupt and returns)
- [x] `app/tools/content_tools.py:_create_post` dispatches via `runner.start("content_creation", ...)`; pre-creation of the `social_posts` row unchanged
- [ ] Tests deferred (`tests/test_v2_outreach_graph.py`, `tests/test_v2_content_creation_graph.py`) — alongside Phases 1 and 2's deferred test work; `tests/test_outreach_workflow.py` and the v1 chain-diagram tests still need a module-level skip

### Phase 4 — Multi-agent primitives — `[-]`
Sub-plan: `.agent/plans/8.path-b-phase-4-multi-agent.md`
- [x] Migration 0013 — `agent_messages` table (`bigserial id`, `thread_id`, nullable `workflow_id` FK with CASCADE, sender/recipient slugs, content, metadata, created_at; 3 indexes including a partial workflow index)
- [x] `app/services/agent_messages.py` — `send_message` (dual pool/conn API), `read_thread`, `get_messages_for_workflow`; service does not write to `audit_log` (the table is the audit)
- [x] `tests/test_agent_messages.py` — 3 tests green: new-thread send, ordered thread read, per-workflow filtering
- [x] `ToolContext` extended with optional `workflow_id: UUID | None = None`; backwards-compatible (existing 3 callsites all use kwargs)
- [x] `app/tools/agent_tools.py` — `ASK_AGENT` ToolDefinition wrapping `agent_messages.send_message` + `invoke_agent` + `agent_messages.send_message`; registered in `app/tools/__init__.py::_ALL_TOOLS`
- [x] `app/orchestrator_v2/graphs/_multi_agent_demo.py` — 3 nodes (`supervisor_decide`, `specialist_propose`, `supervisor_review`), conditional edge after review (both branches → END), no interrupt gate; reuses existing AGENTS (`outreach-agent` as supervisor, `voice-critic`/`accuracy-critic` as specialists); not auto-registered (matches `_critique_poc.py` convention — tests/smoke scripts register explicitly)
- [ ] Manual end-to-end smoke against real Anthropic — pending; service tests pass without LLM access
- [ ] Wiring `ask_agent` into a chat agent's `allowed_tools` — deferred (no current production agent enables it; opt-in for future agents)
- [ ] Native Anthropic tool-use loop in `invoke_agent` — deferred; demo nodes parse JSON responses to drive routing

### Phase 5 — Cleanup and decommission — `[ ]`
Sub-plan: `.agent/plans/9.path-b-phase-5-cleanup.md` (not yet written)

---

## Backlog

- [ ] **Chain visualizer: runtime overlay** — `GET /workflows/{id}/diagram` returns the chain's Mermaid with the active step highlighted and traversed branches colored by completion / failure. Resolve `actions` rows back to chain step indices via the existing retry-chain root logic in `app/orchestrator/prompt_chain.py::_chain_step_index_for_action`. UI: live polling on workflow detail.
- [ ] **Chain visualizer: diagrams-as-code** — `make diagrams` regenerates `docs/chains/*.mmd` from the registry; useful for PR review of chain changes.
