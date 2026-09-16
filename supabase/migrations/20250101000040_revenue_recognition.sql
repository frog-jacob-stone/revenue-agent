-- Revenue recognition, moved off Airtable and into this system.
--
-- Recognized revenue was the last subsystem living outside Postgres. Three
-- Airtable tables held it: a Clients and a Projects table that were a Harvest
-- mirror plus three hand-typed fields, and a Revenue table that was the actual
-- ledger. The first two are replaced entirely by `revenue_project_config`
-- below — the mirror was only ever a way to get Harvest ids into a base that
-- could not reach Harvest itself, and this system already has them.
--
--
-- WHY NOT COLUMNS ON `harvest_projects`
--
-- Because that table is a read-through cache, documented in migration 0024 as
-- "never authoritative; safe to truncate and re-sync". A `contracted_fees`
-- typed by an operator would not survive the next snapshot refresh.
--
-- WHY NOT A NEW `projects` TABLE
--
-- Because the Harvest project already is the project. `excluded_harvest_clients`
-- (0031), `forecast_project_schedule` (0032) and `billing_group_projects` (0024)
-- all key on a bare Harvest id with no FK, precisely so operator intent outlives
-- a cache rebuild. This follows them. A second project entity would need
-- reconciling against the first forever, and would buy nothing: every consumer
-- here already joins on the Harvest id.
--
--
-- THE LEDGER SHAPE: THE PERIOD AMOUNT IS THE FACT
--
-- Airtable stored `Total Recognized Revenue` (cumulative-to-date) as the column
-- and derived `Revenue Delta` (the period amount) as a formula. That is
-- backwards, and it was an Airtable constraint rather than a modelling choice.
--
-- Here `recognized_amount` — revenue recognized *in this period* — is the only
-- monetary fact stored. Cumulative-to-date is SUM() of it, computed at read
-- time in app/services/revenue_ledger.py. Deliberately not a view: there are no
-- views anywhere in this schema, and the established pattern for aggregation is
-- SQL inline in a service query (see billing/planner.py, billing/invoices.py).
--
-- This is not only tidier. Every billing type's computation naturally produces
-- a cumulative number (fixed fee = contracted x percent complete; T&M = Harvest
-- invoiced-to-date), so a run computes:
--
--     period_amount = cumulative_target(this period) - SUM(all prior periods)
--
-- which means a correction to a closed month absorbs into the next open month
-- instead of silently restating history. Storing the cumulative figure would
-- have made that a manual reconciliation every time.
--
-- Dropped from the Airtable field set as pure derivations: Total Recognized
-- Revenue, Total Projected Hours (= logged + scheduled), blended rate, the
-- Project Id link, and the Archive checkbox (`harvest_projects.is_active` plus
-- `excluded_harvest_clients` already answer that).

begin;

-- How revenue is recognized for a project. Five values, three computations:
-- fixed_fee is percent-complete, retainer is manual, and the other three are
-- recognized as invoiced. They stay distinct anyway — the computation is not
-- the only thing the label is for, and revenue-per-project-type reporting
-- groups on exactly this.
--
-- Deliberately NOT split into a `recognition_method` + `revenue_category` pair.
-- The split would add a way to configure a combination that means nothing
-- (a retainer recognized percent-complete) and two fields to keep in step, to
-- express something no one has asked to express.
--
-- Named `revenue_type`, not `billing_type`, because `billing_type` already
-- exists on `billing_groups` and means a different thing: how a client is
-- *invoiced* (time_and_materials, fixed_fee_schedule, recurring_monthly,
-- manual). A project can be invoiced on a draw schedule and recognized
-- percent-complete. Conflating the two vocabularies would be a bug waiting.
create type revenue_type as enum (
    'fixed_fee',
    'time_and_materials',
    'msf',
    'hosting',
    'retainer'
);

-- draft      computed, editable, invisible to every report
-- recognized finalized; the entries are now the ledger
-- abandoned  discarded, frees the month to be planned again
create type revenue_run_status as enum ('draft', 'recognized', 'abandoned');


-- ── Per-project recognition config ──────────────────────────────────────────
--
-- Replaces the three hand-typed Airtable Projects columns (Billing Type,
-- Contracted Fees, Client Id). Client Id has no successor: it was a link field
-- Airtable needed to group projects under a client, and `harvest_projects`
-- already carries `client_id`.
--
-- Rows are created by a human, not by a sync. A project with no row here is
-- unconfigured, and a run refuses to proceed until every in-scope project has
-- one — the same "fix it and re-run" gate the Airtable flow enforced, moved
-- from a validation loop into the schema.

create table revenue_project_config (
    -- No FK to harvest_projects. See the header: this is operator intent and
    -- must survive a cache truncate.
    harvest_project_id bigint primary key,
    revenue_type       revenue_type not null,
    -- The contract value, for percent-complete recognition. Null is legitimate
    -- for every type but fixed_fee, which cannot compute without it.
    contracted_fees    numeric(12,2),
    -- Why this project is recognized the way it is, in a human's words. The
    -- value of the row a year later when someone asks why hosting revenue
    -- lands the way it does.
    notes              text,
    created_at         timestamptz not null default now(),
    updated_at         timestamptz not null default now(),
    created_by         text not null,
    updated_by         text not null,
    -- Enforced here rather than in the service, because a fixed-fee project
    -- with no contract value does not compute to zero — it computes to
    -- nonsense, silently, for every future month.
    constraint fixed_fee_needs_contracted_fees
        check (revenue_type <> 'fixed_fee' or contracted_fees is not null)
);

comment on table revenue_project_config is
    'Per-project revenue recognition configuration: how a project recognizes '
    'and, for fixed fee, against what contract value. Owned by this system and '
    'keyed on the Harvest project id; not synced from anywhere.';


-- ── Runs ────────────────────────────────────────────────────────────────────
--
-- One run per month, planned and finalized by an operator (ADR-0004). Unlike
-- `billing_runs` there is no external write at the end of this — finalizing is
-- a status transition inside Postgres — so there is no in-flight state, no
-- unknown outcome, and nothing to reconcile against a vendor afterwards.

create table revenue_runs (
    id            uuid primary key default gen_random_uuid(),
    -- First of the month being recognized, matching `billing_runs.run_month`.
    -- Airtable used the last day of the month; that was a convention its date
    -- formulas wanted and it does not carry over.
    period_month  date not null
        check (period_month = date_trunc('month', period_month)::date),
    status        revenue_run_status not null default 'draft',
    created_at    timestamptz not null default now(),
    created_by    text not null,
    finalized_at  timestamptz,
    finalized_by  text,
    abandoned_at  timestamptz,
    abandoned_by  text
);

-- The duplicate-run guard, structural. Replaces a read of Airtable's most
-- recent entry followed by a date comparison — which could only ever catch the
-- common case, and raced with itself if two people ran it at once.
--
-- Abandoned runs are excluded so a discarded month can be planned again.
create unique index revenue_runs_one_live_per_month
    on revenue_runs(period_month) where status <> 'abandoned';

comment on table revenue_runs is
    'One revenue recognition run per month. Operator-initiated: planned, '
    'reviewed and finalized by a human (ADR-0004). Finalizing writes nothing '
    'outside this database.';


-- ── Entries — the ledger ────────────────────────────────────────────────────

create table revenue_entries (
    id                   uuid primary key default gen_random_uuid(),
    revenue_run_id       uuid not null references revenue_runs(id) on delete cascade,
    -- Denormalized from the run, as `billing_run_items.run_month` is, so the
    -- per-project period queries that drive every report and every prior-period
    -- sum do not join back to the run to find out when they happened.
    period_month         date not null,
    harvest_project_id   bigint not null,
    -- Snapshotted, not joined. Projects get renamed, and a ledger row should
    -- read the way it read when it was recognized. Same reasoning as
    -- `billing_group_projects.harvest_project_name`.
    harvest_project_name text not null,
    -- Snapshotted for the same reason: config changes, and how this row was
    -- computed is a fact about the past.
    revenue_type         revenue_type not null,

    -- THE fact. Revenue recognized in this period, for this project. Everything
    -- cumulative is SUM() over this; see the header.
    recognized_amount    numeric(12,2) not null,

    -- ── Evidence ────────────────────────────────────────────────────────────
    -- How the number was arrived at. Kept so a figure can be defended months
    -- later without re-querying Harvest for a period that has since moved, not
    -- because anything reports on them.
    --
    -- `computed_amount` is what the system calculated, always, even when an
    -- operator overrode it. The pair is the whole point: "what did it say, what
    -- did we book, and who changed it" is one row.
    computed_amount      numeric(12,2) not null,

    -- Hours logged **in this period**, not cumulative-to-date. Same convention
    -- as `recognized_amount`, and for the same reason — the two are divided by
    -- each other to get revenue per billable hour, and mixing a period
    -- numerator with a cumulative denominator produces a number that falls
    -- every month on a healthy project.
    --
    -- Harvest answers cumulatively (`get_time_entries` sums to a date), and
    -- Airtable stored it cumulatively too, so both the runner and the backfill
    -- difference against the prior period to get here.
    logged_hours         numeric(10,2),

    -- Forward-looking and therefore NOT a period quantity: hours still booked
    -- in Forecast as of the period end. A snapshot, like `invoiced_to_date`
    -- and `contracted_fees` below. Summing it across months is meaningless.
    scheduled_hours      numeric(10,2),

    percent_complete     numeric(6,4),
    contracted_fees      numeric(12,2),
    invoiced_to_date     numeric(12,2),
    notes                text,

    -- ── Operator override ───────────────────────────────────────────────────
    -- Retainers compute to zero by design and always need a human number, but
    -- any entry can be overridden while its run is a draft. Null `overridden_at`
    -- means recognized_amount = computed_amount and nobody intervened.
    override_reason      text,
    overridden_by        text,
    overridden_at        timestamptz,

    unique (revenue_run_id, harvest_project_id)
);

-- Drives both the prior-period sum a run needs and the per-project history the
-- Revenue tab renders.
create index revenue_entries_project_period_idx
    on revenue_entries(harvest_project_id, period_month);

-- No unique index on (harvest_project_id, period_month): it would be redundant.
-- `revenue_runs_one_live_per_month` allows one live run per month and the
-- unique above allows one entry per project per run, so a project already
-- cannot have two live entries in a period. Abandoned runs may duplicate, and
-- every reader filters on the run being recognized.

comment on table revenue_entries is
    'The revenue recognition ledger: one row per project per month. '
    '`recognized_amount` is the revenue recognized in that period and is the '
    'only monetary fact stored — cumulative-to-date is summed at read time. '
    'Rows belonging to a draft or abandoned run are not part of the ledger.';


alter table revenue_project_config enable row level security;
alter table revenue_runs enable row level security;
alter table revenue_entries enable row level security;

create policy revenue_project_config_service_all on revenue_project_config
    for all to service_role using (true) with check (true);

create policy revenue_runs_service_all on revenue_runs
    for all to service_role using (true) with check (true);

create policy revenue_entries_service_all on revenue_entries
    for all to service_role using (true) with check (true);

commit;
