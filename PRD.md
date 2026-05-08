# Revenue Agents Masterclass — PRD

## What We're Building

An AI-powered revenue operations system — agents replace an entire revenue team. There are two primary surfaces:

1. **Agent Control Center** (UI) — Monitor agents, review and approve proposed actions, chat with agents conversationally, inspect the audit trail
2. **Agentic Workflows** (Backend) — Multi-step chains that research, draft, critique, and execute with a human approval gate before any write

This is **not** a chatbot or a demo. Every create/update/delete operation flows through an approval inbox. Agents propose; humans decide; the system executes.

## Target Users

Technical professionals (revenue ops, sales leaders, founders) who want to build production AI agent systems using Claude Code. They don't need to write every line — that's the AI's job.

**They need to understand:**
- The propose/approve/execute pattern — why it exists and when to enforce it
- Multi-step chain design — LLM steps, tool calls, critique loops, checkpoints
- How to wire agents to real business data (Harvest, HubSpot, Airtable)
- How to direct AI coding tools to build, extend, and debug agentic systems

## Scope

### In Scope
- ✅ Approval inbox with payload editing and workflow trace
- ✅ Agent dashboard (status cards, activity feed, manual triggers)
- ✅ Per-agent detail pages with run history and config panels
- ✅ Audit log with full event history
- ✅ Conversational chat interface for applicable agents
- ✅ Knowledge base / memory viewer
- ✅ Analytics (runs, approval rates, summary stats)
- ✅ Settings (integrations, cron schedules, preferences)
- ✅ Revenue recognition workflow (Harvest → compute → Airtable)
- ✅ Outreach workflow (HubSpot → draft → critique × 2 → Gmail)
- ✅ Content creation + publishing workflow (brief → strategy → draft → voice review → LinkedIn)
- ✅ Critique loops with retry budgets and rewind logic
- ✅ Append-only audit log with full state machine coverage

### Out of Scope
- ❌ Multi-user / role-based access control (v1 is single-user)
- ❌ LangChain, CrewAI, and similar agent frameworks — raw SDKs only for LLM calls
- ❌ Document ingestion pipeline (SharePoint → pgvector)
- ❌ Brand research workflow (deferred — needs ingestion first)
- ❌ Real-time worker queue (Arq + Redis) — FastAPI BackgroundTasks for now
- ❌ Invoice operations (retired; re-implement when use case lands)
- ❌ Agent self-modification of system prompts at runtime

## Stack

| Layer | Choice |
|-------|--------|
| Frontend | React + TypeScript + Vite + Tailwind |
| Backend | FastAPI + Python 3.12 |
| Database | Supabase (Postgres + pgvector + Realtime) |
| Orchestration | LangGraph (graphs, conditional edges, checkpointer); migration in progress per `.agent/plans/3.langgraph-multi-agent-rearchitecture.md` |
| LLM | Anthropic + OpenAI (raw SDKs; no LangChain) |
| Integration bus | n8n (triggers + third-party I/O only; no business logic) |
| Integrations | Harvest, Airtable, Forecast, HubSpot, Gmail |
| Secrets | Doppler |

## Constraints

- **No write without an approved action row** — every CUD operation flows through `proposed → approved → executing → completed` (v1) or `pending → approved → executed` on the `approvals` table (v2)
- Agents never call HubSpot / Gmail / Harvest directly — only services execute after approval
- LangGraph is the orchestration layer (graphs, state, checkpointer, interrupts). LLM calls inside nodes use raw `anthropic` / `openai` SDKs — no LangChain runtime.
- Async everywhere; every state-changing service function calls `write_audit_event()`
- Schema changes go through `supabase/migrations/` — never edit the DB by hand
- Routers validate and call services; services hold business logic; agents only propose

---

## Module 1: Approval Inbox

**Build:** Action list view with agent/type/status filter dropdowns wired to backend query params; per-row payload previews (rev rec shows entries table; outreach shows email stub); inline approve and reject flows with free-text rejection reason; action detail view with full JSON payload; Edit & Approve (inline payload editing via `EditBodyModal` before approval, `Modified` badge, diff stored as `executed_payload`); workflow trace component (chain execution tree, retry attempts indented under root, critiques expandable); pending badge with live count; empty state; Supabase Realtime subscription for new items without page refresh

**Learn:** The approval inbox as a universal operator surface — one UI, every agent, every action type. How `proposed_payload` vs. `executed_payload` preserves the agent's original draft while allowing human correction. The action state machine. The step-kind filter: only `checkpoint` and `execution` steps appear in the inbox; `tool_call`, `llm_step`, and `critique` auto-progress silently.

---

## Architectural Decision: Propose / Approve / Execute

The load-bearing pattern of the entire system. No agent may execute a create, update, or delete without a prior approved action row.

```
agent proposes → action (proposed) → human approves → system executes → completed | failed
```

Every state transition writes a row to `audit_log`. The inbox is the canonical review surface.

**The decision you need to make:** How much context do you surface in the list row?

| Option | Approach | Pros | Cons |
|--------|----------|------|------|
| **A: Summary only** | Show summary text + risk level; full detail on click-through | Fast to build, low noise | Every approval requires a page navigation |
| **B: Inline type-specific previews** | Show a compact, action-type-aware payload preview in the list row | High-density review without click-through | More complex list component; per-type rendering logic |

**In this masterclass, we chose Option B** — action-type-specific inline previews in the list row (rev rec shows a mini entries table with totals; outreach shows the email subject and body stub), with full JSON editing in the detail view. This is a real design decision with tradeoffs; you'll need to keep the inline previews in sync as new action types are added.

---

## Module 2: Agent Dashboard

**Build:** Agent status cards grid (agent name, color indicator, status chip, last run time, pending approval count, actions taken today); global error banner when any agent is in error state; recent activity feed (last 10–20 actions across all agents, with agent, type, target, outcome); manual trigger buttons per agent; "Reach out" button wired to the outreach workflow trigger (prompts for HubSpot contact ID, fires `POST /workflows/outreach`, handles async 202 response)

**Learn:** Agent status as a runtime concept — idle, running, paused. How a dashboard button triggers an orchestrated backend workflow without blocking the HTTP response (async 202 + BackgroundTasks pattern). Designing for operator trust: the dashboard's job is to answer "is everything working?" at a glance, not to expose implementation details.

---

## Module 3: Agent Detail Pages

**Build:** Per-agent status indicator (idle/running/paused); enable/disable toggle wired to `setAgentActive()` API call; last run summary (timestamp + outcome); pending approvals mini-panel with inline approve/reject icons; run history table (last N actions, outcome, reasoning); manual trigger button with error handling; agent tools list from the registry; agent-specific config panels for six agents: Content Writer, Outreach Agent, Proposal Generator, Revenue Recognition, SDR Researcher, Slide Deck Agent

**Learn:** Agent identity — one coherent job, one audit trail, one approval context. Why read-only analytics agents and write-proposing operations agents are separate even when they work on the same data. How config panels expose per-agent settings without a deploy (stored in `agents.config` jsonb).

---

## Module 4: Audit Log

**Build:** Chronological event table (timestamp, agent, action type, target, outcome, reason); filter bar (agent dropdown, date input, outcome dropdown) — all params sent to `GET /audit_log`; expandable rows revealing full JSON payload; loading spinner and empty state; CSV export button

**Learn:** Why audit logs are non-negotiable in agentic systems. Append-only design enforced at the DB role level — no UPDATE or DELETE on `audit_log`. How to use the log to reconstruct what happened when an agent fails. The event taxonomy: `workflow.started`, `action.proposed`, `action.approved`, `action.rejected`, `action.completed`, `workflow.cancelled`.

---

## Module 5: Agent Chat Interface

**Build:** Conversational chat UI with message bubbles; agent selector sidebar filtered to conversational agents only (content-orchestrator, revenue-recognition); typing indicator (animated dots); auto-scroll to latest message; "Actions from this chat route to your Approval Inbox" routing notice; message history in component state (max 20); Markdown rendering for structured agent responses; `agentChat()` API call wired to `POST /chat/{agent_slug}`

**Learn:** Chat as an interface, not an agent type. The same agent (revenue-recognition) is triggered by webhook, schedule, or chat — all paths that propose writes flow through the same approval inbox. Message history management when the LLM API is stateless — the component owns the conversation context. Why conversational agents are a subset of all agents (only agents with `is_conversational=True` appear in the sidebar).

---

## Module 6: Knowledge Base / Memory Viewer

**Build:** Memory list view with per-agent tabs and entry metadata (content, source, date, tags); search filtering wired to API; add memory modal (agent selector, content textarea, comma-separated tags) wired to `POST /memories`; delete memory entry wired to `DELETE /memories/{id}`; full backend integration for all read/write operations

**Learn:** Two memory primitives: **memories** (what agents learn — emergent, runtime-written) vs. **knowledge base** (what you give them — curated, human-managed). Memory scoping: `company:123`, `deal:456`, `global` — why scope matters for multi-agent retrieval. How voice profiles are stored as `kind=preference` memories and loaded at chain runtime without a deploy. The `memories.embedding` column (pgvector 1536-dim) as the foundation for semantic search.

---

## Module 7: Analytics

**Build:** Summary stat cards (accounts researched, outreach sent, proposals generated, approval rate, avg time-to-approve, most active agent); agent runs per day line chart (multi-agent, real API data); approval rate by agent bar chart (real API data); date range selector (7 / 30 / 90 days + custom date range picker) — all wired to `GET /analytics?days=N`

**Learn:** What metrics matter in an agentic system and why: volume (is it working?), approval rate (are the drafts good?), time-to-approve (are humans a bottleneck?). Building analytics on top of `audit_log` as a fact table — the log already captures everything needed. Why the analytics agent and the operations agent for the same domain are separate agents (different audit trails, different inbox behavior, read-only vs. write-proposing).

---

## Module 8: Settings

**Build:** Integration status cards (HubSpot, Apollo, OpenAI, Slack — connected/not-configured indicators with edit/connect modals); cron schedule table (6 agents × cron expression + description, with expression editor); timezone preference selector with save handler; integration connect/edit flows with credential input fields

**Learn:** Integration management at the operator layer. How cron schedules map to n8n trigger configurations (n8n calls FastAPI; FastAPI does not call n8n). Doppler as the secrets layer — why credentials are never stored in the DB. When to surface integration health in the UI vs. relying on alerting. The boundary between what belongs in Settings vs. what belongs in agent config panels.

---

## Agentic Workflows

These are backend capabilities that the UI above operates on. Each is a self-contained module with its own chain definition, step sequence, and integration dependencies.

---

## Workflow A: Revenue Recognition

**Build:** Monthly `rev_rec_monthly` chain (`supervised_automation` pattern): `_sync_and_validate` (sync Harvest projects → Airtable, validate completeness) → `_propose_configure` checkpoint (surfaces incomplete projects; `on_approve` requeues a fresh validation cycle) — **skipped** when data is complete → `_compute_entries` (Harvest invoice totals + Forecast scheduled hours; Fixed Fee: `contracted_fees × logged_hours / total_hours`; T&M/MSF/Hosting: `total_invoiced`) → `_propose_write` execution approval → `_write_entries` (batch upsert to Airtable). Duplicate guard (refuses to run twice for the same period).

**Learn:** `supervised_automation` pattern — deterministic pipeline with a single human checkpoint near the end. `skip_if` predicates: how one chain handles two paths (complete data vs. incomplete data) without forking into separate chains. `CheckpointStep.on_approve` callbacks: how the "fix data externally, then re-trigger" loop is wired. Harvest + Airtable as the integration pair — Harvest as source of truth for time and invoices; Airtable as the revenue recognition ledger.

---

## Workflow B: Outreach

**Build:** `outreach_chain` (`prompt_chain_action` pattern): `_pull_hubspot_contact` (contact + company data) → `_web_search_company` (funding, news signals) → `_consolidate_context` (LLM: 3–4 sentence brief) → `_retrieve_knowledge_base` (GTM blurb) → `_draft_email` (LLM: to, subject, body) → `_voice_critique` (voice-critic agent; max 3 attempts; retries step 4 on fail) → `_accuracy_critique` (accuracy-critic agent; max 2 attempts; retries step 4 on fail) → `_propose_send` execution approval → `_gmail_send`. Voice profile loaded from `memories` (`kind=preference`, `metadata.kind=voice_profile`) at chain runtime. Stub fallbacks for all external calls (HubSpot, web search, Gmail, LLM) enable full chain execution in dev without credentials.

**Learn:** `prompt_chain_action` pattern. Critique loops as a quality gate: how rewind-and-retry works (`retry_of_action_id` chains, `attempt_number` increments, `max_attempts` budget). Why voice and accuracy are separate critics — different failure modes, different retry budgets, different evaluation logic. Budget exhaustion → workflow `failed` (not silent skip). How stub fallbacks let you build and test a complete chain before any integration is live.

---

## Workflow C: Content Creation & Publishing

**Build:** Two chains: **content_creation** (`prompt_chain_action`): `_interpret_brief` (LLM: strategy idea with title, angle, target, type) → `_draft_post` (LLM: post text, hook, CTA; writes `social_posts` row) → `_voice_review` (PersonalVoiceAgent critique; max 3 attempts; on pass: status → `ready`; on exhaustion: status → `needs_revision`, workflow → `failed`). **content_publish** (`supervised_automation`): `_propose_linkedin_post` execution approval → `_linkedin_post_stub` (stub; status → `published`). Content orchestrator (`content-orchestrator`) as the conversational front door — users never see the chains. Post state machine: `draft` → `needs_revision` → `ready` → `published | rejected`.

**Learn:** Conversational approval — how a chat agent drives an orchestrated chain without exposing chain internals to the user. Two-chain composition: create and publish are separate chains with separate approval gates, not one long chain. Social post state machine design: why `needs_revision` is a distinct state from `draft` (it carries critique context). All LLM calls use OpenAI — gpt-4o for reasoning-heavy steps, gpt-4o-mini for drafting and critique steps where latency matters more than depth.

---

## Success Criteria

By the end, you should have:

- ✅ A running revenue operations system connected to real business data (Harvest, Airtable, HubSpot)
- ✅ Deep understanding of the propose/approve/execute pattern — why it exists and how to enforce it
- ✅ Ability to design multi-step chains with LLM steps, tool calls, critique loops, and conditional paths
- ✅ Full schema fluency — `actions`, `workflows`, `memories`, `audit_log`, `social_posts`, `knowledge_base`
- ✅ Experience with conversational agents that route writes through the same approval inbox as automated workflows
- ✅ Ability to direct Claude Code to add new agents, new action types, and new chains without breaking existing patterns
- ✅ A real operator control plane you can hand to a revenue team
