"""Unit tests for app.services.revenue.calc_revenue.

Pure function — no DB, no async. Covers each billing type branch.
"""
from __future__ import annotations

import pytest

from app.services.revenue import calc_revenue


def _project(billing_type: str, *, contracted_fees: float = 0.0, hours_logged: float = 0.0, forecast_hours: float = 0.0) -> dict:
    return {
        "Billing Type": billing_type,
        "Contracted Fees": contracted_fees,
        "_hours_logged": hours_logged,
        "_forecast_hours": forecast_hours,
    }


class TestFixedFee:
    def test_partial_completion(self):
        project = _project("Fixed Fee", contracted_fees=100_000, hours_logged=200, forecast_hours=300)
        revenue, percent_complete, notes = calc_revenue(project, {})

        assert percent_complete == 0.4  # 200 / (200 + 300)
        assert revenue == 40_000.0
        assert notes == ""

    def test_with_billable_expenses(self):
        project = _project("Fixed Fee", contracted_fees=100_000, hours_logged=100, forecast_hours=100)
        revenue, percent_complete, notes = calc_revenue(project, {"billable_expenses": 1_500.55})

        assert percent_complete == 0.5
        # base 50_000 + expenses 1_500.55
        assert revenue == 51_500.55
        assert "1,500.55" in notes
        assert "billable expenses" in notes

    def test_zero_total_projected_hours(self):
        project = _project("Fixed Fee", contracted_fees=100_000, hours_logged=0, forecast_hours=0)
        revenue, percent_complete, notes = calc_revenue(project, {})

        assert percent_complete == 0.0
        assert revenue == 0.0
        assert notes == ""

    def test_percent_complete_rounded_to_4_places(self):
        project = _project("Fixed Fee", contracted_fees=10_000, hours_logged=1, forecast_hours=2)
        revenue, percent_complete, _ = calc_revenue(project, {})

        # 1 / 3 → rounded to 4 dp
        assert percent_complete == 0.3333
        assert revenue == round(10_000 * 0.3333, 2)


class TestTimeAndMaterialsFamily:
    @pytest.mark.parametrize("billing_type", ["T&M", "MSF", "Hosting"])
    def test_uses_invoice_total(self, billing_type: str):
        project = _project(billing_type, hours_logged=50)
        revenue, percent_complete, notes = calc_revenue(project, {"total_amount": 12_345.678})

        assert revenue == 12_345.68  # rounded to 2 dp
        assert percent_complete is None
        assert notes == ""

    def test_missing_invoice_amount_defaults_to_zero(self):
        project = _project("T&M")
        revenue, _, _ = calc_revenue(project, {})

        assert revenue == 0.0


class TestRetainer:
    def test_returns_zero_with_manual_note(self):
        project = _project("Retainer", contracted_fees=5_000)
        revenue, percent_complete, notes = calc_revenue(project, {"total_amount": 9_999})

        assert revenue == 0.0
        assert percent_complete is None
        assert "manually" in notes.lower()


class TestUnknownBillingType:
    def test_raises(self):
        project = _project("WeirdNewType")
        with pytest.raises(ValueError, match="Unexpected billing type"):
            calc_revenue(project, {})

    def test_default_unknown_raises(self):
        project = {"_hours_logged": 0, "_forecast_hours": 0}  # no "Billing Type" → "Unknown"
        with pytest.raises(ValueError, match="Unexpected billing type"):
            calc_revenue(project, {})
