-- 0033 — placeholder resolution moves out of Harvest and into the pre-flight.
--
-- Migration 0025 introduced `recurring_line_items.is_placeholder` for the line
-- whose description and category are stable but whose amount is only knowable
-- after the fact — hosting pass-through, a percentage-based tooling fee, a
-- retainer overage. The line went out at $0 and the operator was expected to
-- complete it by hand in the Harvest draft before sending (PRD §10, §13).
--
-- That put the last step in a system this one does not read, so nothing could
-- notice when it was skipped: the invoice goes out short while `planned_amount`
-- still reads as correct, because the placeholder was deliberately excluded from
-- it. This table moves the decision here, where approval can be blocked on it.
--
-- Two things a reader of the schema cannot infer from the table name:
--
--   1. A row only ever applies to a line with `is_placeholder = true`. There is
--      no constraint expressing that — the parent's flag can be toggled after
--      the fact, and a foreign key cannot see a column on the row it points at.
--      Enforced in app/services/billing/placeholders.py; a resolution on a line
--      that is no longer a placeholder is ignored, not an error.
--
--   2. The key is `(line, month)`, not the ledger row. A resolution is a fact
--      about a month — "hosting for August 2026 was $1,240" — so it survives
--      Re-plan, which abandons the run and builds a new one. Keyed on
--      `billing_run_items` instead, the normal act of fixing a config problem
--      and re-planning would silently discard every amount already entered,
--      which is the forgetting this table exists to prevent.
--
-- `omitted` is a first-class resolution, not an absence. A retainer overage is
-- configured as a permanent monthly reminder to *check* for an overage; most
-- months there is none, and "no overage in August" is a decision worth
-- recording rather than a line to leave undecided.

begin;

create type recurring_line_item_resolution as enum ('amount', 'omitted');

create table recurring_line_item_resolutions (
    id                     uuid primary key default gen_random_uuid(),
    -- Cascade is deliberate: deleting a line from config should drop the
    -- decisions made about it. Nothing downstream keeps a reference.
    recurring_line_item_id uuid not null
        references recurring_line_items(id) on delete cascade,
    run_month              date not null
        check (run_month = date_trunc('month', run_month)::date),
    resolution             recurring_line_item_resolution not null,
    -- Null falls back to the template's quantity. Present when the placeholder
    -- is quantity-shaped — 12 overage hours at $175 rather than a flat sum.
    quantity               numeric,
    unit_price             numeric(12,2),
    -- Why, in the operator's words. Most valuable on an omit, where the record
    -- would otherwise be indistinguishable from a line that was never there.
    note                   text,
    resolved_by            text,
    resolved_at            timestamptz not null default now(),
    unique (recurring_line_item_id, run_month),
    constraint recurring_line_item_resolution_amount_needs_price
        check (resolution <> 'amount' or unit_price is not null)
);

comment on table recurring_line_item_resolutions is
    'One operator decision about one placeholder line for one run month: an
     entered amount, or an explicit omit. Keyed on the line and the month rather
     than the ledger row so it survives a Re-plan. An undecided placeholder
     blocks approval of the planned invoice and is not overridable.';

comment on column recurring_line_item_resolutions.run_month is
    'First of the billing run''s month — not the service period start, which
     differs from it for arrears groups.';

comment on column recurring_line_item_resolutions.resolution is
    '`amount` bills the line at unit_price; `omitted` drops it from this
     month''s Harvest payload while leaving the template in place.';

comment on column recurring_line_item_resolutions.quantity is
    'Overrides the template quantity when present. Null means use the template''s.';

-- 0025's comment described the old workflow, which this migration replaces.
comment on column recurring_line_items.is_placeholder is
    'Line whose amount is only knowable after the fact (hosting pass-through,
     percentage-based tooling fee, retainer overage). The operator enters the
     amount — or omits the line for the month — on the pre-flight each month,
     recorded in recurring_line_item_resolutions. Until then the line plans at
     $0, is excluded from planned_amount, and blocks approval.';

alter table recurring_line_item_resolutions enable row level security;

create policy recurring_line_item_resolutions_service_all
    on recurring_line_item_resolutions
    for all to service_role using (true) with check (true);

commit;
