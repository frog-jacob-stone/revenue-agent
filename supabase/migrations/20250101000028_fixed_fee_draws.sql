-- 0028 — fixed-fee draws become billable work.
--
-- A draw is release-gated: the contract's `scheduled_date` commits to a day and
-- drives the overdue prompt, but only a human confirming delivery makes a draw
-- billable. Draws are then billed one at a time, off-cycle — never swept up by
-- the monthly run.
--
-- Three states, derived rather than stored, so there is no status column to
-- drift out of sync with the two facts that define it:
--
--   pending   released_at is null
--   ready     released_at is not null and invoiced_run_id is null
--   invoiced  invoiced_run_id is not null

begin;

-- ── Release state ───────────────────────────────────────────────────────────

alter table fixed_fee_schedule_items
    add column released_at timestamptz,
    add column released_by text;

comment on column fixed_fee_schedule_items.released_at is
    'When a human confirmed delivery. Null until then; this is the only thing
     that makes a draw billable. Never set by the system.';
comment on column fixed_fee_schedule_items.released_by is
    'Email of the human who confirmed delivery.';

-- The queue: everything confirmed but not yet billed.
drop index if exists fixed_fee_schedule_items_unconsumed_idx;
create index fixed_fee_schedule_items_ready_idx
    on fixed_fee_schedule_items(billing_group_id, scheduled_date)
    where released_at is not null and invoiced_run_id is null;

-- ── Ledger linkage, and the double-billing guard ────────────────────────────
--
-- C6 (one live ledger row per group per month) is right for T&M and recurring,
-- where the risk is billing the same *period* twice. It is wrong for draws: a
-- group can legitimately bill two milestones in one calendar month, and the
-- index would block the second. For a draw the unit of duplication risk is the
-- draw itself, so it gets its own index rather than sharing the month's.

create type billing_run_kind as enum ('monthly', 'draw');

alter table billing_runs
    add column kind billing_run_kind not null default 'monthly';

create index billing_runs_kind_idx on billing_runs(kind, run_month desc);

alter table billing_run_items
    add column fixed_fee_schedule_item_id uuid
        references fixed_fee_schedule_items(id) on delete restrict;

comment on column billing_run_items.fixed_fee_schedule_item_id is
    'Set only on draw runs. ON DELETE RESTRICT: a draw with a live ledger row
     cannot be deleted out from under it.';

drop index billing_run_items_one_live_per_month;
create unique index billing_run_items_one_live_per_month
    on billing_run_items(billing_group_id, run_month)
    where fixed_fee_schedule_item_id is null
      and status not in ('failed', 'skipped', 'abandoned');

create unique index billing_run_items_one_live_per_draw
    on billing_run_items(fixed_fee_schedule_item_id)
    where fixed_fee_schedule_item_id is not null
      and status not in ('failed', 'skipped', 'abandoned');

commit;
