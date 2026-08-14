# Progress

Track progress through implementation. Update this file as you complete modules - Claude Code reads this to understand where you are in the project.

## Convention
- `[ ]` = Not started
- `[-]` = In progress / partial
- `[x]` = Completed

This file tracks two tracks: **Revenue Operations Automation** (billing/invoicing, revenue recognition, and planned revenue/project reporting — deterministic, no agent in the write path) and **Agent Framework** (the approval-gated conversational/drafting layer). See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for how the two relate.

---

## Agent Framework

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
- [-] Summary cards — real agents from `GET /agents`: name, description, active/disabled. Last-run, actioned-today, and idle/running/error status were mock-only inventions with no backing data; removed rather than faked
- [x] Activity feed — last 10 rows from `GET /audit_log`
- [ ] Global status banner — removed; nothing records an agent error state to banner on
- [-] Quick-trigger buttons — all agents log to console with StubBadge (the one real trigger, "Reach out", went away with the outreach workflow)

> **No mock fixtures left in the UI.** `ui/src/mocks/index.ts` — a prototype
> fixture for five agents (`sdr-researcher`, `outreach-agent`, `content-writer`,
> `proposal-generator`, `slide-deck-agent`) that were never in
> `app/agents/registry.py` — was deleted on 2026-08-10, along with their five
> unreachable config panels. Every screen now reads the real API or shows an
> honest empty state. `ui/src/mocks/` is gone entirely — the Invoices module's
> shared types and formatters, which were never mock data, now live at
> `ui/src/invoicing.ts`.

### Module 3: Agent Detail Pages — `[-]`
- [x] Agent status indicator — idle/running/paused
- [x] Enable/disable toggle — `setAgentActive()` API call
- [x] Last run summary — timestamp and outcome
- [-] Pending approvals panel — renders filtered mini-list; approve/reject icons are stubbed (console.log, no API call)
- [x] Run history — table of last N actions with outcome and reasoning
- [x] Manual trigger button — `triggerAgent()` real API call with error handling
- [x] Agent tools list — tools registry view
- [ ] Agent-specific config panels — deferred. Original storage target (`agents.config` jsonb) was dropped in migration 0020 as unused; if/when this feature returns, design the storage shape from scratch

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
- [-] Memory list view — agent tabs are real (`GET /agents`); the entry list is an honest empty state. Both `/memories` endpoints return 501, so there is nothing to show. Previously filled with mock entries, which made an unbuilt feature look shipped
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
- [-] Integration status cards — Harvest, Airtable, OpenAI, Slack connection indicators render (hardcoded static data)
- [-] Cron schedule table — 6 agent schedules with cron expressions display (hardcoded static data)
- [ ] Integration connect/edit — buttons render with StubBadge; no modal or API calls
- [ ] Cron expression editor — edit button renders with StubBadge; no editor UI
- [ ] Timezone save — selector renders; no save handler

---

## Agentic Workflows

### Workflow B: Outreach — **removed**
Deleted with the LangGraph rip-out, then finished off on 2026-08-10 when HubSpot and Apollo were removed. Nothing here survives in code. What remains of outbound is the BDR agent, which drafts from context the caller supplies and has no tools.

### Workflow C: Content Creation & Publishing — `[-]`
- [x] `content_creation` — 4-node LangGraph; `voice_review` loops to `draft_post` on fail; no interrupt gate
- [x] `interpret_brief` — direct OpenAI call (`ContentStrategyAgent` system prompt; title, angle, target, type)
- [x] `draft_post` — direct OpenAI call (`LinkedInWritingAgent`); writes/updates `social_posts` row
- [x] `voice_review` — direct OpenAI call (`PersonalVoiceAgent`); max 3 attempts; pass → `social_posts.status=ready`; exhausted → `failed_terminal` (post stays at `status=draft`; future: surface as `needs_revision` once the inbox supports it)
- [x] `content_publish` — 2-node LangGraph; `propose_post` → [interrupt_before] → `post_to_linkedin`
- [x] `propose_post` — execution approval gate (`action_type=post_to_linkedin`)
- [-] `post_to_linkedin` — stub only; updates DB status to `published` but does not post; no LinkedIn integration
- [x] LinkedIn agent (`linkedin`) — domain worker that owns the content tools (`create_post`, `publish_post`, etc.); invoked via `ask_agent` from the single front-door `chief-of-staff` agent. Internal LLM calls inside `create_post` (interpret_brief, draft_post, voice_review) are inlined as prompt constants in `app/agents/tools/content/_creation_prompts.py` rather than separate agent classes.
- [x] Post state machine — `draft` → `ready` → `published | rejected` (`needs_revision` aspirational; not currently emitted)

---

## Revenue Operations Automation

### Workflow A: Revenue Recognition — `[-]`
- [x] `rev_rec_monthly` chain — `supervised_automation` pattern
- [x] `_sync_and_validate` — real Harvest → Airtable sync; completeness validation
- [x] `_propose_configure` checkpoint — surfaces incomplete projects; `on_approve` requeues a fresh validation cycle
- [x] `skip_if` predicate — checkpoint skipped when data is complete
- [x] `_compute_entries` — real Harvest invoice totals + Forecast scheduled hours; Fixed Fee / T&M / MSF / Hosting formulas
- [x] `_propose_write` — execution approval gate
- [x] `_write_entries` — real Airtable batch upsert
- [x] Duplicate guard — refuses to run twice for the same period

#### Conversational Querying — `[-]`
- [-] Slim payload for LLM context — `get_revenue_data_slim` maps Airtable fields to compact slim keys and derives `blended_rate`; defaults to last 12 months when no range given (`app/services/revenue.py`)
- [-] Date-filtered Airtable pulls — `get_revenue_records` accepts `date_from` / `date_to` and pushes the filter into Airtable's `filterByFormula` (`app/integrations/airtable.py`)
- [-] Agent prompt guidance — system prompt documents slim fields, distinguishes `revenue_delta` vs `total_recognized_revenue`, instructs narrowest-date-range usage, and forbids inventing profit/margin numbers (`app/agents/revenue.py`)
- [ ] Wire `get_revenue_data_slim` into the agent's `get_revenue_data` tool surface — verify the tool actually calls the slim variant, not the full pull
- [ ] Token-budget guardrail — cap rows returned (or summarize) when a wide date range would blow context; current default is 12-month window but no row cap

### Revenue Reporting & Project Tracking — `[ ]` (not started; UI mocked)
Nothing here is built in code or schema: no `projects` table beyond the Harvest cache (`harvest_projects`), no revenue-per-type view, no Postgres-backed rev-rec data, and no rev-rec endpoints in `ui/src/api.ts`. Recognised revenue still lives only in Airtable. Added to scope in `PRD.md`.

A **non-functional UI mockup** now exists at `/revenue` (`ui/src/pages/Revenue/`, plan `.claude/tasks/24.revenue-tab-mockup.md`) — Overview / Runs / Entries, built to the Invoices tab's conventions so the shape can be reviewed before the backend is designed. Every figure comes from `pages/Revenue/mockData.ts`, whose types deliberately mirror the real slim schema (`app/services/revenue.py::_SLIM_FIELDS`) so live wiring replaces that one file rather than redesigning the screens. An amber "sample data — not live" banner renders once in `RevenueLayout`. Recharts was added to `ui/package.json` for the TTM bar chart; it is the app's only chart library.

Overview carries two project × month grids (shared renderer, `pages/Revenue/components/MonthGrid.tsx`): revenue, and **revenue per billable hour**. The per-hour cell is that month's `revenue_delta` ÷ that month's `logged_hours`; row and column totals are blended (total revenue ÷ total hours), never a mean of the cells, which would weight a light month like a heavy one. **This is not the same as `blended_rate` in `app/services/revenue.py`**, which divides *cumulative* `total_recognized_revenue` by a *single period's* `logged_hours` — a figure that climbs every month regardless of performance and cannot be trended. Anything wiring this grid to real data must recompute the ratio, not reuse that field.
- [ ] Revenue dashboard — business-facing revenue metrics, distinct from the agent-status dashboard in Module 2. Mocked, not built: needs a rev-rec data source in Postgres and an API before the mockup can be wired
- [ ] Project-completion tracking — no schema concept of "complete" beyond Harvest's own project-archived flag. A stub tab exists at `/projects` (`ui/src/pages/Projects/`): a sample-data table of project / start / committed end / projected end, with the same amber "not live" banner as Revenue. Active work is the default list; a "See archived" button appends archived rows. Its active roster imports `MOCK_PROJECTS` from `pages/Revenue/mockData.ts` so the two mockups cannot disagree about which engagements exist; dates, archive state, and three archived-only projects that closed before the trailing twelve months live in `pages/Projects/mockData.ts`. `harvest_projects` has no field for either end date and no archive flag this system owns, so this needs a project record the system owns
- [ ] Revenue-per-project-type reporting — `billing_type` exists as a config enum (T&M / fixed_fee_schedule / recurring_monthly / manual) but nothing reports revenue rolled up by it

### Contracts — `[ ]` (not started, not scoped)
New in the sidebar on 2026-08-14 as a placeholder tab only (`ui/src/pages/Contracts.tsx`). Nothing models a contract anywhere in the repo. The terms that behave like contract terms are split between `contracted_fees` in the Airtable rev rec ledger and the payment terms / billing type / draw schedules in billing group config. Whether Contracts becomes its own record or a view over what exists is undecided.
- [ ] Decide the shape — own record vs. view over billing groups + Airtable terms
- [ ] Everything else — no schema, no API, no design

> **Placeholder tabs are not features.** `/projects` and `/contracts` are nav destinations that
> exist ahead of their backends. Both render `components/shared/PlaceholderPage.tsx`, which
> carries a `NOT IMPLEMENTED` badge and states what has to exist first. Deliberately not
> `EmptyState` — "no items yet" would imply the screen works and simply has no rows.

### Module 8: Invoicing (Harvest) — `[-]`

Spec: `docs/prd/harvest-invoicing-requirements.md` (+ §12 amendments).
Plan: `.agent/plans/21.harvest-invoicing-preflight.md`.
**Drafts only.** One code path writes to Harvest — `POST /v2/invoices` for a single
released draw. The system cannot **send**, delete, or modify an invoice; those endpoints
are banned in CI by `tests/test_harvest_write_guardrail.py`. Everything else, including
every monthly run, is read-only.

Phase 0 — Foundation — `[x]`
- [x] Harvest client hardening — typed exceptions per status class, contact email in User-Agent, new read methods (`list_projects_detailed`, `list_time_entries`, `list_expenses`, `list_invoices`, `get_invoice_item_categories`, `get_task_assignments`). Rev-rec's `get_time_entries` / `get_invoice_totals_by_project` contracts untouched
- [x] Dual-bucket rate limiter (`app/integrations/harvest_limiter.py`) — general 100/15s, reports 100/15min, honors `Retry-After`; unit-tested against a fake clock
- [x] Pagination + 2000-record `per_page` ceiling
- [x] Migration `20250101000024_billing_invoicing.sql` — 11 tables, RLS, and the two partial unique indexes that make double-billing structurally impossible
- [x] Config: `HARVEST_USER_AGENT_CONTACT` and credentials only. Every billing tuning value is a constant in the module that reads it — `USE_ROUNDED_HOURS` (rates), `STRAGGLER_LOOKBACK_DAYS` (estimator), `UNMAPPED_LOOKBACK_DAYS` (reconcile), `VARIANCE_PCT_THRESHOLD` (planner). `Settings` holds deployment identity; per-environment or secret only
- [x] Write guardrail test — scans `app/` for invoice send/delete/patch/payments and `retainer_id`; fails the build if any appears

Phase 1 — Config layer — `[x]`
- [x] Harvest snapshot (clients, projects, invoice item categories, task assignments) — idempotent upsert
- [x] Billing-group CRUD with project↔client validation at write time (the 422 caught before it can reach a run)
- [x] Config reconciliation — unmapped projects with priced uninvoiced time (`UNMAPPED_PROJECT`, error) and without (`UNMAPPED_PROJECT_NO_TIME`, warning), type/client/currency mismatches, archived-project and exhausted-schedule checks. `manual` groups suppress both
- [x] `app/routers/billing.py` registered with auth

Phase 2 — T&M pre-flight — `[x]`
- [x] Date resolver — arrears/advance periods, issue dates; tested across month lengths, year boundaries, leap Feb
- [x] Due-date resolver — enum terms pass through to Harvest, `custom` computed locally
- [x] T&M estimator — rate ladder (entry → project → task assignment), rounding config, summary types, expenses, straggler/late time
- [x] Duplicate guard — ledger-aware, so multi-group clients don't false-positive
- [x] Payload builder — exact `line_items_import` body, always-bounded `from`/`to`
- [x] Flag engine — the T&M-relevant §7 catalog plus `UNRESOLVED_IN_FLIGHT` and `ALREADY_INVOICED_THIS_RUN`
- [x] Planner + `plan_snapshot`, re-plan abandons the prior live plan (never an in-flight row)
- [x] UI wired to live API — runs list, pre-flight, groups, group detail, health strip
- [x] Persisted per-group approval (migration `0026`, `app/services/billing/review.py`) — nothing is approved by default, the decision survives a reload, error overrides are recorded and sticky, and `UNRESOLVED_IN_FLIGHT` is refused at the service layer

Phase 4 (partial) — Recurring monthly — `[x]`
- [x] Migration `0025` — `kind` (Harvest invoice item category) on both line-item tables, `is_placeholder` on recurring
- [x] Recurring resolver — effective-dated line items, `{period_label}` / `{client_name}` rendering
- [x] Free-form payload builder — literal `line_items`, each with its own `project_id`, so one invoice spans several projects
- [x] Placeholder lines — hosting pass-through / percentage-based fees go out at $0 for manual completion on the draft; excluded from the estimate, surfaced as `PLACEHOLDER_LINE_ITEMS`
- [x] `kind` validated against the account's categories at save time **and** plan time (`INVALID_ITEM_CATEGORY`)
- [x] Line-item editor in the group form, with fee-type dropdown sourced from Harvest
- [x] Fixed-fee draws — release-gated and billed one at a time from the Draws tab, never on the monthly run (plan `.agent/plans/22.fixed-fee-draws.md`). Migrations `0027` (scheduled to a day, not a month) and `0028` (release state, draw runs, the C6 index split). `app/services/billing/draws.py`; `GET /billing/draws`, `POST /billing/draws/{id}/release`, `POST /billing/draws/{id}/invoice`
- [x] Draw double-billing guard — one live ledger row per *draw*, a partial unique index alongside the per-month one. Two milestones in one calendar month both bill; the same milestone never bills twice
- [x] Schedule editing preserves history — `save_draws` upserts by id, refuses to change or remove an invoiced draw. A slipped date is a routine edit and must not reset delivery confirmations
- [x] Draw invoices are computed, not staged — `GET /billing/draws/{id}/preview` returns the exact POST body and writes nothing. A ready draw expands in the queue for review and is created from there; there is no intermediate persisted invoice to discard
- [x] `in_flight` — the fourth derived state, read from the live ledger row. Keeps a draw mid-write out of the billable queue and locks its billable fields
- [x] `DRAW_OVERDUE` / `DRAWS_AWAITING_RELEASE` / `DRAWS_READY_TO_BILL` on the monthly run, so a delivered milestone can't sit unbilled unnoticed

Phase 3 — Execution — `[~]` **the single-draw write path ships; the monthly run's does not.**
Plan: `.agent/plans/23.draw-invoice-write-path.md`. Operator-initiated with no approval
row ([ADR-0004](docs/adr/0004-operator-initiated-writes.md)) — the system is
automation-first now, and the click on a screen showing the exact payload *is* the
authorization.
- [x] `harvest.create_invoice` + a `_post` sibling to `_request`. Retries **only** on 429 (the one status proving nothing was created); 4xx, 5xx, and timeouts propagate untouched. The write guardrail needed no loosening — it bans send/delete/patch/payments, never `POST /v2/invoices`
- [x] §8 protocol in `draws.invoice_draw` — the `in_flight` ledger row is written **and committed** before the POST, in its own transaction. Sharing one transaction with the request would roll back the lock on a crash and leave an invoice in Harvest with no record on our side
- [x] Four outcomes kept distinct: `created` · `failed` (a 4xx verdict, draw returns to `ready`) · **unknown** (timeout/5xx — row stays `in_flight`, nothing inferred, `DrawWriteUnknown` raised) · refused before any POST
- [x] PRD 4.4 — `invoiced_run_id` stamped on the consumed draw in the same transaction as the ledger update
- [x] `POST /billing/draws/{id}/invoice`, human-only. Status codes carry the §8 distinction: 200 created · 409 nothing attempted · 422 Harvest refused · 502 outcome unknown
- [x] In-flight resolution — `app/services/billing/inflight.py`, `GET /billing/in-flight`, `POST /billing/runs/{run_id}/items/{item_id}/resolve`. Item-level, so the monthly run reuses it verbatim. Linking without an amount leaves `variance` null rather than recording an unverifiable zero
- [x] Real resolve controls on the Draws tab and in `InFlightModal` (was a stub explaining a manual DB edit)
- [x] `ApiError` in `ui/src/api.ts` preserves status + raw `detail`, so the 502's recovery instructions render instead of `[object Object]`
- [x] **Dated when drafted, not when previewed.** `issue_date` defaults to today on every `preview_draw_invoice` call, so a preview from the 10th created on the 12th is issued the 12th and due the 22nd on net-10 terms. Issue and due always move together — Harvest derives the due date from the issue date for enum terms, so they cannot be decoupled without producing an invoice the client can see is wrong. The UI never caches the preview and the create response returns the dates actually used
- [x] **Invoice notes are sent explicitly** (`payload.resolve_notes`, used by draws *and* the planner). Harvest's account-level default notes reach only invoices created in its own UI — the API neither applies them nor exposes them for reading, so the first live invoice arrived with blank notes and no remit-to instructions. The text duplicates what Harvest stores because nothing can read the original; keep them in step by hand
- [x] **Settings → Billing** (migration `0029` `billing_settings`, `app/services/billing/settings_store.py`, `GET`/`PATCH /billing/settings`) — `default_invoice_notes` is editable in the UI with no restart, audited with the new value, and validated against a known-key allowlist. Chosen over an env var because this is copy a human edits and reads back; Harvest credentials and `HARVEST_BASE_URI` stay in env, where a wrong value is a broken deploy rather than a business decision
- [x] **A billed draw no longer vanishes.** Three separate causes, all fixed: the in-card success banner unmounted with the card the moment the draw became `invoiced`; nothing listed billed draws; and draw runs were filtered out of the runs list by default. Now a dismissible page-level confirmation on Draws, plus `/invoices/runs?kind=draw` pre-selecting the draw filter
- [x] **Drafted tab** (`/invoices/drafted`, `GET /billing/invoices` + `/totals`, `app/services/billing/invoices.py`) — every invoice the system created, both kinds in one list, because the ledger records both and "what have we drafted" is not a question about runs. Named "Drafted" rather than "Billed": the system pushes a draft to Harvest and stops, and billing happens when a human sends it from there. Kind-agnostic by construction: monthly rows appear once that execution ships, with no code change. Filters by kind, can show failed attempts separately, and never counts `failed` or `in_flight` as drafted. Ordered by creation with `harvest_invoice_id` as tiebreak — issue dates are backdated for monthly runs, and one transaction stamps a single `now()`
- [x] `HARVEST_BASE_URI` for linking out to a created invoice — no API exposes the account web address. Unset, the UI omits the link rather than guessing a subdomain
- [ ] **Decide the monthly run's dating rule before its execution ships.** `resolve_period` dates those to the period boundary (PRD §2.3), so a July-arrears run executed 3 Sep would be issued 31 Jul and already overdue on net 30. The draw behaviour above does **not** carry over automatically
- [ ] Monthly-run execution (`create_harvest_draft_invoices` across many groups, sequential, partial-failure handling)
- [ ] Post-run variance reconciliation
- [ ] Candidate-invoice picker for in-flight resolution (today: paste the id from Harvest)

Phase 4 — remainder — `[x]`. Every `billing_type` is handled: T&M and `recurring_monthly` plan, `fixed_fee_schedule` bills off-cycle from the Draws tab, `manual` is skipped with no ledger row.

**Gate before monthly-run execution:** run the pre-flight against production Harvest
and reconcile a full month by hand. Note the ordering trap — the estimator only counts
time Harvest has not marked `is_billed`, so an already-invoiced month re-plans to
empty. Plan first, invoice by hand second, compare third.

The gate does **not** cover the draw path and never did: a draw's amount is a number a
human typed into the schedule and released, so there is no estimate to reconcile.

**Not built and deliberately so:** rev rec has no runner. `TRIGGER_REVENUE_RECOGNITION`
came out of `revenue-ops` under ADR-0004 and no operator-initiated endpoint has replaced
it. The Revenue tab's "Run Revenue Recognition" button exists but is a mockup control —
permanently disabled and badged `NOT IMPLEMENTED`, wired to nothing. The
`write_rev_rec_entries` executor is untouched and waiting.

---

## Tooling

### Workflow Visualizer — `[ ]` (Backlogged, stale)
Originally scoped around LangGraph's `get_graph().draw_mermaid()`. LangGraph was removed per [ADR-0002](docs/adr/0002-tools-not-graphs.md) — there is no graph object left to draw. A read-only trace view over tool-based workflows (event timeline, not a graph diagram) would need to be re-scoped from scratch if this is still wanted.

---

## Architecture status

One orchestrator (`app/orchestrator/`) — `run_agent_task` drives a ReAct loop for agents with tools; prescribed workflows are tools returning `Done | AwaitingApproval | Blocked`, with loops/retries as inline Python, not a graph engine (LangGraph was removed; see [ADR-0002](docs/adr/0002-tools-not-graphs.md) and [ADR-0003](docs/adr/0003-single-agent-class-structural-delegation.md)). One approval surface (`/approvals`). One inbox type (`Approval`). One conversational agent (`chief-of-staff`) sitting in front of three worker agents (`bdr`, `revenue-ops`, `linkedin`). The chat-turn module (`app/services/chat_turn.py`) owns the LLM tool-call loop, turn lifecycle, and persistence; `app/services/chat_sessions.py` is pure CRUD. Single-turn LLM calls for sub-steps (consolidate, draft, voice critique, accuracy critique, voice review, idea interpretation, post drafting) live inline in their tool modules as `MODEL` + `SYSTEM_PROMPT` constants — not as agent classes. Every LLM call (single-turn or streaming) flows through the dispatcher at `app/integrations/llm.py`, which absorbs provider details, the `llm_calls` row write, and attribution (`Attribution(agent_slug, purpose, ...)` — required argument, not a contextvar). Chat turns emit `CHAT_TURN_STARTED` / `CHAT_TURN_COMPLETED` / `CHAT_TURN_FAILED` audit events. Test suite covers runner, approval flow, agent invocation, sub-workflow spawn, agent messaging, chat turn lifecycle, the LLM dispatcher in isolation, and the production tool-based workflows end-to-end.

Known gaps (tracked in Backlog):
- Multi-turn thread context in `ask_agent`
- Anthropic provider adapter (lands behind the existing dispatcher seam; no caller changes)
- Workflow visualizer — backlogged, and stale: it assumed a LangGraph `get_graph().draw_mermaid()` call that no longer exists post-ADR-0002. Needs re-scoping against tool-based workflows before it's buildable, if still wanted.

---

## Backlog

- [ ] **Workflow visualizer** — stale as scoped (assumed LangGraph's `get_graph().draw_mermaid()`, removed per ADR-0002). If still wanted, re-scope as a trace view over tool-based workflows instead of a graph diagram.
- [ ] **Diagrams-as-code** — stale as scoped (`make diagrams` assumed registered LangGraph graphs, which no longer exist). Drop or re-scope against the current tool/executor structure.
