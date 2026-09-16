"""The Airtable -> Postgres revenue import, and the gate that guards it.

The import inverts the ledger: Airtable stored the cumulative figure and
derived the period amount; Postgres stores the period amount and derives the
cumulative. `reconcile()` is the proof that inversion is lossless, so most of
what is worth testing here is that the gate actually catches the ways it could
fail — a wrong delta, a missing month, a cumulative figure substituted for a
period one.

These tests are deleted in Phase 4 along with the script.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.db import get_pool
from scripts.backfill_rev_rec import (
    BACKFILL_ACTOR,
    BackfillError,
    Plan,
    build_configs,
    build_entries,
    map_revenue_type,
    reconcile,
    write_plan,
)


def _record(
    *,
    harvest_id: int = 14307913,
    name: str = "Acme Platform",
    recognized_on: str,
    delta: float | None,
    cumulative: float,
    billing_type: str = "Fixed Fee",
    **extra,
) -> dict:
    """A revenue record shaped the way Airtable returns one, flattened by
    `airtable.get_revenue_records` (fields hoisted, `airtableId` added)."""
    return {
        "airtableId": f"rec{harvest_id}{recognized_on}",
        "Harvest Id": harvest_id,
        "Project Name": name,
        "Date Recognized": recognized_on,
        "Revenue Delta": delta,
        "Total Recognized Revenue": cumulative,
        "Billing Type": billing_type,
        **extra,
    }


# ── Mapping ─────────────────────────────────────────────────────────────────


def test_every_airtable_billing_type_maps():
    assert map_revenue_type("Fixed Fee") == "fixed_fee"
    assert map_revenue_type("T&M") == "time_and_materials"
    assert map_revenue_type("MSF") == "msf"
    assert map_revenue_type("Hosting") == "hosting"
    assert map_revenue_type("Retainer") == "retainer"


def test_unknown_billing_type_aborts_rather_than_defaulting():
    """A new single-select option means someone deliberately added one. Guessing
    would recognize that project by the wrong formula from then on."""
    with pytest.raises(BackfillError, match="Unmapped Billing Type"):
        map_revenue_type("Subscription")
    with pytest.raises(BackfillError, match="no Billing Type"):
        map_revenue_type(None)


def test_date_recognized_becomes_first_of_month():
    """Airtable used the last day of the month; the column is first-of-month by
    CHECK, so this has to be converted rather than passed through."""
    entries = build_entries([
        _record(recognized_on="2026-03-31", delta=1000, cumulative=1000)
    ])
    assert entries[0].period_month == date(2026, 3, 1)


def test_amounts_survive_as_exact_decimals():
    """Airtable sends JSON floats. Read via str() so 12345.67 is that number and
    not the float's binary approximation — the gate reconciles to the cent."""
    entries = build_entries([
        _record(recognized_on="2026-03-31", delta=12345.67, cumulative=12345.67)
    ])
    assert entries[0].recognized_amount == Decimal("12345.67")


def test_first_entry_falls_back_to_the_cumulative_figure():
    """A project's first record has no prior month, so Airtable's formula is
    empty there and the cumulative total is the period amount."""
    entries = build_entries([
        _record(recognized_on="2026-01-31", delta=None, cumulative=5000)
    ])
    assert entries[0].recognized_amount == Decimal("5000.00")


def test_records_with_no_harvest_id_abort_the_import():
    with pytest.raises(BackfillError, match="no Harvest Id"):
        build_entries([
            _record(recognized_on="2026-01-31", delta=None, cumulative=100),
            {"airtableId": "recBad", "Project Name": "Orphan",
             "Date Recognized": "2026-01-31", "Billing Type": "T&M",
             "Total Recognized Revenue": 50},
        ])


def test_every_bad_record_is_reported_not_just_the_first():
    """A one-time import of years of history. Dying on record 1 of 960 means
    fixing one cell, re-running for minutes, and finding the next one — the
    whole list in a single pass is the difference between an afternoon and a
    week. Each problem must name the record it came from."""
    with pytest.raises(BackfillError) as exc:
        build_entries([
            _record(recognized_on="2026-01-31", delta=None, cumulative=100),
            {"airtableId": "recNoId", "Project Name": "Orphan",
             "Date Recognized": "2026-01-31", "Billing Type": "T&M"},
            {"airtableId": "recNoType", "Project Name": "Untyped",
             "Harvest Id": 5, "Date Recognized": "2026-01-31"},
            {"airtableId": "recWeird", "Project Name": "Odd",
             "Harvest Id": 6, "Date Recognized": "2026-01-31",
             "Billing Type": "Subscription"},
        ])
    message = str(exc.value)
    assert "3 of 4" in message
    for name, reason in [
        ("Orphan", "no Harvest Id"),
        ("Untyped", "no Billing Type"),
        ("Odd", "Unmapped Billing Type"),
    ]:
        assert name in message and reason in message


def test_an_unmapped_project_billing_type_names_the_project():
    with pytest.raises(BackfillError, match="Ambiguous"):
        build_configs([
            {"Harvest Id": 1, "Project Name": "Ambiguous",
             "Billing Type": "Subscription"},
        ])


# ── The gate ────────────────────────────────────────────────────────────────


def test_consistent_history_reconciles():
    entries = build_entries([
        _record(recognized_on="2026-01-31", delta=None, cumulative=1000),
        _record(recognized_on="2026-02-28", delta=500, cumulative=1500),
        _record(recognized_on="2026-03-31", delta=250.50, cumulative=1750.50),
    ])
    assert reconcile(entries) == []


def test_gate_catches_a_wrong_delta():
    entries = build_entries([
        _record(recognized_on="2026-01-31", delta=None, cumulative=1000),
        # Says 400, but the cumulative moved by 500.
        _record(recognized_on="2026-02-28", delta=400, cumulative=1500),
    ])
    mismatches = reconcile(entries)
    assert len(mismatches) == 1
    assert mismatches[0].period_month == date(2026, 2, 1)
    assert mismatches[0].expected == Decimal("1500.00")
    assert mismatches[0].actual == Decimal("1400.00")
    assert mismatches[0].difference == Decimal("-100.00")


def test_gate_catches_a_cumulative_figure_used_as_a_period_one():
    """The exact failure the first-entry fallback would cause if it ever fired
    on a later record — the running sum doubles up immediately."""
    entries = build_entries([
        _record(recognized_on="2026-01-31", delta=None, cumulative=1000),
        _record(recognized_on="2026-02-28", delta=1500, cumulative=1500),
    ])
    assert len(reconcile(entries)) == 1


def test_gate_is_per_project():
    """Two projects' histories must not be summed together, and a break in one
    must not be reported against the other."""
    entries = build_entries([
        _record(recognized_on="2026-01-31", delta=None, cumulative=1000),
        _record(recognized_on="2026-02-28", delta=500, cumulative=1500),
        _record(harvest_id=999, name="Beta", recognized_on="2026-01-31",
                delta=None, cumulative=7000),
        _record(harvest_id=999, name="Beta", recognized_on="2026-02-28",
                delta=1, cumulative=9000),
    ])
    mismatches = reconcile(entries)
    assert len(mismatches) == 1
    assert mismatches[0].harvest_project_id == 999


def test_a_cent_of_rounding_is_tolerated_but_a_dollar_is_not():
    assert reconcile(build_entries([
        _record(recognized_on="2026-01-31", delta=None, cumulative=1000),
        _record(recognized_on="2026-02-28", delta=500, cumulative=1500.01),
    ])) == []
    assert len(reconcile(build_entries([
        _record(recognized_on="2026-01-31", delta=None, cumulative=1000),
        _record(recognized_on="2026-02-28", delta=500, cumulative=1501),
    ]))) == 1


# ── Hours ───────────────────────────────────────────────────────────────────
#
# Airtable's `Logged Hours` is cumulative-to-date — verified against the live
# base, where all 76 projects with three or more months show hours that never
# decrease. The column stores a period quantity, because it is the denominator
# of revenue-per-hour and the numerator is one too.


def test_cumulative_hours_become_period_hours():
    entries = build_entries([
        _record(recognized_on="2026-01-31", delta=None, cumulative=1000, **{"Logged Hours": 100}),
        _record(recognized_on="2026-02-28", delta=500, cumulative=1500, **{"Logged Hours": 160}),
        _record(recognized_on="2026-03-31", delta=500, cumulative=2000, **{"Logged Hours": 200}),
    ])
    assert [e.logged_hours for e in entries] == [
        Decimal("100.00"), Decimal("60.00"), Decimal("40.00")
    ]


def test_the_first_record_keeps_its_lump_when_revenue_starts_there_too():
    """Airtable leaves `Revenue Delta` empty on a project's first record, so
    `recognized_amount` is a lump over the same unknown span. The two agree,
    and the rate between them holds."""
    entries = build_entries([
        _record(recognized_on="2026-01-31", delta=None, cumulative=5000, **{"Logged Hours": 40}),
        _record(recognized_on="2026-02-28", delta=1000, cumulative=6000, **{"Logged Hours": 48}),
    ])
    assert entries[0].logged_hours == Decimal("40.00")
    assert entries[0].recognized_amount == Decimal("5000.00")


def test_hours_starting_mid_history_are_dropped_rather_than_believed():
    """The live base does this: one project's revenue history begins 2024-12
    but its hours begin 2025-04. That lump spans four months of hours against
    one month of revenue and reads as $12/hour on a project running at $155. A
    dash says "not known", which is true."""
    entries = build_entries([
        _record(recognized_on="2026-01-31", delta=None, cumulative=1000),
        _record(recognized_on="2026-02-28", delta=500, cumulative=1500),
        _record(recognized_on="2026-03-31", delta=500, cumulative=2000, **{"Logged Hours": 900}),
        _record(recognized_on="2026-04-30", delta=500, cumulative=2500, **{"Logged Hours": 930}),
    ])
    assert [e.logged_hours for e in entries] == [
        None, None, None, Decimal("30.00")
    ]


def test_hours_removed_after_a_period_closed_stay_negative():
    """Not clamped. It means hours were deleted in Harvest, and flooring it
    would leave the summed hours disagreeing with Harvest forever with nothing
    to show why."""
    entries = build_entries([
        _record(recognized_on="2026-01-31", delta=None, cumulative=1000, **{"Logged Hours": 100}),
        _record(recognized_on="2026-02-28", delta=500, cumulative=1500, **{"Logged Hours": 90}),
    ])
    assert entries[1].logged_hours == Decimal("-10.00")


def test_hours_are_differenced_per_project():
    entries = build_entries([
        _record(recognized_on="2026-01-31", delta=None, cumulative=1000, **{"Logged Hours": 100}),
        _record(recognized_on="2026-02-28", delta=500, cumulative=1500, **{"Logged Hours": 160}),
        _record(harvest_id=999, name="Beta", recognized_on="2026-01-31",
                delta=None, cumulative=7000, **{"Logged Hours": 12}),
        _record(harvest_id=999, name="Beta", recognized_on="2026-02-28",
                delta=1000, cumulative=8000, **{"Logged Hours": 20}),
    ])
    beta = [e for e in entries if e.harvest_project_id == 999]
    assert [e.logged_hours for e in beta] == [Decimal("12.00"), Decimal("8.00")]


# ── Project config ──────────────────────────────────────────────────────────


def test_build_configs_takes_only_the_hand_typed_columns():
    configs = build_configs([
        {"airtableId": "recA", "Harvest Id": 14307913, "Project Name": "Acme",
         "Billing Type": "Fixed Fee", "Contracted Fees": 250000, "Client Id": 5735774},
    ])
    assert configs == [{
        "harvest_project_id": 14307913,
        "revenue_type": "fixed_fee",
        "contracted_fees": Decimal("250000.00"),
    }]


def test_build_configs_skips_unconfigured_and_incomplete_projects():
    """A project the CHECK would reject is left unconfigured on purpose — the
    run surfaces it by name, which is where a human should be asked."""
    configs = build_configs([
        {"Harvest Id": 1, "Project Name": "No type"},
        {"Harvest Id": 2, "Project Name": "Fixed, no fee", "Billing Type": "Fixed Fee"},
        {"Harvest Id": 3, "Project Name": "Retainer", "Billing Type": "Retainer"},
    ])
    assert [c["harvest_project_id"] for c in configs] == [3]
    assert configs[0]["contracted_fees"] is None


# ── The write ───────────────────────────────────────────────────────────────


async def test_import_writes_one_run_per_month_and_an_audit_row(client):
    plan = Plan(
        entries=build_entries([
            _record(recognized_on="2026-01-31", delta=None, cumulative=1000),
            _record(recognized_on="2026-02-28", delta=500, cumulative=1500),
            _record(harvest_id=999, name="Beta", recognized_on="2026-02-28",
                    delta=None, cumulative=2000, billing_type="Retainer"),
        ]),
        configs=build_configs([
            {"Harvest Id": 999, "Project Name": "Beta", "Billing Type": "Retainer"},
        ]),
    )
    pool = await get_pool()
    counts = await write_plan(pool, plan)

    assert counts == {"runs": 2, "entries": 3, "configs": 1}

    runs = await pool.fetch(
        "SELECT period_month, status, created_by FROM revenue_runs ORDER BY period_month"
    )
    assert [r["period_month"] for r in runs] == [date(2026, 1, 1), date(2026, 2, 1)]
    # Recognized from the outset: these months were closed years ago and there
    # is nothing left for anyone to review.
    assert {r["status"] for r in runs} == {"recognized"}
    assert {r["created_by"] for r in runs} == {BACKFILL_ACTOR}

    # No override history exists, so computed and recognized must agree.
    rows = await pool.fetch(
        "SELECT recognized_amount, computed_amount, overridden_at FROM revenue_entries"
    )
    assert all(r["recognized_amount"] == r["computed_amount"] for r in rows)
    assert all(r["overridden_at"] is None for r in rows)

    assert await pool.fetchval(
        "SELECT count(*) FROM audit_log WHERE event_type = 'revenue.backfill.imported'"
    ) == 1


async def test_imported_entries_land_in_the_right_run(client):
    plan = Plan(entries=build_entries([
        _record(recognized_on="2026-01-31", delta=None, cumulative=1000),
        _record(recognized_on="2026-02-28", delta=500, cumulative=1500),
    ]))
    pool = await get_pool()
    await write_plan(pool, plan)

    rows = await pool.fetch(
        "SELECT e.period_month, e.recognized_amount, r.period_month AS run_month "
        "FROM revenue_entries e JOIN revenue_runs r ON r.id = e.revenue_run_id "
        "ORDER BY e.period_month"
    )
    # The denormalized period_month must agree with the run it hangs off.
    assert all(r["period_month"] == r["run_month"] for r in rows)
    assert [r["recognized_amount"] for r in rows] == [
        Decimal("1000.00"), Decimal("500.00")
    ]
