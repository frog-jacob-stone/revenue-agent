-- Harvest invoicing — data model for automated monthly draft-invoice creation.
-- Specified by docs/prd/harvest-invoicing-requirements.md §3.
-- Plan: .agent/plans/21.harvest-invoicing-preflight.md
--
-- Three groups of tables:
--   1. harvest_* — a read-through cache of the Harvest account, refreshed by
--      the snapshot step. Never authoritative; safe to truncate and re-sync.
--   2. billing_groups + children — our config. Harvest has no concept of a
--      billing group; it lives entirely here.
--   3. billing_runs / billing_run_items / billing_run_flags — the ledger.
--
-- Two constraints in this file are load-bearing rather than decorative, and
-- both required denormalizing a column the PRD left on the parent table:
--   * billing_group_projects.group_is_active — so "one project belongs to at
--     most one ACTIVE group" can be a real partial unique index. Postgres
--     cannot reference billing_groups.is_active from an index on the child.
--   * billing_run_items.run_month — so "a group is invoiced at most once per
--     run month" (constraint C6) can be a real partial unique index. The PRD
--     put run_month on billing_runs, where an index on billing_run_items
--     cannot see it.

begin;

-- ── Enums ───────────────────────────────────────────────────────────────────

create type billing_type as enum (
    'time_and_materials',
    'fixed_fee_schedule',
    'recurring_monthly',
    'manual'
);

create type billing_timing as enum ('arrears', 'advance');

create type payment_term as enum (
    'upon receipt', 'net 15', 'net 30', 'net 45', 'net 60', 'custom'
);

-- Harvest uses different vocabularies for the two summaries: time supports
-- `task`, expenses support `category`. Deliberately two enums, not one.
create type time_summary_type as enum ('project', 'task', 'people', 'detailed');
create type expense_summary_type as enum ('project', 'category', 'people', 'detailed');

create type billing_run_status as enum (
    'planning', 'awaiting_approval', 'executing', 'completed', 'failed', 'abandoned'
);

create type billing_run_item_status as enum (
    'planned', 'approved', 'skipped', 'in_flight', 'created', 'failed', 'abandoned'
);

create type billing_flag_severity as enum ('error', 'warning', 'info');


-- ── 1. Harvest snapshot cache ───────────────────────────────────────────────

create table harvest_clients (
    harvest_id  bigint primary key,
    name        text not null,
    currency    text,
    is_active   boolean not null default true,
    synced_at   timestamptz not null default now()
);

create table harvest_projects (
    harvest_id        bigint primary key,
    name              text not null,
    code              text,
    client_id         bigint not null,
    client_name       text,
    client_currency   text,
    is_billable       boolean not null default false,
    is_fixed_fee      boolean not null default false,
    bill_by           text,
    hourly_rate       numeric(12,2),
    fee               numeric(12,2),
    budget            numeric(12,2),
    budget_by         text,
    budget_is_monthly boolean,
    is_active         boolean not null default true,
    synced_at         timestamptz not null default now()
);

create index harvest_projects_client_id_idx on harvest_projects(client_id);

create table harvest_invoice_item_categories (
    harvest_id  bigint primary key,
    name        text not null,
    synced_at   timestamptz not null default now()
);

-- Last rung of the rate-resolution ladder in the T&M estimator. Cached at
-- snapshot time so planning doesn't spend one request per project against the
-- general rate-limit bucket.
create table harvest_task_assignments (
    harvest_id        bigint primary key,
    harvest_project_id bigint not null,
    task_id           bigint not null,
    task_name         text,
    hourly_rate       numeric(12,2),
    is_active         boolean not null default true,
    synced_at         timestamptz not null default now()
);

create index harvest_task_assignments_project_idx
    on harvest_task_assignments(harvest_project_id);


-- ── 2. Billing configuration ────────────────────────────────────────────────

create table billing_groups (
    id                      uuid primary key default gen_random_uuid(),
    name                    text not null,
    harvest_client_id       bigint not null,
    harvest_client_name     text,
    billing_type            billing_type not null,
    billing_timing          billing_timing not null default 'arrears',
    payment_term            payment_term not null default 'net 30',
    custom_net_days         int,
    time_summary_type       time_summary_type,
    include_expenses        boolean not null default false,
    expense_summary_type    expense_summary_type,
    attach_receipts         boolean not null default false,
    subject_template        text not null default '{client_name} — {period_label}',
    notes_template          text,
    purchase_order          text,
    requires_purchase_order boolean not null default false,
    currency                text,
    is_active               boolean not null default true,
    created_at              timestamptz not null default now(),
    updated_at              timestamptz not null default now(),

    constraint billing_groups_custom_net_days_required
        check (payment_term <> 'custom' or custom_net_days is not null)
);

create index billing_groups_client_idx on billing_groups(harvest_client_id);
create index billing_groups_active_idx on billing_groups(is_active) where is_active;

comment on column billing_groups.requires_purchase_order is
    'When true and purchase_order is null/empty, pre-flight raises MISSING_PO.';

create table billing_group_projects (
    billing_group_id     uuid not null references billing_groups(id) on delete cascade,
    harvest_project_id   bigint not null,
    harvest_project_name text,
    sort_order           int not null default 0,
    -- Denormalized from the parent, maintained by trigger. Exists solely so
    -- the partial unique index below can be a real constraint.
    group_is_active      boolean not null default true,

    primary key (billing_group_id, harvest_project_id)
);

-- THE double-billing guard: a project may belong to at most one ACTIVE group.
create unique index billing_group_projects_one_active_group
    on billing_group_projects(harvest_project_id) where group_is_active;

create or replace function billing_group_projects_sync_active()
returns trigger language plpgsql as $$
begin
    if new.is_active is distinct from old.is_active then
        update billing_group_projects
           set group_is_active = new.is_active
         where billing_group_id = new.id;
    end if;
    return new;
end;
$$;

create trigger billing_groups_sync_child_active
    after update of is_active on billing_groups
    for each row execute function billing_group_projects_sync_active();

-- Keep the denormalized flag correct for rows inserted against an already
-- inactive group.
create or replace function billing_group_projects_set_active()
returns trigger language plpgsql as $$
begin
    select is_active into new.group_is_active
      from billing_groups where id = new.billing_group_id;
    return new;
end;
$$;

create trigger billing_group_projects_default_active
    before insert on billing_group_projects
    for each row execute function billing_group_projects_set_active();


-- Fixed-fee draw schedules and recurring line items. Created now so config can
-- be entered ahead of Phase 4; no planning logic consumes them yet.
create table fixed_fee_schedule_items (
    id                 uuid primary key default gen_random_uuid(),
    billing_group_id   uuid not null references billing_groups(id) on delete cascade,
    harvest_project_id bigint not null,
    sequence           int not null,
    description        text not null,
    amount             numeric(12,2) not null,
    scheduled_month    date not null,
    invoiced_run_id    uuid,
    created_at         timestamptz not null default now(),

    constraint fixed_fee_scheduled_month_is_first_of_month
        check (extract(day from scheduled_month) = 1)
);

create index fixed_fee_schedule_items_group_idx
    on fixed_fee_schedule_items(billing_group_id);
create index fixed_fee_schedule_items_unconsumed_idx
    on fixed_fee_schedule_items(billing_group_id, scheduled_month)
    where invoiced_run_id is null;

create table recurring_line_items (
    id                 uuid primary key default gen_random_uuid(),
    billing_group_id   uuid not null references billing_groups(id) on delete cascade,
    harvest_project_id bigint not null,
    description        text not null,
    quantity           numeric not null default 1,
    unit_price         numeric(12,2) not null,
    sort_order         int not null default 0,
    effective_from     date,
    effective_to       date,
    created_at         timestamptz not null default now()
);

create index recurring_line_items_group_idx on recurring_line_items(billing_group_id);


-- ── 3. The ledger ───────────────────────────────────────────────────────────

create table billing_runs (
    id             uuid primary key default gen_random_uuid(),
    run_month      date not null,
    status         billing_run_status not null default 'planning',
    plan_snapshot  jsonb not null default '{}'::jsonb,
    -- Phase 3 hook: the single approvals row whose executed_payload carries
    -- the operator's per-group selection. Null until execution ships.
    approval_id    uuid references approvals(id) on delete set null,
    created_at     timestamptz not null default now(),
    approved_at    timestamptz,
    executed_at    timestamptz,
    completed_at   timestamptz,

    constraint billing_runs_run_month_is_first_of_month
        check (extract(day from run_month) = 1)
);

create index billing_runs_run_month_idx on billing_runs(run_month desc);
create index billing_runs_status_idx on billing_runs(status);

create table billing_run_items (
    id                     uuid primary key default gen_random_uuid(),
    billing_run_id         uuid not null references billing_runs(id) on delete cascade,
    billing_group_id       uuid not null references billing_groups(id),
    -- Denormalized from billing_runs so C6 can be a real index (see header).
    run_month              date not null,
    status                 billing_run_item_status not null default 'planned',
    planned_amount         numeric(12,2) not null default 0,
    planned_payload        jsonb not null default '{}'::jsonb,
    estimated_line_items   jsonb not null default '[]'::jsonb,
    prior_amount           numeric(12,2),
    issue_date             date,
    due_date               date,
    period_start           date,
    period_end             date,
    harvest_invoice_id     bigint,
    harvest_invoice_number text,
    -- The amount at creation time. NOT what the client was ultimately billed:
    -- drafts are freely edited in Harvest before sending (PRD §10).
    actual_amount          numeric(12,2),
    variance               numeric(12,2),
    error_message          text,
    skip_reason            text,
    created_at             timestamptz not null default now(),
    updated_at             timestamptz not null default now(),

    constraint billing_run_items_one_per_group_per_run
        unique (billing_group_id, billing_run_id)
);

-- Constraint C6, for real: at most one live ledger row per group per month.
-- Terminal-but-not-successful states are excluded so a failed or abandoned
-- attempt can be re-planned.
create unique index billing_run_items_one_live_per_month
    on billing_run_items(billing_group_id, run_month)
    where status not in ('failed', 'skipped', 'abandoned');

create index billing_run_items_run_idx on billing_run_items(billing_run_id);
create index billing_run_items_invoice_idx
    on billing_run_items(harvest_invoice_id) where harvest_invoice_id is not null;
-- Drives the unresolved-in-flight block on the next planning run.
create index billing_run_items_in_flight_idx
    on billing_run_items(billing_group_id) where status = 'in_flight';

create table billing_run_flags (
    id                  uuid primary key default gen_random_uuid(),
    billing_run_id      uuid not null references billing_runs(id) on delete cascade,
    -- Null for run-level flags (e.g. UNMAPPED_PROJECT), which belong to no group.
    billing_run_item_id uuid references billing_run_items(id) on delete cascade,
    code                text not null,
    severity            billing_flag_severity not null,
    message             text not null,
    context             jsonb not null default '{}'::jsonb,
    created_at          timestamptz not null default now()
);

create index billing_run_flags_run_idx on billing_run_flags(billing_run_id);
create index billing_run_flags_item_idx on billing_run_flags(billing_run_item_id);


-- ── RLS ─────────────────────────────────────────────────────────────────────
-- The FastAPI backend uses a service_role asyncpg pool (RLS-bypassing). These
-- policies block the anon/PostgREST path, matching 20250101000018_enable_rls_gaps.sql.

alter table harvest_clients                 enable row level security;
alter table harvest_projects                enable row level security;
alter table harvest_invoice_item_categories enable row level security;
alter table harvest_task_assignments        enable row level security;
alter table billing_groups                  enable row level security;
alter table billing_group_projects          enable row level security;
alter table fixed_fee_schedule_items        enable row level security;
alter table recurring_line_items            enable row level security;
alter table billing_runs                    enable row level security;
alter table billing_run_items               enable row level security;
alter table billing_run_flags               enable row level security;

create policy harvest_clients_service_all on harvest_clients
    for all to service_role using (true) with check (true);
create policy harvest_projects_service_all on harvest_projects
    for all to service_role using (true) with check (true);
create policy harvest_invoice_item_categories_service_all on harvest_invoice_item_categories
    for all to service_role using (true) with check (true);
create policy harvest_task_assignments_service_all on harvest_task_assignments
    for all to service_role using (true) with check (true);
create policy billing_groups_service_all on billing_groups
    for all to service_role using (true) with check (true);
create policy billing_group_projects_service_all on billing_group_projects
    for all to service_role using (true) with check (true);
create policy fixed_fee_schedule_items_service_all on fixed_fee_schedule_items
    for all to service_role using (true) with check (true);
create policy recurring_line_items_service_all on recurring_line_items
    for all to service_role using (true) with check (true);
create policy billing_runs_service_all on billing_runs
    for all to service_role using (true) with check (true);
create policy billing_run_items_service_all on billing_run_items
    for all to service_role using (true) with check (true);
create policy billing_run_flags_service_all on billing_run_flags
    for all to service_role using (true) with check (true);

commit;
