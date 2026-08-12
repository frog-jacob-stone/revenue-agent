"""End-to-end pre-flight planning, against the Harvest fake.

Nothing here may write to Harvest. The fake exposes no write surface at all,
so a planner that tried would fail with AttributeError.
"""
from __future__ import annotations

from datetime import date

import pytest

from app.config import settings
from app.db import get_pool
from app.services.billing import groups as groups_service
from app.services.billing import harvest_snapshot, planner
from tests.fakes.harvest import FakeHarvest

ACME = 5735774
NORTHWIND = 5735801

PLATFORM = 14307913
MOBILE = 14307914
LAB = 14307915
NW_DATA = 14308221

AUGUST = date(2026, 8, 1)


@pytest.fixture
async def fake(monkeypatch):
    f = FakeHarvest()
    f.add_client(ACME, "Acme Corp")
    f.add_client(NORTHWIND, "Northwind Industrial")
    f.add_project(PLATFORM, "Acme Platform", client_id=ACME)
    f.add_project(MOBILE, "Acme Mobile", client_id=ACME)
    f.add_project(LAB, "Acme Innovation Lab", client_id=ACME)
    f.add_project(NW_DATA, "Northwind Data Platform", client_id=NORTHWIND)
    f.install(monkeypatch)
    # Groups can only be configured against projects the snapshot knows about,
    # so prime the cache before any test builds config.
    await harvest_snapshot.refresh_snapshot(await get_pool(), settings)
    return f


async def _group(pool, name, client_id, project_ids, **over):
    return await groups_service.create_group(pool, {
        "name": name,
        "harvest_client_id": client_id,
        "billing_type": over.pop("billing_type", "time_and_materials"),
        "time_summary_type": over.pop("time_summary_type", "task"),
        "projects": [{"harvest_project_id": p} for p in project_ids],
        **over,
    })


def _item_for(run, group_id):
    return next(i for i in run["items"] if i["billing_group_id"] == group_id)


def _codes(item) -> set[str]:
    return {f["code"] for f in item["flags"]}


# ── The happy path ──────────────────────────────────────────────────────────


async def test_plans_a_tm_group_with_estimate_and_payload(fake):
    pool = await get_pool()
    fake.add_time(PLATFORM, spent_date="2026-07-06", hours=100, rate=185)
    fake.add_time(MOBILE, spent_date="2026-07-06", hours=20, rate=185)
    group = await _group(pool, "Acme — Platform + Mobile", ACME, [PLATFORM, MOBILE],
                         purchase_order="PO-4471")

    run_id = await planner.plan_run(pool, settings, run_month=AUGUST)
    run = await planner.get_run(pool, run_id)

    assert run["status"] == "awaiting_approval"
    assert run["planned_count"] == 1
    item = _item_for(run, group["id"])
    assert float(item["planned_amount"]) == pytest.approx(120 * 185)

    body = item["planned_payload"]
    assert body["client_id"] == ACME
    assert body["subject"] == "Acme Corp — July 2026"
    assert body["issue_date"] == "2026-07-31"
    assert body["payment_term"] == "net 30"
    assert body["purchase_order"] == "PO-4471"
    assert body["line_items_import"]["project_ids"] == [PLATFORM, MOBILE]
    assert body["line_items_import"]["time"] == {
        "summary_type": "task", "from": "2026-07-01", "to": "2026-07-31",
    }
    # Expenses off → the key must be absent, not an empty object.
    assert "expenses" not in body["line_items_import"]
    # Enum term → Harvest owns the due date, so we don't send one.
    assert "due_date" not in body


async def test_arrears_and_advance_groups_get_different_periods(fake):
    pool = await get_pool()
    fake.add_time(PLATFORM, spent_date="2026-07-06", hours=10, rate=185)
    fake.add_time(LAB, spent_date="2026-08-04", hours=10, rate=185)
    arrears = await _group(pool, "Acme — Platform", ACME, [PLATFORM])
    advance = await _group(pool, "Acme — Lab", ACME, [LAB], billing_timing="advance")

    run = await planner.get_run(
        pool, await planner.plan_run(pool, settings, run_month=AUGUST)
    )

    a = _item_for(run, arrears["id"])
    b = _item_for(run, advance["id"])
    assert (a["period_start"], a["period_end"]) == (date(2026, 7, 1), date(2026, 7, 31))
    assert (b["period_start"], b["period_end"]) == (date(2026, 8, 1), date(2026, 8, 31))
    assert a["issue_date"] == date(2026, 7, 31)
    assert b["issue_date"] == date(2026, 8, 1)


async def test_custom_payment_term_sends_a_computed_due_date(fake):
    pool = await get_pool()
    fake.add_time(PLATFORM, spent_date="2026-07-06", hours=10, rate=185)
    await _group(pool, "Ridgeway", ACME, [PLATFORM],
                 payment_term="custom", custom_net_days=20)

    run = await planner.get_run(
        pool, await planner.plan_run(pool, settings, run_month=AUGUST)
    )
    body = run["items"][0]["planned_payload"]
    assert body["payment_term"] == "custom"
    assert body["due_date"] == "2026-08-20"


# ── Skips ───────────────────────────────────────────────────────────────────


async def test_manual_group_writes_no_ledger_row(fake):
    """A manual group's only job is suppressing UNMAPPED_PROJECT. It must not
    produce a payload, an estimate, or a row."""
    pool = await get_pool()
    fake.add_time(PLATFORM, spent_date="2026-07-06", hours=50, rate=185)
    await _group(pool, "Kestrel — Milestone", ACME, [PLATFORM], billing_type="manual")

    run = await planner.get_run(
        pool, await planner.plan_run(pool, settings, run_month=AUGUST)
    )
    assert run["items"] == []


async def test_group_with_no_uninvoiced_time_is_skipped_not_zero_invoiced(fake):
    pool = await get_pool()
    group = await _group(pool, "Delta — Analytics", ACME, [PLATFORM])

    run = await planner.get_run(
        pool, await planner.plan_run(pool, settings, run_month=AUGUST)
    )
    item = _item_for(run, group["id"])
    assert item["status"] == "skipped"
    assert "NO_UNINVOICED_TIME" in _codes(item)
    assert run["planned_count"] == 0


async def test_draw_billed_group_is_skipped_with_an_honest_reason(fake):
    """A draw-billed group is skipped by design, not for want of support: draws
    bill individually when delivery is confirmed. It must still appear in the
    run with a reason, never be silently absent.

    See `test_billing_draws.py` for the draw lifecycle itself.
    """
    pool = await get_pool()
    group = await _group(pool, "Summit — Portal Rebuild", ACME, [PLATFORM],
                         billing_type="fixed_fee_schedule")

    run = await planner.get_run(
        pool, await planner.plan_run(pool, settings, run_month=AUGUST)
    )
    item = _item_for(run, group["id"])
    assert item["status"] == "skipped"
    assert "billed individually from the Draws tab" in item["skip_reason"]


# ── The duplicate guard ─────────────────────────────────────────────────────


async def test_multi_group_client_does_not_false_positive(fake):
    """THE regression this guard exists for.

    Acme has two billing groups. Once group A's invoice is recorded in our
    ledger, group B's duplicate check must not flag it — Harvest filters
    invoices by client, and both groups' invoices land in the same window.
    """
    pool = await get_pool()
    fake.add_time(PLATFORM, spent_date="2026-07-06", hours=10, rate=185)
    fake.add_time(LAB, spent_date="2026-07-06", hours=10, rate=185)
    a = await _group(pool, "Acme — Platform", ACME, [PLATFORM])
    b = await _group(pool, "Acme — Lab", ACME, [LAB])

    # Group A was invoiced earlier in the month; Harvest knows about it and so
    # do we.
    fake.add_invoice(ACME, invoice_id=20481001, number="1084",
                     issue_date="2026-07-31", amount=1850.0)
    run_id = await planner.plan_run(pool, settings, run_month=AUGUST)
    run = await planner.get_run(pool, run_id)
    await pool.execute(
        "UPDATE billing_run_items SET status='created', harvest_invoice_id=20481001 "
        "WHERE id = $1",
        _item_for(run, a["id"])["id"],
    )

    # Re-plan. Group B must not be flagged because of group A's invoice.
    run2 = await planner.get_run(
        pool, await planner.plan_run(pool, settings, run_month=AUGUST)
    )
    assert "EXISTING_HARVEST_INVOICE" not in _codes(_item_for(run2, b["id"]))
    assert _item_for(run2, b["id"])["status"] == "planned"

    # Group A, however, is already invoiced this month and must not be
    # re-planned — that would double-bill.
    a_item = _item_for(run2, a["id"])
    assert a_item["status"] == "skipped"
    assert "ALREADY_INVOICED_THIS_RUN" in _codes(a_item)


async def test_created_group_cannot_be_replanned_in_the_same_month(fake):
    """Constraint C6, surfaced as a readable flag rather than a raw unique
    violation."""
    pool = await get_pool()
    fake.add_time(PLATFORM, spent_date="2026-07-06", hours=10, rate=185)
    group = await _group(pool, "Acme — Platform", ACME, [PLATFORM])

    first = await planner.plan_run(pool, settings, run_month=AUGUST)
    await pool.execute(
        "UPDATE billing_run_items SET status='created', harvest_invoice_id=20481001, "
        "harvest_invoice_number='1084' WHERE id = $1",
        _item_for(await planner.get_run(pool, first), group["id"])["id"],
    )

    run2 = await planner.get_run(
        pool, await planner.plan_run(pool, settings, run_month=AUGUST)
    )
    item = _item_for(run2, group["id"])
    assert item["status"] == "skipped"
    assert "1084" in item["skip_reason"]


async def test_invoice_created_by_hand_in_harvest_is_flagged(fake):
    """The other half: an invoice we have no ledger row for is exactly what
    the operator needs to know about."""
    pool = await get_pool()
    fake.add_time(PLATFORM, spent_date="2026-07-06", hours=10, rate=185)
    group = await _group(pool, "Acme — Platform", ACME, [PLATFORM])
    fake.add_invoice(ACME, invoice_id=99887766, number="1099",
                     issue_date="2026-07-31", amount=5000.0)

    run = await planner.get_run(
        pool, await planner.plan_run(pool, settings, run_month=AUGUST)
    )
    item = _item_for(run, group["id"])
    assert "EXISTING_HARVEST_INVOICE" in _codes(item)
    assert item["status"] == "planned"  # a warning, not a block


async def test_unresolved_in_flight_row_blocks_the_group(fake):
    pool = await get_pool()
    fake.add_time(NW_DATA, spent_date="2026-07-06", hours=10, rate=185)
    group = await _group(pool, "Northwind — Data", NORTHWIND, [NW_DATA])

    run_id = await planner.plan_run(pool, settings, run_month=date(2026, 7, 1))
    run = await planner.get_run(pool, run_id)
    await pool.execute(
        "UPDATE billing_run_items SET status='in_flight', "
        "error_message='Read timeout after 30s' WHERE id = $1",
        _item_for(run, group["id"])["id"],
    )

    run2 = await planner.get_run(
        pool, await planner.plan_run(pool, settings, run_month=AUGUST)
    )
    item = _item_for(run2, group["id"])
    assert "UNRESOLVED_IN_FLIGHT" in _codes(item)


# ── Re-planning ─────────────────────────────────────────────────────────────


async def test_replanning_a_month_abandons_the_prior_plan(fake):
    pool = await get_pool()
    fake.add_time(PLATFORM, spent_date="2026-07-06", hours=10, rate=185)
    await _group(pool, "Acme — Platform", ACME, [PLATFORM])

    first = await planner.plan_run(pool, settings, run_month=AUGUST)
    second = await planner.plan_run(pool, settings, run_month=AUGUST)

    assert first != second
    old = await planner.get_run(pool, first)
    assert old["status"] == "abandoned"
    assert all(i["status"] == "abandoned" for i in old["items"])
    assert (await planner.get_run(pool, second))["status"] == "awaiting_approval"


async def test_replanning_does_not_clear_an_in_flight_row(fake):
    """Only a human may resolve an in-flight row. A re-plan must never quietly
    abandon one — that's how a duplicate invoice gets created."""
    pool = await get_pool()
    fake.add_time(PLATFORM, spent_date="2026-07-06", hours=10, rate=185)
    group = await _group(pool, "Acme — Platform", ACME, [PLATFORM])

    first = await planner.plan_run(pool, settings, run_month=AUGUST)
    item = _item_for(await planner.get_run(pool, first), group["id"])
    await pool.execute(
        "UPDATE billing_run_items SET status='in_flight' WHERE id = $1", item["id"]
    )

    # The in-flight row holds the unique index, so the group cannot be
    # re-planned into a live row this month.
    second = await planner.plan_run(pool, settings, run_month=AUGUST)
    still = await pool.fetchval(
        "SELECT status FROM billing_run_items WHERE id = $1", item["id"]
    )
    assert still == "in_flight"
    assert _item_for(await planner.get_run(pool, second), group["id"])["status"] \
        == "skipped"


async def test_abandon_run_endpoint_logic(fake):
    pool = await get_pool()
    fake.add_time(PLATFORM, spent_date="2026-07-06", hours=10, rate=185)
    await _group(pool, "Acme — Platform", ACME, [PLATFORM])
    run_id = await planner.plan_run(pool, settings, run_month=AUGUST)

    assert await planner.abandon_run(pool, run_id) is True
    assert (await planner.get_run(pool, run_id))["status"] == "abandoned"

    with pytest.raises(planner.RunStateError):
        await planner.abandon_run(pool, run_id)


# ── Snapshot and roll-ups ───────────────────────────────────────────────────


async def test_plan_snapshot_is_frozen_at_plan_time(fake):
    """The snapshot must survive later config changes — it's the record of what
    the operator actually reviewed."""
    pool = await get_pool()
    fake.add_time(PLATFORM, spent_date="2026-07-06", hours=10, rate=185)
    group = await _group(pool, "Acme — Platform", ACME, [PLATFORM])
    run_id = await planner.plan_run(pool, settings, run_month=AUGUST)

    await groups_service.update_group(pool, group["id"], {"name": "Renamed later"})

    snapshot = (await planner.get_run(pool, run_id))["plan_snapshot"]
    assert snapshot["planned_count"] == 1
    assert snapshot["planned_total"] == pytest.approx(1850.0)
    assert snapshot["items"][0]["billing_group_name"] == "Acme — Platform"


async def test_run_level_unmapped_flag_is_recorded(fake):
    """A billable project in no group is the failure mode that loses revenue,
    so it must appear on the run itself, not just in config health."""
    pool = await get_pool()
    fake.add_time(NW_DATA, spent_date="2026-07-06", hours=62.5, rate=185)

    run = await planner.get_run(
        pool, await planner.plan_run(pool, settings, run_month=AUGUST)
    )
    assert "UNMAPPED_PROJECT" in {f["code"] for f in run["run_flags"]}
    assert run["flag_counts"]["error"] >= 1


async def test_ungrouped_project_with_no_time_warns_on_the_run(fake):
    """Nothing is missing from this run, but the project is one logged hour
    away from silently accruing uninvoiced time — so it gets said out loud."""
    pool = await get_pool()
    await _group(pool, "Acme — Platform", ACME, [PLATFORM])
    fake.add_time(PLATFORM, spent_date="2026-07-06", hours=10, rate=185)

    run = await planner.get_run(
        pool, await planner.plan_run(pool, settings, run_month=AUGUST)
    )
    warned = {
        f["context"]["harvest_project_id"] for f in run["run_flags"]
        if f["code"] == "UNMAPPED_PROJECT_NO_TIME"
    }
    # MOBILE, LAB and NW_DATA are billable, in no group, and carry no time.
    assert warned == {MOBILE, LAB, NW_DATA}
    # The grouped project must not warn, and nothing here is an error.
    assert PLATFORM not in warned
    assert run["flag_counts"].get("error", 0) == 0


async def test_list_runs_rolls_up_counts_and_totals(fake):
    pool = await get_pool()
    fake.add_time(PLATFORM, spent_date="2026-07-06", hours=10, rate=185)
    await _group(pool, "Acme — Platform", ACME, [PLATFORM])
    await planner.plan_run(pool, settings, run_month=AUGUST)

    runs = await planner.list_runs(pool)
    assert len(runs) == 1
    assert runs[0]["label"] == "August 2026"
    assert runs[0]["planned_count"] == 1
    assert runs[0]["planned_total"] == pytest.approx(1850.0)


async def test_planned_items_are_alphabetical_and_skipped_sink(fake):
    """A review you work top to bottom has to be in the same order every month,
    so ordering is by name — not by amount, which reshuffles monthly."""
    pool = await get_pool()
    fake.add_time(PLATFORM, spent_date="2026-07-06", hours=100, rate=185)
    fake.add_time(MOBILE, spent_date="2026-07-06", hours=1, rate=185)
    fake.add_time(NW_DATA, spent_date="2026-07-06", hours=10, rate=185)

    # Deliberately created out of order, and with amounts that would invert it.
    await _group(pool, "zeta retainer", ACME, [MOBILE])
    await _group(pool, "Acme — Platform", ACME, [PLATFORM])
    await _group(pool, "Northwind — Data", NORTHWIND, [NW_DATA])
    await _group(pool, "Acme — Lab (manual)", ACME, [LAB], billing_type="fixed_fee_schedule")

    run = await planner.get_run(
        pool, await planner.plan_run(pool, settings, run_month=AUGUST)
    )
    names = [i["billing_group_name"] for i in run["items"]]
    assert names == [
        "Acme — Platform",
        "Northwind — Data",
        "zeta retainer",       # lowercase sorts with the rest, not after it
        "Acme — Lab (manual)",  # skipped, so it sinks regardless of name
    ]


async def test_planning_defaults_to_the_current_month(fake):
    pool = await get_pool()
    run_id = await planner.plan_run(pool, settings)
    run = await planner.get_run(pool, run_id)
    assert run["run_month"] == date.today().replace(day=1)
