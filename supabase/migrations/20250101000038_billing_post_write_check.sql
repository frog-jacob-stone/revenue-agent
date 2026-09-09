-- Post-write verification of a created T&M invoice.
--
-- A T&M invoice is created with `line_items_import`, which is what makes
-- Harvest generate the line items itself and mark the underlying time entries
-- billed. That mechanism is the whole reason the system cannot leave time
-- "billed here, uninvoiced in Harvest" — but it is Harvest's behaviour, not
-- ours, and nothing observed it. These two columns are the observation.
--
-- Read them as a note about Harvest at one moment, not as an accounting
-- figure: null means the check did not run (a free-form `recurring_monthly`
-- invoice, where no time was ever meant to be marked, or a check that itself
-- failed and was swallowed so it could not turn a good invoice into a bad
-- run). Zero means the import took everything it should have.

alter table billing_run_items
    add column unbilled_hours_after   numeric(10,2),
    add column unbilled_entries_after integer;

comment on column billing_run_items.unbilled_hours_after is
    'Billable time still unbilled in Harvest for this item''s projects over its '
    'service period, measured just after the invoice was created. Null = not checked.';
comment on column billing_run_items.unbilled_entries_after is
    'Count of the time entries behind unbilled_hours_after. Null = not checked.';
