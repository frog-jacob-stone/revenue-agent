"""Deriving cumulative-to-date from stored period amounts.

The ledger stores only the period amount. Everything cumulative is summed at
read time, which means two things have to hold or the model is unsound:

  - the running total is per project and in period order, and it spans a
    project's whole history rather than restarting at a date filter's boundary;
  - a draft run's entries count for nothing. They are a proposal, and if they
    leaked into a prior-period sum then merely *planning* a month would change
    what the next month computes.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.db import get_pool
from app.services import revenue_ledger

ACME = 14307913
BETA = 14307914


async def _run(conn, period: date, status: str = "recognized") -> str:
    return await conn.fetchval(
        "INSERT INTO revenue_runs (period_month, status, created_by) "
        "VALUES ($1, $2::revenue_run_status, 'test') RETURNING id",
        period,
        status,
    )


async def _entry(
    conn, run_id: str, period: date, amount: str, *,
    project_id: int = ACME, name: str = "Acme Platform", hours: str | None = None,
) -> str:
    return await conn.fetchval(
        """
        INSERT INTO revenue_entries (
            revenue_run_id, period_month, harvest_project_id,
            harvest_project_name, revenue_type, recognized_amount,
            computed_amount, logged_hours
        ) VALUES ($1, $2, $3, $4, 'fixed_fee', $5, $5, $6) RETURNING id
        """,
        run_id, period, project_id, name,
        Decimal(amount), Decimal(hours) if hours else None,
    )


async def _seed_three_months(conn) -> None:
    for period, amount in [
        (date(2026, 1, 1), "1000.00"),
        (date(2026, 2, 1), "500.00"),
        (date(2026, 3, 1), "250.50"),
    ]:
        await _entry(conn, await _run(conn, period), period, amount, hours="10")


async def test_cumulative_is_the_running_sum(client):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _seed_three_months(conn)

    rows = await revenue_ledger.list_entries(pool)

    # Newest first — the list is a lookup, not a trend.
    assert [r["period_month"] for r in rows] == [
        date(2026, 3, 1), date(2026, 2, 1), date(2026, 1, 1)
    ]
    assert [r["recognized_amount"] for r in rows] == [
        Decimal("250.50"), Decimal("500.00"), Decimal("1000.00")
    ]
    assert [r["cumulative_recognized"] for r in rows] == [
        Decimal("1750.50"), Decimal("1500.00"), Decimal("1000.00")
    ]


async def test_cumulative_spans_the_whole_history_not_the_filtered_range(client):
    """The window runs before the date filter. A twelve-month view has to show
    the true cumulative figure, not one that restarts at the boundary."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _seed_three_months(conn)

    rows = await revenue_ledger.list_entries(pool, date_from=date(2026, 3, 1))

    assert len(rows) == 1
    assert rows[0]["recognized_amount"] == Decimal("250.50")
    # Not 250.50 — January and February still count, they are just not shown.
    assert rows[0]["cumulative_recognized"] == Decimal("1750.50")


async def test_cumulative_is_per_project(client):
    pool = await get_pool()
    async with pool.acquire() as conn:
        jan, feb = await _run(conn, date(2026, 1, 1)), await _run(conn, date(2026, 2, 1))
        await _entry(conn, jan, date(2026, 1, 1), "1000.00")
        await _entry(conn, feb, date(2026, 2, 1), "500.00")
        await _entry(conn, jan, date(2026, 1, 1), "7000.00",
                     project_id=BETA, name="Beta")
        await _entry(conn, feb, date(2026, 2, 1), "1.00",
                     project_id=BETA, name="Beta")

    rows = await revenue_ledger.list_entries(pool, harvest_project_id=BETA)
    assert [r["cumulative_recognized"] for r in rows] == [
        Decimal("7001.00"), Decimal("7000.00")
    ]


# ── Draft entries must not leak ─────────────────────────────────────────────


async def test_draft_entries_are_invisible_to_the_ledger(client):
    pool = await get_pool()
    async with pool.acquire() as conn:
        recognized = await _run(conn, date(2026, 1, 1))
        await _entry(conn, recognized, date(2026, 1, 1), "1000.00")
        draft = await _run(conn, date(2026, 2, 1), status="draft")
        await _entry(conn, draft, date(2026, 2, 1), "500.00")

    rows = await revenue_ledger.list_entries(pool)
    assert [r["period_month"] for r in rows] == [date(2026, 1, 1)]
    assert rows[0]["cumulative_recognized"] == Decimal("1000.00")

    totals = await revenue_ledger.monthly_totals(pool)
    assert [t["period_month"] for t in totals] == [date(2026, 1, 1)]


async def test_abandoned_entries_are_invisible_too(client):
    """An abandoned run is a discarded draft that is kept, not history."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        abandoned = await _run(conn, date(2026, 1, 1), status="abandoned")
        await _entry(conn, abandoned, date(2026, 1, 1), "9999.00")

    assert await revenue_ledger.list_entries(pool) == []


async def test_a_draft_is_still_readable_through_get_run(client):
    """The review screen reads the draft directly. If `recognized_only_sql()`
    applied here it would be permanently empty, and under ADR-0004 there would
    be no payload for an operator to authorize."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        draft = await _run(conn, date(2026, 2, 1), status="draft")
        await _entry(conn, draft, date(2026, 2, 1), "500.00")

    run = await revenue_ledger.get_run(pool, draft)
    assert run["status"] == "draft"
    assert len(run["entries"]) == 1
    assert run["total_recognized"] == Decimal("500.00")


async def test_get_run_totals_an_empty_run_as_a_decimal(client):
    pool = await get_pool()
    async with pool.acquire() as conn:
        empty = await _run(conn, date(2026, 2, 1), status="draft")

    run = await revenue_ledger.get_run(pool, empty)
    assert run["entries"] == []
    assert run["total_recognized"] == Decimal("0.00")


async def test_get_run_returns_none_for_an_unknown_id(client):
    pool = await get_pool()
    assert await revenue_ledger.get_run(
        pool, "00000000-0000-0000-0000-000000000000"
    ) is None


# ── prior_recognized — what a run computes against ──────────────────────────


async def test_prior_recognized_sums_strictly_earlier_periods(client):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _seed_three_months(conn)

        # March must not count itself, or re-planning a month would double it.
        assert await revenue_ledger.prior_recognized(
            conn, ACME, date(2026, 3, 1)
        ) == 1500.0
        assert await revenue_ledger.prior_recognized(
            conn, ACME, date(2026, 4, 1)
        ) == 1750.5


async def test_prior_recognized_is_zero_for_a_new_project(client):
    """The honest answer: its first recognized month is its cumulative total."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        assert await revenue_ledger.prior_recognized(
            conn, 99999, date(2026, 3, 1)
        ) == 0.0


async def test_prior_recognized_ignores_a_draft(client):
    """The one that matters most: planning February must not change what March
    computes. A draft is a proposal, and nobody has decided it."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        jan = await _run(conn, date(2026, 1, 1))
        await _entry(conn, jan, date(2026, 1, 1), "1000.00")
        feb = await _run(conn, date(2026, 2, 1), status="draft")
        await _entry(conn, feb, date(2026, 2, 1), "500.00")

        assert await revenue_ledger.prior_recognized(
            conn, ACME, date(2026, 3, 1)
        ) == 1000.0


# ── Excluding projects with nothing in the window ───────────────────────────
#
# The Overview grid's rows are its projects, so "don't show projects with
# nothing this period" is a question about which rows exist — answered where
# the rows come from, not by dropping them in the browser after they arrive.
#
# What counts as "nothing" depends on the metric being viewed, which is why
# `non_empty` names a measure rather than being a boolean.


async def test_a_project_with_nothing_in_the_window_is_dropped(client):
    pool = await get_pool()
    async with pool.acquire() as conn:
        jan = await _run(conn, date(2026, 1, 1))
        await _entry(conn, jan, date(2026, 1, 1), "1000.00")
        await _entry(conn, jan, date(2026, 1, 1), "0.00",
                     project_id=BETA, name="Beta")

    assert len({r["harvest_project_id"] for r in
                await revenue_ledger.list_entries(pool)} ) == 2

    rows = await revenue_ledger.list_entries(pool, non_empty="revenue")
    assert {r["harvest_project_id"] for r in rows} == {ACME}


async def test_omitting_non_empty_keeps_every_project(client):
    """The Entries tab is a ledger, not a report — it shows what is there."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        jan = await _run(conn, date(2026, 1, 1))
        await _entry(conn, jan, date(2026, 1, 1), "1000.00")
        await _entry(conn, jan, date(2026, 1, 1), "0.00",
                     project_id=BETA, name="Beta")

    rows = await revenue_ledger.list_entries(pool)
    assert {r["harvest_project_id"] for r in rows} == {ACME, BETA}


async def test_the_two_measures_drop_different_projects(client):
    """The crossing case, and the whole reason this is not a boolean.

    Acme recognized revenue without logging hours — its first imported month is
    a catch-up lump covering years of prior work. Beta logged hours and
    recognized nothing, which is real unrecognized work. Each belongs in
    exactly one of the two views.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        jan = await _run(conn, date(2026, 1, 1))
        await _entry(conn, jan, date(2026, 1, 1), "1000.00", hours=None)
        await _entry(conn, jan, date(2026, 1, 1), "0.00", hours="40",
                     project_id=BETA, name="Beta")

    by_revenue = await revenue_ledger.list_entries(pool, non_empty="revenue")
    assert {r["harvest_project_id"] for r in by_revenue} == {ACME}

    by_hours = await revenue_ledger.list_entries(pool, non_empty="hours")
    assert {r["harvest_project_id"] for r in by_hours} == {BETA}


async def test_hours_keeps_a_project_earning_nothing(client):
    """Under `hours` it reads $0/hr, which is exactly what someone looking at
    rates wants to see: work went in and nothing came out."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        jan = await _run(conn, date(2026, 1, 1))
        await _entry(conn, jan, date(2026, 1, 1), "0.00", hours="40")

    rows = await revenue_ledger.list_entries(pool, non_empty="hours")
    assert [r["logged_hours"] for r in rows] == [Decimal("40.00")]
    assert rows[0]["recognized_amount"] == Decimal("0.00")


async def test_it_is_project_level_not_row_level(client):
    """A qualifying project keeps its zero months. Dropping the zero rows
    themselves would silently change the revenue-per-hour blend: a month with
    hours but no revenue is real work that went unrecognized, and it should
    drag the rate down rather than vanish."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        jan, feb = await _run(conn, date(2026, 1, 1)), await _run(conn, date(2026, 2, 1))
        await _entry(conn, jan, date(2026, 1, 1), "1000.00", hours="10")
        await _entry(conn, feb, date(2026, 2, 1), "0.00", hours="40")

    rows = await revenue_ledger.list_entries(pool, non_empty="revenue")
    assert [r["recognized_amount"] for r in rows] == [
        Decimal("0.00"), Decimal("1000.00")
    ]
    # The zero month's hours survive, which is the point.
    assert rows[0]["logged_hours"] == Decimal("40.00")


async def test_empty_means_empty_for_this_window_not_ever(client):
    """A project that earned last year but nothing this year is dropped from
    this year — otherwise the filter would only ever hide projects that have
    never earned anything, which is not the question being asked."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        jan = await _run(conn, date(2026, 1, 1))
        await _entry(conn, jan, date(2026, 1, 1), "5000.00", project_id=BETA, name="Beta")
        mar = await _run(conn, date(2026, 3, 1))
        await _entry(conn, mar, date(2026, 3, 1), "1000.00")
        await _entry(conn, mar, date(2026, 3, 1), "0.00", project_id=BETA, name="Beta")

    rows = await revenue_ledger.list_entries(
        pool, date_from=date(2026, 3, 1), non_empty="revenue"
    )
    assert {r["harvest_project_id"] for r in rows} == {ACME}

    # Widen the window and Beta comes back, because January is now in scope.
    rows = await revenue_ledger.list_entries(pool, non_empty="revenue")
    assert {r["harvest_project_id"] for r in rows} == {ACME, BETA}


async def test_the_hours_window_is_bounded_the_same_way(client):
    """Same remembered placeholders, so both measures see the same window."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        jan = await _run(conn, date(2026, 1, 1))
        await _entry(conn, jan, date(2026, 1, 1), "0.00", hours="40",
                     project_id=BETA, name="Beta")
        mar = await _run(conn, date(2026, 3, 1))
        await _entry(conn, mar, date(2026, 3, 1), "1000.00", hours="10")
        await _entry(conn, mar, date(2026, 3, 1), "0.00", hours=None,
                     project_id=BETA, name="Beta")

    rows = await revenue_ledger.list_entries(
        pool, date_from=date(2026, 3, 1), non_empty="hours"
    )
    assert {r["harvest_project_id"] for r in rows} == {ACME}


async def test_a_draft_does_not_rescue_an_empty_project(client):
    """The subquery applies the same finalized-run predicate as the outer one,
    so a proposed figure cannot make a project look active."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        jan = await _run(conn, date(2026, 1, 1))
        await _entry(conn, jan, date(2026, 1, 1), "0.00")
        draft = await _run(conn, date(2026, 2, 1), status="draft")
        await _entry(conn, draft, date(2026, 2, 1), "9999.00", hours="80")

    assert await revenue_ledger.list_entries(pool, non_empty="revenue") == []
    assert await revenue_ledger.list_entries(pool, non_empty="hours") == []


# ── The client filter ───────────────────────────────────────────────────────


async def _two_clients(conn) -> None:
    await conn.execute(
        "INSERT INTO harvest_projects (harvest_id, name, client_id, client_name) "
        "VALUES ($1, 'Acme Platform', 500, 'Acme'), ($2, 'Beta App', 600, 'Beta')",
        ACME, BETA,
    )
    run = await _run(conn, date(2026, 1, 1))
    await _entry(conn, run, date(2026, 1, 1), "1000.00", hours="10")
    await _entry(conn, run, date(2026, 1, 1), "500.00",
                 project_id=BETA, name="Beta App", hours="5")


async def test_entries_and_totals_narrow_to_the_same_clients(client):
    """Both, or the chart and the grid beside it would show two different
    books on one screen."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _two_clients(conn)

    rows = await revenue_ledger.list_entries(pool, client_ids=[500])
    assert [r["harvest_project_id"] for r in rows] == [ACME]

    totals = await revenue_ledger.monthly_totals(pool, client_ids=[500])
    assert totals[0]["recognized_amount"] == Decimal("1000.00")
    assert totals[0]["logged_hours"] == Decimal("10.00")


async def test_several_clients_at_once(client):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _two_clients(conn)

    rows = await revenue_ledger.list_entries(pool, client_ids=[500, 600])
    assert {r["harvest_project_id"] for r in rows} == {ACME, BETA}

    totals = await revenue_ledger.monthly_totals(pool, client_ids=[500, 600])
    assert totals[0]["recognized_amount"] == Decimal("1500.00")


async def test_no_clients_selected_means_all_not_none(client):
    """The state the screen opens in. An empty selection showing an empty
    report would be a trap rather than a filter."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _two_clients(conn)

    assert len(await revenue_ledger.list_entries(pool, client_ids=[])) == 2
    assert len(await revenue_ledger.list_entries(pool, client_ids=None)) == 2


async def test_entries_carry_the_client_id_for_the_filter(client):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _two_clients(conn)

    rows = await revenue_ledger.list_entries(pool, harvest_project_id=ACME)
    assert rows[0]["client_id"] == 500
    assert rows[0]["client_name"] == "Acme"


async def test_client_options_come_with_their_totals(client):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _two_clients(conn)

    clients = await revenue_ledger.list_clients(pool)
    assert [c["client_name"] for c in clients] == ["Acme", "Beta"]  # alphabetical
    assert clients[0]["recognized_amount"] == Decimal("1000.00")
    assert clients[0]["project_count"] == 1
    # Hours ride along so the number beside each name can follow the metric
    # the Overview is currently showing.
    assert clients[0]["logged_hours"] == Decimal("10.00")


async def test_client_options_are_bounded_by_the_period(client):
    """The list has to match the window it filters, or it would offer a client
    that yields nothing."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _two_clients(conn)
        feb = await _run(conn, date(2026, 2, 1))
        await _entry(conn, feb, date(2026, 2, 1), "700.00")

    clients = await revenue_ledger.list_clients(pool, date_from=date(2026, 2, 1))
    assert [c["client_name"] for c in clients] == ["Acme"]


async def test_a_client_with_nothing_at_all_is_not_offered(client):
    """Picking it could only ever empty the screen — the same noise
    `non_empty` keeps out of the grid."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _two_clients(conn)
        await conn.execute(
            "UPDATE revenue_entries SET recognized_amount = 0, logged_hours = 0 "
            "WHERE harvest_project_id = $1", BETA,
        )

    assert [c["client_name"] for c in await revenue_ledger.list_clients(pool)] == ["Acme"]


async def test_the_option_list_does_not_move_when_the_metric_does(client):
    """Revenue *or* hours qualifies, so the same names are offered whichever
    view the reader is in. A client disappearing out from under a selection
    because they switched to hours would be worse than one extra option."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _two_clients(conn)
        # Beta logged hours but recognized nothing; Acme the reverse.
        await conn.execute(
            "UPDATE revenue_entries SET recognized_amount = 0 "
            "WHERE harvest_project_id = $1", BETA,
        )
        await conn.execute(
            "UPDATE revenue_entries SET logged_hours = NULL "
            "WHERE harvest_project_id = $1", ACME,
        )

    clients = await revenue_ledger.list_clients(pool)
    assert [c["client_name"] for c in clients] == ["Acme", "Beta"]
    assert clients[0]["logged_hours"] is None
    assert clients[1]["recognized_amount"] == Decimal("0.00")
    assert clients[1]["logged_hours"] == Decimal("5.00")


async def test_a_draft_does_not_put_a_client_in_the_list(client):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO harvest_projects (harvest_id, name, client_id, client_name) "
            "VALUES ($1, 'Acme Platform', 500, 'Acme')", ACME,
        )
        draft = await _run(conn, date(2026, 1, 1), status="draft")
        await _entry(conn, draft, date(2026, 1, 1), "9999.00")

    assert await revenue_ledger.list_clients(pool) == []


async def test_excluded_clients_are_not_offered(client):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _two_clients(conn)
        await conn.execute(
            "INSERT INTO excluded_harvest_clients (harvest_client_id, excluded_by) "
            "VALUES (600, 'test')"
        )

    assert [c["client_name"] for c in await revenue_ledger.list_clients(pool)] == ["Acme"]


# ── Client exclusion ────────────────────────────────────────────────────────


async def test_excluded_clients_are_dropped(client):
    """Exclusion is account-wide — our own company is a Harvest client — so it
    applies to the ledger exactly as it does to the Projects roster."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO harvest_projects (harvest_id, name, client_id, client_name) "
            "VALUES ($1, 'Acme Platform', 500, 'Acme'), ($2, 'Internal', 999, 'Us')",
            ACME, BETA,
        )
        await conn.execute(
            "INSERT INTO excluded_harvest_clients (harvest_client_id, excluded_by) "
            "VALUES (999, 'test')"
        )
        run = await _run(conn, date(2026, 1, 1))
        await _entry(conn, run, date(2026, 1, 1), "1000.00")
        await _entry(conn, run, date(2026, 1, 1), "5000.00",
                     project_id=BETA, name="Internal")

    rows = await revenue_ledger.list_entries(pool)
    assert [r["harvest_project_id"] for r in rows] == [ACME]
    assert rows[0]["client_name"] == "Acme"

    totals = await revenue_ledger.monthly_totals(pool)
    assert totals[0]["recognized_amount"] == Decimal("1000.00")


async def test_an_entry_survives_its_project_leaving_the_cache(client):
    """`harvest_projects` is truncate-safe, so the join is LEFT. A ledger row
    that predates a cache rebuild must still list — its own snapshotted name is
    the fallback."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        run = await _run(conn, date(2026, 1, 1))
        await _entry(conn, run, date(2026, 1, 1), "1000.00")

    rows = await revenue_ledger.list_entries(pool)
    assert len(rows) == 1
    assert rows[0]["client_name"] is None
    assert rows[0]["harvest_project_name"] == "Acme Platform"


# ── Trend and run list ──────────────────────────────────────────────────────


async def test_monthly_totals_read_oldest_first(client):
    """The opposite order to every other list here, because it is a chart."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _seed_three_months(conn)

    totals = await revenue_ledger.monthly_totals(pool)
    assert [t["period_month"] for t in totals] == [
        date(2026, 1, 1), date(2026, 2, 1), date(2026, 3, 1)
    ]
    assert totals[0]["logged_hours"] == Decimal("10.00")
    assert totals[0]["entry_count"] == 1


async def test_monthly_totals_bound_by_date_not_a_trailing_count(client):
    """A count can only say "the last N months", which is a reporting window.
    A calendar year or an arbitrary range is what someone actually asks for,
    and only dates can express either."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _seed_three_months(conn)

    totals = await revenue_ledger.monthly_totals(pool, date_from=date(2026, 2, 1))
    assert [t["period_month"] for t in totals] == [date(2026, 2, 1), date(2026, 3, 1)]

    totals = await revenue_ledger.monthly_totals(pool, date_to=date(2026, 1, 1))
    assert [t["period_month"] for t in totals] == [date(2026, 1, 1)]

    # A single month — the degenerate range the custom picker allows.
    totals = await revenue_ledger.monthly_totals(
        pool, date_from=date(2026, 2, 1), date_to=date(2026, 2, 1)
    )
    assert [t["period_month"] for t in totals] == [date(2026, 2, 1)]

    # Unbounded is every month, which the Overview tab needs to know the span.
    assert len(await revenue_ledger.monthly_totals(pool)) == 3


async def test_list_runs_reports_every_status_with_its_own_total(client):
    pool = await get_pool()
    async with pool.acquire() as conn:
        jan = await _run(conn, date(2026, 1, 1))
        await _entry(conn, jan, date(2026, 1, 1), "1000.00")
        feb = await _run(conn, date(2026, 2, 1), status="draft")
        await _entry(conn, feb, date(2026, 2, 1), "500.00")

    runs = await revenue_ledger.list_runs(pool)
    assert [r["status"] for r in runs] == ["draft", "recognized"]
    # A draft reports what it currently proposes.
    assert runs[0]["total_recognized"] == Decimal("500.00")
    assert runs[0]["entry_count"] == 1
