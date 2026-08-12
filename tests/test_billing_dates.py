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
