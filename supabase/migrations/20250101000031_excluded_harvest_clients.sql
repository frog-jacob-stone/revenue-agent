-- Harvest clients that are not clients.
--
-- Frogslayer is the motivating case: our own company is a Harvest client, and
-- its projects (Time Off, R&D, internal products like Olympus and Trident) are
-- not engagements. They were being suppressed by hand — a `manual` billing
-- group named "Frogslayer - Exclusion" existed for no reason other than to stop
-- reconciliation flagging two internal projects as unmapped. That is
-- project-level, so it needed maintenance every time an internal project was
-- created.
--
-- Keyed on the *client*, so one row covers every present and future project
-- underneath it.
--
-- A table this system owns rather than a flag on `harvest_clients`, because
-- that table is a Harvest read-through cache documented as "never
-- authoritative; safe to truncate and re-sync" — operator intent must survive
-- a resync. No FK to it for the same reason: an exclusion is a statement about
-- a Harvest id, and it stays true across a cache rebuild.
--
-- Deliberately not seeded. Which clients are "us" is account-specific, and a
-- hardcoded id (or name) in a migration is exactly the thing this replaces.

create table excluded_harvest_clients (
    harvest_client_id bigint primary key,
    -- Why, in a human's words. Optional, but the reason is the whole value of
    -- the row six months later: "is this us, a defunct test account, or a
    -- client someone hid by mistake?"
    reason            text,
    excluded_at       timestamptz not null default now(),
    excluded_by       text not null
);

comment on table excluded_harvest_clients is
    'Harvest clients treated as not-a-client account-wide: hidden from the '
    'Projects roster and skipped by billing config reconciliation. Owned by '
    'this system, not synced from Harvest.';

alter table excluded_harvest_clients enable row level security;

create policy excluded_harvest_clients_service_all on excluded_harvest_clients
    for all to service_role using (true) with check (true);
