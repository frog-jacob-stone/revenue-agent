"""Schema-level guarantees from migration 0024.

Two constraints in that migration are the whole reason the double-billing
guard is trustworthy, and both needed a denormalized column plus a trigger to
be expressible at all. If they silently degrade to "no constraint", every
downstream check becomes application-level convention. Hence these tests.
"""
from __future__ import annotations

from datetime import date

import asyncpg
import pytest

from app.db import get_pool

JULY = date(2026, 7, 1)
AUGUST = date(2026, 8, 1)


async def _make_group(pool, *, name: str, client_id: int = 5735774, is_active: bool = True):
    return await pool.fetchval(
        """
        INSERT INTO billing_groups (name, harvest_client_id, billing_type, is_active)
        VALUES ($1, $2, 'time_and_materials', $3)
        RETURNING id
        """,
        name, client_id, is_active,
    )


async def _attach(pool, group_id, project_id: int):
    await pool.execute(
        """
        INSERT INTO billing_group_projects (billing_group_id, harvest_project_id)
        VALUES ($1, $2)
        """,
        group_id, project_id,
    )


# ── One project belongs to at most one ACTIVE group ─────────────────────────


async def test_project_cannot_be_in_two_active_groups():
    pool = await get_pool()
    a = await _make_group(pool, name="Group A")
    b = await _make_group(pool, name="Group B")

    await _attach(pool, a, 14307913)

    with pytest.raises(asyncpg.UniqueViolationError):
        await _attach(pool, b, 14307913)


async def test_deactivating_a_group_releases_its_projects():
    pool = await get_pool()
    a = await _make_group(pool, name="Group A")
    b = await _make_group(pool, name="Group B")
    await _attach(pool, a, 14307913)

    await pool.execute("UPDATE billing_groups SET is_active = false WHERE id = $1", a)

    # The trigger must have propagated is_active to the child row.
    assert await pool.fetchval(
        "SELECT group_is_active FROM billing_group_projects WHERE billing_group_id = $1", a
    ) is False

    await _attach(pool, b, 14307913)  # must not raise


async def test_project_attached_to_an_inactive_group_does_not_hold_the_lock():
    """Insert-time trigger: a row added to an already-inactive group must not
    claim the project."""
    pool = await get_pool()
    inactive = await _make_group(pool, name="Archived", is_active=False)
    active = await _make_group(pool, name="Live")

    await _attach(pool, inactive, 14307913)
    assert await pool.fetchval(
        "SELECT group_is_active FROM billing_group_projects WHERE billing_group_id = $1",
        inactive,
    ) is False

    await _attach(pool, active, 14307913)  # must not raise


async def test_one_client_may_own_several_active_groups():
    """A client gets one billing group per invoice it should receive. The
    uniqueness rule is on the project, never on the client."""
    pool = await get_pool()
    a = await _make_group(pool, name="Acme — Platform + Mobile", client_id=5735774)
    b = await _make_group(pool, name="Acme — Innovation Lab", client_id=5735774)

    await _attach(pool, a, 14307913)
    await _attach(pool, a, 14307914)
    await _attach(pool, b, 14307915)  # must not raise

    count = await pool.fetchval(
        "SELECT count(*) FROM billing_groups WHERE harvest_client_id = 5735774 AND is_active"
    )
    assert count == 2


# ── Constraint C6: one live ledger row per group per run month ──────────────


async def _make_run(pool, run_month: date):
    return await pool.fetchval(
        "INSERT INTO billing_runs (run_month) VALUES ($1) RETURNING id", run_month
    )


async def _make_item(pool, run_id, group_id, run_month: date, status: str = "planned"):
    return await pool.fetchval(
        """
        INSERT INTO billing_run_items
            (billing_run_id, billing_group_id, run_month, status)
        VALUES ($1, $2, $3, $4::billing_run_item_status)
        RETURNING id
        """,
        run_id, group_id, run_month, status,
    )


async def test_group_cannot_have_two_live_rows_in_one_run_month():
    pool = await get_pool()
    group = await _make_group(pool, name="Acme")
    first = await _make_run(pool, AUGUST)
    second = await _make_run(pool, AUGUST)

    await _make_item(pool, first, group, AUGUST)

    with pytest.raises(asyncpg.UniqueViolationError):
        await _make_item(pool, second, group, AUGUST)


@pytest.mark.parametrize("terminal_status", ["failed", "skipped", "abandoned"])
async def test_terminal_rows_do_not_block_a_replan(terminal_status: str):
    """A failed, skipped, or abandoned attempt must be re-plannable — otherwise
    one bad run wedges that group for the rest of the month."""
    pool = await get_pool()
    group = await _make_group(pool, name="Acme")
    first = await _make_run(pool, AUGUST)
    second = await _make_run(pool, AUGUST)

    await _make_item(pool, first, group, AUGUST, status=terminal_status)
    await _make_item(pool, second, group, AUGUST)  # must not raise


async def test_in_flight_row_still_blocks_a_replan():
    """The poison pill. An in-flight row means we don't know whether Harvest
    created the invoice, so re-planning must be impossible until a human
    resolves it."""
    pool = await get_pool()
    group = await _make_group(pool, name="Northwind")
    first = await _make_run(pool, AUGUST)
    second = await _make_run(pool, AUGUST)

    await _make_item(pool, first, group, AUGUST, status="in_flight")

    with pytest.raises(asyncpg.UniqueViolationError):
        await _make_item(pool, second, group, AUGUST)


async def test_same_group_may_be_invoiced_in_different_months():
    pool = await get_pool()
    group = await _make_group(pool, name="Acme")
    july = await _make_run(pool, JULY)
    august = await _make_run(pool, AUGUST)

    await _make_item(pool, july, group, JULY, status="created")
    await _make_item(pool, august, group, AUGUST)  # must not raise


async def test_one_row_per_group_per_run():
    pool = await get_pool()
    group = await _make_group(pool, name="Acme")
    run = await _make_run(pool, AUGUST)

    await _make_item(pool, run, group, AUGUST)
    with pytest.raises(asyncpg.UniqueViolationError):
        await _make_item(pool, run, group, AUGUST, status="skipped")


# ── C6 for draws: one live ledger row per draw (0028) ───────────────────────


async def _make_draw_group(pool, *, name: str = "Ridgeway ERP"):
    group = await pool.fetchval(
        """
        INSERT INTO billing_groups (name, harvest_client_id, billing_type)
        VALUES ($1, 5735774, 'fixed_fee_schedule')
        RETURNING id
        """,
        name,
    )
    draw = await pool.fetchval(
        """
        INSERT INTO fixed_fee_schedule_items
            (billing_group_id, harvest_project_id, sequence, description, amount,
             kind, scheduled_date)
        VALUES ($1, 14307914, 1, 'Draw 1 — Signing', 37500, 'Service', $2)
        RETURNING id
        """,
        group, AUGUST,
    )
    return group, draw


async def _make_draw_item(pool, run_id, group_id, draw_id, status: str = "in_flight"):
    return await pool.fetchval(
        """
        INSERT INTO billing_run_items
            (billing_run_id, billing_group_id, fixed_fee_schedule_item_id,
             run_month, status)
        VALUES ($1, $2, $3, $4, $5::billing_run_item_status)
        RETURNING id
        """,
        run_id, group_id, draw_id, AUGUST, status,
    )


async def test_a_draw_cannot_have_two_live_ledger_rows():
    """The lock that makes the Harvest write safe under a double-click.

    `invoice_draw` commits this row before it POSTs, so a second concurrent
    attempt must be stopped by the index rather than by application state — the
    two racers both read `ready` before either wrote.
    """
    pool = await get_pool()
    group, draw = await _make_draw_group(pool)
    first = await _make_run(pool, AUGUST)
    second = await _make_run(pool, AUGUST)

    await _make_draw_item(pool, first, group, draw)

    with pytest.raises(asyncpg.UniqueViolationError):
        await _make_draw_item(pool, second, group, draw)


@pytest.mark.parametrize("terminal_status", ["failed", "skipped", "abandoned"])
async def test_a_resolved_draw_attempt_can_be_retried(terminal_status: str):
    """A 4xx, or a human resolving an in-flight row as failed, must free the draw.
    Otherwise one refused payload wedges that milestone permanently."""
    pool = await get_pool()
    group, draw = await _make_draw_group(pool)
    first = await _make_run(pool, AUGUST)
    second = await _make_run(pool, AUGUST)

    await _make_draw_item(pool, first, group, draw, status=terminal_status)
    await _make_draw_item(pool, second, group, draw)  # must not raise


async def test_two_draws_in_one_month_may_both_bill():
    """Why 0028 split the index. Two milestones landing in the same calendar
    month is ordinary; the per-month index would have blocked the second."""
    pool = await get_pool()
    group, first_draw = await _make_draw_group(pool)
    second_draw = await pool.fetchval(
        """
        INSERT INTO fixed_fee_schedule_items
            (billing_group_id, harvest_project_id, sequence, description, amount,
             kind, scheduled_date)
        VALUES ($1, 14307914, 2, 'Draw 2 — UAT', 40000, 'Service', $2)
        RETURNING id
        """,
        group, AUGUST,
    )
    run_a = await _make_run(pool, AUGUST)
    run_b = await _make_run(pool, AUGUST)

    await _make_draw_item(pool, run_a, group, first_draw, status="created")
    await _make_draw_item(pool, run_b, group, second_draw)  # must not raise


async def test_a_draw_with_a_live_row_cannot_be_deleted():
    """ON DELETE RESTRICT. Editing a schedule must not remove a draw that a
    half-finished write still points at."""
    pool = await get_pool()
    group, draw = await _make_draw_group(pool)
    run = await _make_run(pool, AUGUST)
    await _make_draw_item(pool, run, group, draw)

    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await pool.execute(
            "DELETE FROM fixed_fee_schedule_items WHERE id = $1", draw
        )


# ── Guards on config validity ───────────────────────────────────────────────


async def test_custom_payment_term_requires_net_days():
    pool = await get_pool()
    with pytest.raises(asyncpg.CheckViolationError):
        await pool.execute(
            """
            INSERT INTO billing_groups
                (name, harvest_client_id, billing_type, payment_term, custom_net_days)
            VALUES ('Bad', 1, 'time_and_materials', 'custom', NULL)
            """
        )


async def test_run_month_must_be_first_of_month():
    pool = await get_pool()
    with pytest.raises(asyncpg.CheckViolationError):
        await pool.execute("INSERT INTO billing_runs (run_month) VALUES ('2026-08-15')")
