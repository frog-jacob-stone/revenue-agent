"""Revenue recognition schema — the constraints that carry logic.

Three of them do real work and are worth pinning, because each replaces a check
that used to live in Python (or in Airtable, or nowhere):

  revenue_runs_one_live_per_month    replaces reading Airtable's most recent
                                     entry and comparing dates — a guard that
                                     raced with itself and only caught the
                                     common case.
  period_month first-of-month        the Airtable convention was the *last* day
                                     of the month. A stray last-day date would
                                     silently split a month in two.
  fixed_fee_needs_contracted_fees    a fixed-fee project with no contract value
                                     does not compute to zero, it computes to
                                     nonsense, every month, silently.
"""
from __future__ import annotations

import contextlib
from datetime import date
from decimal import Decimal

import asyncpg
import pytest

from app.db import get_pool


async def _new_run(conn, period_month: date, status: str = "draft") -> str:
    return await conn.fetchval(
        "INSERT INTO revenue_runs (period_month, status, created_by) "
        "VALUES ($1, $2::revenue_run_status, 'test') RETURNING id",
        period_month,
        status,
    )


@contextlib.asynccontextmanager
async def _rejects(conn, exc: type[Exception]):
    """Assert the block raises, without poisoning the surrounding transaction.

    conftest runs every test inside one outer transaction it rolls back, so a
    constraint violation would abort it and every later statement in the test
    would fail with InFailedSQLTransactionError instead of whatever it was
    actually checking. A nested `conn.transaction()` is a SAVEPOINT, which
    rolls back just the failed statement.
    """
    with pytest.raises(exc):
        async with conn.transaction():
            yield


async def test_period_month_must_be_first_of_month(client):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _new_run(conn, date(2026, 3, 1))

        # The Airtable convention. It has to be rejected rather than coerced —
        # a silently-truncated date would look like it worked.
        async with _rejects(conn, asyncpg.CheckViolationError):
            await _new_run(conn, date(2026, 3, 31))


async def test_one_live_run_per_month(client):
    pool = await get_pool()
    async with pool.acquire() as conn:
        first = await _new_run(conn, date(2026, 3, 1))

        async with _rejects(conn, asyncpg.UniqueViolationError):
            await _new_run(conn, date(2026, 3, 1))

        # A recognized run still holds the month — you cannot re-recognize
        # March by planning it again.
        await conn.execute(
            "UPDATE revenue_runs SET status = 'recognized' WHERE id = $1", first
        )
        async with _rejects(conn, asyncpg.UniqueViolationError):
            await _new_run(conn, date(2026, 3, 1))


async def test_abandoning_frees_the_month(client):
    """The whole reason the index is partial: a discarded draft must not lock
    the month out forever."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        first = await _new_run(conn, date(2026, 3, 1))
        await conn.execute(
            "UPDATE revenue_runs SET status = 'abandoned', abandoned_at = now(), "
            "abandoned_by = 'test' WHERE id = $1",
            first,
        )
        second = await _new_run(conn, date(2026, 3, 1))
        assert second != first


async def test_fixed_fee_requires_contracted_fees(client):
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with _rejects(conn, asyncpg.CheckViolationError):
            await conn.execute(
                "INSERT INTO revenue_project_config "
                "(harvest_project_id, revenue_type, created_by, updated_by) "
                "VALUES (14307913, 'fixed_fee', 'test', 'test')"
            )

        # Every other type may legitimately have none.
        await conn.execute(
            "INSERT INTO revenue_project_config "
            "(harvest_project_id, revenue_type, created_by, updated_by) "
            "VALUES (14307914, 'retainer', 'test', 'test')"
        )
        assert await conn.fetchval(
            "SELECT contracted_fees FROM revenue_project_config "
            "WHERE harvest_project_id = 14307914"
        ) is None


async def test_one_entry_per_project_per_run(client):
    pool = await get_pool()
    async with pool.acquire() as conn:
        run = await _new_run(conn, date(2026, 3, 1))
        args = (run, date(2026, 3, 1), 14307913, "Acme Platform", "fixed_fee")
        sql = (
            "INSERT INTO revenue_entries (revenue_run_id, period_month, "
            "harvest_project_id, harvest_project_name, revenue_type, "
            "recognized_amount, computed_amount) "
            "VALUES ($1, $2, $3, $4, $5::revenue_type, 1000, 1000)"
        )
        await conn.execute(sql, *args)
        async with _rejects(conn, asyncpg.UniqueViolationError):
            await conn.execute(sql, *args)


async def test_entries_cascade_when_a_run_is_deleted(client):
    """An abandoned run is kept, but a genuinely deleted one must not leave
    orphan ledger rows that every reader would then have to filter out."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        run = await _new_run(conn, date(2026, 3, 1))
        await conn.execute(
            "INSERT INTO revenue_entries (revenue_run_id, period_month, "
            "harvest_project_id, harvest_project_name, revenue_type, "
            "recognized_amount, computed_amount) "
            "VALUES ($1, $2, 14307913, 'Acme Platform', 'fixed_fee', 1000, 1000)",
            run,
            date(2026, 3, 1),
        )
        await conn.execute("DELETE FROM revenue_runs WHERE id = $1", run)
        assert await conn.fetchval("SELECT count(*) FROM revenue_entries") == 0


async def test_amounts_keep_cents(client):
    """numeric(12,2), not float. Recognized revenue is money and the backfill
    reconciles to the cent."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        run = await _new_run(conn, date(2026, 3, 1))
        await conn.execute(
            "INSERT INTO revenue_entries (revenue_run_id, period_month, "
            "harvest_project_id, harvest_project_name, revenue_type, "
            "recognized_amount, computed_amount, percent_complete) "
            "VALUES ($1, $2, 14307913, 'Acme Platform', 'fixed_fee', "
            "$3, $3, 0.3333)",
            run,
            date(2026, 3, 1),
            Decimal("12345.67"),
        )
        row = await conn.fetchrow(
            "SELECT recognized_amount, percent_complete FROM revenue_entries"
        )
        assert row["recognized_amount"] == Decimal("12345.67")
        assert row["percent_complete"] == Decimal("0.3333")
