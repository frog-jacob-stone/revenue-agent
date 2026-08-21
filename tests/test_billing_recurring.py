"""Recurring monthly groups — hosting, management fees, retainers.

The shape this exists for: one invoice, several line items, spanning more than
one project, some with amounts only knowable after the fact.
"""
from __future__ import annotations

from datetime import date

import pytest

from app.config import settings
from app.db import get_pool
from app.services.billing import groups as groups_service
from app.services.billing import harvest_snapshot, planner
from tests.fakes.harvest import FakeHarvest

CLIENT = 5735833
HOSTING = 14308510
APP = 14308511

AUGUST = date(2026, 8, 1)


@pytest.fixture
async def fake(monkeypatch):
    f = FakeHarvest()
    f.add_client(CLIENT, "Brightline Logistics")
    f.add_project(HOSTING, "Brightline Hosting", client_id=CLIENT, is_fixed_fee=True)
    f.add_project(APP, "Brightline App Support", client_id=CLIENT, is_fixed_fee=True)
    f.install(monkeypatch)
    await harvest_snapshot.refresh_snapshot(await get_pool(), settings)
    return f


def _line(project_id: int, description: str, **over):
    return {
        "harvest_project_id": project_id,
        "description": description,
        "quantity": 1,
        "unit_price": 0,
        "kind": "Service",
        "is_placeholder": False,
        "sort_order": 0,
        "effective_from": None,
        "effective_to": None,
        **over,
    }


async def _make_group(pool, items, **over):
    return await groups_service.create_group(pool, {
        "name": "Brightline — Monthly",
        "harvest_client_id": CLIENT,
        "billing_type": "recurring_monthly",
        "billing_timing": "advance",
        "payment_term": "net 30",
        "projects": [
            {"harvest_project_id": HOSTING},
            {"harvest_project_id": APP},
        ],
        "recurring_items": items,
        **over,
    })


def _item(run, group_id):
    return next(i for i in run["items"] if i["billing_group_id"] == group_id)


def _codes(item) -> set[str]:
    return {f["code"] for f in item["flags"]}


# ── The real-world shape ────────────────────────────────────────────────────


async def test_multi_project_invoice_with_fixed_and_placeholder_lines(fake):
    """Hosting (entered later) + management fee (flat) + tooling (entered later)
    on the hosting project, plus a service fee on a different project — all on
    one invoice."""
    pool = await get_pool()
    group = await _make_group(pool, [
        _line(HOSTING, "Hosting — {period_label}", kind="Billable Expense",
              is_placeholder=True),
        _line(HOSTING, "Hosting management fee — {period_label}", unit_price=1500),
        _line(HOSTING, "Tooling fee — {period_label}", kind="Billable Expense",
              is_placeholder=True),
        _line(APP, "Monthly service fee — {period_label}", unit_price=4200),
    ])

    run = await planner.get_run(
        pool, await planner.plan_run(pool, settings, run_month=AUGUST)
    )
    item = _item(run, group["id"])

    assert item["status"] == "planned"
    # Only the two fixed lines contribute; placeholders must not be guessed at.
    assert float(item["planned_amount"]) == pytest.approx(5700.0)

    lines = item["planned_payload"]["line_items"]
    assert len(lines) == 4
    assert [line["project_id"] for line in lines] == [HOSTING, HOSTING, HOSTING, APP]
    assert [line["kind"] for line in lines] == [
        "Billable Expense", "Service", "Billable Expense", "Service",
    ]
    # {period_label} is rendered at plan time, not stored rendered.
    assert lines[0]["description"] == "Hosting — August 2026"
    assert lines[1]["unit_price"] == 1500
    # An undecided placeholder still goes out at zero, so the shape of the
    # invoice is visible before any amount is known. It cannot reach Harvest
    # that way — approval is blocked while it is unresolved.
    assert lines[0]["unit_price"] == 0
    assert lines[2]["unit_price"] == 0

    assert "PLACEHOLDER_LINE_ITEMS" in _codes(item)
    ph = next(f for f in item["flags"] if f["code"] == "PLACEHOLDER_LINE_ITEMS")
    assert len(ph["context"]["unresolved"]) == 2
    assert ph["context"]["resolved"] == []
    assert ph["context"]["omitted"] == []


async def test_advance_timing_bills_the_current_month(fake):
    pool = await get_pool()
    group = await _make_group(pool, [_line(HOSTING, "Retainer", unit_price=8500)])

    run = await planner.get_run(
        pool, await planner.plan_run(pool, settings, run_month=AUGUST)
    )
    item = _item(run, group["id"])
    assert item["period_start"] == date(2026, 8, 1)
    assert item["period_end"] == date(2026, 8, 31)
    assert item["issue_date"] == date(2026, 8, 1)


async def test_quantity_multiplies_unit_price(fake):
    pool = await get_pool()
    group = await _make_group(pool, [
        _line(HOSTING, "Support blocks", quantity=3, unit_price=500),
    ])

    run = await planner.get_run(
        pool, await planner.plan_run(pool, settings, run_month=AUGUST)
    )
    assert float(_item(run, group["id"])["planned_amount"]) == pytest.approx(1500.0)


async def test_group_with_no_line_items_is_skipped(fake):
    pool = await get_pool()
    group = await _make_group(pool, [])

    run = await planner.get_run(
        pool, await planner.plan_run(pool, settings, run_month=AUGUST)
    )
    item = _item(run, group["id"])
    assert item["status"] == "skipped"
    assert "NO_RECURRING_ITEMS" in _codes(item)


async def test_all_placeholder_group_still_plans(fake):
    """A group whose every line is a placeholder is worth planning — the shape
    of the invoice is real even before any amount is. It must not be mistaken
    for an empty group and skipped."""
    pool = await get_pool()
    group = await _make_group(pool, [
        _line(HOSTING, "Hosting", kind="Billable Expense", is_placeholder=True),
    ])

    run = await planner.get_run(
        pool, await planner.plan_run(pool, settings, run_month=AUGUST)
    )
    item = _item(run, group["id"])
    assert item["status"] == "planned"
    assert float(item["planned_amount"]) == 0.0
    assert "PLACEHOLDER_LINE_ITEMS" in _codes(item)
    assert "NO_RECURRING_ITEMS" not in _codes(item)


# ── Effective dating ────────────────────────────────────────────────────────


async def test_superseded_fee_stops_applying_without_losing_history(fake):
    pool = await get_pool()
    group = await _make_group(pool, [
        _line(HOSTING, "Management fee", unit_price=1200,
              effective_from=date(2025, 1, 1), effective_to=date(2026, 7, 31)),
        _line(HOSTING, "Management fee", unit_price=1500,
              effective_from=date(2026, 8, 1)),
    ])

    run = await planner.get_run(
        pool, await planner.plan_run(pool, settings, run_month=AUGUST)
    )
    item = _item(run, group["id"])

    assert float(item["planned_amount"]) == pytest.approx(1500.0)
    assert len(item["planned_payload"]["line_items"]) == 1
    # The old row is still configured, just not in force.
    detail = await groups_service.get_group(pool, group["id"])
    assert len(detail["recurring_items"]) == 2


async def test_item_not_yet_effective_is_excluded(fake):
    pool = await get_pool()
    group = await _make_group(pool, [
        _line(HOSTING, "Future fee", unit_price=999,
              effective_from=date(2026, 12, 1)),
        _line(HOSTING, "Current fee", unit_price=100),
    ])

    run = await planner.get_run(
        pool, await planner.plan_run(pool, settings, run_month=AUGUST)
    )
    assert float(_item(run, group["id"])["planned_amount"]) == pytest.approx(100.0)


# ── Validation ──────────────────────────────────────────────────────────────


async def test_invalid_item_category_is_rejected_at_save(fake):
    """Caught at config time so it can never become a 422 mid-execution."""
    pool = await get_pool()
    with pytest.raises(groups_service.BillingConfigError, match="does not exist"):
        await _make_group(pool, [_line(HOSTING, "Bad kind", kind="Nonsense")])


async def test_line_item_must_target_a_project_in_the_group(fake):
    pool = await get_pool()
    with pytest.raises(groups_service.BillingConfigError, match="must target a project"):
        await groups_service.create_group(pool, {
            "name": "Brightline — Hosting only",
            "harvest_client_id": CLIENT,
            "billing_type": "recurring_monthly",
            "projects": [{"harvest_project_id": HOSTING}],
            "recurring_items": [_line(APP, "Wrong project", unit_price=100)],
        })


async def test_rejection_names_the_valid_categories(fake):
    """The operator needs to know what *is* allowed, not just that they were
    wrong."""
    pool = await get_pool()
    with pytest.raises(groups_service.BillingConfigError) as exc:
        await _make_group(pool, [_line(HOSTING, "x", kind="Retainer")])
    message = str(exc.value)
    for expected in ("Service", "Billable Expense", "Discount", "Advanced Deposit"):
        assert expected in message


# ── Round-tripping through the service layer ────────────────────────────────


async def test_line_items_round_trip(fake):
    pool = await get_pool()
    group = await _make_group(pool, [
        _line(HOSTING, "Hosting", kind="Billable Expense", is_placeholder=True),
        _line(APP, "Service fee", unit_price=4200, quantity=2),
    ])

    detail = await groups_service.get_group(pool, group["id"])
    items = sorted(detail["recurring_items"], key=lambda r: r["sort_order"])
    assert [r["description"] for r in items] == ["Hosting", "Service fee"]
    assert items[0]["is_placeholder"] is True
    assert items[0]["kind"] == "Billable Expense"
    assert float(items[1]["unit_price"]) == 4200
    assert float(items[1]["quantity"]) == 2


async def test_update_replaces_line_items(fake):
    pool = await get_pool()
    group = await _make_group(pool, [_line(HOSTING, "Old", unit_price=100)])

    updated = await groups_service.update_group(pool, group["id"], {
        "recurring_items": [
            _line(HOSTING, "New A", unit_price=200),
            _line(APP, "New B", unit_price=300),
        ],
    })
    assert {r["description"] for r in updated["recurring_items"]} == {"New A", "New B"}


# ── Placeholder resolution applied at plan time ─────────────────────────────


async def _resolve(pool, group_id, description: str, run_month=AUGUST, **fields):
    """Record a resolution the way the operator's click eventually will."""
    line_id = await pool.fetchval(
        "SELECT id FROM recurring_line_items "
        "WHERE billing_group_id = $1 AND description = $2",
        group_id, description,
    )
    await pool.execute(
        """
        INSERT INTO recurring_line_item_resolutions
            (recurring_line_item_id, run_month, resolution, unit_price, quantity)
        VALUES ($1, $2, $3::recurring_line_item_resolution, $4, $5)
        """,
        line_id, run_month, fields.get("resolution", "amount"),
        fields.get("unit_price"), fields.get("quantity"),
    )
    return line_id


def _line_by(item, label: str):
    return next(li for li in item["estimated_line_items"] if li["label"] == label)


async def test_a_resolved_placeholder_bills_at_its_entered_price(fake):
    pool = await get_pool()
    group = await _make_group(pool, [
        _line(HOSTING, "Hosting", kind="Billable Expense", is_placeholder=True),
        _line(APP, "Service fee", unit_price=4200),
    ])
    await _resolve(pool, group["id"], "Hosting", unit_price=1240)

    run = await planner.get_run(
        pool, await planner.plan_run(pool, settings, run_month=AUGUST)
    )
    item = _item(run, group["id"])

    # The total is now a forecast, not a floor: 4200 + 1240.
    assert float(item["planned_amount"]) == pytest.approx(5440.0)
    hosting = next(
        li for li in item["planned_payload"]["line_items"]
        if li["description"] == "Hosting"
    )
    assert hosting["unit_price"] == 1240
    assert _line_by(item, "Hosting")["placeholder_state"] == "resolved"

    ph = next(f for f in item["flags"] if f["code"] == "PLACEHOLDER_LINE_ITEMS")
    assert ph["context"]["unresolved"] == []
    assert len(ph["context"]["resolved"]) == 1


async def test_an_omitted_placeholder_leaves_the_payload_but_stays_on_screen(fake):
    """The point of omitting rather than deleting: next month it is back,
    asking again."""
    pool = await get_pool()
    group = await _make_group(pool, [
        _line(HOSTING, "Retainer overage", is_placeholder=True),
        _line(APP, "Service fee", unit_price=4200),
    ])
    await _resolve(pool, group["id"], "Retainer overage", resolution="omitted")

    run = await planner.get_run(
        pool, await planner.plan_run(pool, settings, run_month=AUGUST)
    )
    item = _item(run, group["id"])

    assert float(item["planned_amount"]) == pytest.approx(4200.0)
    descriptions = [li["description"] for li in item["planned_payload"]["line_items"]]
    assert descriptions == ["Service fee"]
    # Still in the pre-flight table, so the reminder survives the omission.
    omitted = _line_by(item, "Retainer overage")
    assert omitted["placeholder_state"] == "omitted"
    assert "omitted for August 2026" in omitted["detail"]


async def test_a_resolution_may_override_the_quantity(fake):
    """An overage is often quantity-shaped — 12 hours at $175, not a flat sum."""
    pool = await get_pool()
    group = await _make_group(pool, [
        _line(HOSTING, "Overage hours", is_placeholder=True),
    ])
    await _resolve(pool, group["id"], "Overage hours", unit_price=175, quantity=12)

    run = await planner.get_run(
        pool, await planner.plan_run(pool, settings, run_month=AUGUST)
    )
    item = _item(run, group["id"])

    assert float(item["planned_amount"]) == pytest.approx(2100.0)
    line = item["planned_payload"]["line_items"][0]
    assert line["quantity"] == 12
    assert line["unit_price"] == 175


async def test_a_resolution_for_another_month_does_not_apply(fake):
    """The key is the month. July's hosting figure must not bill in August."""
    pool = await get_pool()
    group = await _make_group(pool, [
        _line(HOSTING, "Hosting", kind="Billable Expense", is_placeholder=True),
    ])
    await _resolve(pool, group["id"], "Hosting",
                   run_month=date(2026, 7, 1), unit_price=1180)

    run = await planner.get_run(
        pool, await planner.plan_run(pool, settings, run_month=AUGUST)
    )
    item = _item(run, group["id"])

    assert float(item["planned_amount"]) == 0.0
    assert _line_by(item, "Hosting")["placeholder_state"] == "unresolved"


async def test_a_group_whose_every_line_is_omitted_is_skipped(fake):
    """No lines means no invoice — but the reason must say what happened, not
    send the operator looking for missing config."""
    pool = await get_pool()
    group = await _make_group(pool, [
        _line(HOSTING, "Retainer overage", is_placeholder=True),
    ])
    await _resolve(pool, group["id"], "Retainer overage", resolution="omitted")

    run = await planner.get_run(
        pool, await planner.plan_run(pool, settings, run_month=AUGUST)
    )
    item = _item(run, group["id"])

    assert item["status"] == "skipped"
    assert "omitted for August 2026" in item["skip_reason"]


async def test_a_resolution_is_ignored_once_the_line_is_no_longer_a_placeholder(fake):
    """Turning the flag off in config means the configured price is the honest
    answer. The resolution row stays, in case the flag comes back on."""
    pool = await get_pool()
    group = await _make_group(pool, [
        _line(HOSTING, "Hosting", kind="Billable Expense", is_placeholder=True),
    ])
    line_id = await _resolve(pool, group["id"], "Hosting", unit_price=1240)

    await groups_service.update_group(pool, group["id"], {
        "recurring_items": [
            {**_line(HOSTING, "Hosting", kind="Billable Expense",
                     is_placeholder=False, unit_price=900),
             "id": line_id},
        ],
    })

    run = await planner.get_run(
        pool, await planner.plan_run(pool, settings, run_month=AUGUST)
    )
    item = _item(run, group["id"])

    assert float(item["planned_amount"]) == pytest.approx(900.0)
    assert _line_by(item, "Hosting")["placeholder_state"] is None
    assert "PLACEHOLDER_LINE_ITEMS" not in _codes(item)
    # The decision is still on record.
    assert await pool.fetchval(
        "SELECT count(*) FROM recurring_line_item_resolutions "
        "WHERE recurring_line_item_id = $1", line_id,
    ) == 1


async def test_the_payload_is_rebuildable_from_the_estimated_line_items(fake):
    """The property the annotation exists for. Resolving a placeholder rebuilds
    the payload from the ledger row alone, so the row has to describe every
    line completely — project, category, rendered description, and price."""
    pool = await get_pool()
    group = await _make_group(pool, [
        _line(HOSTING, "Hosting — {period_label}", kind="Billable Expense",
              is_placeholder=True),
        _line(APP, "Service fee", unit_price=4200, quantity=2),
    ])
    await _resolve(pool, group["id"], "Hosting — {period_label}", unit_price=1240)

    run = await planner.get_run(
        pool, await planner.plan_run(pool, settings, run_month=AUGUST)
    )
    item = _item(run, group["id"])

    rebuilt = [
        {
            "project_id": li["harvest_project_id"],
            "kind": li["kind"],
            "description": li["label"],
            "quantity": li["quantity"],
            "unit_price": li["unit_price"],
        }
        for li in item["estimated_line_items"]
        if li["placeholder_state"] != "omitted"
    ]
    assert rebuilt == item["planned_payload"]["line_items"]


async def test_placeholder_price_is_forced_to_zero_on_save(fake):
    """Belt and braces: a placeholder must never carry a stale amount that
    would silently bill the client."""
    pool = await get_pool()
    group = await _make_group(pool, [
        _line(HOSTING, "Hosting", kind="Billable Expense",
              is_placeholder=True, unit_price=9999),
    ])
    detail = await groups_service.get_group(pool, group["id"])
    assert float(detail["recurring_items"][0]["unit_price"]) == 0.0


@pytest.mark.parametrize("first_month_billed", [
    date(2026, 8, 1),   # first of the month
    date(2026, 8, 15),  # mid-month — must still mean "bill August"
    date(2026, 8, 31),  # last of the month
])
async def test_effective_from_is_month_granular(fake, first_month_billed):
    """The UI labels these "first / last month billed", so any day within a
    month has to mean that whole month. Comparing exact dates would silently
    skip the very month the operator named."""
    pool = await get_pool()
    group = await _make_group(pool, [
        _line(HOSTING, "Fee", unit_price=500, effective_from=first_month_billed),
    ])

    run = await planner.get_run(
        pool, await planner.plan_run(pool, settings, run_month=AUGUST)
    )
    assert float(_item(run, group["id"])["planned_amount"]) == pytest.approx(500.0)


@pytest.mark.parametrize("last_month_billed", [
    date(2026, 7, 1),
    date(2026, 7, 15),
    date(2026, 7, 31),
])
async def test_effective_to_is_month_granular(fake, last_month_billed):
    """Retiring a fee "through July" must not leak into August."""
    pool = await get_pool()
    group = await _make_group(pool, [
        _line(HOSTING, "Retired fee", unit_price=500, effective_to=last_month_billed),
        _line(HOSTING, "Live fee", unit_price=100),
    ])

    run = await planner.get_run(
        pool, await planner.plan_run(pool, settings, run_month=AUGUST)
    )
    item = _item(run, group["id"])
    assert float(item["planned_amount"]) == pytest.approx(100.0)
    assert [line["description"] for line in item["planned_payload"]["line_items"]] == ["Live fee"]


async def test_effective_to_in_the_billed_month_still_applies(fake):
    """"Last month billed = August" means August bills."""
    pool = await get_pool()
    group = await _make_group(pool, [
        _line(HOSTING, "Final month", unit_price=750, effective_to=date(2026, 8, 1)),
    ])

    run = await planner.get_run(
        pool, await planner.plan_run(pool, settings, run_month=AUGUST)
    )
    assert float(_item(run, group["id"])["planned_amount"]) == pytest.approx(750.0)
