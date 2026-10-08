"""Invoice totals by project — the two figures revenue recognition runs on.

This function had no test, and that is exactly why it carried a bug for the
whole life of the subsystem. `calc_revenue` is well covered and takes
`billable_expenses` as an argument; every run-level test stubbed this function
out and hardcoded that argument to zero. So the formula was proved and the thing
that *feeds* the formula never executed — the seam between them is where the
defect sat.

The defect: `kind` is the Harvest **invoice item category name**, configured per
account, and this one calls expenses "Billable Expense". The code tested
`kind.lower() == "expense"`, which matched nothing, so `billable_expenses` was
0.0 on every run ever performed and every fixed-fee project recognized its fee
with none of its pass-through costs.

No network: `_get_all` is the only I/O and is replaced per test.
"""
from __future__ import annotations

import pytest

from app.config import settings
from app.integrations import harvest

#: The four categories this Harvest account actually has, as cached in
#: `harvest_invoice_item_categories` and named at `app/models/billing.py:280`.
#: Exactly one of them is an expense, and it is not spelled "Expense".
SERVICE = "Service"
BILLABLE_EXPENSE = "Billable Expense"
DISCOUNT = "Discount"
ADVANCED_DEPOSIT = "Advanced Deposit"


def _invoice(issue_date: str, *items: tuple[int | None, str, float]) -> dict:
    """An invoice shaped the way Harvest's /v2/invoices returns one.

    Each item is `(project_id, kind, amount)`; a null project id is a line item
    attached to no project, which Harvest does emit.
    """
    return {
        "issue_date": issue_date,
        "line_items": [
            {
                "project": None if pid is None else {"id": pid},
                "kind": kind,
                "amount": amount,
            }
            for pid, kind, amount in items
        ],
    }


@pytest.fixture
def harvest_invoices(monkeypatch):
    """Set the invoices Harvest would return. Call the returned setter."""

    def _set(invoices: list[dict]) -> None:
        async def _get_all(cfg, path, key, params=None, **kwargs):
            assert path == "/invoices"
            return invoices

        monkeypatch.setattr(harvest, "_get_all", _get_all)

    return _set


async def test_a_billable_expense_line_item_is_counted_as_an_expense(
    harvest_invoices,
):
    """The regression guard, using this account's real category name.

    If this ever fails because the category was renamed in Harvest, the fix is
    to widen the match in `get_invoice_totals_by_project` — not to change this
    string, which is here precisely because it is the live value.
    """
    harvest_invoices([
        _invoice("2026-09-30", (10, SERVICE, 100_000.0), (10, BILLABLE_EXPENSE, 6_404.16))
    ])

    totals = await harvest.get_invoice_totals_by_project(settings, "2026-09-30")

    assert totals[10]["billable_expenses"] == 6_404.16


async def test_expense_categories_are_matched_however_they_are_worded(
    harvest_invoices,
):
    """Substring, not an exact name. A single literal is what broke this, and
    any of these is a plausible label for the same pass-through cost."""
    harvest_invoices([
        _invoice(
            "2026-09-30",
            (10, "Billable Expense", 100.0),
            (10, "billable expense", 10.0),
            (10, "Expenses", 1.0),
            (10, "Reimbursable Expense", 0.5),
        )
    ])

    totals = await harvest.get_invoice_totals_by_project(settings, "2026-09-30")

    assert totals[10]["billable_expenses"] == 111.5


async def test_the_accounts_other_categories_are_not_expenses(harvest_invoices):
    """Service, Discount and Advanced Deposit all count toward the invoice total
    and none of them toward expenses. A fixed-fee project adds only expenses on
    top of its earned fee, so a false positive here would over-recognize."""
    harvest_invoices([
        _invoice(
            "2026-09-30",
            (10, SERVICE, 1_000.0),
            (10, DISCOUNT, -50.0),
            (10, ADVANCED_DEPOSIT, 250.0),
        )
    ])

    totals = await harvest.get_invoice_totals_by_project(settings, "2026-09-30")

    assert totals[10]["billable_expenses"] == 0.0
    assert totals[10]["total_amount"] == 1_200.0


async def test_total_amount_includes_expense_line_items(harvest_invoices):
    """The two figures overlap by design, and the callers rely on it.

    T&M, MSF and hosting recognize `total_amount`, so an expense invoiced
    against them is already revenue. Fixed fee recognizes
    `fees × percent_complete + billable_expenses` and never reads
    `total_amount` — which is what keeps the overlap from double counting.
    """
    harvest_invoices([
        _invoice("2026-09-30", (10, SERVICE, 900.0), (10, BILLABLE_EXPENSE, 100.0))
    ])

    totals = await harvest.get_invoice_totals_by_project(settings, "2026-09-30")

    assert totals[10]["total_amount"] == 1_000.0
    assert totals[10]["billable_expenses"] == 100.0


async def test_invoices_issued_after_the_cutoff_are_excluded(harvest_invoices):
    """Cumulative to the period end, not to today. Billing issues an arrears
    invoice dated the last day of the period it covers, so the boundary is
    inclusive and a September invoice belongs to September."""
    harvest_invoices([
        _invoice("2026-09-30", (10, SERVICE, 500.0)),
        _invoice("2026-10-01", (10, SERVICE, 999.0)),
    ])

    totals = await harvest.get_invoice_totals_by_project(settings, "2026-09-30")

    assert totals[10]["total_amount"] == 500.0


async def test_a_line_item_with_no_project_is_skipped(harvest_invoices):
    """Harvest allows a line item attached to no project. It belongs to no
    project's revenue, and keying on `project["id"]` would raise."""
    harvest_invoices([
        _invoice("2026-09-30", (None, SERVICE, 750.0), (10, SERVICE, 250.0))
    ])

    totals = await harvest.get_invoice_totals_by_project(settings, "2026-09-30")

    assert list(totals) == [10]
    assert totals[10]["total_amount"] == 250.0


async def test_amounts_accumulate_across_invoices(harvest_invoices):
    """Several months of invoices against one project sum into one figure —
    the cumulative total the runner differences against prior recognition."""
    harvest_invoices([
        _invoice("2026-07-31", (10, SERVICE, 100.0), (10, BILLABLE_EXPENSE, 5.0)),
        _invoice("2026-08-31", (10, SERVICE, 200.0), (10, BILLABLE_EXPENSE, 7.5)),
    ])

    totals = await harvest.get_invoice_totals_by_project(settings, "2026-09-30")

    assert totals[10]["total_amount"] == 312.5
    assert totals[10]["billable_expenses"] == 12.5
