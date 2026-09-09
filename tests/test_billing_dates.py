"""Date and due-date resolution.

Month arithmetic is where invoicing quietly goes wrong, so this is
parametrized across month lengths, year boundaries, and a leap February.
"""
from __future__ import annotations

from datetime import date

import pytest

from app.services.billing import dates

# ── Service period ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "run_month,start,end,issue",
    [
        # 31-day prior month
        (date(2026, 8, 1), date(2026, 7, 1), date(2026, 7, 31), date(2026, 7, 31)),
        # 30-day prior month
        (date(2026, 5, 1), date(2026, 4, 1), date(2026, 4, 30), date(2026, 4, 30)),
        # year boundary: January's arrears period is the prior December
        (date(2026, 1, 1), date(2025, 12, 1), date(2025, 12, 31), date(2025, 12, 31)),
        # non-leap February
        (date(2026, 3, 1), date(2026, 2, 1), date(2026, 2, 28), date(2026, 2, 28)),
        # leap February
        (date(2028, 3, 1), date(2028, 2, 1), date(2028, 2, 29), date(2028, 2, 29)),
    ],
)
def test_arrears_period_is_the_previous_month(run_month, start, end, issue):
    p = dates.resolve_period(run_month, "arrears")
    assert (p.start, p.end, p.issue_date) == (start, end, issue)


@pytest.mark.parametrize(
    "run_month,start,end",
    [
        (date(2026, 8, 1), date(2026, 8, 1), date(2026, 8, 31)),
        (date(2026, 4, 1), date(2026, 4, 1), date(2026, 4, 30)),
        (date(2026, 2, 1), date(2026, 2, 1), date(2026, 2, 28)),
        (date(2028, 2, 1), date(2028, 2, 1), date(2028, 2, 29)),
        (date(2026, 12, 1), date(2026, 12, 1), date(2026, 12, 31)),
    ],
)
def test_advance_period_is_the_current_month_issued_on_day_one(run_month, start, end):
    p = dates.resolve_period(run_month, "advance")
    assert (p.start, p.end, p.issue_date) == (start, end, start)


def test_one_run_month_yields_both_periods():
    """The case that makes a client with two groups get two invoices covering
    different months in a single run."""
    arrears = dates.resolve_period(date(2026, 8, 1), "arrears")
    advance = dates.resolve_period(date(2026, 8, 1), "advance")

    assert arrears.label == "July 2026"
    assert advance.label == "August 2026"
    assert arrears.issue_date < advance.issue_date


def test_run_month_is_normalized_to_the_first():
    """Planning on the 7th must not shift the period."""
    assert dates.resolve_period(date(2026, 8, 7), "arrears") == \
           dates.resolve_period(date(2026, 8, 1), "arrears")


def test_unknown_timing_is_rejected():
    with pytest.raises(ValueError, match="unknown billing timing"):
        dates.resolve_period(date(2026, 8, 1), "whenever")


# ── Due dates ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "term,expected",
    [
        ("upon receipt", date(2026, 7, 31)),
        ("net 15", date(2026, 8, 15)),
        ("net 30", date(2026, 8, 30)),
        ("net 45", date(2026, 9, 14)),
        ("net 60", date(2026, 9, 29)),
    ],
)
def test_enum_terms_pass_through_and_let_harvest_do_the_maths(term, expected):
    payload_term, due = dates.resolve_due_date(date(2026, 7, 31), term)
    assert payload_term == term
    # Computed for display only — never sent, because Harvest ignores due_date
    # for enum terms and would disagree with us silently if we did.
    assert due == expected


def test_custom_term_computes_the_due_date_locally():
    payload_term, due = dates.resolve_due_date(date(2026, 7, 31), "custom", 20)
    assert payload_term == "custom"
    assert due == date(2026, 8, 20)


def test_custom_term_without_net_days_is_an_error():
    with pytest.raises(ValueError, match="requires custom_net_days"):
        dates.resolve_due_date(date(2026, 7, 31), "custom", None)


def test_custom_due_date_crosses_a_year_boundary():
    _, due = dates.resolve_due_date(date(2026, 12, 31), "custom", 20)
    assert due == date(2027, 1, 20)


def test_unknown_payment_term_is_rejected():
    with pytest.raises(ValueError, match="unknown payment term"):
        dates.resolve_due_date(date(2026, 7, 31), "net 10")


# ── Draft-day dating ────────────────────────────────────────────────────────
#
# The issue date stays at the period boundary because that is the accounting
# answer; the payment clock starts when the invoice exists because that is when
# the client can act on it. Harvest cannot express the two separately under an
# enum term, so every one of these comes back as `custom`.


def test_arrears_drafted_the_same_month_still_moves_the_due_date():
    term, due = dates.resolve_draft_dating(
        issue_date=date(2026, 8, 31),
        planned_due_date=date(2026, 9, 30),   # net 30
        draft_date=date(2026, 9, 9),
    )
    assert term == "custom"
    assert due == date(2026, 10, 9)


def test_arrears_drafted_a_month_late_never_arrives_overdue():
    """The failure this exists to prevent: issued 31 July, net 30, drafted
    5 October would otherwise be due 30 August — overdue on arrival."""
    _, due = dates.resolve_draft_dating(
        issue_date=date(2026, 7, 31),
        planned_due_date=date(2026, 8, 30),
        draft_date=date(2026, 10, 5),
    )
    assert due == date(2026, 11, 4)
    assert due > date(2026, 10, 5)


def test_advance_drafted_on_its_issue_date_is_unchanged():
    _, due = dates.resolve_draft_dating(
        issue_date=date(2026, 9, 1),
        planned_due_date=date(2026, 10, 1),
        draft_date=date(2026, 9, 1),
    )
    assert due == date(2026, 10, 1)


def test_advance_drafted_before_its_issue_date_is_not_pulled_earlier():
    """The clock starts at max(draft, issue). An advance group can be planned
    and drafted in the last days of the prior month, and re-dating from the
    draft day would make it due sooner than the operator approved."""
    _, due = dates.resolve_draft_dating(
        issue_date=date(2026, 9, 1),
        planned_due_date=date(2026, 10, 1),
        draft_date=date(2026, 8, 28),
    )
    assert due == date(2026, 10, 1)


def test_upon_receipt_is_due_the_day_it_is_drafted():
    """Net zero. Due 31 August for an invoice created on 9 September would be
    the same wrongness as any other stale term, just more obviously."""
    _, due = dates.resolve_draft_dating(
        issue_date=date(2026, 8, 31),
        planned_due_date=date(2026, 8, 31),
        draft_date=date(2026, 9, 9),
    )
    assert due == date(2026, 9, 9)


def test_net_days_are_read_off_the_frozen_row_not_from_config():
    """The gap between the two stored columns *is* the term, whatever it was.
    Nothing here consults `billing_groups`, so a group edited since planning
    cannot change an invoice already under review."""
    _, due = dates.resolve_draft_dating(
        issue_date=date(2026, 8, 31),
        planned_due_date=date(2026, 10, 30),   # 60 days
        draft_date=date(2026, 9, 9),
    )
    assert due == date(2026, 11, 8)
