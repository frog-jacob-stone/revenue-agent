# Automated Monthly Invoice Creation — Requirements & Work Breakdown

**Target system:** existing local Python application, Supabase backing store
**External system:** Harvest API v2 (`https://api.harvestapp.com/v2`)
**Trigger:** manual, operator-initiated
**Scope of this document:** Phase 1 (Time & Materials) and Phase 2 (Fixed Fee). Retainers and off-cycle invoices are specified at the architectural level only.

---

## 1. Hard Constraints

These are non-negotiable and must be enforced in code, not just convention.

| # | Constraint | Implementation requirement |
|---|---|---|
| C1 | **The system never sends an invoice to a client.** | `POST /v2/invoices/{id}/messages` must not appear anywhere in the codebase. Add a lint/CI check or a code comment guard on the Harvest client wrapper. |
| C2 | **All invoices are created in `draft` state.** | Harvest creates invoices as `draft` by default. Never transition state. |
| C3 | **Nothing is written to Harvest without explicit operator approval.** | Pre-flight is strictly read-only. Execution requires an approval action recorded in the database. |
| C4 | **The system never deletes or modifies an existing Harvest invoice.** | No `DELETE /v2/invoices/{id}`, no `PATCH /v2/invoices/{id}` in Phase 1–2. |
| C5 | **No scheduling.** | Manual invocation only. No cron, no background workers. |
| C6 | **A group is invoiced at most once per run month.** | Enforced by a unique constraint in the ledger, not by application logic alone. |

---

## 2. Core Concepts

### 2.1 Billing Group

**A billing group is the unit that produces exactly one Harvest invoice.**

This is the central abstraction. Harvest has no concept of it — it must live entirely in Supabase.

- One billing group belongs to exactly one Harvest client.
- One billing group contains one or more Harvest projects.
- A client with three projects that go on one combined invoice has **one** billing group.
- A client with three projects that go on three separate invoices has **three** billing groups.
- A client with two projects combined plus one standalone has **two** billing groups.

Every billable Harvest project must map to exactly one active billing group — including projects you invoice by hand, which map to a `manual` group. A project mapped to zero groups, or to more than one, is a configuration error the pre-flight must surface.

### 2.2 Billing Type

Three billing types plus one marker type:

| Type | Line items come from | Harvest mechanism |
|---|---|---|
| `time_and_materials` | Uninvoiced time & expenses in Harvest | `line_items_import` |
| `fixed_fee_schedule` | Predetermined dated draw schedule in Supabase | Free-form `line_items` |
| `recurring_monthly` | Static line-item template in Supabase | Free-form `line_items` |
| `manual` | **Nothing — no invoice is created** | None |

`manual` exists solely so that projects you invoice by hand (milestone-based fixed fee, one-offs, anything not yet automated) can be explicitly acknowledged in config. A `manual` group is skipped entirely during planning: no payload is built, no estimate is computed, no ledger row is written. Its only function is to suppress the `UNMAPPED_PROJECT` error so that manually-handled projects don't generate recurring false alarms every month.

This matters more than it looks. Without it, every milestone-billed project raises an error on every single run, and error fatigue is how a real `UNMAPPED_PROJECT` eventually gets ignored.

### 2.3 Billing Timing

Determines the service period and issue date.

| Timing | Service period | Issue date |
|---|---|---|
| `arrears` | Previous calendar month | Last day of previous month |
| `advance` | Current calendar month | First day of current month |

A single run produces both. If the run month is August 2026:
- Arrears groups get `period 2026-07-01 → 2026-07-31`, `issue_date 2026-07-31`
- Advance groups get `period 2026-08-01 → 2026-08-31`, `issue_date 2026-08-01`

### 2.4 Billing Run

One operator-initiated execution against a single `run_month`. `run_month` defaults to the current calendar month and is overridable (needed for backfills and for the case where you run on the 1st or 2nd and mean the prior month).

A run has a lifecycle: `planning` → `awaiting_approval` → `executing` → `completed` (or `failed` / `abandoned`).

---

## 3. Data Model (Supabase)

### `billing_groups`
| Column | Type | Notes |
|---|---|---|
| `id` | uuid PK | |
| `name` | text | Human label, e.g. "Acme — Platform + Mobile" |
| `harvest_client_id` | bigint | |
| `harvest_client_name` | text | Denormalized for readability |
| `billing_type` | enum | See 2.2 |
| `billing_timing` | enum | `arrears` \| `advance` |
| `payment_term` | enum | `upon receipt` \| `net 15` \| `net 30` \| `net 45` \| `net 60` \| `custom` |
| `custom_net_days` | int | Required when `payment_term = 'custom'` |
| `time_summary_type` | enum | `project` \| `task` \| `people` \| `detailed` — T&M only |
| `include_expenses` | bool | |
| `expense_summary_type` | enum | `project` \| `category` \| `people` \| `detailed` |
| `attach_receipts` | bool | Default false |
| `subject_template` | text | e.g. `"{client_name} — {period_label}"` |
| `notes_template` | text | Nullable |
| `purchase_order` | text | Nullable |
| `currency` | text | Nullable; falls back to Harvest client currency |
| `is_active` | bool | |
| `created_at` / `updated_at` | timestamptz | |

### `billing_group_projects`
| Column | Type | Notes |
|---|---|---|
| `billing_group_id` | uuid FK | |
| `harvest_project_id` | bigint | |
| `harvest_project_name` | text | Denormalized |
| `sort_order` | int | Controls `project_ids` array order |

Unique constraint on `harvest_project_id` where the parent group is active — this is what prevents double-billing a project.

### `fixed_fee_schedule_items`
For `fixed_fee_schedule` only.

| Column | Type | Notes |
|---|---|---|
| `id` | uuid PK | |
| `billing_group_id` | uuid FK | |
| `harvest_project_id` | bigint | Line item is attributed to this project |
| `sequence` | int | Draw 1 of 5, etc. |
| `description` | text | Appears verbatim on the invoice |
| `amount` | numeric(12,2) | |
| `scheduled_month` | date | First of month. Not nullable. |
| `invoiced_run_id` | uuid FK | Null until consumed. Prevents re-billing. |

### `recurring_line_items`
For `recurring_monthly`.

| Column | Type | Notes |
|---|---|---|
| `billing_group_id` | uuid FK | |
| `harvest_project_id` | bigint | |
| `description` | text | Supports `{period_label}` token |
| `quantity` | numeric | Default 1 |
| `unit_price` | numeric(12,2) | |
| `sort_order` | int | |
| `effective_from` / `effective_to` | date | Nullable. Lets a fee change without losing history. |

### `billing_runs`
| Column | Type | Notes |
|---|---|---|
| `id` | uuid PK | |
| `run_month` | date | First of month |
| `status` | enum | See 2.4 |
| `created_at`, `approved_at`, `executed_at`, `completed_at` | timestamptz | |
| `plan_snapshot` | jsonb | Full pre-flight output, frozen at plan time |

### `billing_run_items`
One row per billing group per run. **This is the ledger.**

| Column | Type | Notes |
|---|---|---|
| `id` | uuid PK | |
| `billing_run_id` | uuid FK | |
| `billing_group_id` | uuid FK | |
| `status` | enum | `planned` \| `approved` \| `skipped` \| `in_flight` \| `created` \| `failed` |
| `planned_amount` | numeric(12,2) | Estimate from pre-flight |
| `planned_payload` | jsonb | Exact POST body that will be sent |
| `issue_date`, `due_date`, `period_start`, `period_end` | date | |
| `harvest_invoice_id` | bigint | Null until created |
| `harvest_invoice_number` | text | |
| `actual_amount` | numeric(12,2) | From Harvest response |
| `variance` | numeric(12,2) | `actual − planned` |
| `error_message` | text | |
| `created_at`, `updated_at` | timestamptz | |

**Unique constraint: `(billing_group_id, billing_run_id)`.**
**Additional unique partial index: one non-failed, non-skipped row per `(billing_group_id, run_month)`** — this is the real double-billing guard.

### `billing_run_flags`
| Column | Type | Notes |
|---|---|---|
| `billing_run_item_id` | uuid FK | Nullable — some flags are run-level, not group-level |
| `billing_run_id` | uuid FK | |
| `code` | text | See flag catalog, §7 |
| `severity` | enum | `error` \| `warning` \| `info` |
| `message` | text | Human-readable |
| `context` | jsonb | Project IDs, amounts, entry IDs, etc. |

---

## 4. Harvest API Reference

All requests require three headers:

```
Authorization: Bearer $HARVEST_ACCESS_TOKEN
Harvest-Account-Id: $HARVEST_ACCOUNT_ID
User-Agent: <AppName> (<contact email>)
```

**Rate limits:** 100 requests / 15 seconds for general endpoints; **100 requests / 15 minutes for `/v2/reports/*`**. A 429 returns a `Retry-After` header. The Reports limit is the binding one — do not build the pre-flight on top of Reports endpoints. Implement a token-bucket limiter in the client wrapper with separate buckets for general vs. reports, plus exponential backoff honoring `Retry-After`.

### 4.1 Read: clients

```
GET /v2/clients?is_active=true&per_page=100
```
Used for building group config and resolving client names and currency.

### 4.2 Read: projects

```
GET /v2/projects?is_active=true&per_page=100
```

Relevant response fields: `id`, `name`, `code`, `client.id`, `client.name`, `client.currency`, `is_billable`, `is_fixed_fee`, `bill_by`, `hourly_rate`, `fee`, `budget`, `budget_by`, `budget_is_monthly`, `is_active`.

`is_fixed_fee` is the authoritative check that a project's Harvest configuration matches the billing group's `billing_type`. A mismatch is a flag.

### 4.3 Read: time entries (pre-flight estimate)

```
GET /v2/time_entries?project_id={id}&from=2026-07-01&to=2026-07-31&per_page=100
```

Relevant fields per entry: `id`, `spent_date`, `hours`, `rounded_hours`, `billable`, `billable_rate`, `is_billed`, `is_locked`, `locked_reason`, `approval_status`, `user.name`, `task.name`, `project.id`.

- **Verify at implementation time** whether `is_billed=false` is honored as a query parameter on v2. If not, filter client-side on the `is_billed` field. Do not assume.
- **Estimate formula:** sum over entries where `billable = true` and `is_billed = false` of `effective_hours × billable_rate`, where `effective_hours` is `rounded_hours` if the account has time rounding enabled, else `hours`. Built as a code constant, `USE_ROUNDED_HOURS` in `app/services/billing/rates.py`, rather than the config flag originally specified here — it mirrors a Harvest account setting, so it cannot legitimately differ between environments.
- `billable_rate` can be null. Fall back to the project's `hourly_rate`, then to the task assignment rate. If no rate resolves, emit an error-severity flag rather than silently estimating zero.

### 4.4 Read: expenses (pre-flight estimate)

```
GET /v2/expenses?project_id={id}&from=2026-07-01&to=2026-07-31&per_page=100
```
Fields: `total_cost`, `billable`, `is_billed`, `approval_status`, `expense_category.name`.

### 4.5 Read: existing invoices (duplicate guard)

```
GET /v2/invoices?client_id={id}&from=2026-07-01&to=2026-08-31&per_page=100
```
`from`/`to` filter on `issue_date`. Used to detect an invoice already created for this client and period — whether by this system or manually in the Harvest UI.

### 4.6 Write: create T&M invoice

```bash
curl "https://api.harvestapp.com/v2/invoices" \
  -H "Authorization: Bearer $HARVEST_ACCESS_TOKEN" \
  -H "Harvest-Account-Id: $HARVEST_ACCOUNT_ID" \
  -H "User-Agent: MyApp (jacob@frogslayer.com)" \
  -X POST \
  -H "Content-Type: application/json" \
  -d '{
    "client_id": 5735774,
    "subject": "Acme Corp — July 2026",
    "issue_date": "2026-07-31",
    "payment_term": "net 30",
    "purchase_order": "PO-4471",
    "line_items_import": {
      "project_ids": [14307913, 14307914],
      "time": {
        "summary_type": "task",
        "from": "2026-07-01",
        "to": "2026-07-31"
      },
      "expenses": {
        "summary_type": "category",
        "from": "2026-07-01",
        "to": "2026-07-31"
      }
    }
  }'
```

Returns `201 Created` with the full invoice object including `id`, `number`, `amount`, `period_start`, `period_end`, `due_date`, `state: "draft"`, and generated `line_items`.

**Critical notes:**
- All `project_ids` must belong to `client_id`. A mismatch returns `422`. Validate in pre-flight.
- Harvest marks the imported time entries and expenses as billed. This is the mechanism that makes the operation naturally non-repeatable — but it is *not* an idempotency guarantee. A second identical call returns a second invoice, likely with zero or partial line items.
- Omit the `expenses` object entirely when `include_expenses` is false. Do not pass an empty object.
- If both `from` and `to` are omitted from the `time` object, **all** unbilled time is pulled regardless of date. Always pass both.

### 4.7 Write: create fixed-fee / recurring invoice

```bash
curl "https://api.harvestapp.com/v2/invoices" \
  -H "Authorization: Bearer $HARVEST_ACCESS_TOKEN" \
  -H "Harvest-Account-Id: $HARVEST_ACCOUNT_ID" \
  -H "User-Agent: MyApp (jacob@frogslayer.com)" \
  -X POST \
  -H "Content-Type: application/json" \
  -d '{
    "client_id": 5735774,
    "subject": "Acme Corp — August 2026 Retainer",
    "issue_date": "2026-08-01",
    "payment_term": "custom",
    "due_date": "2026-08-21",
    "line_items": [
      {
        "project_id": 14307913,
        "kind": "Service",
        "description": "Monthly platform support — August 2026",
        "quantity": 1,
        "unit_price": 8500.00
      },
      {
        "project_id": 14307913,
        "kind": "Service",
        "description": "Milestone 3 — Data migration complete",
        "quantity": 1,
        "unit_price": 25000.00
      }
    ]
  }'
```

**`kind` must be a valid invoice item category name from the account.** Fetch and cache these:

```
GET /v2/invoice_item_categories
```
Harvest ships two non-removable defaults (the hours and expenses categories). Validate `kind` against this list in pre-flight rather than discovering a `422` at execution time.

### 4.8 Payment term and due date behavior

This is the single easiest thing to get wrong:

- **`due_date` is ignored unless `payment_term` is `"custom"`.**
- When `payment_term` is one of the enum values (`upon receipt`, `net 15`, `net 30`, `net 45`, `net 60`), Harvest computes `due_date` from `issue_date`. Prefer this — let Harvest own the arithmetic.
- Any other terms (net 10, net 20, "due the 15th") require `payment_term: "custom"` **and** an explicitly computed `due_date`.
- Verify the returned `due_date` against expectation after creation and flag any mismatch.

### 4.9 Explicitly out of scope

Do not implement, do not call:
- `POST /v2/invoices/{id}/messages` — any variant, including `event_type: "send"`
- `DELETE /v2/invoices/{id}`
- `PATCH /v2/invoices/{id}`
- `POST /v2/invoices/{id}/payments`

---

## 5. Run Lifecycle

```
[Operator presses "Plan"]
        │
        ▼
  ┌─────────────────┐
  │  1. SNAPSHOT    │  Fetch clients, projects, invoice item categories.
  │                 │  Cache to Supabase. Read-only.
  └────────┬────────┘
           ▼
  ┌─────────────────┐
  │  2. RECONCILE   │  Every active billable Harvest project ↔ billing group.
  │     CONFIG      │  Flag orphans, duplicates, type mismatches.
  └────────┬────────┘
           ▼
  ┌─────────────────┐
  │  3. BUILD PLAN  │  Per group: resolve dates, build payload,
  │                 │  compute estimate, run duplicate guard.
  └────────┬────────┘
           ▼
  ┌─────────────────┐
  │  4. RENDER      │  Human-readable plan. Persist to plan_snapshot.
  │     PRE-FLIGHT  │  Run status → awaiting_approval.
  └────────┬────────┘
           ▼
[Operator reviews, approves per-group]
           ▼
  ┌─────────────────┐
  │  5. EXECUTE     │  For each APPROVED group only: write in_flight
  │                 │  ledger row → POST → record result.
  └────────┬────────┘
           ▼
  ┌─────────────────┐
  │  6. RECONCILE   │  Compare actual vs. planned amounts. Report.
  │     RESULT      │  Run status → completed.
  └─────────────────┘
```

**Approval is per-group, not per-run.** The operator will routinely want to approve 18 of 20 groups, fix two config problems, and re-plan. Do not build all-or-nothing approval.

---

## 6. Pre-flight Plan Output

The plan must be readable in a terminal and persistable. Suggested rendering: a grouped table plus a flag summary.

Per planned invoice, display:

- Billing group name, Harvest client name, billing type, timing
- Service period and issue date, due date, payment term
- Projects included (name + Harvest ID)
- **Estimated line items** — for T&M, the aggregation grouped by the configured `summary_type`; for fixed fee, the literal line items
- Estimated total
- Comparison to the same group's prior-month invoice amount, with percentage delta (cheap, high-value sanity check)
- All flags with severity

Run-level summary:

- Count of invoices to be created, total estimated value
- Count of flags by severity
- **Unmapped projects with uninvoiced billable time** — listed prominently, because this is the failure mode that silently loses revenue
- Groups skipped and why

### 6.1 On estimate fidelity

The T&M estimate is computed independently of Harvest's own invoice generation. It will not always match to the penny — time rounding rules, rate resolution order, and mid-period rate changes can diverge. This is acceptable and expected.

Handle it this way: the pre-flight number is a **sanity check**, not a contract. After execution, compare `invoice.amount` from the Harvest response against `planned_amount` and record `variance`. Surface any variance over a configurable threshold (suggest $50 or 2%, whichever is greater) in the post-run report. Over a few months this will either converge to zero or teach you exactly where your estimator is wrong.

---

## 7. Flag Catalog

| Code | Severity | Condition |
|---|---|---|
| `UNMAPPED_PROJECT` | error | Active billable Harvest project with uninvoiced billable time, not in any active billing group. Projects in a `manual` group are exempt. |
| `UNMAPPED_PROJECT_NO_TIME` | warning | Same, but with no uninvoiced billable time in the lookback window. Nothing is missing from the run; the gap is that time logged to it would go uninvoiced. `manual` groups exempt it too. |
| `PROJECT_IN_MULTIPLE_GROUPS` | error | Project appears in more than one active billing group |
| `PROJECT_CLIENT_MISMATCH` | error | Project's `client.id` ≠ group's `harvest_client_id`. Would cause a 422. |
| `ALREADY_INVOICED_THIS_RUN` | error | Non-failed ledger row already exists for this group and run month |
| `EXISTING_HARVEST_INVOICE` | warning | Harvest already has an invoice for this client with an overlapping issue date |
| `NO_RATE_RESOLVED` | error | A billable time entry has no resolvable rate |
| `INVALID_ITEM_CATEGORY` | error | `kind` on a fixed-fee line item is not a valid invoice item category |
| `NO_UNINVOICED_TIME` | warning | T&M group with zero billable uninvoiced time in the period. Skip by default; make skip-vs-zero-invoice configurable. |
| `UNAPPROVED_TIME` | warning | Time entries in the period with `approval_status` ≠ `approved` |
| `STRAGGLER_TIME` | warning | Uninvoiced billable time **before** the period start. Will not be captured by the bounded `from`/`to` import and will silently roll forward. |
| `LATE_TIME` | warning | Uninvoiced billable time **after** the period end on an arrears group |
| `SCHEDULE_GAP` | warning | `fixed_fee_schedule` group with no schedule item for this month |
| `SCHEDULE_EXHAUSTED` | info | Active fixed-fee group with all schedule items consumed |
| `TYPE_MISMATCH` | warning | `time_and_materials` group pointing at a project with `is_fixed_fee = true`, **or** a `fixed_fee_schedule` / `recurring_monthly` group pointing at a project with `is_fixed_fee = false` |
| `FIXED_FEE_TIME_NOISE` | info | Billable uninvoiced time tracked to a fixed-fee or retainer project. Expected — Harvest never clears it — but worth surfacing so it isn't mistaken for missed revenue. On retainer projects this accumulates indefinitely; see §10. |
| `MISSING_PO` | warning | Group has `purchase_order` configured as required but empty |
| `CURRENCY_MISMATCH` | error | Projects in one group resolve to different currencies |
| `AMOUNT_VARIANCE` | warning | Estimate deviates from prior month by more than a configured threshold |
| `INACTIVE_PROJECT_WITH_TIME` | warning | Archived/inactive project carrying uninvoiced time |

**Nothing in this catalog auto-blocks execution.** Errors mean the group cannot be safely executed and should default to unapproved; the operator can still override. Warnings are informational. The design principle: the system's job is to *notice*, the operator's job is to *decide*.

---

## 8. Idempotency & Failure Handling

Harvest has no idempotency keys. A retried POST creates a second invoice. The protocol:

1. **Before** the POST, insert the `billing_run_items` row with `status = 'in_flight'`. The unique constraint makes this the lock.
2. Issue the POST with a conservative timeout (30s) and **zero automatic retries**.
3. On `201`: update to `status = 'created'`, store `harvest_invoice_id`, `harvest_invoice_number`, `actual_amount`, compute `variance`.
4. On `4xx` (excluding 429): update to `status = 'failed'`, store the response body in `error_message`. Safe to re-plan.
5. On `429`: honor `Retry-After` and retry — this is the one case where retry is safe, since the request never reached invoice creation.
6. On timeout, connection error, or `5xx`: **leave the row as `in_flight`.** Do not retry.

An `in_flight` row is a poison pill by design. On the next planning run, any `in_flight` row for a group produces a blocking error instructing the operator to check Harvest manually and resolve the row — either linking the invoice that was in fact created, or marking it failed. Ambiguous states get escalated to a human rather than guessed at.

Execution processes groups sequentially, not concurrently. Volume is low, and sequential execution makes partial-failure state trivial to reason about.

---

## 9. Work Breakdown

### Phase 0 — Foundation

| # | Task | Acceptance criteria |
|---|---|---|
| 0.1 | Harvest API client wrapper | Bearer + account-id + user-agent headers on every call. Typed response models. Raises distinct exception types for 401/403/404/422/429/5xx. |
| 0.2 | Dual-bucket rate limiter | Separate buckets for general (100/15s) and reports (100/15min). Honors `Retry-After` on 429. Unit-tested against a mocked clock. |
| 0.3 | Pagination helper | Transparently follows `links.next` / `page`. Handles the 2000-record `per_page` ceiling. |
| 0.4 | Supabase schema migration | All tables from §3, with the unique constraints. Migration is reversible. |
| 0.5 | Config: secrets & environment | `HARVEST_ACCESS_TOKEN`, `HARVEST_ACCOUNT_ID`, Supabase credentials. No secrets in the repo. |
| 0.6 | Guardrail test | An automated test asserting no invocation of the invoice-messages, delete, or patch endpoints exists in the codebase. |

### Phase 1 — Config Layer

| # | Task | Acceptance criteria |
|---|---|---|
| 1.1 | Harvest snapshot command | Fetches clients, projects, invoice item categories into Supabase cache tables. Idempotent; safe to re-run. |
| 1.2 | Billing group CRUD | Create, read, update, deactivate. Validates project→client consistency on write. |
| 1.3 | Config validation command | Runs the §2.1 mapping checks standalone, so config can be fixed outside a billing run. |

Billing groups are built by hand in the UI. There is deliberately no auto-seeding
helper: grouping decisions (which projects share an invoice, which are manual,
which summary type a client wants) are exactly the judgment a generated
one-project-one-group baseline would have to guess at, and a wrong draft group is
harder to notice than a missing one. `UNMAPPED_PROJECT` is the signal that config
is incomplete.

### Phase 2 — T&M Pre-flight (thin vertical slice)

| # | Task | Acceptance criteria |
|---|---|---|
| 2.1 | Date resolver | Given `run_month` + `billing_timing`, returns period start/end and issue date. Unit-tested across month lengths, year boundaries, and leap years. |
| 2.2 | Due date resolver | Enum term → pass through to Harvest. Custom → `payment_term: "custom"` plus computed `due_date`. Unit-tested. |
| 2.3 | T&M estimator | Fetches time entries and expenses per project, resolves rates, applies rounding config, aggregates by `summary_type`. Returns line-item estimates plus total. |
| 2.4 | Duplicate guard | Ledger check + Harvest `GET /v2/invoices` check. |
| 2.5 | Payload builder | Produces the exact `line_items_import` POST body. Persisted to `planned_payload`. |
| 2.6 | Flag engine | Implements the §7 catalog for T&M-relevant codes. Table-driven so codes are easy to add. |
| 2.7 | Plan renderer | Terminal-readable output per §6. Persists to `plan_snapshot`. |
| 2.8 | Plan command | `plan --run-month 2026-08`. End-to-end, read-only, writes no invoices. |

**Milestone: run this against production Harvest data and manually verify every planned invoice against what you'd have created by hand. Do not proceed to monthly-run execution until a full month reconciles.**

Ordering trap discovered in implementation: the estimator counts only time Harvest
has not marked `is_billed`, so a month that has already been invoiced re-plans to
empty. The reconcile has to run *before* the manual invoicing, not after — plan
first, invoice by hand second, compare third.

This gate applies to the **monthly run** and not to draws. A draw's amount is a
number a human typed into the contract schedule and then released; there is no
estimate involved, so there is nothing for a hand-reconcile to check.

### Phase 3 — Execution

Status: the single-draw write path is **built**; the monthly run's is not.
Superseded on one point by [ADR-0004](../adr/0004-operator-initiated-writes.md) —
these writes are operator-initiated and carry no `approvals` row. The click on a
screen showing the exact payload is the authorization.

| # | Task | Status | Acceptance criteria |
|---|---|---|---|
| 3.1 | Approval interface | ✅ | Per-group approve / skip, `approved_at` recorded, error flags need an explicit override. For draws the release + the create click serve this role. |
| 3.2 | Execution engine | ◐ | §8 implemented exactly, for one draw (`draws.invoice_draw`). The in-flight row is committed before the POST. Sequential multi-group execution is unbuilt. |
| 3.3 | In-flight resolution | ◐ | `GET /billing/in-flight` lists unresolved rows; `POST /billing/runs/{run_id}/items/{item_id}/resolve` links or marks failed, audit-logged. The candidate-invoice picker is not built — the operator pastes the id from Harvest. |
| 3.4 | Post-run reconciliation | ○ | Variance is computed and stored per row at creation; there is no run-level report or threshold flag yet. |
| 3.5 | Execute command | ○ | Monthly-run execution. Needs the subset filter below before its first live use. |

**Milestone: first live run. Create drafts for a subset of clients only — needs a `--only-group` equivalent in the API/UI, since the UI is the only surface.**

### Phase 4 — Fixed Fee, Recurring & Retainers

| # | Task | Acceptance criteria |
|---|---|---|
| 4.1 | Recurring line-item resolver | Selects items where `run_month` falls within `effective_from`/`effective_to`. Renders `{period_label}` tokens. Covers both flat monthly fees and retainers. |
| 4.2 | Scheduled draw resolver | Selects `fixed_fee_schedule_items` where `scheduled_month = run_month` and `invoiced_run_id IS NULL`. |
| 4.3 | Free-form payload builder | Builds `line_items` arrays. Validates `kind` against cached invoice item categories. |
| 4.4 | Schedule consumption | On successful creation, stamps `invoiced_run_id` on consumed schedule items within the same transaction boundary as the ledger update. |
| 4.5 | Manual group handling | `manual` groups are skipped in planning with no ledger row and no estimate, and suppress `UNMAPPED_PROJECT` for their projects. |
| 4.6 | Fixed-fee flags | `SCHEDULE_GAP`, `SCHEDULE_EXHAUSTED`, `FIXED_FEE_TIME_NOISE`, `TYPE_MISMATCH`. |

### Phase 5 — Operational Polish

| # | Task |
|---|---|
| 5.1 | Prior-month comparison in pre-flight |
| 5.2 | Plan export to CSV/markdown for record-keeping |
| 5.3 | Run history view |
| 5.4 | Dry-run mode that prints exact HTTP requests without sending |

### Phase 6 — Deferred (not in scope now, but don't design them out)

- **Off-cycle / mid-month invoices.** Should reuse the same group config with an ad-hoc period override rather than a parallel code path. Keep the date resolver injectable so an arbitrary period can be supplied.
- **Milestone-driven fixed fee.** Currently handled by hand via `manual` groups. When automated, it becomes a fourth billing type with an operator-set release date on each draw — the `fixed_fee_schedule_items` table already has the right shape, needing only a nullable `released_at` and a nullable `scheduled_month`.
- **Retainer overage automation.** See §10.
- **Multi-currency handling** beyond the mismatch flag.

---

## 10. Retainers

Retainers are set up in Harvest as fixed-fee projects and are billed as a standard flat monthly fee. **They are therefore just `recurring_monthly` groups and require no distinct billing type, no distinct code path, and no separate phase.** Configure them as `recurring_monthly` with `billing_timing = advance` and a single static line item.

Harvest's first-class **retainer object** is not used and must not be touched. Do not pass `retainer_id` on invoice creation. The API cannot manage retainer drawdown in any case — the only supported action is adding funds.

Two operational consequences worth building for:

**Overages are added by hand.** The system creates the flat monthly draft; the operator opens it in Harvest and adds overage lines before sending. This works because drafts are freely editable. But it means the `actual_amount` captured in the ledger at creation time is the **pre-edit** amount, and the variance report will show a false zero even when the final invoice was larger. Record the amount at creation, label the field clearly as the created amount rather than the sent amount, and don't build reporting that assumes it reflects what the client was ultimately billed.

**Retainer projects accumulate permanent time noise.** Billable hours tracked to a fixed-fee project remain flagged as uninvoiced in Harvest forever, since Harvest has no way to relate tracked time to a fixed fee. On a long-running retainer this grows without bound. This is why `FIXED_FEE_TIME_NOISE` is `info` and not `warning` — it will fire on every retainer group on every run, and it is never actionable through this system. If it becomes visually noisy in the pre-flight, suppress it behind a `--verbose` flag rather than removing it.

---

## 11. Open Items for the Operator

Things the spec can't determine and that need a decision or a data-gathering pass before or during Phase 1:

1. **Groups with no uninvoiced time** — skip silently, or create a zero invoice? Spec defaults to skip with a warning; confirm.
2. **Non-enum payment terms** — do any clients actually need net 10, net 20, or day-of-month terms? If none do, `custom_net_days` can be dropped and the due-date resolver gets much simpler.
3. **Straggler time policy** — when time for June is entered in August, do you want it swept into the current invoice, or handled manually? Spec currently flags it and leaves it. Sweeping it means widening the `from` date, which changes the invoice's stated service period.
4. **Time rounding** — confirm whether rounding is enabled in Harvest preferences, which sets `USE_ROUNDED_HOURS` in `app/services/billing/rates.py`. Answered: off. Nothing detects drift if it is later switched on in Harvest.
5. **Invoice numbering** — let Harvest auto-generate (recommended), or does anything downstream depend on a controlled sequence?
6. **`summary_type` per client** — gather the actual per-client preference while building Phase 1 config. Getting this wrong is the most likely source of "the client complained about the invoice format."
7. **Inventory of manual projects** — while building Phase 1 config, every milestone-billed project needs a `manual` group created for it. Skipping this means the first several runs will be buried in `UNMAPPED_PROJECT` errors.

---

## 12. Amendments (implementation, Phases 0–2)

Recorded during implementation of `.agent/plans/21.harvest-invoicing-preflight.md`.
Where these disagree with the sections above, these win — the sections above are
left unedited so the original reasoning stays legible.

### 12.1 Defects corrected

| # | Section | Problem | Resolution |
|---|---|---|---|
| 1 | §3, C6 | The partial unique index on `(billing_group_id, run_month)` is unwritable: `run_month` lives on `billing_runs`, and Postgres cannot reference it from an index on `billing_run_items`. | `run_month` is denormalized onto `billing_run_items`. |
| 2 | §3 | "Unique constraint on `harvest_project_id` where the parent group is active" has the same problem — an index on the child cannot see `billing_groups.is_active`. | `group_is_active` is denormalized onto `billing_group_projects`, maintained by triggers on both insert and parent update. |
| 3 | §7, §8 | §8 makes an unresolved `in_flight` row a blocking error, but the flag catalog has no code for it. | Added `UNRESOLVED_IN_FLIGHT` (error). It is the **only** non-overridable error: overriding risks the duplicate invoice the §8 protocol exists to prevent. The UI offers resolution instead of override. |
| 4 | §4.5, §7 | The duplicate guard queries `GET /v2/invoices?client_id=…`, which is scoped to the **client**. A client with more than one billing group has several legitimate invoices in the window, so `EXISTING_HARVEST_INVOICE` would fire on every multi-group client every month — exactly the error fatigue §2.2 warns about. | The guard cross-references `billing_run_items.harvest_invoice_id` and flags only invoices this system did not create. Regression test: `tests/test_billing_planner.py::test_multi_group_client_does_not_false_positive`. |
| 5 | §3, §7 | `MISSING_PO` is defined as "purchase_order configured as required but empty", but no column expresses "required". | Added `billing_groups.requires_purchase_order`. |
| 6 | §7 | `ALREADY_INVOICED_THIS_RUN` was specified but its trigger condition — a `created` ledger row for the group this month — would surface as a raw unique-constraint violation rather than a flag. | The planner checks for it explicitly and skips the group with a readable reason. |

### 12.2 Gaps filled

- **§4.3's rate ladder** ends at "the task assignment rate", which requires
  `GET /v2/projects/{id}/task_assignments` — an endpoint §4 never documents. It
  costs one request per project, so it is fetched during the snapshot and cached
  in `harvest_task_assignments`, keeping it off the rate limiter's hot path
  during planning.
- **`PROJECT_IN_MULTIPLE_GROUPS`** (§7) is now structurally impossible — the
  partial unique index rejects it at write time — so no runtime check exists for
  it. Group configuration returns a 400 naming the conflicting group instead.

### 12.3 Deliberate deviations

- **§6's terminal-readable plan renderer** was not built. The pre-flight is a web
  UI (`ui/src/pages/Invoices/`). The §5.2 CSV/markdown export retains its value
  and is still deferred.
- **Migrations are forward-only**, matching every existing migration in this
  repo, rather than reversible as §9 task 0.4 asks.
- **Approval model.** §5 says "approval is per-group, not per-run". The
  granularity is preserved — the operator selects individual groups — but it is
  carried as one `approvals` row per run whose `executed_payload` lists the
  approved item ids, rather than N separate rows. This satisfies the codebase's
  Unbreakable Rule #1 without putting twenty rows in the inbox every month.

### 12.4 Still open from §11

Unchanged and still needing a decision before the first live run: straggler-time
policy (#3), whether time rounding is enabled in Harvest preferences (#4, sets
`USE_ROUNDED_HOURS`), and per-client `summary_type` preferences (#6).
The spec defaults are implemented for #1 (skip with a warning) and #5 (Harvest
auto-numbers).

---

## 13. Amendment — recurring monthly shipped ahead of Phase 4

`recurring_monthly` planning was pulled forward out of Phase 4 because it is
the shape Frogslayer actually bills monthly: hosting, a management fee, a
tooling fee, and a service fee on a second project, all on one invoice.

**Schema gap closed.** §3 defines neither `recurring_line_items.kind` nor an
equivalent on `fixed_fee_schedule_items`, but §4.7 requires `kind` on every
free-form line item and says it must be validated against the account's
categories. Migration `0025` adds it to both, defaulting to `Service`.

**New concept: placeholder lines.** §10 notes that retainer overages are added
by hand to the draft. The same is true of any amount only knowable after the
fact — hosting pass-through, a tooling fee computed as a percentage of it.
`recurring_line_items.is_placeholder` marks those: the line is created at $0 so
the draft carries the correct description, category, and project, and the
operator fills in the amount in Harvest before sending. Placeholders are
excluded from `planned_amount` and surfaced as `PLACEHOLDER_LINE_ITEMS` (info),
so a deliberately-zero line never reads as a bug — and so the pre-flight total
is honest about being a floor rather than a forecast.

**Flag catalog additions** (beyond §7): `PLACEHOLDER_LINE_ITEMS` (info),
`NO_RECURRING_ITEMS` (warning — skip rather than create an empty invoice),
`LINE_ITEM_OFF_GROUP_PROJECT` (error — a line targeting a project outside the
group would be a 422). `INVALID_ITEM_CATEGORY` from §7 is now implemented, and
is additionally enforced at config-save time so it rarely reaches a run.

**Effective dating is month-granular.** §4.1 says "selects items where
`run_month` falls within `effective_from`/`effective_to`" without fixing the
granularity. Compared as exact dates, an `effective_from` of 2026-08-15 would
skip August — the month the operator meant to start. Both bounds are therefore
truncated to month before comparison, and the UI uses month pickers so the
ambiguity can't be entered at all.

**Approval defaults to no, and is persisted.** §6 describes per-group approval
in the pre-flight without saying where that decision lives or how it starts. It
was first built as component state pre-checked for every group without an error
flag — so a reload silently discarded the review, and a distracted operator
could execute a set of invoices nobody had actually looked at. Approval is now a
`planned` → `approved` status transition on `billing_run_items`, stamped with
who and when (migration `0026`). Nothing is approved until a human says so.
Error-severity flags block approval until an override is recorded — the override
is persisted too, and is sticky, so un-approving does not force the operator to
re-accept a flag they already accepted. `UNRESOLVED_IN_FLIGHT` remains
non-overridable, enforced in `review.py` rather than only in the UI.

**Fixed-fee draws are release-gated and billed off-cycle.** §4.2 specified a
dated-draw resolver — "select schedule items where `scheduled_month = run_month`"
— which assumes the schedule is a billing trigger and that draws ride the monthly
run. Neither holds. A contract commits to dates, but whether a draw bills depends
on delivery being accepted, which slips; and a milestone accepted on the 12th is
invoiced on the 12th, not at month end.

So there is one model, not two: **every draw is release-gated**, and there is no
separate milestone billing type. `scheduled_date` (renamed from
`scheduled_month`, migration `0027` — a schedule commits to days) drives the
overdue prompt and forecasting only. A draw becomes billable when a human
confirms delivery, and is then billed individually, for exactly its scheduled
amount, as a `kind='draw'` run holding one ledger row.

This also settles a structural question: `billing_group_projects_one_active_group`
allows a project in exactly one active group, so a contract mixing dated and
milestone draws — "30% on signing, 40% at UAT, 30% at go-live" — cannot be split
across two groups. The trigger has to live on the draw.

A draw's invoice is **computed, never staged**: `GET /billing/draws/{id}/preview`
returns the exact POST body and writes nothing, so a ready draw is reviewed in
place in the queue and created from there. There is no intermediate "prepared"
step. Persisting an invoice ahead of the Harvest draft would invent a state that
is neither planned-in-a-run nor real, and then require a way to unwind it; the
ledger row belongs to execution, written immediately before the POST as §8
already requires.

The four derived states are therefore `pending`, `ready`, `in_flight`, and
`invoiced` — `in_flight` meaning a live ledger row exists, which only execution
can produce. It locks the draw's billable fields and keeps a draw mid-write out
of the billable queue.

Consequences: `billing_timing` does not apply (a draw covers no period, so
`period_start`/`period_end` are null and `resolve_period` is never called);
`{draw_description}`, `{draw_number}`, and `{draw_count}` join the subject-template tokens because `{period_label}`
has nothing to render; C6 splits into two partial unique indexes, since the unit
of double-billing risk is the *draw* for draws and the *period* for everything
else (migration `0028`).

**Flag catalog changes:** `SCHEDULE_GAP` is retired — "no schedule item for this
month" can never fire under this model. Added: `DRAW_OVERDUE` (warning),
`DRAWS_AWAITING_RELEASE` (info), `DRAWS_READY_TO_BILL` (info). §6's Phase 6
"milestone-driven fixed fee" is now the primary model, not a deferred variant.

---

## 14. Amendment — placeholder amounts are entered on the pre-flight, not in Harvest

Supersedes §10 ("Overages are added by hand… the operator opens it in Harvest
and adds overage lines before sending") and §13 ("the operator fills in the
amount in Harvest before sending") on this one point. Both sections are left
unedited above so the original reasoning stays legible.

**Why it moved.** §13 introduced `is_placeholder` so a deliberately-zero line
would not read as a bug in the pre-flight estimate. That solved the reporting
problem and left the operational one: the last step of the invoice lived in a
system this one cannot read, so nothing could notice when it was skipped. The
failure is quiet and always in the same direction — the invoice goes out short,
and `planned_amount` still reads as correct, *because* the placeholder was
excluded from it. Nothing downstream disagrees.

So the decision moves to the pre-flight, and approval is gated on it. Two
answers, both decisions:

- **an amount** — `unit_price`, with an optional `quantity` override for the
  cases that are quantity-shaped (12 overage hours at $175, not a flat sum);
- **omit for this month** — the line leaves this month's payload and the
  template stays put, so it returns next month.

**Omitting is the half §10 and §13 never modelled.** A retainer overage is
configured precisely so that it comes up every month, and most months there is
no overage. Without an omit, the only ways to clear the gate would be to bill $0
(a line on the client's invoice saying nothing happened) or to delete the line
from config (losing the reminder). "No overage in August" is a decision worth
recording, and the `note` field is where the operator says why.

**The gate is not a flag, and not overridable.** `PLACEHOLDER_LINE_ITEMS` stays
`info` and stays a frozen record of what the plan contained. Making it `error`
would have reused §7's existing machinery, but it would also have handed over
the `error_override` escape hatch — and an override is exactly the click that
loses the invoice line. It would additionally have required rewriting flags
after plan time, breaking them as a faithful record. The block therefore lives
in `review.py`, derived live from the ledger row's own `estimated_line_items`.
This makes it the second non-overridable gate after `UNRESOLVED_IN_FLIGHT`, and
like that one the UI offers *resolution* where it would otherwise offer
*override*.

**A resolution is a fact about a month, not about a run.** Keyed on
`(recurring_line_item_id, run_month)`, so it survives Re-plan — which matters
because Re-plan is the ordinary response to fixing a config problem, and
retyping every amount afterwards would reintroduce the forgetting this closes.
Keying it there required making `recurring_line_items.id` stable across a group
save; it had been delete-and-reinsert, so editing one fee would have silently
discarded the month's other amounts.

**What this does not change.** §10 is still right that `actual_amount` is the
amount **at creation** and not what the client was ultimately billed: a Harvest
draft remains freely editable after we create it, and nothing here detects a
later edit. This narrows the gap — the amounts that used to be typed into the
draft are now settled before it exists — rather than closing it. Do not build
reporting that assumes `actual_amount` is what was sent.

**Deferred, and cheap when wanted.** The `(line, month)` key already allows a
value to be recorded before a run exists ("next month's hosting is $1,240"),
from the group page. That needs a surface, not a schema change. §6's Phase 6
"Retainer overage automation" is no longer the deferred item it was — the
overage is now entered here; only *deriving* it automatically remains open.
