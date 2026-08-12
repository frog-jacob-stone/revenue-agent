-- 0027 — a draw is scheduled to a day, not a month.
--
-- Migration 0024 modelled the payment schedule as month-granular, because the
-- original design had a draw bill itself during the monthly run for its month.
-- That is not how these contracts work: a schedule commits to dates —
-- "30% on 15 Sep" — and draws are billed one at a time on the day delivery is
-- confirmed, not swept up by a monthly run.
--
-- Note this is the opposite call to `recurring_line_items.effective_from` /
-- `effective_to`, which are deliberately compared month-granular. That is not
-- an inconsistency: a recurring line is billed *for a month*, so any day in it
-- means the whole month. A draw is a dated event, so the day is the point.

begin;

alter table fixed_fee_schedule_items
    drop constraint fixed_fee_scheduled_month_is_first_of_month;

alter table fixed_fee_schedule_items
    rename column scheduled_month to scheduled_date;

comment on column fixed_fee_schedule_items.scheduled_date is
    'The date the contract commits to for this draw. Drives the overdue prompt
     and forecasting only — it never authorises a bill. A draw becomes billable
     when a human confirms delivery.';

commit;
