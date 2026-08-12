"""The Billed list — every invoice this system created, both kinds together.

Exists because the first live invoice was created and then had nowhere to be
seen. The list is deliberately kind-agnostic: draws are all that can produce a
`created` row today, but a monthly row must appear here without a code change
when that execution ships, so the monthly cases below are set up by inserting
ledger rows directly.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.config import settings
from app.db import get_pool
from app.services.billing import draws, harvest_snapshot, invoices
from app.services.billing import groups as groups_service
from tests.fakes.harvest import FakeHarvest

CLIENT = 5735774
ERP = 14308912
# A second project, because a project belongs to at most one active group — the
# draw group and the T&M group cannot share one.
PORTAL = 14308913
AUGUST = date(2026, 8, 1)
LAST_MONTH = (date.today().replace(day=1) - timedelta(days=1)).replace(day=15)


@pytest.fixture
async def fake(monkeypatch):
    f = FakeHarvest()
    f.add_client(CLIENT, "Ridgeway Industrial")
    f.add_project(ERP, "Ridgeway ERP", client_id=CLIENT, is_fixed_fee=True)
    f.add_project(PORTAL, "Ridgeway Portal", client_id=CLIENT, hourly_rate=185)
    f.install(monkeypatch)
    await harvest_snapshot.refresh_snapshot(await get_pool(), settings)
    return f


async def _bill_a_draw(pool, *, description: str = "Draw 1 — Signing"):
    group = await groups_service.create_group(pool, {
        "name": f"Ridgeway — {description}",
        "harvest_client_id": CLIENT,
        "billing_type": "fixed_fee_schedule",
        "payment_term": "net 30",
        "projects": [{"harvest_project_id": ERP}],
        "schedule_items": [{
            "harvest_project_id": ERP,
            "description": description,
            "amount": 37500,
            "kind": "Service",
            "scheduled_date": LAST_MONTH,
        }],
    })
    draw_id = (await draws.list_draws(pool, group_id=group["id"]))[0]["id"]
    await draws.set_release(pool, draw_id, released=True, actor="jacob@f.com")
    return await draws.invoice_draw(pool, settings, draw_id, actor="jacob@f.com")


async def _monthly_row(pool, *, amount: float = 12000.0, invoice_id: int = 7777):
    """A created monthly ledger row, written directly.

    Monthly execution does not exist yet, so this stands in for it. If this test
    ever fails because the shape changed, that is the signal that the new
    execution path and this list have diverged.
    """
    group = await groups_service.create_group(pool, {
        "name": "Ridgeway — Portal T&M",
        "harvest_client_id": CLIENT,
        "billing_type": "time_and_materials",
        "payment_term": "net 30",
        "projects": [{"harvest_project_id": PORTAL}],
    })
    run_id = await pool.fetchval(
        "INSERT INTO billing_runs (run_month, status, kind) "
        "VALUES ($1, 'completed', 'monthly') RETURNING id",
        AUGUST,
    )
    item_id = await pool.fetchval(
        """
        INSERT INTO billing_run_items
            (billing_run_id, billing_group_id, run_month, status,
             planned_amount, actual_amount, variance, issue_date, due_date,
             period_start, period_end, harvest_invoice_id,
             harvest_invoice_number)
        VALUES ($1,$2,$3,'created'::billing_run_item_status,
                $4,$4,0,$5,$6,$7,$8,$9,'INV-7777')
        RETURNING id
        """,
        run_id, group["id"], AUGUST, amount,
        date(2026, 7, 31), date(2026, 8, 30),
        date(2026, 7, 1), date(2026, 7, 31), invoice_id,
    )
    return {"billing_run_id": run_id, "billing_run_item_id": item_id}


# ── Both kinds, one list ────────────────────────────────────────────────────


async def test_lists_a_billed_draw(fake):
    pool = await get_pool()
    created = await _bill_a_draw(pool)

    rows = await invoices.list_created_invoices(pool)

    assert len(rows) == 1
    row = rows[0]
    assert row["kind"] == "draw"
    assert row["harvest_invoice_id"] == created["harvest_invoice_id"]
    assert row["harvest_invoice_number"] == created["harvest_invoice_number"]
    assert float(row["planned_amount"]) == created["planned_amount"]
    assert float(row["actual_amount"]) == created["actual_amount"]
    assert row["draw_description"] == "Draw 1 — Signing"
    assert row["harvest_client_name"] == "Ridgeway Industrial"
    # A draw covers no service period — the milestone identifies it.
    assert row["period_start"] is None
    assert row["period_end"] is None


async def test_draw_and_monthly_appear_together(fake):
    """The reason this is its own list rather than a section on the Draws tab."""
    pool = await get_pool()
    await _bill_a_draw(pool)
    await _monthly_row(pool)

    rows = await invoices.list_created_invoices(pool)

    assert {r["kind"] for r in rows} == {"draw", "monthly"}
    assert len(rows) == 2
    # The monthly row keeps its service period; the draw has none.
    monthly = next(r for r in rows if r["kind"] == "monthly")
    assert monthly["period_start"] == date(2026, 7, 1)
    assert monthly["draw_description"] is None


async def test_newest_created_first_not_newest_issue_date(fake):
    """A July invoice created in September belongs at the top, not filed under
    July. Monthly issue dates are backdated to the period, so ordering by them
    would bury the most recent work.

    The two rows here share a `created_at` — one transaction, one `now()` — so
    this also pins the `harvest_invoice_id` tiebreak that keeps the order stable
    when timestamps collide.
    """
    pool = await get_pool()
    await _monthly_row(pool)            # issued 2026-07-31, older invoice id
    created = await _bill_a_draw(pool)  # issued today, higher invoice id

    rows = await invoices.list_created_invoices(pool)

    assert rows[0]["harvest_invoice_id"] == created["harvest_invoice_id"]
    assert rows[1]["issue_date"] == date(2026, 7, 31)
    # Stable across identical queries.
    again = await invoices.list_created_invoices(pool)
    assert [r["harvest_invoice_id"] for r in again] == [
        r["harvest_invoice_id"] for r in rows
    ]


async def test_kind_filter(fake):
    pool = await get_pool()
    await _bill_a_draw(pool)
    await _monthly_row(pool)

    assert len(await invoices.list_created_invoices(pool, kind="draw")) == 1
    assert len(await invoices.list_created_invoices(pool, kind="monthly")) == 1


async def test_group_filter(fake):
    pool = await get_pool()
    created = await _bill_a_draw(pool)
    await _monthly_row(pool)

    row = (await invoices.list_created_invoices(pool))[0]
    scoped = await invoices.list_created_invoices(
        pool, group_id=row["billing_group_id"],
    )

    assert len(scoped) == 1
    assert scoped[0]["harvest_invoice_id"] == created["harvest_invoice_id"]


# ── What must not appear ────────────────────────────────────────────────────


async def test_a_failed_attempt_is_not_billed(fake):
    """`failed` means nothing exists in Harvest. Listing it under Billed would
    overstate what a client has been sent."""
    import httpx
    pool = await get_pool()
    group = await groups_service.create_group(pool, {
        "name": "Ridgeway — Draw",
        "harvest_client_id": CLIENT,
        "billing_type": "fixed_fee_schedule",
        "payment_term": "net 30",
        "projects": [{"harvest_project_id": ERP}],
        "schedule_items": [{
            "harvest_project_id": ERP, "description": "Draw 1", "amount": 100,
            "kind": "Service", "scheduled_date": LAST_MONTH,
        }],
    })
    draw_id = (await draws.list_draws(pool, group_id=group["id"]))[0]["id"]
    await draws.set_release(pool, draw_id, released=True, actor="jacob@f.com")

    from app.integrations import harvest
    fake.fail_create_invoice(harvest.HarvestValidationError(
        "Harvest 422", status=422, path="/invoices", body={"message": "nope"},
    ))
    with pytest.raises(harvest.HarvestValidationError):
        await draws.invoice_draw(pool, settings, draw_id, actor="jacob@f.com")

    assert await invoices.list_created_invoices(pool) == []
    # Still findable when asked for explicitly — it is history, just not billing.
    failed = await invoices.list_created_invoices(pool, status="failed")
    assert len(failed) == 1
    assert "nope" in failed[0]["error_message"]
    assert httpx  # keep the import meaningful if the body above changes


async def test_an_in_flight_row_is_not_billed(fake):
    """Its outcome is unknown; claiming it as billed would be a guess."""
    import httpx
    pool = await get_pool()
    group = await groups_service.create_group(pool, {
        "name": "Ridgeway — Draw",
        "harvest_client_id": CLIENT,
        "billing_type": "fixed_fee_schedule",
        "payment_term": "net 30",
        "projects": [{"harvest_project_id": ERP}],
        "schedule_items": [{
            "harvest_project_id": ERP, "description": "Draw 1", "amount": 100,
            "kind": "Service", "scheduled_date": LAST_MONTH,
        }],
    })
    draw_id = (await draws.list_draws(pool, group_id=group["id"]))[0]["id"]
    await draws.set_release(pool, draw_id, released=True, actor="jacob@f.com")
    fake.fail_create_invoice(httpx.TimeoutException("timed out"))
    with pytest.raises(draws.DrawWriteUnknown):
        await draws.invoice_draw(pool, settings, draw_id, actor="jacob@f.com")

    assert await invoices.list_created_invoices(pool) == []
    assert len(await invoices.list_created_invoices(pool, status="in_flight")) == 1


async def test_unknown_filters_are_refused(fake):
    pool = await get_pool()
    with pytest.raises(invoices.InvoiceQueryError, match="Unknown kind"):
        await invoices.list_created_invoices(pool, kind="quarterly")
    with pytest.raises(invoices.InvoiceQueryError, match="Unknown status"):
        await invoices.list_created_invoices(pool, status="sent")


# ── Totals ──────────────────────────────────────────────────────────────────


async def test_totals_split_by_kind(fake):
    pool = await get_pool()
    await _bill_a_draw(pool)
    await _monthly_row(pool, amount=12000.0)

    totals = await invoices.created_invoice_totals(pool)

    assert totals["count"] == 2
    assert totals["draw_count"] == 1
    assert totals["monthly_count"] == 1
    assert totals["total_amount"] == 49500.0
    assert totals["unverified_count"] == 0


async def test_a_hand_linked_row_counts_at_its_planned_amount(fake):
    """Resolving an in-flight row without an amount leaves `actual_amount` null
    on purpose. Dropping it from the total would understate what was billed, so
    it counts at planned and is flagged as unverified."""
    import httpx

    from app.services.billing import inflight

    pool = await get_pool()
    group = await groups_service.create_group(pool, {
        "name": "Ridgeway — Draw",
        "harvest_client_id": CLIENT,
        "billing_type": "fixed_fee_schedule",
        "payment_term": "net 30",
        "projects": [{"harvest_project_id": ERP}],
        "schedule_items": [{
            "harvest_project_id": ERP, "description": "Draw 1", "amount": 5000,
            "kind": "Service", "scheduled_date": LAST_MONTH,
        }],
    })
    draw_id = (await draws.list_draws(pool, group_id=group["id"]))[0]["id"]
    await draws.set_release(pool, draw_id, released=True, actor="jacob@f.com")
    fake.fail_create_invoice(httpx.TimeoutException("timed out"))
    with pytest.raises(draws.DrawWriteUnknown):
        await draws.invoice_draw(pool, settings, draw_id, actor="jacob@f.com")

    stuck = await pool.fetchrow(
        "SELECT id, billing_run_id FROM billing_run_items "
        "WHERE fixed_fee_schedule_item_id = $1", draw_id,
    )
    await inflight.resolve_item(
        pool, stuck["billing_run_id"], stuck["id"],
        resolution="link", harvest_invoice_id=4242, actor="jacob@f.com",
    )

    totals = await invoices.created_invoice_totals(pool)
    assert totals["count"] == 1
    assert totals["total_amount"] == 5000.0
    assert totals["unverified_count"] == 1
