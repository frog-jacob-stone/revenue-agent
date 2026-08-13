# Revenue Operations System — PRD

## What We're Building

An internal Revenue Operations platform for Frogslayer. It automates the recurring mechanics of running revenue operations — Harvest billing/invoicing, revenue recognition, and (planned) revenue reporting — and gives Jacob a single control plane to manage them. There are two primary surfaces:

1. **Operator Control Plane** (UI) — the invoicing workspace (billing groups, runs, draws, drafted invoices), the approval inbox, an audit trail, and chat with agents for conversational/judgment tasks
2. **Automation Engines** (Backend) — a deterministic Harvest billing/invoicing engine (no agent in its write path) and a revenue-recognition pipeline (Harvest → compute → Airtable), plus an approval-gated agent framework for tasks that need judgment rather than a fixed procedure

This is **not** a chatbot or an agent demo. The billing/invoicing engine — the system's largest and most mature subsystem — is deliberately non-agentic: a human reads the exact computed payload and clicks to create a draft invoice in Harvest. Where agents *are* used (drafting, conversational Q&A over revenue data, content), every create/update/delete they propose flows through an approval inbox. Agents propose; humans decide; the system executes.

## Target Users

Jacob, VP of Revenue at Frogslayer, managing revenue operations day-to-day: closing the books each month, keeping client invoicing accurate and on schedule, and (as this system grows) tracking project completion and revenue by project type without hand-built spreadsheets.

**The system needs to:**
- Automate Harvest invoicing without ever risking a duplicate, wrong, or unauthorized send — the write path is deterministic and every write is either operator-clicked or approval-gated
- Compute revenue recognition correctly across billing types (Fixed Fee, T&M, MSF, Hosting, Retainer) and write it to the Airtable ledger
- Surface an honest, current picture of what's happening — via the audit log today, and via revenue/project reporting once built
- Support agent-assisted tasks (BDR drafting, LinkedIn content, conversational revenue Q&A) without ever letting an LLM hold a write capability

## Scope

### In Scope
- ✅ Approval inbox with payload editing and event trace
- ✅ Agent dashboard (status cards, activity feed, manual triggers)
- ✅ Per-agent detail pages with run history and config panels
- ✅ Audit log with full event history
- ✅ Conversational chat interface for applicable agents
- ✅ Knowledge base / memory viewer
- ✅ Analytics (runs, approval rates, summary stats)
- ✅ Settings (integrations, cron schedules, preferences)
- ✅ Revenue recognition workflow (Harvest → compute → Airtable)
- ✅ Content creation + publishing workflow (brief → strategy → draft → voice review → LinkedIn)
- ✅ Harvest billing/invoicing automation — billing groups, Harvest snapshot sync, T&M estimation, duplicate-invoice guarding, fixed-fee draw scheduling/release, plan → approve → execute ledger. Operator-initiated per [ADR-0004](docs/adr/0004-operator-initiated-writes.md); no agent in the write path.
- ✅ Append-only audit log with full state machine coverage
- 🔲 **Planned — Revenue dashboard.** A business-facing view of revenue (not the existing agent-status dashboard), tracked in `PROGRESS.md` under "Revenue Reporting & Project Tracking." Not yet designed or built.
- 🔲 **Planned — Project-completion tracking.** No `projects`-level completion concept exists in the schema today (only the Harvest project cache). Tracked in `PROGRESS.md`; not yet designed or built.
- 🔲 **Planned — Revenue-per-project-type reporting.** Closest existing concept is `billing_type` (T&M / fixed_fee_schedule / recurring_monthly / manual) as a config enum — no reporting is built on top of it yet. Tracked in `PROGRESS.md`; not yet designed or built.

### Out of Scope
- ❌ CRM and prospecting integrations — HubSpot and Apollo were removed on 2026-08-10; Frogslayer no longer uses either. This retired the outreach workflow (was: CRM pull → draft → critique × 2 → Gmail) and left the BDR agent toolless, drafting from context the caller supplies.
- ❌ Multi-user / role-based access control (v1 is single-user)
- ❌ LangChain, CrewAI, and similar agent frameworks — raw SDKs only for LLM calls
- ❌ Document ingestion pipeline (SharePoint → pgvector)
- ❌ Brand research workflow (deferred — needs ingestion first)
- ❌ Real-time worker queue (Arq + Redis) — FastAPI BackgroundTasks for now
- ❌ Monthly-run invoice execution — the single-draw write path ships; batch execution across many groups (`create_harvest_draft_invoices`), post-run variance reconciliation, and a candidate-invoice picker for in-flight resolution are not yet built. Tracked in `PROGRESS.md`.
- ❌ Agent self-modification of system prompts at runtime

## Stack

| Layer | Choice |
|-------|--------|
| Frontend | React + TypeScript + Vite + Tailwind |
| Backend | FastAPI + Python 3.12 |
| Database | Supabase (Postgres + pgvector + Realtime) |
| Orchestration | Tools, not graphs — a tool returns `Done \| AwaitingApproval \| Blocked`; loops, retries, and conditional branches are inline Python. No graph engine. See [ADR-0002](docs/adr/0002-tools-not-graphs.md). |
| LLM | Anthropic + OpenAI (raw SDKs; no LangChain) |
| Integration bus | n8n (triggers + third-party I/O only; no business logic) |
| Integrations | Harvest, Airtable, Forecast |
| Secrets | Gitignored env files (`app/.env`, `.env.production`) |

## Constraints

- **No write without a human authorizing that specific payload.** Agent-initiated writes flow through `tool returns AwaitingApproval → approval (pending) → human approves → executor runs → executed | failed`. Operator-initiated writes (billing/invoicing) skip the approval row when the exact payload is shown before the click, the endpoint is human-only, and the transition writes `audit_log`. See [ADR-0004](docs/adr/0004-operator-initiated-writes.md).
- Agents never call Harvest / Gmail / Airtable directly — only services execute, either after approval or as an operator-initiated write
- Executors live in a registry separate from tools and are never in any agent's `allowed_tools` — the trust boundary is structural, enforced by `tests/test_no_agent_approval_tools.py`
- Async everywhere; every state-changing service function calls `write_audit_event()`
- Schema changes go through `supabase/migrations/` — never edit the DB by hand
- Routers validate and call services; services hold business logic; agents only propose

---

## Module 1: Approval Inbox

**Build:** Action list view with agent/type/status filter dropdowns wired to backend query params; per-row payload previews (rev rec shows entries table; outreach shows email stub); inline approve and reject flows with free-text rejection reason; action detail view with full JSON payload; Edit & Approve (inline payload editing via `EditBodyModal` before approval, `Modified` badge, diff stored as `executed_payload`); event trace component (chain execution tree, retry attempts indented under root, critiques expandable); pending badge with live count; empty state; Supabase Realtime subscription for new items without page refresh

---

## Architectural Decision: Propose / Approve / Execute

The load-bearing pattern for agent-initiated writes. No agent may execute a create, update, or delete without a prior approved approval row.

```
agent proposes → approval (pending) → human approves → executor runs → executed | failed
```

Every state transition writes a row to `audit_log`. The inbox is the canonical review surface for agent-initiated work.

Operator-initiated writes (billing/invoicing) follow a parallel pattern with the same invariant — no write without a human authorizing that specific payload — but skip the approval row per [ADR-0004](docs/adr/0004-operator-initiated-writes.md), because the human is already looking at the payload in the UI and the click is the authorization.

**The decision made for the inbox:** How much context do you surface in the list row?

| Option | Approach | Pros | Cons |
|--------|----------|------|------|
| **A: Summary only** | Show summary text + risk level; full detail on click-through | Fast to build, low noise | Every approval requires a page navigation |
| **B: Inline type-specific previews** | Show a compact, action-type-aware payload preview in the list row | High-density review without click-through | More complex list component; per-type rendering logic |

**Chosen: Option B** — action-type-specific inline previews in the list row (rev rec shows a mini entries table with totals; outreach shows the email subject and body stub), with full JSON editing in the detail view. Inline previews need to stay in sync as new action types are added.

---

## Module 2: Agent Dashboard

**Build:** Agent status cards grid (agent name, color indicator, status chip, last run time, pending approval count, actions taken today); global error banner when any agent is in error state; recent activity feed (last 10–20 actions across all agents, with agent, type, target, outcome); manual trigger buttons per agent

---

## Module 3: Agent Detail Pages

**Build:** Per-agent status indicator (idle/running/paused); enable/disable toggle wired to `setAgentActive()` API call; last run summary (timestamp + outcome); pending approvals mini-panel with inline approve/reject icons; run history table (last N actions, outcome, reasoning); manual trigger button with error handling; agent tools list from the registry.

---

## Module 4: Audit Log

**Build:** Chronological event table (timestamp, agent, action type, target, outcome, reason); filter bar (agent dropdown, date input, outcome dropdown) — all params sent to `GET /audit_log`; expandable rows revealing full JSON payload; loading spinner and empty state; CSV export button

---

## Module 5: Agent Chat Interface

**Build:** Conversational chat UI with message bubbles; the user only chats with `chief-of-staff` (the single front door); typing indicator (animated dots); auto-scroll to latest message; "Actions from this chat route to your Approval Inbox" routing notice; message history in component state (max 20); Markdown rendering for structured agent responses; `agentChat()` API call wired to `POST /chat/{agent_slug}`

---

## Module 6: Knowledge Base / Memory Viewer

**Build:** Memory list view with per-agent tabs and entry metadata (content, source, date, tags); search filtering wired to API; add memory modal (agent selector, content textarea, comma-separated tags) wired to `POST /memories`; delete memory entry wired to `DELETE /memories/{id}`; full backend integration for all read/write operations

---

## Module 7: Analytics

**Build:** Summary stat cards (accounts researched, outreach sent, proposals generated, approval rate, avg time-to-approve, most active agent); agent runs per day line chart (multi-agent, real API data); approval rate by agent bar chart (real API data); date range selector (7 / 30 / 90 days + custom date range picker) — all wired to `GET /analytics?days=N`

---

## Module 8: Settings

**Build:** Integration status cards (Harvest, Airtable, OpenAI, Slack — connected/not-configured indicators with edit/connect modals); cron schedule table (agent schedules with cron expression + description, with expression editor); timezone preference selector with save handler; integration connect/edit flows with credential input fields

---

## Revenue Operations Automation

These are backend capabilities the Operator Control Plane operates on. Each is a self-contained module with its own step sequence and integration dependencies. Detailed build status for all of these lives in `PROGRESS.md`.

---

## Workflow A: Revenue Recognition

**Build:** Monthly `rev_rec_monthly` pipeline: sync Harvest projects → Airtable, validate completeness → surface incomplete projects for a fix-and-retrigger loop (skipped when data is complete) → compute entries (Harvest invoice totals + Forecast scheduled hours; Fixed Fee: `contracted_fees × logged_hours / total_hours`; T&M/MSF/Hosting: `total_invoiced`) → execution approval → batch upsert to Airtable. Duplicate guard refuses to run twice for the same period. Harvest is the source of truth for time and invoices; Airtable is the revenue recognition ledger.

---

## Workflow B: Outreach — *removed*

Retired on 2026-08-10 along with the HubSpot integration. The chain began with a CRM contact pull, so removing HubSpot removed its first step and its reason to exist. Nothing replaced it; outbound is not currently a system concern.

What the BDR agent retains is the drafting half: hand it a name, role, company, and a signal, and it returns a first-touch draft in the Frogslayer voice. It cannot look anything up.

---

## Workflow C: Content Creation & Publishing

**Build:** Two chains: **content_creation**: interpret brief (LLM: strategy idea with title, angle, target, type) → draft post (LLM: post text, hook, CTA; writes `social_posts` row) → voice review (critique; max 3 attempts; on pass: status → `ready`; on exhaustion: status → `needs_revision`, workflow → `failed`). **content_publish**: execution approval → post to LinkedIn (stub; status → `published`). The LinkedIn agent (`linkedin`) owns the content tools and is invoked by `chief-of-staff` via `ask_agent` — users never see the chains directly. Post state machine: `draft` → `needs_revision` → `ready` → `published | rejected`.

---

## Workflow D: Harvest Billing / Invoicing

**Build:** Harvest snapshot sync (clients, projects, rates) → billing-group config (one Harvest client → one invoice, an abstraction Harvest itself lacks) → reconciliation (every billable project maps to exactly one active group) → T&M estimation from uninvoiced time, or fixed-fee draw / recurring line-item resolution → duplicate guard → plan → per-group approval on the ledger → operator clicks to create a draft invoice in Harvest. No agent or LLM anywhere in this path — deterministic by design, per [ADR-0004](docs/adr/0004-operator-initiated-writes.md). Full phase-by-phase status (T&M pre-flight complete, single-draw execution shipped, monthly-run execution not yet built) is tracked in `PROGRESS.md`.

---

## Success Criteria

The system should deliver:

- ✅ A running revenue operations platform connected to real business data (Harvest, Airtable)
- ✅ Reliable, auditable Harvest invoicing with no duplicate or unauthorized writes
- ✅ Correct revenue recognition across all billing types, written to the Airtable ledger
- ✅ A single approval inbox for every agent-initiated write, and a clear audit trail for every operator-initiated one
- 🔲 Revenue and project reporting (dashboard, project-completion tracking, revenue-per-project-type) — planned, not yet built
- ✅ Agent-assisted conversational and drafting tasks (BDR, LinkedIn, revenue Q&A) that can never hold a write capability directly
