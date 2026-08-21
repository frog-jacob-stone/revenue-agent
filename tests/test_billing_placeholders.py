"""Placeholder resolution — pricing or omitting a line before the draft exists.

The property this file is built around: after any resolution, the three things
the operator and the eventual Harvest write both read — `planned_amount`,
`planned_payload`, and `estimated_line_items` — agree with each other. A
resolution that updated the display but not the payload would be worse than no
feature at all: the pre-flight would show the right number and the invoice would
carry the wrong one.
"""
from __future__ import annotations

from datetime import date
from uuid import uuid4

import pytest

from app.config import settings
from app.db import get_pool
from app.services.billing import groups as groups_service
from app.services.billing import harvest_snapshot, placeholders, planner
from tests.fakes.harvest import FakeHarvest

CLIENT = 5735901
HOSTING = 14309101
APP = 14309102

AUGUST = date(2026, 8, 1)


@pytest.fixture
async def fake(monkeypatch):
    f = FakeHarvest()
    f.add_client(CLIENT, "Kestrel Freight")
    f.add_project(HOSTING, "Kestrel Hosting", client_id=CLIENT, is_fixed_fee=True)
    f.add_project(APP, "Kestrel App Support", client_id=CLIENT, is_fixed_fee=True)
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
        "name": "Kestrel — Monthly",
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


async def _plan(pool):
    run_id = await planner.plan_run(pool, settings, run_month=AUGUST)
    return await planner.get_run(pool, run_id)


def _item(run, group_id):
    return next(i for i in run["items"] if i["billing_group_id"] == group_id)


def _line_by(item, label: str):
    return next(li for li in item["estimated_line_items"] if li["label"] == label)


def _line_id(item, label: str):
    return _line_by(item, label)["recurring_line_item_id"]


def _payload_descriptions(item):
    return [li["description"] for li in item["planned_payload"]["line_items"]]


async def _standard(pool):
    """One placeholder, one fixed line, planned and ready to resolve."""
    group = await _make_group(pool, [
        _line(HOSTING, "Hosting", kind="Billable Expense", is_placeholder=True),
        _line(APP, "Service fee", unit_price=4200),
    ])
    run = await _plan(pool)
    return group, run, _item(run, group["id"])


# ── Pricing a placeholder ───────────────────────────────────────────────────


async def test_pricing_a_placeholder_updates_amount_payload_and_display(fake):
    """The coherence property. All three move together, or none of them should."""
    pool = await get_pool()
    group, run, item = await _standard(pool)
    assert float(item["planned_amount"]) == pytest.approx(4200.0)

    assert await placeholders.set_resolution(
        pool, run["id"], item["id"], _line_id(item, "Hosting"),
        resolution="amount", unit_price=1240, actor="jacob@frogslayer.com",
    )

    fresh = _item(await planner.get_run(pool, run["id"]), group["id"])
    assert float(fresh["planned_amount"]) == pytest.approx(5440.0)

    hosting_payload = next(
        li for li in fresh["planned_payload"]["line_items"]
        if li["description"] == "Hosting"
    )
    assert hosting_payload["unit_price"] == 1240

    hosting_display = _line_by(fresh, "Hosting")
    assert hosting_display["placeholder_state"] == "resolved"
    assert hosting_display["unit_price"] == 1240
    assert hosting_display["amount"] == 1240
    # The "needs an amount" nudge is gone once it has one.
    assert hosting_display["detail"] == "Billable Expense"

    # And the total the payload implies matches the stored one.
    assert sum(
        li["quantity"] * li["unit_price"]
        for li in fresh["planned_payload"]["line_items"]
    ) == pytest.approx(float(fresh["planned_amount"]))


async def test_a_quantity_override_multiplies_out(fake):
    pool = await get_pool()
    group, run, item = await _standard(pool)

    await placeholders.set_resolution(
        pool, run["id"], item["id"], _line_id(item, "Hosting"),
        resolution="amount", unit_price=175, quantity=12,
    )

    fresh = _item(await planner.get_run(pool, run["id"]), group["id"])
    assert float(fresh["planned_amount"]) == pytest.approx(4200.0 + 2100.0)
    line = _line_by(fresh, "Hosting")
    assert (line["quantity"], line["unit_price"], line["amount"]) == (12, 175, 2100)


async def test_re_pricing_replaces_rather_than_stacks(fake):
    """The unique index makes this an update. A second row would be unreachable
    and the two would disagree about what August costs."""
    pool = await get_pool()
    group, run, item = await _standard(pool)
    line_id = _line_id(item, "Hosting")

    await placeholders.set_resolution(
        pool, run["id"], item["id"], line_id, resolution="amount", unit_price=1240,
    )
    await placeholders.set_resolution(
        pool, run["id"], item["id"], line_id, resolution="amount", unit_price=1310,
    )

    fresh = _item(await planner.get_run(pool, run["id"]), group["id"])
    assert float(fresh["planned_amount"]) == pytest.approx(5510.0)
    assert await pool.fetchval(
        "SELECT count(*) FROM recurring_line_item_resolutions "
        "WHERE recurring_line_item_id = $1", line_id,
    ) == 1


async def test_an_amount_with_no_price_is_refused(fake):
    """A missing price must not land as a silent $0 — indistinguishable from
    unresolved, and bills the client nothing."""
    pool = await get_pool()
    _, run, item = await _standard(pool)

    with pytest.raises(placeholders.PlaceholderError, match="needs a unit price"):
        await placeholders.set_resolution(
            pool, run["id"], item["id"], _line_id(item, "Hosting"),
            resolution="amount",
        )


# ── Omitting for the month ──────────────────────────────────────────────────


async def test_omitting_drops_the_payload_line_but_keeps_it_on_screen(fake):
    pool = await get_pool()
    group, run, item = await _standard(pool)

    await placeholders.set_resolution(
        pool, run["id"], item["id"], _line_id(item, "Hosting"),
        resolution="omitted", note="no hosting overage in August",
    )

    fresh = _item(await planner.get_run(pool, run["id"]), group["id"])
    assert float(fresh["planned_amount"]) == pytest.approx(4200.0)
    assert _payload_descriptions(fresh) == ["Service fee"]

    # Still on screen — that is what makes it a reminder next month.
    omitted = _line_by(fresh, "Hosting")
    assert omitted["placeholder_state"] == "omitted"
    assert omitted["amount"] == 0
    assert "omitted for August 2026" in omitted["detail"]


async def test_the_omit_note_is_recorded(fake):
    """Most valuable on an omit, where the record would otherwise be
    indistinguishable from a line that was never there."""
    pool = await get_pool()
    _, run, item = await _standard(pool)
    line_id = _line_id(item, "Hosting")

    await placeholders.set_resolution(
        pool, run["id"], item["id"], line_id,
        resolution="omitted", note="checked Harvest — no overage",
    )

    assert await pool.fetchval(
        "SELECT note FROM recurring_line_item_resolutions "
        "WHERE recurring_line_item_id = $1", line_id,
    ) == "checked Harvest — no overage"


async def test_omitting_then_pricing_brings_the_line_back(fake):
    pool = await get_pool()
    group, run, item = await _standard(pool)
    line_id = _line_id(item, "Hosting")

    await placeholders.set_resolution(
        pool, run["id"], item["id"], line_id, resolution="omitted",
    )
    await placeholders.set_resolution(
        pool, run["id"], item["id"], line_id, resolution="amount", unit_price=1240,
    )

    fresh = _item(await planner.get_run(pool, run["id"]), group["id"])
    assert _payload_descriptions(fresh) == ["Hosting", "Service fee"]
    assert float(fresh["planned_amount"]) == pytest.approx(5440.0)


# ── Withdrawing a decision ──────────────────────────────────────────────────


async def test_clearing_returns_the_line_to_undecided_at_zero(fake):
    pool = await get_pool()
    group, run, item = await _standard(pool)
    line_id = _line_id(item, "Hosting")

    await placeholders.set_resolution(
        pool, run["id"], item["id"], line_id, resolution="amount", unit_price=1240,
    )
    assert await placeholders.clear_resolution(
        pool, run["id"], item["id"], line_id,
    )

    fresh = _item(await planner.get_run(pool, run["id"]), group["id"])
    assert float(fresh["planned_amount"]) == pytest.approx(4200.0)
    line = _line_by(fresh, "Hosting")
    assert line["placeholder_state"] == "unresolved"
    assert line["unit_price"] == 0
    assert "needs an amount" in line["detail"]
    # The line is back in the payload at $0 — undecided, not omitted.
    assert _payload_descriptions(fresh) == ["Hosting", "Service fee"]
    assert await pool.fetchval(
        "SELECT count(*) FROM recurring_line_item_resolutions "
        "WHERE recurring_line_item_id = $1", line_id,
    ) == 0


# ── Everything else on the payload is left alone ────────────────────────────


async def test_only_the_line_items_change(fake):
    """Subject, dates, notes, and payment term were settled at plan time. A
    resolution has no business touching them."""
    pool = await get_pool()
    group, run, item = await _standard(pool)
    before = {k: v for k, v in item["planned_payload"].items() if k != "line_items"}

    await placeholders.set_resolution(
        pool, run["id"], item["id"], _line_id(item, "Hosting"),
        resolution="amount", unit_price=1240,
    )

    fresh = _item(await planner.get_run(pool, run["id"]), group["id"])
    after = {k: v for k, v in fresh["planned_payload"].items() if k != "line_items"}
    assert after == before


# ── Approval interaction ────────────────────────────────────────────────────


async def test_resolving_an_approved_item_un_approves_it(fake):
    """The approval described the old payload. ADR-0004 condition 1 says the
    operator must have seen the exact payload, so they get to look again."""
    pool = await get_pool()
    from app.services.billing import review

    group, run, item = await _standard(pool)
    line_id = _line_id(item, "Hosting")

    await placeholders.set_resolution(
        pool, run["id"], item["id"], line_id, resolution="amount", unit_price=1240,
    )
    await review.set_item_approval(
        pool, run["id"], item["id"], approved=True, actor="jacob@frogslayer.com",
    )
    assert _item(await planner.get_run(pool, run["id"]), group["id"])["status"] == "approved"

    await placeholders.set_resolution(
        pool, run["id"], item["id"], line_id, resolution="amount", unit_price=1310,
    )

    fresh = _item(await planner.get_run(pool, run["id"]), group["id"])
    assert fresh["status"] == "planned"
    assert fresh["approved_at"] is None
    assert fresh["approved_by"] is None


# ── Guards ──────────────────────────────────────────────────────────────────


async def test_a_time_and_materials_item_is_refused(fake):
    """T&M lines are aggregated from Harvest time entries — there is no config
    row to resolve, and no placeholder concept."""
    pool = await get_pool()
    tm = await groups_service.create_group(pool, {
        "name": "Kestrel — T&M",
        "harvest_client_id": CLIENT,
        "billing_type": "time_and_materials",
        "projects": [{"harvest_project_id": APP}],
    })
    recurring_group = await _make_group(
        pool,
        [_line(HOSTING, "Hosting", kind="Billable Expense", is_placeholder=True)],
        projects=[{"harvest_project_id": HOSTING}],
    )
    run = await _plan(pool)
    tm_item = next((i for i in run["items"] if i["billing_group_id"] == tm["id"]), None)
    if tm_item is None:
        pytest.skip("T&M group planned no ledger row (no uninvoiced time)")
    line_id = _line_id(_item(run, recurring_group["id"]), "Hosting")

    with pytest.raises(placeholders.PlaceholderError, match="time_and_materials"):
        await placeholders.set_resolution(
            pool, run["id"], tm_item["id"], line_id,
            resolution="amount", unit_price=100,
        )


async def test_a_line_that_is_not_on_this_invoice_is_not_found(fake):
    pool = await get_pool()
    _, run, item = await _standard(pool)

    with pytest.raises(placeholders.PlaceholderNotFound):
        await placeholders.set_resolution(
            pool, run["id"], item["id"], uuid4(),
            resolution="amount", unit_price=100,
        )


async def test_a_fixed_line_cannot_be_resolved(fake):
    """Resolution is for lines whose amount is genuinely unknown. A fixed fee is
    changed in config, where the change persists past this month."""
    pool = await get_pool()
    _, run, item = await _standard(pool)

    with pytest.raises(placeholders.PlaceholderNotFound):
        await placeholders.set_resolution(
            pool, run["id"], item["id"], _line_id(item, "Service fee"),
            resolution="amount", unit_price=9999,
        )


async def test_an_abandoned_run_is_refused(fake):
    pool = await get_pool()
    _, run, item = await _standard(pool)
    line_id = _line_id(item, "Hosting")
    await planner.abandon_run(pool, run["id"])

    with pytest.raises(placeholders.PlaceholderError, match="under review"):
        await placeholders.set_resolution(
            pool, run["id"], item["id"], line_id,
            resolution="amount", unit_price=1240,
        )


async def test_an_unknown_item_returns_false(fake):
    pool = await get_pool()
    _, run, item = await _standard(pool)

    assert await placeholders.set_resolution(
        pool, run["id"], uuid4(), _line_id(item, "Hosting"),
        resolution="amount", unit_price=100,
    ) is False


# ── Survival ────────────────────────────────────────────────────────────────


async def test_a_resolution_survives_a_replan(fake):
    """Re-plan is the normal response to fixing a config problem. If it discarded
    the amounts already entered, the forgetting this feature prevents would come
    straight back in through that door."""
    pool = await get_pool()
    group, run, item = await _standard(pool)

    await placeholders.set_resolution(
        pool, run["id"], item["id"], _line_id(item, "Hosting"),
        resolution="amount", unit_price=1240,
    )

    replanned = _item(await _plan(pool), group["id"])

    assert replanned["id"] != item["id"]
    assert float(replanned["planned_amount"]) == pytest.approx(5440.0)
    assert _line_by(replanned, "Hosting")["placeholder_state"] == "resolved"


async def test_a_resolution_survives_an_unrelated_config_edit(fake):
    """Stable line-item ids in service of the same guarantee: changing the
    service fee must not silently discard August's hosting amount."""
    pool = await get_pool()
    group, run, item = await _standard(pool)
    hosting_id = _line_id(item, "Hosting")

    await placeholders.set_resolution(
        pool, run["id"], item["id"], hosting_id,
        resolution="amount", unit_price=1240,
    )

    detail = await groups_service.get_group(pool, group["id"])
    await groups_service.update_group(pool, group["id"], {
        "recurring_items": [
            {**_line(r["harvest_project_id"], r["description"],
                     kind=r["kind"], is_placeholder=r["is_placeholder"],
                     unit_price=5000 if r["description"] == "Service fee"
                     else float(r["unit_price"])),
             "id": r["id"]}
            for r in detail["recurring_items"]
        ],
    })

    replanned = _item(await _plan(pool), group["id"])
    assert float(replanned["planned_amount"]) == pytest.approx(6240.0)
    assert _line_by(replanned, "Hosting")["unit_price"] == 1240


async def test_the_decision_is_audited_with_the_amount_and_the_actor(fake):
    """"Who said August's hosting was $1,240" is the question worth answering
    later — no config row accounts for that number."""
    pool = await get_pool()
    _, run, item = await _standard(pool)

    await placeholders.set_resolution(
        pool, run["id"], item["id"], _line_id(item, "Hosting"),
        resolution="amount", unit_price=1240, actor="jacob@frogslayer.com",
    )

    row = await pool.fetchrow(
        "SELECT actor, payload FROM audit_log "
        "WHERE event_type = 'billing.placeholder.resolved' "
        "ORDER BY occurred_at DESC, id DESC LIMIT 1"
    )
    assert row["actor"] == "jacob@frogslayer.com"
    assert row["payload"]["unit_price"] == 1240
    assert row["payload"]["resolution"] == "amount"
    assert row["payload"]["description"] == "Hosting"
    assert row["payload"]["run_month"] == "2026-08-01"
