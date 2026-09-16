"""Unit tests for app.services.revenue.calc_revenue.

Pure function — no DB, no async. Covers each recognition method.

Note what the returned `amount` is: **cumulative-to-date**, not the period's
revenue. Every formula here naturally produces a cumulative figure, and the
run turns it into a period amount by subtracting what was already recognized.
That subtraction is tested in `test_revenue_run.py`, not here.
"""
from __future__ import annotations

import pytest

from app.services.revenue import calc_revenue


class TestFixedFee:
    def test_partial_completion(self):
        result = calc_revenue(
            revenue_type="fixed_fee",
            contracted_fees=100_000,
            hours_logged=200,
            forecast_hours=300,
        )

        assert result.percent_complete == 0.4  # 200 / (200 + 300)
        assert result.amount == 40_000.0
        assert result.notes == ""

    def test_with_billable_expenses(self):
        """Expenses are passed through at cost and are not part of the contract
        value, so they sit on top of the earned fee rather than being scaled by
        completion."""
        result = calc_revenue(
            revenue_type="fixed_fee",
            contracted_fees=100_000,
            hours_logged=100,
            forecast_hours=100,
            billable_expenses=1_500.55,
        )

        assert result.percent_complete == 0.5
        assert result.amount == 51_500.55  # base 50_000 + expenses
        assert "1,500.55" in result.notes
        assert "billable expenses" in result.notes

    def test_zero_total_projected_hours(self):
        """A project nobody has worked or scheduled has earned nothing. Zero,
        not a division error."""
        result = calc_revenue(
            revenue_type="fixed_fee", contracted_fees=100_000,
            hours_logged=0, forecast_hours=0,
        )

        assert result.percent_complete == 0.0
        assert result.amount == 0.0
        assert result.notes == ""

    def test_percent_complete_rounded_to_4_places(self):
        result = calc_revenue(
            revenue_type="fixed_fee", contracted_fees=10_000,
            hours_logged=1, forecast_hours=2,
        )

        assert result.percent_complete == 0.3333  # 1/3, to 4dp
        assert result.amount == round(10_000 * 0.3333, 2)

    def test_missing_contracted_fees_is_zero_not_a_crash(self):
        """The CHECK on `revenue_project_config` makes this unreachable through
        the run, but the function is pure and should not assume its caller."""
        result = calc_revenue(
            revenue_type="fixed_fee", hours_logged=10, forecast_hours=10,
        )
        assert result.amount == 0.0


class TestRecognizedAsInvoiced:
    @pytest.mark.parametrize(
        "revenue_type", ["time_and_materials", "msf", "hosting"]
    )
    def test_uses_invoiced_to_date(self, revenue_type: str):
        """Harvest's invoiced-to-date is already cumulative, so it is the
        cumulative figure directly."""
        result = calc_revenue(
            revenue_type=revenue_type, hours_logged=50, invoiced_to_date=12_345.678,
        )

        assert result.amount == 12_345.68  # rounded to 2 dp
        assert result.percent_complete is None
        assert result.notes == ""

    def test_nothing_invoiced_is_zero(self):
        assert calc_revenue(revenue_type="time_and_materials").amount == 0.0

    def test_expenses_are_not_added_twice(self):
        """Already inside the Harvest invoice total, unlike the fixed-fee case
        where the fee is computed rather than invoiced."""
        result = calc_revenue(
            revenue_type="time_and_materials",
            invoiced_to_date=10_000,
            billable_expenses=500,
        )
        assert result.amount == 10_000.0


class TestRetainer:
    def test_returns_zero_with_a_note_asking_for_a_human(self):
        """Not an oversight — a retainer's recognition is a judgement call, and
        the zero is a prompt. `finalize_run` refuses to close a month while an
        undecided retainer still sits at zero."""
        result = calc_revenue(
            revenue_type="retainer", contracted_fees=5_000, invoiced_to_date=9_999,
        )

        assert result.amount == 0.0
        assert result.percent_complete is None
        assert "manually" in result.notes.lower()


class TestUnknownRevenueType:
    def test_raises(self):
        with pytest.raises(ValueError, match="Unexpected revenue type"):
            calc_revenue(revenue_type="subscription")

    def test_the_airtable_labels_no_longer_work(self):
        """The old vocabulary must not silently half-work: 'Fixed Fee' hitting
        the `_` arm would recognize nothing rather than failing loudly."""
        for label in ["Fixed Fee", "T&M", "MSF", "Hosting", "Retainer"]:
            with pytest.raises(ValueError, match="Unexpected revenue type"):
                calc_revenue(revenue_type=label)
