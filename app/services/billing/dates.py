"""Date resolution — service period, issue date, due date.

Pure functions, no I/O. Kept injectable so Phase 6's off-cycle invoices can
supply an arbitrary period without a parallel code path.

Timing determines both the period and the issue date (PRD §2.3):

    arrears — previous calendar month, issued on its last day
    advance — current calendar month, issued on its first day

A single run produces both. For run month August 2026:
    arrears → 2026-07-01 … 2026-07-31, issued 2026-07-31
    advance → 2026-08-01 … 2026-08-31, issued 2026-08-01
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta

# Terms Harvest computes the due date for itself. Anything else needs
# payment_term="custom" plus an explicitly computed due_date.
_ENUM_TERM_DAYS: dict[str, int] = {
    "upon receipt": 0,
    "net 15": 15,
    "net 30": 30,
    "net 45": 45,
    "net 60": 60,
}


@dataclass(frozen=True)
class Period:
    start: date
    end: date
    issue_date: date
    label: str


def normalize_run_month(value: date) -> date:
    """Any day in a month → the first of that month."""
    return value.replace(day=1)


def month_label(value: date) -> str:
    return f"{calendar.month_name[value.month]} {value.year}"


def last_day_of(value: date) -> date:
    return value.replace(day=calendar.monthrange(value.year, value.month)[1])


def resolve_period(run_month: date, timing: str) -> Period:
    """Service period and issue date for one group in one run."""
    run_month = normalize_run_month(run_month)

    if timing == "advance":
        start = run_month
        end = last_day_of(run_month)
        issue = start
    elif timing == "arrears":
        end = run_month - timedelta(days=1)     # last day of the prior month
        start = end.replace(day=1)
        issue = end
    else:
        raise ValueError(f"unknown billing timing: {timing!r}")

    return Period(start=start, end=end, issue_date=issue, label=month_label(start))


def resolve_due_date(
    issue_date: date, payment_term: str, custom_net_days: int | None = None
) -> tuple[str, date | None]:
    """Return `(payment_term_for_payload, due_date_or_None)`.

    Harvest ignores `due_date` unless `payment_term` is `"custom"`, so for enum
    terms we send the term alone and let Harvest own the arithmetic — one fewer
    place for our date maths to disagree with the invoice the client receives.

    The returned due date for enum terms is still computed locally, but only so
    the pre-flight can *display* it and so execution can verify what Harvest
    came back with. It is never sent.
    """
    if payment_term == "custom":
        if custom_net_days is None:
            raise ValueError("custom payment_term requires custom_net_days")
        return "custom", issue_date + timedelta(days=custom_net_days)

    if payment_term not in _ENUM_TERM_DAYS:
        raise ValueError(f"unknown payment term: {payment_term!r}")
    return payment_term, issue_date + timedelta(days=_ENUM_TERM_DAYS[payment_term])


def expected_due_date(
    issue_date: date, payment_term: str, custom_net_days: int | None = None
) -> date:
    """The due date we expect on the created invoice, for display and for the
    post-execution mismatch check."""
    _, due = resolve_due_date(issue_date, payment_term, custom_net_days)
    assert due is not None
    return due
