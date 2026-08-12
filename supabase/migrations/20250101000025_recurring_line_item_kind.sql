-- Free-form line items need a Harvest invoice item category.
--
-- Harvest requires `kind` on every free-form line item, validated against the
-- account's invoice item categories (`GET /v2/invoice_item_categories` — e.g.
-- Service, Billable Expense, Discount, Advanced Deposit). Migration 0024
-- created both line-item tables without it, which would have produced a 422 at
-- invoice creation with no way to fix it from config. See PRD §4.7.
--
-- `is_placeholder` supports the common retainer/hosting shape: a line whose
-- description and category are known monthly but whose amount is only knowable
-- after the fact — hosting pass-through, a tooling fee that is a percentage of
-- it. The line is created at zero so the draft carries the right scaffolding,
-- and the operator fills in the amount in Harvest before sending. Marking it
-- explicitly is what stops a deliberately-zero line from reading as a bug in
-- the pre-flight estimate.

begin;

alter table recurring_line_items
    add column if not exists kind text not null default 'Service',
    add column if not exists is_placeholder boolean not null default false;

alter table fixed_fee_schedule_items
    add column if not exists kind text not null default 'Service';

comment on column recurring_line_items.kind is
    'Harvest invoice item category name. Validated against harvest_invoice_item_categories at plan time so an invalid value surfaces in pre-flight rather than as a 422 mid-execution.';

comment on column recurring_line_items.is_placeholder is
    'Line is created at zero for the operator to complete in the Harvest draft (e.g. hosting pass-through, percentage-based tooling fee). Excluded from the pre-flight estimate total and surfaced as a PLACEHOLDER_LINE_ITEMS flag.';

comment on column fixed_fee_schedule_items.kind is
    'Harvest invoice item category name. See recurring_line_items.kind.';

commit;
