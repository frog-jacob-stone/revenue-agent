# Architecture

The durable shape of the system. Update this when boundaries, layering, or integration flow change.

## Stack

| Layer | Tool |
|---|---|
| API / agent logic | FastAPI + Python 3.12 + OpenAI |
| Memory & state | Supabase (Postgres + pgvector) — agent memory, approval queue, audit log |
| UI | React + TypeScript + Vite (`ui/`) |
| Secrets | Gitignored env files — `app/.env` locally, `.env.production` for deploys |
| Runtime | Docker Compose locally → Azure Container Apps (API), Netlify (UI), hosted Supabase (DB). See [DEPLOY.md](../DEPLOY.md) |

## Authentication

- The FastAPI app sits behind a single auth gate: `app/auth.py::get_current_user` is wired as `dependencies=[…]` on every router in `app/main.py`. The only public endpoint is `/healthz`. Anything that can't produce a valid bearer token gets a 401.
- Tokens are Supabase-issued JWTs, verified against the project's JWKS (`{SUPABASE_URL}/auth/v1/.well-known/jwks.json`) with `ES256`/`RS256`. **Asymmetric only** — any other `alg`, `HS256` included, is a 401. A legacy `SUPABASE_JWT_SECRET` HS256 fallback was removed on 2026-08-10: this project issues asymmetric tokens, so it was unreachable in production and existed only to let the auth tests sign their own tokens. It was also the weaker path — one leaked shared secret would forge a token for any user, which the JWKS path does not allow. `tests/test_auth.py` generates a P-256 keypair and stubs the JWKS client instead, and asserts an `HS256` token is rejected so the branch cannot quietly return.
- The UI (`ui/src/`) uses `@supabase/supabase-js` for sign-in (email + password). Every API call goes through `authedFetch` in `ui/src/api.ts`, which attaches `Authorization: Bearer <access_token>`. A 401 response signs the user out and bounces them to `/login`.
- DB access from FastAPI stays on the asyncpg service-role connection — no per-request user switching at the DB layer. RLS is on for every `public` table with a `service_role`-only policy. Defense-in-depth against accidental anon-key exposure; user-scoped policies are deferred until multi-user.

## The Propose / Approve / Execute Pattern

No agent may execute a create, update, or delete without a prior approved approval row. This governs **agent-initiated** writes; operator-initiated writes are covered below. Per [ADR-0002](adr/0002-tools-not-graphs.md):

```
tool returns AwaitingApproval(executor, payload, …)
  → approvals row (pending, executor set)
  → human approves (UI inbox only)
  → grant handler invokes the named executor in a background task
  → approvals row → executed | failed
```

- The `approvals` table is the queue of pending and historical work.
- The `audit_log` table receives a row at every state transition.
- The approval inbox in the UI is the canonical (and only) surface for grant.
- Agents and tools never call Harvest, Gmail, Airtable, etc. directly. The boundary is enforced structurally — executors live in their own registry and are **never** added to any agent's `allowed_tools`. This is [Unbreakable Rule #3](../CLAUDE.md): approvals are human-only.

### What Rule #1 does and doesn't cover

Rule #1 governs **agent-initiated writes**. It is not a rule that every INSERT
anywhere passes through the inbox — `memories`, `chat_sessions`, `social_posts`,
and `billing_groups` are all written directly by authenticated, audited,
human-initiated requests.

The distinguishing question is **who decided**. An LLM deciding to write is what
the approval chain exists for, because nobody was watching at the moment of the
decision.

### Operator-initiated writes

Per [ADR-0004](adr/0004-operator-initiated-writes.md), a write initiated by a
human in the UI needs no approval row, even when it leaves our boundary — the
click *is* the authorization. Three conditions, all required:

1. the exact payload is shown before the click,
2. the endpoint is human-only — never in any agent's `allowed_tools`, never an
   executor,
3. the transition writes `audit_log`.

Condition 2 is the load-bearing one, and it is enforced by
`tests/test_no_agent_approval_tools.py` rather than by convention: every tool in
every agent's `allowed_tools` is scanned, and any whose handler can return
`AwaitingApproval` fails the build. A human-only endpoint later wrapped in a tool
would be an agent-initiated write with no approval row — the one failure this
pattern must not permit.

This is the shape the invoicing module uses (`POST /billing/draws/{id}/invoice`,
`POST /billing/runs/{run_id}/items/{item_id}/resolve`) and the shape new
automation should use. The approvals module, `/approvals`, and both registered
executors remain in place for agent-initiated work; as of ADR-0004 no agent holds
a tool that reaches them.

## Billing / invoicing (`app/services/billing/`)

Harvest draft-invoice creation. Specified in
`docs/prd/harvest-invoicing-requirements.md`. Planning is complete for every
billing type. Execution exists for **one draw at a time**; the monthly run is
still plan-only and stays behind the reconcile gate.

```
harvest_snapshot → read-through cache of clients, projects, categories, rates
groups           → billing-group configuration (CRUD + validation)
reconcile        → every billable project ↔ exactly one active group
rates            → the rate-resolution ladder, shared by estimator and reconcile
dates            → run month + timing → service period, issue date, due date
estimator        → T&M line-item estimates from uninvoiced time and expenses
payload          → the exact POST /v2/invoices body
duplicate_guard  → ledger-aware detection of invoices already created
flags            → the §7 flag catalog
planner          → orchestrates a run and writes the ledger
review           → per-group approval of a planned run (persisted, human-only)
draws            → fixed-fee contract draws, billed off-cycle one at a time,
                   and `invoice_draw` — the only Harvest write in the system
inflight         → resolving a write whose outcome is unknown (human-only)
```

**The write, and its one ordering rule.** `draws.invoice_draw` commits the
`in_flight` ledger row *before* it POSTs, in a separate transaction:

```
transaction A: write in_flight, COMMIT   ← the lock, now durable
POST /v2/invoices                        ← may time out, may 5xx
transaction B: record the outcome
```

Wrapping the POST inside transaction A is the natural way to write this and it is
wrong: a process death mid-request rolls back the lock, leaving an invoice in
Harvest that this system has no record of — and the next click creates a second
one. Committing first means the worst case is a row nobody can interpret, which a
human can fix, rather than money nobody can see.

Four outcomes, kept distinct because §8 forbids collapsing them:

| Outcome | Ledger row | Draw |
|---|---|---|
| 201 | `created` + invoice id, amount, variance | `invoiced` |
| 4xx (a verdict) | `failed` + `error_message` | back to `ready` |
| 429 past the cap | `failed` — a 429 never reached creation | back to `ready` |
| timeout / 5xx / unexpected | **untouched**, stays `in_flight` | locked |

The last row is the point of the whole design. Nothing is inferred: `DrawWriteUnknown`
is raised, the API answers 502 with the ids and the remedy, and only a human
looking at Harvest can settle it via `inflight.resolve_item`.

**A draw is dated when it is drafted.** `preview_draw_invoice` defaults
`issue_date` to today on every call, so a preview looked at on the 10th and
created on the 12th is issued the 12th and — net 10 — due the 22nd. The client's
payment clock starts when the invoice exists, not when someone first looked at it.
The UI never serves this preview from cache for the same reason, and the create
response returns the dates actually used so the card stops showing its own guess.

Issue and due always move together. Harvest accepts an explicit `due_date` only
for `custom` terms and derives it from `issue_date` for the enum terms, so there
is no way to shift one without the other — and "issued the 10th, net 10, due the
22nd" is an invoice the client can see is wrong.

**Two things Harvest's API will not do for you**, both found on the first live
invoice rather than in review:

- **Default invoice notes are not applied.** The account-level defaults you
  configure in Harvest apply only to invoices created through Harvest's own UI,
  and no endpoint exposes them for reading (`GET /v2/company` has no such field).
  An API-created invoice arrives with notes blank — including the remit-to
  instructions the client needs in order to pay. `payload.resolve_notes` sends
  them explicitly: the group's `notes_template` if set, else the
  `default_invoice_notes` row in `billing_settings` (Settings → Billing). That
  value is knowingly a second copy of something Harvest also stores; there is no
  way to read the original, so the two must be kept in step by hand and nothing
  detects drift.
- **The account's web address is not discoverable.** `HARVEST_BASE_URI` exists so
  the UI can link to an invoice it created. Unset, the UI omits the link rather
  than guessing a subdomain and sending someone to a 404.

**A write needs a record on the surface that performed it.** The first live draw
was invoiced correctly and appeared to do nothing: it left the ready queue, its
in-card confirmation unmounted with the card, no screen listed drafted draws, and
the runs list filters `kind='draw'` out by default. The ledger had it the whole
time; nothing showed it. Treat "can the operator see what just happened, after a
reload, tomorrow" as part of shipping a write.

The answer is a **Drafted** tab (`/invoices/drafted`) backed by
`billing.invoices.list_created_invoices` — the ledger read back as a flat list,
newest-created first. It is deliberately *kind-agnostic*: draws are all that can
produce a `created` row today, but monthly rows appear in it without a code change
once that execution ships, because `kind` is a column on the run rather than a
branch in the query. Only `created` appears — `failed` means nothing exists in
Harvest and `in_flight` means nobody knows, so neither may be presented as an
invoice that reached a client.

**The tab is "Drafted", not "Billed".** This system creates a Harvest *draft* and
stops; the invoice is billed when a human sends it from Harvest, which is an event
this system never observes. "Billed" asserted a state we have no evidence for, on
the one screen whose whole job is to say what actually happened. The same
correction applies to the draw state labels — `ready` reads "Ready to draft" and
`invoiced` reads "Drafted".

Note the split between identifiers and copy. The `invoiced` state key,
`invoiced_run_id` / `invoiced_amount` columns, the `created` run-item status, and
the `DRAWS_READY_TO_BILL` flag code all keep their names: renaming them would mean
a migration and would break audit-log rows already written. Only what a human
reads changed. Where text describes the eventual business outcome rather than a
system state — "not necessarily what the client was billed", which months a
recurring line is billed in — "billed" is still the right word and was left
alone.

Ordering is by `created_at`, not `issue_date`: monthly issue dates are backdated
to period boundaries, so a July invoice created in September would otherwise be
buried among July's own work. `harvest_invoice_id` breaks ties, because one
transaction stamps a single `now()` and the list would otherwise reshuffle between
identical queries.

**Open question for the monthly run.** `resolve_period` deliberately dates those
invoices to the *period boundary* (arrears → last day of the month billed), not
the draft day, per PRD §2.3. That is the right accounting answer, but it means a
July-arrears run executed on 3 September would be issued 31 July and — net 30 —
already overdue on arrival. Decide the rule before monthly execution ships; do not
assume the draw behaviour above carries over.

**Draws do not ride the monthly run.** A fixed-fee contract's payment schedule
commits to dates, but a date never bills anything: a draw becomes billable only
when a human confirms delivery (`released_at`), and is then invoiced on its own
day, as a `kind='draw'` billing run holding a single ledger row. `scheduled_date`
drives the overdue prompt and forecasting, nothing else. The monthly run skips
these groups by design and surfaces `DRAW_OVERDUE` so a delivered milestone
cannot sit unbilled unnoticed. Consequences worth knowing before touching this:
`preview_draw_invoice` never calls `resolve_period` (a draw covers no month, so
`billing_timing` does not apply and `period_start`/`period_end` stay null), and
`save_draws` upserts by id rather than replacing the set, because draws carry
release and billing state that a wholesale replace would destroy.

**A draw's invoice is computed, never staged.** `preview_draw_invoice` builds
the exact POST body and writes nothing; the queue expands a ready draw in place
and creates the draft from there. There is deliberately no step that persists a
pending invoice ahead of the Harvest draft — it would invent a state that is
neither planned-in-a-run nor real, and then need a way to unwind it. The ledger
row belongs to the execution path, written immediately before the POST, which is
what the §8 in-flight protocol requires regardless.

A draw's four states are derived, and the third — `in_flight` — is read from the
ledger rather than from a column: a live `billing_run_items` row means execution
has begun. Nothing can produce it until execution ships. It exists now so that
when execution does ship, a draw mid-write can never be offered for billing
again, and a half-completed write is visible in the queue rather than silently
gone from it.

**Approval is persisted review state, not UI state.** The planner writes every
group as `planned`; a human moves it to `approved` through `review.py`, which
stamps `approved_at` / `approved_by`. Nothing defaults to approved, and closing
the tab does not discard the review. `review.py` owns two rules the UI must not
be trusted with: an error-severity flag blocks approval until a human records an
override, and flags in `flags.NON_OVERRIDABLE` can never be overridden at all.
This is *review* state, distinct from the Rule #1 approval chain below. Since
[ADR-0004](adr/0004-operator-initiated-writes.md) it is also the *only* approval
this path has — there is no `approvals` row behind it.

**No agent, deliberately.** Invoicing is fully deterministic — dates, rates,
groupings, and the payload are all computed, never inferred. An LLM in this path
would add a failure mode without adding a capability. The surface is
`app/routers/billing.py` plus the Invoices UI. A tool wrapper can be added later
without restructuring anything if chat access becomes useful.

**Writes are operator-initiated, with no `approvals` row.** Superseding the
earlier Phase 3 design (one approval row per run, executor
`create_harvest_draft_invoices`): the operator reads the computed invoice on
screen and clicks, and that click is the authorization —
[ADR-0004](adr/0004-operator-initiated-writes.md). The endpoints are human-only
and audit-logged; `audit_log` records both what happened and who authorized it.
`billing_runs.approval_id` is left in place, unused, in case agentic execution
returns.

Built so far: the single-draw write path (`POST /billing/draws/{id}/invoice`).
The monthly run's execution is still unbuilt and stays behind the reconcile gate.

**Two hard guarantees, both structural rather than conventional:**

- The system cannot send, delete, or modify a Harvest invoice.
  `tests/test_harvest_write_guardrail.py` scans all of `app/` and fails the
  build if the relevant endpoints appear anywhere.
- A project cannot be billed twice, and a group cannot be invoiced twice in a
  month. Both are partial unique indexes in migration `0024`, not application
  checks. `0028` adds a third for draws, keyed on the draw rather than the month
  (two milestones in one month is ordinary; the same milestone twice is not) —
  and that index is what stops two simultaneous clicks from creating two
  invoices, since both requests read `ready` before either writes. See
  `docs/SCHEMA.md`.

## Configuration: constant vs. env vs. database

Three homes. The split is by *who owns the value*, not by sensitivity.

| | Module constant | `app/config.py` (env) | `billing_settings` (DB) |
|---|---|---|---|
| Holds | tuning values identical in every environment | secrets, credentials, deployment identity | copy and preferences a human edits |
| Changing it | code edit + deploy | redeploy / restart | immediate, in the UI |
| A wrong value is | a bug, caught in review | a broken deploy | a business mistake |
| Audited | git history | no | yes, with the new value |
| Examples | `USE_ROUNDED_HOURS`, the lookback windows, `VARIANCE_PCT_THRESHOLD` | `HARVEST_TOKEN`, `HARVEST_BASE_URI`, `DATABASE_URL` | `default_invoice_notes` |

**The test for env is "does this differ per environment, or must it stay out of
the repo?"** If neither, it is a constant. Billing's tuning values were all
config once and none of them was ever set in any environment, which made them
invisible: nothing in `app/.env` mentioned them, and finding the live value meant
knowing to look in `config.py`. They now sit in the module that reads them —
`rates.USE_ROUNDED_HOURS`, `estimator.STRAGGLER_LOOKBACK_DAYS`,
`reconcile.UNMAPPED_LOOKBACK_DAYS`, `planner.VARIANCE_PCT_THRESHOLD` — each with
a comment on why the number is what it is.

Read a constant inside the function body rather than as a default argument, so
tests can patch the module attribute. Defaults bind once at import.

If a constant ever needs changing without a deploy, the answer is
`billing_settings`, not the environment: it gets a UI, an audit row, and no
restart.

`app/.env` is the only env source the app reads. `docker-compose.yml` points
`env_file` at it and interpolates nothing, so there is no root-level `.env`. Note
that Compose is what injects those values as environment variables — the
`env_file` in `SettingsConfigDict` is a fallback for non-Docker launches, and it
is anchored to `app/config.py`'s own directory so the working directory cannot
change what gets loaded.

One trap remains: `Settings` uses `extra="ignore"`, so **a misspelled env key is
silently ignored** — no error, just the default. Check names against
`app/config.py`.

### Credentials never render

Pydantic prints every field value in a `repr`, which leaked live tokens into
terminal output twice — once via a failing test whose `AttributeError` rendered a
`Settings` object. Two independent paths had to be closed, and `SecretStr` alone
only closed the first:

1. **The repr.** `database_url`, `airtable_api_key`, `harvest_token`, and
   `openai_api_key` are `SecretStr`, so they render as `**********`. Read one with
   `.get_secret_value()`. `supabase_publishable_key` is deliberately *not*
   wrapped — it is the anon key, designed to ship to browsers.
2. **`ValidationError`.** It embeds `input_value=`, the entire raw input dict, so
   one malformed field printed every credential beside it — `SecretStr` does not
   help, because the leak is in the wrapper rather than the field. `_load_settings`
   catches it and re-raises field locations and messages only, with `from None` so
   the traceback cannot reintroduce the original.

For the same reason the `ENV=test` database guard is a plain function
(`guard_test_db`) rather than a `@model_validator`: a validator's `ValueError`
gets wrapped in exactly the `ValidationError` described above. It raises
`RuntimeError` and redacts the DSN via `_redact_dsn`, keeping host and database
name so the error stays diagnosable.

`tests/test_config_redaction.py` guards all of it, including a test asserting a
raw `ValidationError` *does* leak — if pydantic ever changes that, the sanitizer
is dead weight and should be reconsidered rather than trusted.

## Layering

```
router  →  service  →  (agent | integration client | db)
                   ↘  tool (Done | AwaitingApproval | Blocked)
                   ↘  executor (post-approval side effects only)
```

- **Routers** validate input and call services. No business logic.
- **Services** hold business logic. Never call routers. Every state-changing service function calls `write_audit_event()`.
- **Agents** are services that drive LLM tool-call loops. They propose actions by returning `AwaitingApproval` from a tool. They never call third-party systems on their own.
- **Tools** (`app/agents/tools/`) are the unit of agent capability. A tool returns one of `Done | AwaitingApproval | Blocked`. The runtime — `app/orchestrator/dispatch.py::dispatch_tool` — pattern-matches the return shape, writes audit + (for `AwaitingApproval`) approval rows, and hands a status dict back to the LLM.
- **Executors** (`app/executors/`) are the post-approval side-effect performers. They live in a registry separate from tools, indexed by name, and are invoked only by the approval grant handler. Never added to any agent's `allowed_tools`.
- **Integration clients** (Harvest, Forecast, Airtable, Gmail) are called by executors, by read-only services, and — since [ADR-0004](adr/0004-operator-initiated-writes.md) — by operator-initiated services behind a human-only endpoint (`draws.invoice_draw` is the only one).
- All DB, HTTP, and agent calls are async.

## LLM dispatch

Every LLM call in the system flows through the dispatcher at `app/integrations/llm.py`. Callers reach for `dispatch()` (non-streaming) or `dispatch_stream()` (streaming with `StreamDelta` chunks + a terminal `LlmResponse`). Provider details, the OpenAI SDK types, request snapshotting, latency timing, and the `llm_calls` row write all live inside the dispatcher. Nothing outside `app/integrations/` imports from the `openai` package.

Attribution is structural, not ambient: every call passes `Attribution(agent_slug, purpose, workflow_id?, thread_id?)` as a required argument. `purpose` is a dotted free-form label that lands on `llm_calls.purpose` and is how telemetry gets sliced (`"chat"`, `"agent:bdr"`, `"create_post.voice_review"`, etc.). There is no contextvar form — forgetting attribution is a type error, not a silent NULL.

Tests scope a fake provider with `use_provider(FakeProvider(...))` from `tests/fakes/llm.py`. The `LlmProvider` Protocol is the internal seam — production adapter is `_OpenAiProvider` (`app/integrations/_openai_provider.py`). A second provider lands as another adapter behind the same seam; no caller changes.

## Agent Scoping Principles

An agent has a single coherent identity: one job, one audit trail, one approval context. To decide whether a capability belongs in an existing agent or a new one, ask:

1. **Same trigger schedule?** Different cadence or trigger source → likely a separate agent.
2. **Same approval context?** Different approver, risk level, or stakes → separate it.
3. **Same action type categorically?** Sending emails and generating invoices are categorically different even within one business domain.
4. **Does it propose actions, or just answer?** Read-only "answer questions" agents are separate from write-proposing "do work" agents, even on the same data.

**Read-only vs. write-proposing is a hard split.** An analytics agent and an operations agent for the same domain are distinct agents — different audit trails, different inbox behavior, different UI presentation.

**Agents vs. inline LLM calls.** Classes in `app/agents/` represent identity-bearing things: the conversational front door (`ChiefOfStaffAgent`) or worker personas meant to be invoked via `ask_agent` / `run_agent_task` and accountable in the audit trail (`BDRAgent`, `RevenueOpsAgent`, `LinkedInAgent`). Single-turn, fixed-prompt LLM calls made inline inside a tool are NOT agents — they live in the tool file (or a sibling `_prompts.py`) as `MODEL` + `SYSTEM_PROMPT` constants, attributed via `Attribution(agent_slug=..., purpose=...)` on the dispatch call. Rule of thumb: if a "thing" has no identity, no autonomy, and one caller, it's a prompt, not an agent.

**Chat is an interface, not an agent type.** Chat is one of several trigger sources (webhook, schedule, chat). Any *agent* path that proposes a write flows through the approval inbox; operator-initiated writes from the UI do not (ADR-0004). As of ADR-0004 no agent holds a write tool, so chat is read-and-draft only.

**Single front-door pattern.** Exactly one agent is conversational: `ChiefOfStaffAgent` (slug `chief-of-staff`), defined in `app/agents/chief_of_staff_agent.py`. The user only ever chats with this agent. Specialist agents (`revenue-ops`, `linkedin`, `bdr`) are plain `Agent` workers invoked via `ask_agent` from the front door, which routes through `run_agent_task`. The front-door slug is hardcoded as `FRONT_DOOR_SLUG` in `app/services/chat_turn.py` — there is no agent picker.

## Orchestrator runtime (`app/orchestrator/`)

Post-ADR-0002 the orchestrator is small. Its surface:

- **`dispatch_tool(tool, ctx, args)` — `app/orchestrator/dispatch.py`.** The runtime that pattern-matches a tool's return value. On `Done(payload)` it writes a `tool.completed` audit row and hands `payload` back to the LLM. On `AwaitingApproval(executor, payload, summary, action_type, risk_level)` it creates the approval row (status `pending`, `executor` populated) and hands `{"status": "awaiting_approval", "approval_id", "summary"}` back to the LLM. On `Blocked(reason, hint)` it writes a `tool.blocked` audit row and tells the LLM what's missing so the next assistant turn can explain to the user.
- **`run_agent_task(slug, task, ctx, *, progress=None)` — `app/orchestrator/agent_invoke.py`.** The single agent-invocation primitive. Brackets the call with `agent.invoked` / `agent.completed` / `agent.failed` audit events. Drives a ReAct loop when the target has `allowed_tools` (re-entering `dispatch_tool` for each tool call) and falls back to a single-turn dispatch when it doesn't. `ask_agent` is the canonical caller; tests also use it directly.
- **`events` — `app/orchestrator/events.py`.** Audit-event constants. Imported everywhere; never written as string literals.

What's intentionally *not* here:
- **No graph engine.** Conditional branches, retry loops, and critique-and-rewrite live as inline Python (`for`, `if`, `while`) inside the tool that owns the workflow. The old LangGraph runner / state / spawn / critique_loop machinery was removed in plan 19. See [ADR-0002](adr/0002-tools-not-graphs.md).
- **No multi-gate approvals.** Every production workflow today is single-gate (or zero-gate). If a future workflow needs more than one approval, revisit; do not pre-build the abstraction.
- **No persistent workflow state.** Audit log + approvals carry enough state to reconstruct what happened. The `workflows` table was dropped in migration `0022`.

## Tools (`app/agents/tools/`)

A tool exports a `ToolDefinition` constant — name, description, OpenAI input schema, async `execute` callable. The `execute` returns `Done | AwaitingApproval | Blocked` (the `ToolReturn` union, defined in `app/agents/tools/base.py`). Helpers:

- **`ProgressEmitter`** — tools may emit `tool_step_started` / `tool_step_completed` events for in-tool observability. These bubble up to the chat UI and appear as nested activity lines under the tool's call.
- **`ToolContext`** — carries `agent_id`, `agent_slug`, optional `workflow_id`, and the optional `ProgressEmitter`. Passed by `dispatch_tool`.

Today's production tools include: social-content tools (`create_post`, `rewrite_post`, `reject_post`, `get_posts`, `export_posts`), revenue analysis (`get_revenue_data`), and the agent-delegation tool (`ask_agent`). The three read-only HubSpot lookups (`get_contact_by_email`, `get_company_by_id`, `get_form_submission`) were deleted on 2026-08-10 when HubSpot was removed; they were the BDR agent's only tools, so the BDR is now toolless by design and drafts from supplied context.

**No agent holds a tool that proposes an approval.** `publish_post` and `trigger_revenue_recognition` still exist and still return `AwaitingApproval`, but ADR-0004 removed them from `LinkedInAgent` and `RevenueOpsAgent` respectively — publishing and running rev rec are operator actions now. `tests/test_no_agent_approval_tools.py` scans every reachable tool's handler source and fails the build if this regresses.

## Executors (`app/executors/`)

Executors are the only code path that performs side effects against external systems on behalf of an approval. Each executor has a name (matching the `executor` column on its approval rows), a description, and an async `execute(ctx, payload)` callable. They are registered in `app/executors/registry.py::EXECUTORS_BY_NAME`. Today: `post_to_linkedin`, `write_rev_rec_entries`. They are **never** exposed to an LLM — that's the structural enforcement of Unbreakable Rule #3.

## Approval Inbox

The inbox UI sources from `/approvals` only. `Approval` rows discriminate by status: `pending` is the queue; `approved/rejected/executed/failed` are history. Editing the `executed_payload` before approving is supported — the executor runs with the edited shape if present.

## Multi-Agent Orchestration

The system is a **single front door + specialist workers**. There is one conversational agent (`chief-of-staff`); every other agent is a worker.

Dispatch shape:

- **User → front door (chat).** `app/services/chat_turn.py::start_turn` drives the OpenAI tool-call loop against `ChiefOfStaffAgent`. The front door owns the cross-domain content tools (`create_post`, `publish_post`, etc.) directly; revenue tools (`trigger_revenue_recognition`, `get_revenue_data`) live on the `revenue-ops` domain agent and are reached via `ask_agent`.
- **Front door → specialist (LLM delegation).** The front door calls `ask_agent(target_slug, prompt)` for domain-specific explanation or reasoning. `ask_agent` writes the outbound prompt to `agent_messages`, calls `run_agent_task` (which is either single-turn or a ReAct loop depending on whether the target has `allowed_tools`), writes the inbound reply, and returns `{answer, thread_id}`. The specialist's `system_prompt` is what's load-bearing.

Specialists never appear in the chat surface. To iterate on a specialist's prompt, drive `run_agent_task` from a test or shell — there is intentionally no admin chat endpoint for them.

## Anti-Patterns

- Agents or tools mutating an approval's status (calling `approve()` / `reject()` / `mark_executed`). Approvals are human-only — Unbreakable Rule #3.
- Adding an executor to any agent's `allowed_tools`. This collapses the trust boundary.
- Calling integration clients (Harvest, Gmail, Airtable, etc.) from a tool. Reads are fine; writes belong in an executor that runs after approval.
- Secrets committed to the repo, or read from anywhere but the environment. Local values live in `app/.env`, production values in `.env.production`; both are gitignored, and only `*.example` templates are tracked. A deploy copies `.env.production` to Azure and Netlify (see [DEPLOY.md](../DEPLOY.md#secrets)) — setting one directly in a cloud console instead makes that console the only record of it. A hosted secret manager was specified once and never implemented; the env files are what the project actually uses.
- Re-introducing a graph engine for what could be a `for` loop. See [ADR-0002](adr/0002-tools-not-graphs.md).
- Putting an LLM in a deterministic path because the system is "an agent system". Invoicing computes; it does not infer.
- Enforcing a money-safety invariant in application code when a partial unique index can enforce it in the database.
