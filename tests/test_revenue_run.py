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

    `refreshed` counts the snapshot refreshes the run performed. Stubbed rather
    than disabled so the tests exercise the real default (`refresh=True`) — a
    suite that only ever plans with the refresh turned off would not have
    noticed it was missing in the first place.

    `expenses` is the billable-expense subset of `invoiced`, keyed the same way.
    It used to be hardcoded to zero here, which is the reason no run-level test
    could have caught `get_invoice_totals_by_project` classifying no line item
    as an expense: the stub agreed with the bug.
    """
    state = {
        "hours": {}, "scheduled": {}, "invoiced": {}, "expenses": {}, "refreshed": 0
    }

    async def _refresh(pool, cfg, *, actor="system"):
        state["refreshed"] += 1
        return {"projects": 0, "clients": 0}

    async def _entries(cfg, *, from_, to):
        return [
            {"project": {"id": pid}, "hours": hrs}
            for pid, hrs in state["hours"].items()
        ]

    async def _scheduled(cfg, from_date):
        return dict(state["scheduled"])

    async def _invoiced(cfg, to_date):
        # Expenses are a subset of the invoice total, as Harvest reports them —
        # a project with expenses and nothing else still has a total.
        pids = set(state["invoiced"]) | set(state["expenses"])
        return {
            pid: {
                "total_amount": state["invoiced"].get(pid, 0.0),
                "billable_expenses": state["expenses"].get(pid, 0.0),
            }
            for pid in pids
        }

    monkeypatch.setattr(
        revenue_run.harvest_snapshot, "refresh_snapshot", _refresh
    )
    monkeypatch.setattr(revenue_run.harvest, "list_time_entries_all", _entries)
    monkeypatch.setattr(
        revenue_run.forecast, "get_scheduled_hours_by_harvest_id", _scheduled
    )
    monkeypatch.setattr(
        revenue_run.harvest, "get_invoice_totals_by_project", _invoiced
    )
    return state


async def _project(
    conn, pid: int, name: str, *, billable: bool = True, active: bool = True
) -> None:
    await conn.execute(
        "INSERT INTO harvest_projects "
        "(harvest_id, name, client_id, client_name, is_billable, is_active) "
        "VALUES ($1, $2, 500, 'Acme', $3, $4)",
        pid, name, billable, active,
    )


async def _archive(conn, pid: int) -> None:
    await conn.execute(
        "UPDATE harvest_projects SET is_active = false WHERE harvest_id = $1", pid
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


async def test_a_retainer_with_history_does_not_claw_it_back(client, harvest_stub):
    """A retainer's zero is "nothing computed", not "nothing earned to date".

    Differencing it against the ledger the way a cumulative figure is differenced
    reads it as the second, and books the project's entire history as a negative.
    The live September draft did exactly that to two retainers, at -19,841.25 and
    -84,751.25, and because neither was *equal* to zero both then slipped past
    the finalize gate below — so the month could have closed $104,592.50 light
    with nothing flagged.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _project(conn, RETAINER_PROJECT, "Support Retainer")
    await revenue_config.set_config(
        pool, RETAINER_PROJECT, revenue_type="retainer", actor="t"
    )

    # Three months of history, decided by a human and finalized each time.
    for period, amount in ((JAN, "7500.00"), (FEB, "7500.00"), (MAR, "4841.25")):
        run = await _plan(pool, period)
        await revenue_run.override_entry(
            pool, run["id"], _entry(run, RETAINER_PROJECT)["id"],
            recognized_amount=Decimal(amount),
            override_reason="base retainer", actor="t",
        )
        await revenue_run.finalize_run(pool, run["id"], actor="t")

    april = await _plan(pool, date(2026, 4, 1))
    entry = _entry(april, RETAINER_PROJECT)

    # 19,841.25 recognized to date. The bug made this -19841.25.
    assert entry["recognized_amount"] == Decimal("0.00")
    assert entry["computed_amount"] == Decimal("0.00")
    # And the history is untouched — nothing was restated.
    assert await revenue_ledger.prior_recognized(
        pool, RETAINER_PROJECT, date(2026, 4, 1)
    ) == 19841.25

    # The zero is what makes it undecided, so the gate still catches it.
    with pytest.raises(revenue_run.RevenueRunConflict, match="still need an amount"):
        await revenue_run.finalize_run(pool, april["id"], actor="t")


async def test_planning_refreshes_the_harvest_snapshot(client, harvest_stub):
    """Scope is read from a cache, so planning against a stale one silently
    omits every project created since the last sync — they are not unconfigured,
    they are absent, and the config gate cannot flag a row that does not exist.
    That is how the live September close missed three real projects."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _project(conn, ACME, "Acme Platform")
    await revenue_config.set_config(pool, ACME, revenue_type="hosting", actor="t")

    assert harvest_stub["refreshed"] == 0
    await _plan(pool, JAN)
    assert harvest_stub["refreshed"] == 1

    # Opt-out exists for tests and for replanning against a known cache state.
    await revenue_run.abandon_run(pool, (await _plan_ids(pool))[0], actor="t")
    await revenue_run.plan_run(
        pool, settings, period_month=JAN, actor="t", refresh=False
    )
    assert harvest_stub["refreshed"] == 1


async def _plan_ids(pool) -> list:
    rows = await pool.fetch(
        "SELECT id FROM revenue_runs WHERE status = 'draft' ORDER BY created_at"
    )
    return [r["id"] for r in rows]


async def test_a_fixed_fee_project_recognizes_billable_expenses_on_top(
    client, harvest_stub
):
    """Expenses are passed through at cost, so they sit on top of the earned fee
    rather than being scaled by completion. A project 40% through its contract
    has earned 40% of the fee and *all* of the expenses already invoiced."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _project(conn, ACME, "Acme Platform")
    await revenue_config.set_config(
        pool, ACME, revenue_type="fixed_fee",
        contracted_fees=Decimal("100000.00"), actor="t",
    )
    harvest_stub["hours"] = {ACME: 200.0}
    harvest_stub["scheduled"] = {ACME: 300.0}
    harvest_stub["invoiced"] = {ACME: 46_404.16}
    harvest_stub["expenses"] = {ACME: 6_404.16}

    entry = _entry(await _plan(pool, JAN), ACME)

    # 40% of 100,000, plus the expenses in full — not 40% of them.
    assert entry["recognized_amount"] == Decimal("46404.16")
    assert "6,404.16" in entry["notes"]


async def test_a_settled_fixed_fee_project_does_not_claw_back_its_expenses(
    client, harvest_stub
):
    """The live failure in miniature.

    `kind` is the Harvest invoice item category name, and this account calls
    expenses "Billable Expense". The classifier tested `== "expense"`, matched
    nothing, and returned 0.00 for every project — so a project whose history
    included expenses computed a cumulative target *without* them and booked the
    difference as a clawback. Five archived projects showed exactly their own
    expense total as a negative: LBMA SOW #6 at -6,404.16 against $330,000
    contracted and $336,404.16 recognized.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _project(conn, ACME, "ECS Modernization Scaffolding")
        await _project(conn, RETAINER_PROJECT, "Ongoing Support")
    await revenue_config.set_config(
        pool, ACME, revenue_type="fixed_fee",
        contracted_fees=Decimal("330000.00"), actor="t",
    )
    await revenue_config.set_config(
        pool, RETAINER_PROJECT, revenue_type="hosting", actor="t"
    )

    # Runs to completion with expenses, recognizing 330,000 + 6,404.16.
    harvest_stub["hours"] = {ACME: 1201.25}
    harvest_stub["scheduled"] = {}
    harvest_stub["invoiced"] = {ACME: 336_404.16}
    harvest_stub["expenses"] = {ACME: 6_404.16}
    jan = await _plan(pool, JAN)
    assert _entry(jan, ACME)["recognized_amount"] == Decimal("336404.16")
    await revenue_run.finalize_run(pool, jan["id"], actor="t")

    # Delivered and archived. Nothing further logged or invoiced.
    async with pool.acquire() as conn:
        await _archive(conn, ACME)
    harvest_stub["hours"] = {}

    feb = await _plan(pool, FEB)
    # Settled: no entry at all, rather than a -6,404.16 reversal.
    assert all(e["harvest_project_id"] != ACME for e in feb["entries"])


async def test_an_archived_project_still_recognizes_what_it_is_owed(
    client, harvest_stub
):
    """Archiving is how a fixed-fee project *finishes*, and finishing is when
    its last true-up falls due — percent complete reaches 100% precisely because
    the Forecast bookings ended. Scoping the run on `is_active` therefore
    dropped projects exactly when they had the most left to recognize. D&A
    SOW #7 was archived at 99.80% and stranded the last $110 of a $55,000
    contract."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _project(conn, ACME, "Lenni v1 Pilot")
        # An ongoing engagement, so the months after the pilot ships still have
        # something to plan and the assertion below is about the pilot dropping
        # out rather than about the run being empty.
        await _project(conn, RETAINER_PROJECT, "Ongoing Support")
    await revenue_config.set_config(
        pool, ACME, revenue_type="fixed_fee",
        contracted_fees=Decimal("55000.00"), actor="t",
    )
    await revenue_config.set_config(
        pool, RETAINER_PROJECT, revenue_type="hosting", actor="t"
    )

    # January: 499 hours logged, 1 still booked — 99.80% complete.
    harvest_stub["hours"] = {ACME: 499.0}
    harvest_stub["scheduled"] = {ACME: 1.0}
    jan = await _plan(pool, JAN)
    assert _entry(jan, ACME)["recognized_amount"] == Decimal("54890.00")
    await revenue_run.finalize_run(pool, jan["id"], actor="t")

    # The project ships and is archived. Nothing more is logged or booked.
    async with pool.acquire() as conn:
        await _archive(conn, ACME)
    harvest_stub["hours"] = {}
    harvest_stub["scheduled"] = {}

    feb = await _plan(pool, FEB)
    entry = _entry(feb, ACME)
    assert entry["recognized_amount"] == Decimal("110.00")
    assert entry["percent_complete"] == Decimal("1.0000")
    # Flagged for the reviewer: clearing the balance assumes it completed, and
    # a cancelled project is indistinguishable from a finished one here.
    assert entry["project_is_active"] is False
    await revenue_run.finalize_run(pool, feb["id"], actor="t")

    # Settled, so it stops appearing — the balance reaching zero is what ends
    # it, with no flag to set and nothing to clean up.
    mar = await _plan(pool, MAR)
    assert all(e["harvest_project_id"] != ACME for e in mar["entries"])


async def test_an_archived_time_and_materials_project_picks_up_its_last_invoice(
    client, harvest_stub
):
    """The same relaxation, reached by a different route.

    A fixed-fee project comes back because percent complete moves; a T&M one
    comes back because `invoiced_to_date` moves. An engagement that ends
    mid-month is invoiced *after* it is archived — billing runs in arrears and
    dates the invoice the last day of the period (`billing/dates.py:60-62`) — so
    the final invoice always lands against a project that is already closed.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _project(conn, ACME, "Kiosk Development")
        await _project(conn, RETAINER_PROJECT, "Ongoing Support")
    await revenue_config.set_config(
        pool, ACME, revenue_type="time_and_materials", actor="t"
    )
    await revenue_config.set_config(
        pool, RETAINER_PROJECT, revenue_type="hosting", actor="t"
    )

    harvest_stub["invoiced"] = {ACME: 40000.0}
    jan = await _plan(pool, JAN)
    assert _entry(jan, ACME)["recognized_amount"] == Decimal("40000.00")
    await revenue_run.finalize_run(pool, jan["id"], actor="t")

    # Work stops mid-February and the project is archived. Hours were logged
    # before it closed, and the final invoice has not been raised yet.
    async with pool.acquire() as conn:
        await _archive(conn, ACME)
    harvest_stub["hours"] = {ACME: 60.0}

    feb = await _plan(pool, FEB)
    entry = _entry(feb, ACME)
    # Present on the strength of its hours alone — effort visible, nothing
    # recognized, which is the honest reading until the invoice exists.
    assert entry["recognized_amount"] == Decimal("0.00")
    assert entry["logged_hours"] == Decimal("60.00")
    await revenue_run.finalize_run(pool, feb["id"], actor="t")

    # March: the final invoice is raised. No hours, still archived.
    harvest_stub["hours"] = {}
    harvest_stub["invoiced"] = {ACME: 52500.0}

    mar = await _plan(pool, MAR)
    assert _entry(mar, ACME)["recognized_amount"] == Decimal("12500.00")


async def test_an_archived_project_with_nothing_owed_is_not_planned(
    client, harvest_stub
):
    """Relaxing `is_active` must not drag every finished engagement back into
    every future month."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _project(conn, ACME, "Done And Dusted", active=False)
    await revenue_config.set_config(pool, ACME, revenue_type="hosting", actor="t")
    # Configured, archived, nothing invoiced and no hours — nothing to say.
    with pytest.raises(revenue_run.RevenueRunError, match="Nothing to recognize"):
        await _plan(pool, JAN)
    # And no empty draft left holding the month's one live-run slot.
    assert await pool.fetchval("SELECT count(*) FROM revenue_runs") == 0


async def test_a_non_billable_project_is_never_planned(client, harvest_stub):
    """`is_billable` is not relaxed with `is_active`: internal time buckets are
    Harvest projects but never engagements, however much time they carry."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _project(conn, ACME, "Internal R&D", billable=False)
    await revenue_config.set_config(pool, ACME, revenue_type="hosting", actor="t")
    harvest_stub["hours"] = {ACME: 120.0}
    harvest_stub["invoiced"] = {ACME: 9999.0}

    with pytest.raises(revenue_run.RevenueRunError, match="No billable"):
        await _plan(pool, JAN)


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


async def test_an_abandoned_run_can_be_deleted_outright(client, harvest_stub):
    """Keeping a discarded draft is a default, not an invariant. The runs a
    defect produced are noise in the list used to find real months."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _project(conn, ACME, "Acme Platform")
    await revenue_config.set_config(pool, ACME, revenue_type="hosting", actor="t")
    harvest_stub["invoiced"] = {ACME: 999.0}

    run = await _plan(pool, JAN)
    await revenue_run.abandon_run(pool, run["id"], actor="jacob")
    deleted = await revenue_run.delete_run(pool, run["id"], actor="jacob")

    assert deleted["entry_count"] == 1
    assert deleted["period_month"] == JAN
    assert await revenue_ledger.get_run(pool, run["id"]) is None
    # The entries go with it, by `on delete cascade`.
    async with pool.acquire() as conn:
        assert await conn.fetchval(
            "SELECT count(*) FROM revenue_entries WHERE revenue_run_id = $1",
            run["id"],
        ) == 0

    # The month was already free — abandoning is what frees it — and still is.
    assert (await _plan(pool, JAN))["id"] != run["id"]


async def test_deleting_records_what_the_run_was_before_it_goes(client, harvest_stub):
    """The audit line is all that is left, and its id no longer resolves to
    anything, so it has to carry the run rather than point at it."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _project(conn, ACME, "Acme Platform")
    await revenue_config.set_config(pool, ACME, revenue_type="hosting", actor="t")
    harvest_stub["invoiced"] = {ACME: 999.0}

    run = await _plan(pool, JAN)
    await revenue_run.abandon_run(pool, run["id"], actor="jacob")
    await revenue_run.delete_run(pool, run["id"], actor="jacob")

    payload = await pool.fetchval(
        "SELECT payload FROM audit_log WHERE event_type = 'revenue.run.deleted'"
    )
    assert payload["period_month"] == "2026-01-01"
    assert payload["entry_count"] == 1
    assert payload["total_proposed"] == "999.00"
    assert payload["planned_by"]


async def test_a_draft_cannot_be_deleted(client, harvest_stub):
    """It is live and owns the month. Abandoning is the transition that frees
    it, and deleting instead would skip it."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _project(conn, ACME, "Acme Platform")
    await revenue_config.set_config(pool, ACME, revenue_type="hosting", actor="t")
    run = await _plan(pool, JAN)

    with pytest.raises(revenue_run.RevenueRunConflict, match="Abandon it first"):
        await revenue_run.delete_run(pool, run["id"], actor="t")


async def test_a_finalized_month_cannot_be_deleted(client, harvest_stub):
    """Every cumulative figure is a sum over these entries, so deleting one is
    a restatement rather than a correction."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _project(conn, ACME, "Acme Platform")
    await revenue_config.set_config(pool, ACME, revenue_type="hosting", actor="t")
    run = await _plan(pool, JAN)
    await revenue_run.finalize_run(pool, run["id"], actor="t")

    with pytest.raises(revenue_run.RevenueRunConflict, match="cannot be deleted"):
        await revenue_run.delete_run(pool, run["id"], actor="t")
    assert len(await revenue_ledger.list_entries(pool)) == 1


async def test_unknown_run_ids_are_reported_not_ignored(client, harvest_stub):
    pool = await get_pool()
    missing = "00000000-0000-0000-0000-000000000000"
    for call in (
        revenue_run.finalize_run,
        revenue_run.abandon_run,
        revenue_run.delete_run,
    ):
        with pytest.raises(revenue_run.RevenueRunError, match="not found"):
            await call(pool, missing, actor="t")
