# Progress

Track your progress through the masterclass. Update this file as you complete modules - Claude Code reads this to understand where you are in the project.

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
- [x] `outreach_chain` — `prompt_chain_action` pattern; chain structure and orchestration complete
- [-] `_pull_hubspot_contact` — returns hardcoded stub data; raises `NotImplementedError` if real token present; no HubSpot integration
- [-] `_web_search_company` — returns hardcoded fake signals; no real web search
- [x] `_consolidate_context` — real Anthropic LLM call (3–4 sentence brief)
- [-] `_retrieve_knowledge_base` — hardcoded GTM blurb stub; pgvector retrieval deferred until ingestion pipeline ships
- [x] `_draft_email` — real Anthropic LLM call (to, subject, body)
- [x] `_voice_critique` — real Anthropic LLM call; voice profile loaded from `memories` at runtime; max 3 attempts; rewinds to draft on fail
- [x] `_accuracy_critique` — real Anthropic LLM call; max 2 attempts; rewinds to draft on fail
- [x] `_propose_send` — execution approval gate
- [ ] `_gmail_send` — stub only; logs to console; no Gmail integration

### Workflow C: Content Creation & Publishing — `[-]`
- [x] `content_creation` chain — `prompt_chain_action` pattern
- [x] `_interpret_brief` — real OpenAI call (strategy: title, angle, target, type)
- [x] `_draft_post` — real OpenAI call; writes `social_posts` row to DB
- [x] `_voice_review` — real OpenAI call; PersonalVoiceAgent critique; max 3 attempts; pass → `ready`; exhausted → `needs_revision` + workflow `failed`
- [x] `content_publish` chain — `supervised_automation` pattern
- [x] `_propose_linkedin_post` — execution approval gate
- [-] `_linkedin_post_stub` — updates DB status to `published` but does not post; no LinkedIn integration
- [x] Content orchestrator (`content-orchestrator`) — conversational front door
- [x] Post state machine — `draft` → `needs_revision` → `ready` → `published | rejected`
