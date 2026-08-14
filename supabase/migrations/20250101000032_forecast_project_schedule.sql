-- Projected end dates, from Forecast.
--
-- Harvest knows when a project was *planned* to end (`ends_on`, editable and
-- often stale). Forecast knows when people are actually booked on it. The last
-- day someone is scheduled is the delivery forecast, and the gap between the
-- two is the thing worth looking at — several live projects are booked months
-- past their Harvest end date.
--
-- Derived, not raw. Forecast returns ~7,700 assignment rows for a five-year
-- window; storing them all would buy staffing analytics nobody has asked for.
-- What the Projects tab needs is one date per project, so that is what is
-- cached. The raw endpoint is still there if a later feature wants it.
--
-- Keyed on the *Harvest* project id, not the Forecast one. Forecast projects
-- carry a `harvest_id` and that is the id every other table here speaks, so the
-- join is resolved once at sync time rather than at every read.

create table forecast_project_schedule (
    harvest_project_id  bigint primary key,
    forecast_project_id bigint not null,
    -- The last day a person is booked. Null is a real answer, not a gap:
    -- hosting and support retainers have nobody scheduled by design.
    last_scheduled_on   date,
    -- Distinguishes "synced, genuinely nothing scheduled" from "never synced",
    -- which a null date alone cannot.
    assignment_count    integer not null default 0,
    synced_at           timestamptz not null default now()
);

comment on table forecast_project_schedule is
    'Per-project delivery forecast derived from Forecast assignments: the last '
    'day a person is scheduled. Cache — safe to truncate and re-sync.';

alter table forecast_project_schedule enable row level security;

create policy forecast_project_schedule_service_all on forecast_project_schedule
    for all to service_role using (true) with check (true);
