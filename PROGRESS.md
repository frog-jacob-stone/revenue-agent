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
- [x] Workflow trace — audit-log event timeline (sourced from `audit_log` for the workflow_id)
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
- [x] `outreach_chain` — 10-node LangGraph with two critique loops sharing one `compose_email` node; `interrupt_before=("gmail_send",)`
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
- [x] `content_creation` — 4-node LangGraph; `voice_review` loops to `draft_post` on fail; no interrupt gate
- [x] `interpret_brief` — direct OpenAI call (`ContentStrategyAgent` system prompt; title, angle, target, type)
- [x] `draft_post` — direct OpenAI call (`LinkedInWritingAgent`); writes/updates `social_posts` row
- [x] `voice_review` — direct OpenAI call (`PersonalVoiceAgent`); max 3 attempts; pass → `social_posts.status=ready`; exhausted → `failed_terminal` (post stays at `status=draft`; future: surface as `needs_revision` once the inbox supports it)
- [x] `content_publish` — 2-node LangGraph; `propose_post` → [interrupt_before] → `post_to_linkedin`
- [x] `propose_post` — execution approval gate (`action_type=post_to_linkedin`)
- [-] `post_to_linkedin` — stub only; updates DB status to `published` but does not post; no LinkedIn integration
- [x] Content orchestrator (`content-orchestrator`) — conversational front door
- [x] Post state machine — `draft` → `ready` → `published | rejected` (`needs_revision` aspirational; not currently emitted)

---

## Tooling

### Workflow Visualizer — `[ ]` (Backlogged)
LangGraph exposes `get_graph().draw_mermaid()` natively. A read-only `/workflows/{id}/diagram` endpoint plus a UI overlay highlighting active/traversed nodes is on the backlog.

---

## Architecture status

One orchestrator (`app/orchestrator/`) backed by LangGraph + `AsyncPostgresSaver`. One approval surface (`/approvals`). One inbox type (`Approval`). The frontend inbox single-sources from `/approvals`. Test suite: 58 green across runner, approval flow, agent invocation, sub-workflow spawn, agent messaging, and end-to-end graph tests for all four production workflows.

Known gaps (tracked in Backlog):
- Native Anthropic tool-use loop in `invoke_agent`
- Multi-turn thread context in `ask_agent`
- Provider-aware `invoke_agent` so content_creation can route through it instead of direct `call_openai`
- Workflow visualizer (Mermaid) endpoint and UI overlay

---

## Backlog

- [ ] **Workflow visualizer (LangGraph)** — `GET /workflows/{id}/diagram` returns the graph's Mermaid (LangGraph provides this natively via `get_graph().draw_mermaid()`) with the active node highlighted from the latest checkpoint and traversed edges colored by completion / failure. UI: live polling on workflow detail.
- [ ] **Diagrams-as-code** — `make diagrams` regenerates `docs/graphs/*.mmd` from registered graphs; useful for PR review of graph changes.
