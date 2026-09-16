"""The monthly revenue recognition run.

Harvest and Forecast are stubbed throughout — the point of these tests is the
arithmetic and the state machine, not the vendor clients.

The one behaviour worth stating up front, because everything else follows from
it: every method computes a **cumulative** figure, and the period amount is that
minus everything already recognized. So a fixed-fee project's second month
recognizes the *increment*, and a correction to a closed month is absorbed by
the next open one instead of restating history.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.config import settings
from app.db import get_pool
from app.services import revenue_config, revenue_ledger, revenue_run

ACME = 14307913
RETAINER_PROJECT = 14307914

JAN, FEB, MAR = date(2026, 1, 1), date(2026, 2, 1), date(2026, 3, 1)


@pytest.fixture
def harvest_stub(monkeypatch):
    """Stub Harvest and Forecast. Returns a knob the test sets per month.

    `hours` is per-period (the run sweeps a bounded window), `invoiced` is
    cumulative-to-date (Harvest's own semantics), and `scheduled` is
    forward-looking.
    """
    state = {"hours": {}, "scheduled": {}, "invoiced": {}}

    async def _entries(cfg, *, from_, to):
        return [
            {"project": {"id": pid}, "hours": hrs}
            for pid, hrs in state["hours"].items()
        ]

    async def _scheduled(cfg, from_date):
        return dict(state["scheduled"])

    async def _invoiced(cfg, to_date):
        return {
            pid: {"total_amount": amt, "billable_expenses": 0.0}
            for pid, amt in state["invoiced"].items()
        }

    monkeypatch.setattr(revenue_run.harvest, "list_time_entries_all", _entries)
    monkeypatch.setattr(
        revenue_run.forecast, "get_scheduled_hours_by_harvest_id", _scheduled
    )
    monkeypatch.setattr(
        revenue_run.harvest, "get_invoice_totals_by_project", _invoiced
    )
    return state


async def _project(conn, pid: int, name: str) -> None:
    await conn.execute(
        "INSERT INTO harvest_projects "
        "(harvest_id, name, client_id, client_name, is_billable, is_active) "
        "VALUES ($1, $2, 500, 'Acme', true, true)",
        pid, name,
    )


async def _plan(pool, period: date, actor: str = "jacob") -> dict:
    return await revenue_run.plan_run(
        pool, settings, period_month=period, actor=actor
    )


def _entry(run: dict, pid: int) -> dict:
    return next(e for e in run["entries"] if e["harvest_project_id"] == pid)


# ── Planning ────────────────────────────────────────────────────────────────


async def test_unconfigured_projects_block_the_run(client, harvest_stub):
    """The same "fix it and re-run" gate the Airtable flow had, except the
    fixing now happens on a screen in this system."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _project(conn, ACME, "Acme Platform")

    with pytest.raises(revenue_run.RevenueConfigMissing) as exc:
        await _plan(pool, JAN)

    assert [p["harvest_project_id"] for p in exc.value.projects] == [ACME]
    assert "Acme Platform" in str(exc.value)
    # Nothing written — the gate runs before the run row is created.
    assert await pool.fetchval("SELECT count(*) FROM revenue_runs") == 0


async def test_a_fixed_fee_first_month_recognizes_percent_complete(
    client, harvest_stub
):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _project(conn, ACME, "Acme Platform")
    await revenue_config.set_config(
        pool, ACME, revenue_type="fixed_fee",
        contracted_fees=Decimal("100000.00"), actor="t",
    )
    harvest_stub["hours"] = {ACME: 200.0}
    harvest_stub["scheduled"] = {ACME: 300.0}

    run = await _plan(pool, JAN)
    entry = _entry(run, ACME)

    # 200 / (200 + 300) = 40% of 100,000
    assert entry["percent_complete"] == Decimal("0.4000")
    assert entry["recognized_amount"] == Decimal("40000.00")
    assert entry["computed_amount"] == Decimal("40000.00")
    # The period's own hours, not cumulative — it is the denominator of
    # revenue-per-hour, whose numerator is also a period figure.
    assert entry["logged_hours"] == Decimal("200.00")
    assert run["status"] == "draft"


async def test_the_second_month_recognizes_only_the_increment(client, harvest_stub):
    """The heart of the model. Percent complete is cumulative, so without the
    subtraction February would re-recognize January."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _project(conn, ACME, "Acme Platform")
    await revenue_config.set_config(
        pool, ACME, revenue_type="fixed_fee",
        contracted_fees=Decimal("100000.00"), actor="t",
    )

    harvest_stub["hours"] = {ACME: 200.0}
    harvest_stub["scheduled"] = {ACME: 300.0}
    jan = await _plan(pool, JAN)
    await revenue_run.finalize_run(pool, jan["id"], actor="t")

    # Another 100 hours logged; 200 still scheduled. Cumulative completion is
    # now 300/500 = 60%, i.e. 60,000 earned in total.
    harvest_stub["hours"] = {ACME: 100.0}
    harvest_stub["scheduled"] = {ACME: 200.0}
    feb = await _plan(pool, FEB)

    entry = _entry(feb, ACME)
    assert entry["percent_complete"] == Decimal("0.6000")
    # 60,000 total − 40,000 already recognized.
    assert entry["recognized_amount"] == Decimal("20000.00")
    assert entry["logged_hours"] == Decimal("100.00")


async def test_time_and_materials_recognizes_the_period_invoiced(client, harvest_stub):
    """Harvest's invoiced-to-date is cumulative, so the same subtraction
    applies — a T&M project must not re-recognize its whole invoice history
    every month."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _project(conn, ACME, "Acme T&M")
    await revenue_config.set_config(
        pool, ACME, revenue_type="time_and_materials", actor="t"
    )

    harvest_stub["invoiced"] = {ACME: 30000.0}
    jan = await _plan(pool, JAN)
    assert _entry(jan, ACME)["recognized_amount"] == Decimal("30000.00")
    await revenue_run.finalize_run(pool, jan["id"], actor="t")

    harvest_stub["invoiced"] = {ACME: 45000.0}  # cumulative
    feb = await _plan(pool, FEB)
    assert _entry(feb, ACME)["recognized_amount"] == Decimal("15000.00")


async def test_a_correction_absorbs_into_the_next_open_month(client, harvest_stub):
    """Falls out of the model rather than being implemented. January is
    corrected downward after it closed; February's computation subtracts the
    *new* prior sum, so the difference lands there instead of restating
    January."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _project(conn, ACME, "Acme T&M")
    await revenue_config.set_config(
        pool, ACME, revenue_type="time_and_materials", actor="t"
    )

    harvest_stub["invoiced"] = {ACME: 30000.0}
    jan = await _plan(pool, JAN)
    await revenue_run.finalize_run(pool, jan["id"], actor="t")

    # January was wrong: 5,000 of it should not have been recognized. Rather
    # than reopening a closed month, the ledger row is corrected...
    pool_conn = await get_pool()
    await pool_conn.execute(
        "UPDATE revenue_entries SET recognized_amount = 25000 "
        "WHERE harvest_project_id = $1 AND period_month = $2",
        ACME, JAN,
    )

    harvest_stub["invoiced"] = {ACME: 45000.0}
    feb = await _plan(pool, FEB)
    # ...and February picks up the 5,000: 45,000 − 25,000.
    assert _entry(feb, ACME)["recognized_amount"] == Decimal("20000.00")


async def test_a_retainer_computes_to_zero_with_a_note(client, harvest_stub):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _project(conn, RETAINER_PROJECT, "Support Retainer")
    await revenue_config.set_config(
        pool, RETAINER_PROJECT, revenue_type="retainer", actor="t"
    )

    run = await _plan(pool, JAN)
    entry = _entry(run, RETAINER_PROJECT)
    assert entry["recognized_amount"] == Decimal("0.00")
    assert "manually" in entry["notes"].lower()


async def test_planning_a_month_twice_is_refused(client, harvest_stub):
    """From the partial unique index, not a read-then-write check, so two
    people planning at once cannot both succeed."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _project(conn, ACME, "Acme Platform")
    await revenue_config.set_config(pool, ACME, revenue_type="hosting", actor="t")

    await _plan(pool, JAN)
    with pytest.raises(revenue_run.RevenueRunConflict, match="already has a run"):
        await _plan(pool, JAN)


async def test_planning_writes_an_audit_row(client, harvest_stub):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _project(conn, ACME, "Acme Platform")
    await revenue_config.set_config(pool, ACME, revenue_type="hosting", actor="t")
    harvest_stub["invoiced"] = {ACME: 1234.0}

    await _plan(pool, JAN, actor="jacob@frogslayer.com")

    payload = await pool.fetchval(
        "SELECT payload FROM audit_log WHERE event_type = 'revenue.run.planned'"
    )
    assert payload["projects"] == 1
    assert payload["computed_total"] == "1234.00"
    assert payload["period_month"] == "2026-01-01"


async def test_a_draft_does_not_reach_the_ledger(client, harvest_stub):
    """Planning a month must not change what the next month computes."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _project(conn, ACME, "Acme T&M")
    await revenue_config.set_config(
        pool, ACME, revenue_type="time_and_materials", actor="t"
    )

    harvest_stub["invoiced"] = {ACME: 30000.0}
    await _plan(pool, JAN)  # left as a draft

    assert await revenue_ledger.list_entries(pool) == []
    async with pool.acquire() as conn:
        assert await revenue_ledger.prior_recognized(conn, ACME, FEB) == 0.0


# ── Overrides ───────────────────────────────────────────────────────────────


async def test_override_keeps_what_the_system_computed(client, harvest_stub):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _project(conn, RETAINER_PROJECT, "Support Retainer")
    await revenue_config.set_config(
        pool, RETAINER_PROJECT, revenue_type="retainer", actor="t"
    )
    run = await _plan(pool, JAN)
    entry = _entry(run, RETAINER_PROJECT)

    row = await revenue_run.override_entry(
        pool, run["id"], entry["id"],
        recognized_amount=Decimal("8000.00"),
        override_reason="Monthly support fee per SOW #4",
        actor="jacob",
    )

    assert row["recognized_amount"] == Decimal("8000.00")
    # Never touched: "what did it say, what did we book, who changed it" has to
    # be answerable from one row.
    assert row["computed_amount"] == Decimal("0.00")

    payload = await pool.fetchval(
        "SELECT payload FROM audit_log WHERE event_type = 'revenue.entry.overridden'"
    )
    assert payload["computed_amount"] == "0.00"
    assert payload["recognized_amount"] == "8000.00"
    assert payload["reason"] == "Monthly support fee per SOW #4"


async def test_override_requires_a_reason(client, harvest_stub):
    """The only record of why a booked figure is not what the arithmetic
    produced."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _project(conn, ACME, "Acme Platform")
    await revenue_config.set_config(pool, ACME, revenue_type="hosting", actor="t")
    run = await _plan(pool, JAN)
    entry = _entry(run, ACME)

    for blank in ["", "   "]:
        with pytest.raises(revenue_run.RevenueRunError, match="needs a reason"):
            await revenue_run.override_entry(
                pool, run["id"], entry["id"],
                recognized_amount=Decimal("1.00"), override_reason=blank, actor="t",
            )


async def test_a_finalized_month_cannot_be_overridden(client, harvest_stub):
    """History. The way to change it is to recognize the difference in the next
    open month, which happens by itself."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _project(conn, ACME, "Acme Platform")
    await revenue_config.set_config(pool, ACME, revenue_type="hosting", actor="t")
    run = await _plan(pool, JAN)
    entry = _entry(run, ACME)
    await revenue_run.finalize_run(pool, run["id"], actor="t")

    with pytest.raises(revenue_run.RevenueRunConflict, match="not a draft"):
        await revenue_run.override_entry(
            pool, run["id"], entry["id"],
            recognized_amount=Decimal("1.00"), override_reason="nope", actor="t",
        )


# ── Finalize and abandon ────────────────────────────────────────────────────


async def test_finalize_refuses_an_undecided_retainer(client, harvest_stub):
    """A retainer's zero means "nobody has decided", not "nothing was earned".
    Letting it through would silently under-recognize the month."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _project(conn, RETAINER_PROJECT, "Support Retainer")
    await revenue_config.set_config(
        pool, RETAINER_PROJECT, revenue_type="retainer", actor="t"
    )
    run = await _plan(pool, JAN)

    with pytest.raises(revenue_run.RevenueRunConflict, match="need an amount"):
        await revenue_run.finalize_run(pool, run["id"], actor="t")

    # An explicit zero is a decision, and clears the block.
    await revenue_run.override_entry(
        pool, run["id"], _entry(run, RETAINER_PROJECT)["id"],
        recognized_amount=Decimal("0.00"),
        override_reason="Paused this month at the client's request",
        actor="t",
    )
    finalized = await revenue_run.finalize_run(pool, run["id"], actor="t")
    assert finalized["status"] == "recognized"


async def test_a_non_retainer_zero_does_not_block(client, harvest_stub):
    """A project with no activity legitimately recognizes nothing."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _project(conn, ACME, "Dormant T&M")
    await revenue_config.set_config(
        pool, ACME, revenue_type="time_and_materials", actor="t"
    )
    run = await _plan(pool, JAN)

    assert _entry(run, ACME)["recognized_amount"] == Decimal("0.00")
    assert (await revenue_run.finalize_run(pool, run["id"], actor="t"))[
        "status"
    ] == "recognized"


async def test_finalizing_puts_the_entries_in_the_ledger(client, harvest_stub):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _project(conn, ACME, "Acme Platform")
    await revenue_config.set_config(pool, ACME, revenue_type="hosting", actor="t")
    harvest_stub["invoiced"] = {ACME: 5000.0}
    run = await _plan(pool, JAN)

    await revenue_run.finalize_run(pool, run["id"], actor="jacob")

    rows = await revenue_ledger.list_entries(pool)
    assert [r["recognized_amount"] for r in rows] == [Decimal("5000.00")]

    payload = await pool.fetchval(
        "SELECT payload FROM audit_log WHERE event_type = 'revenue.run.finalized'"
    )
    assert payload["total_recognized"] == "5000.00"


async def test_finalize_records_what_was_booked_not_what_was_computed(
    client, harvest_stub
):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _project(conn, RETAINER_PROJECT, "Support Retainer")
    await revenue_config.set_config(
        pool, RETAINER_PROJECT, revenue_type="retainer", actor="t"
    )
    run = await _plan(pool, JAN)
    await revenue_run.override_entry(
        pool, run["id"], _entry(run, RETAINER_PROJECT)["id"],
        recognized_amount=Decimal("8000.00"), override_reason="SOW #4", actor="t",
    )
    await revenue_run.finalize_run(pool, run["id"], actor="t")

    payload = await pool.fetchval(
        "SELECT payload FROM audit_log WHERE event_type = 'revenue.run.finalized'"
    )
    assert payload["total_recognized"] == "8000.00"


async def test_finalizing_twice_is_refused(client, harvest_stub):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _project(conn, ACME, "Acme Platform")
    await revenue_config.set_config(pool, ACME, revenue_type="hosting", actor="t")
    run = await _plan(pool, JAN)
    await revenue_run.finalize_run(pool, run["id"], actor="t")

    with pytest.raises(revenue_run.RevenueRunConflict, match="already recognized"):
        await revenue_run.finalize_run(pool, run["id"], actor="t")


async def test_abandoning_frees_the_month_and_keeps_the_rows(client, harvest_stub):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _project(conn, ACME, "Acme Platform")
    await revenue_config.set_config(pool, ACME, revenue_type="hosting", actor="t")
    harvest_stub["invoiced"] = {ACME: 999.0}

    first = await _plan(pool, JAN)
    abandoned = await revenue_run.abandon_run(pool, first["id"], actor="jacob")
    assert abandoned["status"] == "abandoned"

    # Kept, not deleted — what was proposed and thrown away is worth seeing.
    assert len(abandoned["entries"]) == 1

    second = await _plan(pool, JAN)
    assert second["id"] != first["id"]

    # And the discarded draft still counts for nothing.
    assert await revenue_ledger.list_entries(pool) == []


async def test_a_finalized_month_cannot_be_abandoned(client, harvest_stub):
    """That would silently remove a month from every historical total — a
    restatement, not a correction."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _project(conn, ACME, "Acme Platform")
    await revenue_config.set_config(pool, ACME, revenue_type="hosting", actor="t")
    run = await _plan(pool, JAN)
    await revenue_run.finalize_run(pool, run["id"], actor="t")

    with pytest.raises(revenue_run.RevenueRunConflict, match="cannot be abandoned"):
        await revenue_run.abandon_run(pool, run["id"], actor="t")


async def test_unknown_run_ids_are_reported_not_ignored(client, harvest_stub):
    pool = await get_pool()
    missing = "00000000-0000-0000-0000-000000000000"
    for call in (revenue_run.finalize_run, revenue_run.abandon_run):
        with pytest.raises(revenue_run.RevenueRunError, match="not found"):
            await call(pool, missing, actor="t")
