"""Per-project revenue recognition configuration.

Operator-initiated and human-only (ADR-0004), so every write must be audited,
and the scope question — which projects need configuring — has to match what
the runner asks, or the setup screen says "all done" while the run refuses to
start.
"""
from __future__ import annotations

from decimal import Decimal

import asyncpg
import pytest

from app.db import get_pool
from app.services import revenue_config

ACME = 14307913
INTERNAL = 14307914


async def _projects(conn) -> None:
    await conn.execute(
        """
        INSERT INTO harvest_projects
            (harvest_id, name, client_id, client_name, is_billable, is_active)
        VALUES ($1, 'Acme Platform', 500, 'Acme', true, true),
               ($2, 'Internal R&D',  999, 'Us',   true, true),
               (3, 'Dormant',        500, 'Acme', true, false),
               (4, 'Non-billable',   500, 'Acme', false, true)
        """,
        ACME, INTERNAL,
    )


async def test_scope_is_billable_active_and_not_excluded(client):
    """`is_billable` alone is not enough — our own company is a Harvest client
    and some of its internal work is flagged billable, which is exactly why
    `excluded_harvest_clients` exists."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _projects(conn)
        await conn.execute(
            "INSERT INTO excluded_harvest_clients (harvest_client_id, excluded_by) "
            "VALUES (999, 'test')"
        )

    rows = await revenue_config.list_projects(pool)
    assert [r["harvest_project_id"] for r in rows] == [ACME]


async def test_unconfigured_sort_to_the_top(client):
    """They are the work. An inner join would have hidden them entirely."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO harvest_projects "
            "(harvest_id, name, client_id, is_billable, is_active) "
            "VALUES (1, 'Zeta', 500, true, true), (2, 'Alpha', 500, true, true)"
        )
    await revenue_config.set_config(pool, 2, revenue_type="retainer", actor="t")

    rows = await revenue_config.list_projects(pool)
    assert [r["harvest_project_name"] for r in rows] == ["Zeta", "Alpha"]
    assert rows[0]["revenue_type"] is None


async def test_set_config_is_idempotent_and_audited(client):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _projects(conn)

    await revenue_config.set_config(
        pool, ACME, revenue_type="fixed_fee",
        contracted_fees=Decimal("250000.00"), actor="jacob",
    )
    row = await revenue_config.set_config(
        pool, ACME, revenue_type="fixed_fee",
        contracted_fees=Decimal("300000.00"), notes="amended SOW", actor="jacob",
    )

    assert row["contracted_fees"] == Decimal("300000.00")
    assert row["notes"] == "amended SOW"
    assert await pool.fetchval(
        "SELECT count(*) FROM revenue_project_config"
    ) == 1

    # The values ride along: a contracted fee is the denominator of every future
    # fixed-fee month.
    events = await pool.fetch(
        "SELECT payload FROM audit_log WHERE event_type = 'revenue.config.set' "
        "ORDER BY id"
    )
    assert len(events) == 2
    assert events[1]["payload"]["contracted_fees"] == "300000.00"
    assert events[1]["payload"]["notes"] == "amended SOW"


async def test_fixed_fee_without_fees_is_refused_before_any_write(client):
    pool = await get_pool()
    with pytest.raises(revenue_config.RevenueConfigError, match="contracted fees"):
        await revenue_config.set_config(pool, ACME, revenue_type="fixed_fee")

    assert await pool.fetchval("SELECT count(*) FROM revenue_project_config") == 0
    assert await pool.fetchval("SELECT count(*) FROM audit_log") == 0


async def test_unknown_revenue_type_is_refused(client):
    pool = await get_pool()
    with pytest.raises(revenue_config.RevenueConfigError, match="Unknown revenue type"):
        await revenue_config.set_config(pool, ACME, revenue_type="subscription")


async def test_other_types_may_have_no_contracted_fees(client):
    pool = await get_pool()
    row = await revenue_config.set_config(pool, ACME, revenue_type="retainer")
    assert row["contracted_fees"] is None


async def test_remove_config_reports_whether_it_did_anything(client):
    pool = await get_pool()
    await revenue_config.set_config(pool, ACME, revenue_type="hosting", actor="t")

    assert await revenue_config.remove_config(pool, ACME, actor="t") is True
    # Not an error, but not success either — the caller turns it into a 404,
    # because un-configuring something that was never configured is usually a
    # wrong id.
    assert await revenue_config.remove_config(pool, ACME, actor="t") is False

    assert await pool.fetchval(
        "SELECT count(*) FROM audit_log WHERE event_type = 'revenue.config.removed'"
    ) == 1


async def test_unconfigured_projects_is_the_runs_gate(client):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _projects(conn)
        missing = await revenue_config.unconfigured_projects(conn)
        assert [m["harvest_project_id"] for m in missing] == [ACME, INTERNAL]

    await revenue_config.set_config(pool, ACME, revenue_type="hosting", actor="t")
    await revenue_config.set_config(pool, INTERNAL, revenue_type="retainer", actor="t")

    async with pool.acquire() as conn:
        assert await revenue_config.unconfigured_projects(conn) == []


async def test_configured_but_unusable_cannot_exist(client):
    """Why `unconfigured_projects` only tests for absence. The CHECK is on the
    table, so no writer can leave a fixed-fee project without a contract value
    — not this service, not the backfill, not hand-written SQL. A run computing
    against null would otherwise recognize zero every month in silence."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _projects(conn)
        await conn.execute(
            "INSERT INTO revenue_project_config "
            "(harvest_project_id, revenue_type, contracted_fees, created_by, updated_by) "
            "VALUES ($1, 'fixed_fee', 100, 't', 't')",
            ACME,
        )
        with pytest.raises(asyncpg.CheckViolationError):
            async with conn.transaction():
                await conn.execute(
                    "UPDATE revenue_project_config SET contracted_fees = NULL "
                    "WHERE harvest_project_id = $1",
                    ACME,
                )
