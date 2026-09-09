-- Rejecting one group's invoice from a planned run.
--
-- "Unapprove" only means undecided: the row stays live and says nothing about
-- why. The operator needs to say *this one is not going out, and here is the
-- reason* — most often because they already invoiced it by hand, which is
-- exactly the case where a second draft would be a real duplicate.
--
-- No new status. `skipped` already means "this run will not bill this group",
-- it is already excluded from `billing_run_items_one_live_per_month` so a
-- rejection frees the month's slot, and the pre-flight already renders a
-- Skipped section off `skip_reason`. What was missing is the distinction
-- between the planner finding nothing to bill and a human deciding not to
-- bill. `rejected_by is not null` is that distinction, and it is also what
-- makes the decision reversible: a planner-skipped row must not be un-skipped
-- into an invoice that was never planned.

alter table billing_run_items
    add column rejected_at timestamptz,
    add column rejected_by text;

comment on column billing_run_items.rejected_by is
    'Set when an operator rejected this item, distinguishing it from a row the '
    'planner skipped. Null on a planner skip.';
