# Progress

Track progress through implementation. Update this file as you complete modules - Claude Code reads this to understand where you are in the project.

## Convention
- `[ ]` = Not started
- `[-]` = In progress / partial
- `[x] (YYYY-MM-DD)` = Completed. Items completed before 2026-08-26 predate this dating convention and are undated.

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

### Module 2: Dashboard — `[ ]`
Reduced to a `PlaceholderPage` on 2026-08-25. The agent cards restated the Agents
tab and the activity feed restated the Audit Log tab, so the screen was a
duplicate of two others rather than a landing view. What belongs here — billing
run state, revenue recognition status, exceptions needing a human — is not scoped.
- [ ] Summary tiles — undesigned
- [ ] Exceptions needing attention — undesigned
- [ ] Global status banner — removed; nothing records an agent error state to banner on

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

### Revenue Reporting & Project Tracking — `[~]` (Projects tab live; revenue still mocked)
The **revenue** half is unbuilt in code and schema: no revenue-per-type view, no Postgres-backed rev-rec data, and no rev-rec endpoints in `ui/src/api.ts`. Recognised revenue still lives only in Airtable. Added to scope in `PRD.md`.

The **project-roster** half is live as of 2026-08-14 — `GET /projects` over the Harvest snapshot cache, see the Projects bullet below. There is still no `projects` table this system owns; the tab reads `harvest_projects`, so it can show what Harvest knows (name, client, start, end) and nothing Harvest doesn't (committed end, completion, forecast). Fuller architecture for this cache and its Forecast sync is in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#projects--forecast-snapshot).

A **non-functional UI mockup** now exists at `/revenue` (`ui/src/pages/Revenue/`, plan `.claude/tasks/24.revenue-tab-mockup.md`) — Overview / Runs / Entries, built to the Invoices tab's conventions so the shape can be reviewed before the backend is designed. Every figure comes from `pages/Revenue/mockData.ts`, whose types deliberately mirror the real slim schema (`app/services/revenue.py::_SLIM_FIELDS`) so live wiring replaces that one file rather than redesigning the screens. An amber "sample data — not live" banner renders once in `RevenueLayout`. Recharts was added to `ui/package.json` for the TTM bar chart; it is the app's only chart library.

Overview carries two project × month grids (shared renderer, `pages/Revenue/components/MonthGrid.tsx`): revenue, and **revenue per billable hour**. The per-hour cell is that month's `revenue_delta` ÷ that month's `logged_hours`; row and column totals are blended (total revenue ÷ total hours), never a mean of the cells, which would weight a light month like a heavy one. **This is not the same as `blended_rate` in `app/services/revenue.py`**, which divides *cumulative* `total_recognized_revenue` by a *single period's* `logged_hours` — a figure that climbs every month regardless of performance and cannot be trended. Anything wiring this grid to real data must recompute the ratio, not reuse that field.
- [ ] Revenue dashboard — business-facing revenue metrics, distinct from the agent-status dashboard in Module 2. Mocked, not built: needs a rev-rec data source in Postgres and an API before the mockup can be wired
- [x] **Projects tab, Harvest-backed** — `/projects` is live, not a mockup (`ui/src/pages/Projects/ProjectList.tsx` → `GET /projects` → `app/routers/projects.py` → `app/services/projects.py`). Four columns: project / client / start / end date, over billable projects in the `harvest_projects` snapshot cache. Active is the default; "See archived" **swaps** the list rather than extending it — the two sets are disjoint, and the archived count is deliberately not shown (it would cost a second query to tell you something you cannot act on). Migration `0030` added `starts_on` / `ends_on`; Harvest always returned them on `/v2/projects` and the snapshot upsert had been discarding both. Fixtures deleted: `pages/Projects/mockData.ts` is gone, as is the `MOCK_PROJECTS` export it borrowed from the Revenue mock
- [x] **Projected end, from Forecast** (2026-08-14) — `forecast_project_schedule` (migration `0032`), the last day a **person** is booked on each project. Placeholder bookings are excluded: they are capacity held open, not someone scheduled, and counting them moved three projects later on a booking with no name against it. Forecast projects carry a `harvest_id`, so the join resolves at sync time. `app/services/forecast_snapshot.py`; read-only against Forecast, audited as `forecast.schedule.refreshed`. Live coverage: 27 of 29 active projects — the two blanks are Managed Hosting, where nobody is scheduled by design, so null is the honest answer rather than a gap. The tab ambers a projected end that runs past the Harvest end date: **8 of 29** are booked past their planned end, one by 153 days. The column is shown for active work only — archived projects have already ended, so the archived view drops it (four columns rather than five with one dead). The endpoint still serves the field for both, so "no forecast" and "not provided" stay distinguishable for any other consumer. Forecast costs exactly **2 HTTP calls** per sync (`/projects` for the `harvest_id` map, `/assignments` as one bulk window) — already the floor, so narrowing what is displayed buys nothing there
- [ ] Project-completion tracking — still nothing. Harvest's `is_active` is the only notion of "closed", and it is Harvest's flag, not one this system owns. **Committed end** is still deliberately absent: Harvest's `ends_on` is editable and moves when a project slips, so the tab calls it "End date" rather than pretending it is a commitment. A real committed date needs a project record this system owns
- [x] **One Refresh button on the Projects tab** — `POST /projects/refresh` (`projects.refresh_sources`) pulls Harvest **then** Forecast in one action, ~7s against the live account. Both sources feed the same row, so refreshing one alone left half of it stale while looking like the page had updated. Reported per source, not as a single "ok": Harvest commits first, so a Forecast outage or a missing `FORECAST_ACCOUNT_ID` returns **200** with `forecast: null` and `forecast_error` set, and the tab shows an amber "Harvest updated, but the forecast did not" line. A 5xx there would claim nothing happened when half of it did
- [ ] Nothing schedules either sync — both caches are only as fresh as the last press of that button. The `_LOOKBACK_YEARS` / `_LOOKAHEAD_YEARS` window in `app/integrations/forecast.py` is five years wide because `/assignments` 422s on a long window (six years is accepted, eight is not) and reads with overlap semantics
- [x] **Client exclusions** — `excluded_harvest_clients` (migration `0031`), managed at Settings → Excluded Clients (`GET`/`POST`/`DELETE /client-exclusions`). Our own company is a Harvest client and some of its internal work is flagged *billable*, so `is_billable` could not catch it. Keyed on the **client**, so one row covers every present and future project under it. Applied through one shared SQL predicate, `client_exclusions.not_excluded_sql()`, so a new reader opts in with one line: `app/services/projects.py`, `reconcile._unmapped_candidates`, and both `billing/catalog.py` browsers. The catalog pair take `include_excluded` (default `False`): a **new** billing group cannot select an excluded client, while the **edit** form passes `include_excluded=True` so a group whose client was excluded after it was built stays editable — that select is editable, and a missing option would blank the field and let a save wipe it. Fuller rationale in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#client-exclusions-excluded_harvest_clients)
- [x] `EXCLUDED_CLIENT_HAS_ACTIVE_GROUP` (error) in `reconcile` — the one dangerous combination. Exclusion hides a client from reporting but does **not** stop it billing, so an excluded client with an active group means invoices still go out for an account we have been told is not a client. Silent otherwise: exclusions are set on a Settings screen that knows nothing about billing groups
- [x] Retired the `Frogslayer - Exclusion` billing group (2026-08-14) — a `manual` group that existed solely to suppress `UNMAPPED_PROJECT` on two internal projects, superseded by the client exclusion. Deleted by hand in the local dev DB, not through the app: there is no billing-group delete path, by design. Safe because it had never billed — the `billing_run_items.billing_group_id` FK is `ON DELETE NO ACTION` precisely so a group with ledger history cannot be deleted, and it had zero rows. Recorded in `audit_log` as `billing.group.deleted` with the projects it held. **Production still has this group**; delete it there the same way after the migrations land
- [ ] Snapshot freshness on the Projects tab — the page reads a cache refreshed only by a billing-run plan or `POST /billing/snapshot/refresh`; there is no cron anywhere in the repo. A footnote renders `synced_at` so staleness is visible, but a project created in Harvest today will not appear until someone triggers a sync. A refresh button on the tab is the obvious follow-up. The snapshot also never deletes, so a project removed from Harvest lingers
- [ ] Revenue-per-project-type reporting — `billing_type` exists as a config enum (T&M / fixed_fee_schedule / recurring_monthly / manual) but nothing reports revenue rolled up by it

### Contracts — T&M drafting `[x]` (2026-09-08)
Generates a draft T&M agreement as a Word document by filling the blanks in a checked-in .docx template, and remembers the client identity so the next one for the same client needs no retyping. Deterministic — no LLM and no agent in the path; operator-initiated per [ADR-0004](docs/adr/0004-operator-initiated-writes.md). The placeholder tab is gone.

- [x] `contract_clients` (migration `0036`) — counterparty legal identity only: entity name, one address line, city/state/ZIP, signatory. No Harvest link, on purpose: the primary case is a prospect
- [x] Render pipeline (`app/services/contracts/`) — `fields.py` declares the blanks, `render.py` fills the template, `drafts.py` resolves and audits. `docxtpl` renders the real .docx in place so styles, numbering, tables, headers and fonts survive by construction
- [x] Unfilled fields render as a yellow-highlighted `[REVIEW: <label>]` marker rather than a silent blank, and the form lists them before you generate. A blank is a decision — drafting with terms still to negotiate is the normal case, so generation never refuses
- [x] `/contracts` UI — New T&M draft (form driven by `GET /contracts/tm/fields`, review panel, download) and Saved clients (CRUD). First file download in the app; needed `expose_headers` on the CORS middleware
- [x] Four audit events; `contract.draft.generated` carries the full field set and review list, because the .docx is stored nowhere and the row is the only record

Three things worth knowing, all found by measurement and all easy to undo by accident — see the docstring in `render.py`:
- **Plain `{{ tag }}`, never `{{r tag }}`.** docxtpl's `RichText` replaces the tag's whole run and discards its formatting; a tag in a bold DM Sans clause came back unbolded in the body font. The loader accepts `{{r }}` (docxtpl's own docs steer authors to it) but rewrites it to a plain tag — and must do so *before* `super().patch_xml()`, or the value lands as raw text between two empty runs and Word silently ignores it
- **`autoescape=True` is mandatory.** Without it a value containing `&` or `<` corrupts the file — `"Smith & Co <Holdings>"` dropped the ampersand and swallowed the following table. An ordinary entity name
- **Highlighting is a post-render pass**, splitting the run so the marker keeps the surrounding font

Not built, deliberately: nothing models a *signed* contract or its lifecycle. `contracted_fees` still lives in the Airtable rev rec ledger and payment terms / billing type / draw schedules still live in billing group config, and this slice does not try to unify them. It is a document generator. Contract *intake* (the inverse — ingesting a signed contract) is still the Backlog item below.

- [ ] Contract types beyond T&M — the point at which LLM-drafted prose (scope, assumptions, exclusions) enters. `fields.py` is the seam: an LLM-sourced field is one whose value came from elsewhere and is always flagged for review
- [ ] Rate table and role definitions are static text in the template and are not modelled. Fine while they are identical across engagements

> **Placeholder tabs are not features.** A nav destination that exists ahead of its backend
> renders `components/shared/PlaceholderPage.tsx`, which carries a `NOT IMPLEMENTED` badge and
> states what has to exist first. Deliberately not `EmptyState` — "no items yet" would imply the
> screen works and simply has no rows. Both original placeholders are now gone: `/projects`
> reads live Harvest data, and `/contracts` generates real documents as of 2026-09-08 (the
> badge and `pages/Contracts.tsx` came off with it). `/revenue` is the remaining mockup and
> keeps its banner.

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
- [x] Placeholder lines — hosting pass-through, percentage-based fees, retainer overages: description, category, and project fixed in config, amount decided per month. Surfaced as `PLACEHOLDER_LINE_ITEMS`, and excluded from `planned_amount` until decided
- [x] **Placeholder resolution** (plan `.agent/plans/26.placeholder-resolution.md`, migration `0033` `recurring_line_item_resolutions`, `app/services/billing/placeholders.py`, `POST`/`DELETE /billing/runs/{run_id}/items/{item_id}/placeholders/{line_item_id}`). The amount is entered on the pre-flight, before the draft exists — it used to be typed into the Harvest draft, which put the last step of an invoice in a system this one cannot read, so nothing noticed when it was skipped. The failure was quiet and always the same direction: the invoice went out short while `planned_amount` read as correct, precisely because placeholders were excluded from it. Omit-for-one-month and re-plan/id-stability details are in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#billing--invoicing-appservicesbilling)
- [x] `estimated_line_items` carries `recurring_line_item_id` / `harvest_project_id` / `kind` / `is_placeholder` / `placeholder_state` for recurring groups, making the ledger row complete enough to rebuild `planned_payload` from — so a resolution applies to the plan the operator reviewed rather than to config as it stands now. Resolving withdraws any approval on the row, since an approval describes a payload (ADR-0004 condition 1)
- [x] `kind` validated against the account's categories at save time **and** plan time (`INVALID_ITEM_CATEGORY`)
- [x] Line-item editor in the group form, with fee-type dropdown sourced from Harvest
- [x] Fixed-fee draws — release-gated and billed one at a time from the Draws tab, never on the monthly run (plan `.agent/plans/22.fixed-fee-draws.md`). Migrations `0027` (scheduled to a day, not a month) and `0028` (release state, draw runs, the C6 index split). `app/services/billing/draws.py`; `GET /billing/draws`, `POST /billing/draws/{id}/release`, `POST /billing/draws/{id}/invoice`
- [x] Draw double-billing guard — one live ledger row per *draw*, a partial unique index alongside the per-month one. Two milestones in one calendar month both bill; the same milestone never bills twice
- [x] Schedule editing preserves history — `save_draws` upserts by id, refuses to change or remove an invoiced draw. A slipped date is a routine edit and must not reset delivery confirmations
- [x] Draw invoices are computed, not staged — `GET /billing/draws/{id}/preview` returns the exact POST body and writes nothing. A ready draw expands in the queue for review and is created from there; there is no intermediate persisted invoice to discard
- [x] `in_flight` — the fourth derived state, read from the live ledger row. Keeps a draw mid-write out of the billable queue and locks its billable fields
- [x] `DRAW_OVERDUE` / `DRAWS_AWAITING_RELEASE` / `DRAWS_READY_TO_BILL` on the monthly run, so a delivered milestone can't sit unbilled unnoticed

Phase 3 — Execution — `[x]` **both write paths ship** — one draw at a time, and the monthly run (2026-09-09).
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
- [x] **The monthly run's dating rule, decided 2026-09-09: issued on the period boundary, due from the draft day.** PRD §2.3's issue date is the right accounting answer and stays; what could not stay is a July-arrears run drafted 3 September arriving already overdue on net 30. `dates.resolve_draft_dating` moves only the due date. The cost is that every monthly payload now goes out as `payment_term: "custom"` — Harvest derives the due date from the issue date for every enum term, so there is no other way to split them. Net days come from the frozen row (`due_date − issue_date`), never from live group config
- [x] **Monthly-run execution** — `app/services/billing/execute.py`, `POST /billing/runs/{run_id}/execute`, and the live button on the pre-flight (was a disabled stub). Sequential, same §8 protocol per item as a draw, with the one rule a batch adds: a 4xx fails that group and the loop continues, but an **unknown outcome halts the run** — remaining groups stay approved and un-attempted, the run stays `executing`, and clicking again after a human resolves the row resumes. Answers 200 even when halted, because by then it has usually created real invoices and a 502 would discard the record of which; the unknown rides in the body as `halted` + `unknown_item`
- [x] **`write_protocol.py`** — the failure/unknown recorders and `settle_run_status`, shared by draws and the monthly run. `inflight.resolve_item` used to mark the run completed unconditionally, which is right for a one-item draw run and would have stranded a monthly run's remaining groups
- [x] **Post-write verification** (`app/services/billing/verify.py`, migration `38`) — after each T&M invoice, count the billable time Harvest still reports unbilled for those projects over that period, and record it. Zero is expected: `line_items_import` is what marks the time billed, and nothing had ever checked that it did. Non-zero is shown as an observation and blocks nothing — unapproved time and rate-less entries are ordinary causes
- [x] **Reject one invoice from a run** (migration `39`, `review.set_item_rejection`, `POST /billing/runs/{run_id}/items/{item_id}/rejection`). Un-approving only means undecided; the case that needed more is a recurring group already invoiced by hand, where the run wants to create a duplicate. Reuses `skipped` with `rejected_by` marking an operator's decision, and requires a reason — that row is the only record of why a planned client got nothing
- [x] **`EXISTING_HARVEST_INVOICE` is scoped to the group's projects**, not the client. A firm with several engagements was warned every month because a different project's invoice went out. Invoices with no project on any line become `UNATTRIBUTED_HARVEST_INVOICE` at `info` rather than disappearing
- [x] **`LATE_TIME` deleted.** Billing August in September always finds September time; the flag fired on every arrears group every month and had no action behind it. `STRAGGLER_TIME` — uninvoiced time *before* the period, which the bounded import will miss again — is the half worth saying. Also removes a Harvest round-trip per project per plan
- [ ] Post-run variance reconciliation (per-row variance is stored; there is still no run-level report or threshold flag)
- [ ] Candidate-invoice picker for in-flight resolution (today: paste the id from Harvest)
- [x] **Drafting a subset no longer finishes the run** (2026-09-09). Approving one group of nine and clicking used to mark the whole run `completed`, which locked the other eight out of the run they were planned in — `execute` and `review` both refuse a completed run. A click is now a batch over whatever is approved at that moment: `settle_run_status` sends a run with leftover `planned` rows back to `awaiting_approval`, and the response carries `remaining` so the banner does not read as "the month is billed". The run ends by being closed (`review.close_run`, `POST /billing/runs/{id}/close` — the remainder is rejected with a reason and an actor) or by the month being re-planned, which sweeps the same remainder and settles the old run rather than relabelling it `abandoned`. Abandoning is refused once a run has drafted anything
- [ ] Per-group subset *selection* at execute time is still **decided against** — a checkbox at the button is not how a group is taken out of a run; rejection is, and it leaves a reason behind. The fix above is about *when* approved groups are drafted, not *which*

Phase 4 — remainder — `[x]`. Every `billing_type` is handled: T&M and `recurring_monthly` plan, `fixed_fee_schedule` bills off-cycle from the Draws tab, `manual` is skipped with no ledger row.

**Gate before the monthly run's first live use:** run the pre-flight against production
Harvest and reconcile a full month by hand. The code does not enforce this — it is an
operating instruction, and the button now works. Note the ordering trap: the estimator
only counts time Harvest has not marked `is_billed`, so an already-invoiced month
re-plans to empty. Plan first, invoice by hand second, compare third. And on the first
real run, approve one group rather than all of them.

The gate does **not** cover the draw path and never did: a draw's amount is a number a
human typed into the schedule and released, so there is no estimate to reconcile.

**Not built and deliberately so:** rev rec has no runner. `TRIGGER_REVENUE_RECOGNITION`
came out of `revenue-ops` under ADR-0004 and no operator-initiated endpoint has replaced
it. The Revenue tab's "Run Revenue Recognition" button exists but is a mockup control —
permanently disabled and badged `NOT IMPLEMENTED`, wired to nothing. The
`write_rev_rec_entries` executor is untouched and waiting.

---

## Architecture status

One orchestrator (`app/orchestrator/`) — `run_agent_task` drives a ReAct loop for agents with tools; prescribed workflows are tools returning `Done | AwaitingApproval | Blocked`, with loops/retries as inline Python, not a graph engine (LangGraph was removed; see [ADR-0002](docs/adr/0002-tools-not-graphs.md) and [ADR-0003](docs/adr/0003-single-agent-class-structural-delegation.md)). One approval surface (`/approvals`). One inbox type (`Approval`). One conversational agent (`chief-of-staff`) sitting in front of two worker agents (`bdr`, `revenue-ops`). The chat-turn module (`app/services/chat_turn.py`) owns the LLM tool-call loop, turn lifecycle, and persistence; `app/services/chat_sessions.py` is pure CRUD. Single-turn LLM calls for sub-steps (consolidate, draft, voice critique, accuracy critique) live inline in their tool modules as `MODEL` + `SYSTEM_PROMPT` constants — not as agent classes. Every LLM call (single-turn or streaming) flows through the dispatcher at `app/integrations/llm.py`, which absorbs provider details, the `llm_calls` row write, and attribution (`Attribution(agent_slug, purpose, ...)` — required argument, not a contextvar). Chat turns emit `CHAT_TURN_STARTED` / `CHAT_TURN_COMPLETED` / `CHAT_TURN_FAILED` audit events. Test suite covers runner, approval flow, agent invocation, sub-workflow spawn, agent messaging, chat turn lifecycle, the LLM dispatcher in isolation, and the production tool-based workflows end-to-end.

Removed and not coming back the same way: the LangGraph-based Outreach workflow (deleted with the LangGraph rip-out, finished off 2026-08-10 when HubSpot/Apollo were removed — the BDR agent is now toolless by design, drafting from supplied context) and the Social Content workflow (removed 2026-08-25, [ADR-0006](docs/adr/0006-remove-social-content.md); migration `0034` drops its table). Neither survives in code.

Known gaps (tracked in Backlog):
- Multi-turn thread context in `ask_agent`
- Anthropic provider adapter (lands behind the existing dispatcher seam; no caller changes)
- Workflow visualizer, diagrams-as-code — both stale as scoped (assumed LangGraph APIs removed per ADR-0002); see Backlog

---

## Backlog

- [ ] **Workflow visualizer** — stale as scoped (assumed LangGraph's `get_graph().draw_mermaid()`, removed per ADR-0002). If still wanted, re-scope as a trace view over tool-based workflows instead of a graph diagram.
- [ ] **Diagrams-as-code** — stale as scoped (`make diagrams` assumed registered LangGraph graphs, which no longer exist). Drop or re-scope against the current tool/executor structure.
- [ ] **Placeholder preset mode — quantity or amount, chosen per line item.** A placeholder line item's `unit_price` is forced to `0` at group-save time (`groups.py`, `_save_recurring_items`) and everything is decided fresh each month via `resolve_placeholder` (`app/services/billing/placeholders.py`), which already accepts `quantity` and `unit_price` at resolution time. What's missing is a *default* set on the group itself: some placeholders (e.g. a pass-through with a known monthly quantity but a variable rate) are more naturally preset by quantity, others (e.g. a flat overage fee) by amount — and which one varies per line item, not globally. Needs a preset-mode field on the recurring line item config plus pre-flight defaulting the resolution form to it, without changing what `resolve_placeholder` itself accepts.
- [ ] **Contract intake automation — SharePoint upload + billing group draft from a signed contract.** Today: Contracts tab (`ui/src/pages/Contracts.tsx`) is an acknowledged placeholder — "nothing in this system models a contract" — and there is no SharePoint integration anywhere in the repo. Wanted flow: uploading a signed contract through the Contracts tab suggests (and performs) the SharePoint destination in the client's legal folder, then offers to create a new billing group or edit an existing one, and — if accepted — has an LLM draft the billing group fields (fee type, amount/quantity, schedule, category) from the contract for the operator to review and save. This is a write (new billing group / edited config) initiated from an LLM suggestion, so it needs to land as an operator-initiated write per ADR-0004 (exact payload shown before the click, human clicks save) rather than as an agent-proposed approval — no existing agent should get a Contracts/SharePoint tool in its `allowed_tools`. Needs: a SharePoint integration (new), a contract→billing-group extraction step (LLM, single-turn, likely following the existing dispatcher pattern in `app/integrations/llm.py`), and a decision on whether a contract becomes a record of its own before this is buildable — the placeholder text already flags that as unscoped.
