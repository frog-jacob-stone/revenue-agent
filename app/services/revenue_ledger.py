"""Revenue recognition reads.

Read-only, so nothing here writes `audit_log`. The writers live in
`app/services/revenue_run.py` (the run) and `app/services/revenue_config.py`
(per-project configuration).

`revenue_entries` stores the **period** amount (`recognized_amount`) and nothing
cumulative — see migration `0040`. Cumulative-to-date is derived here, and the
two consumers want different shapes of it:

    prior_recognized()   one scalar, for the run: everything a project has
                         recognized before a given month, which is what the
                         next period's amount is computed against.
    list_entries()       a running total per row, for display.

Deliberately not a database view. There are none anywhere in this schema, and
the established pattern for aggregation is SQL inline in a service query — see
`billing/planner.py` and `billing/invoices.py`. What the two consumers actually
share is one predicate, `recognized_only_sql()`, which follows the shape of
`client_exclusions.not_excluded_sql()`.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

import asyncpg

from app.services.client_exclusions import not_excluded_sql


def recognized_only_sql(alias: str = "e") -> str:
    """SQL predicate: this entry belongs to a finalized run.

    A draft run's entries are a proposal — computed, editable, and not yet
    anyone's decision. They must not reach a report, and they must not reach a
    prior-period sum either, or planning one month would silently change what
    the next month computes. An abandoned run's entries are likewise not
    history; they are a discarded draft that is kept rather than deleted.

    Takes no bind parameters, so it composes into any WHERE clause without
    disturbing a caller's `$n` numbering — the callers build their placeholders
    positionally. Same reasoning as `client_exclusions.not_excluded_sql()`.
    """
    return (
        f"EXISTS (SELECT 1 FROM revenue_runs r "
        f"WHERE r.id = {alias}.revenue_run_id AND r.status = 'recognized')"
    )


async def prior_recognized(
    conn: Any, harvest_project_id: int, before_period: date
) -> float:
    """Everything this project has recognized before `before_period`.

    The number a run subtracts from a cumulative target to get the period
    amount. Strictly before: a re-planned month must not count itself.

    Takes a connection rather than a pool so the planner can call it inside the
    transaction it is already holding — same convention as
    `billing.settings_store.get`.

    Returns 0.0 for a project with no history, which is the honest answer: its
    first recognized month is its cumulative total.
    """
    value = await conn.fetchval(
        f"""
        SELECT coalesce(sum(e.recognized_amount), 0)
        FROM revenue_entries e
        WHERE e.harvest_project_id = $1
          AND e.period_month < $2
          AND {recognized_only_sql()}
        """,
        harvest_project_id,
        before_period,
    )
    return float(value or 0)


async def list_clients(
    pool: asyncpg.Pool,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
) -> list[dict[str, Any]]:
    """Clients with activity in the window, alphabetical — the filter's options.

    Its own query rather than derived from `list_entries`, because a faceted
    filter's options have to come from the *unfiltered* set: building them from
    the filtered rows would make each selection remove the others from the
    list, and there would be no way back.

    "Activity" means revenue **or** hours, not revenue alone. The Overview is
    read through a metric selector, and this list must not change when the
    metric does — a client vanishing out from under a selection because the
    reader switched to hours is worse than one extra option in the list. A
    client with neither is dropped, which is the same noise `non_empty` removes
    from the grid.

    Both totals come back for the same reason: the number shown beside each
    name is whichever measure is currently on screen.

    An entry whose project has left the Harvest cache is dropped — see the
    predicate below for why that is a decision rather than a side effect.
    """
    conditions = [
        recognized_only_sql(),
        # LEFT JOIN below, like every other read in this module, so the join
        # type is no longer what decides an orphaned entry's fate — this is.
        #
        # An entry whose project is no longer in the Harvest cache has no
        # client to be filed under, and this query feeds a list of *selectable*
        # clients: `client_ids` is a list of ints, and `p.client_id = ANY(...)`
        # in `list_entries` and `monthly_summary` can never match NULL. A "no
        # client" option would therefore be one the reader could tick and get
        # an empty grid back — the trap `ClientFilter` exists to avoid.
        #
        # So orphans are dropped here, exactly as the client filter drops them
        # downstream. They still appear in the grid and the trend, which read
        # unfiltered; the asymmetry is the price of not offering a dead option.
        "p.client_id IS NOT NULL",
        not_excluded_sql(),
        "(e.recognized_amount <> 0 OR coalesce(e.logged_hours, 0) <> 0)",
    ]
    params: list[Any] = []

    if date_from is not None:
        params.append(date_from)
        conditions.append(f"e.period_month >= ${len(params)}")
    if date_to is not None:
        params.append(date_to)
        conditions.append(f"e.period_month <= ${len(params)}")

    rows = await pool.fetch(
        f"""
        SELECT p.client_id,
               max(p.client_name)       AS client_name,
               sum(e.recognized_amount) AS recognized_amount,
               sum(e.logged_hours)      AS logged_hours,
               count(DISTINCT e.harvest_project_id) AS project_count
        FROM revenue_entries e
        LEFT JOIN harvest_projects p ON p.harvest_id = e.harvest_project_id
        WHERE {" AND ".join(conditions)}
        GROUP BY p.client_id
        ORDER BY max(p.client_name)
        """,
        *params,
    )
    return [dict(r) for r in rows]


async def list_entries(
    pool: asyncpg.Pool,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    harvest_project_id: int | None = None,
    client_ids: list[int] | None = None,
    non_empty: Literal["revenue", "hours"] | None = None,
) -> list[dict[str, Any]]:
    """Ledger rows, newest first, with cumulative-to-date on each.

    The window function runs over a project's **whole** history, not just the
    rows the date filter returns — so a twelve-month view still shows the true
    cumulative figure rather than one that restarts at the range boundary. That
    is why the filtering happens outside the window, in the outer query.

    `client_name` is joined from the Harvest snapshot rather than snapshotted on
    the entry: unlike the project name it is not part of what was recognized,
    and a client that gets renamed should read consistently across the whole
    ledger. The join is LEFT, so an entry whose project has since vanished from
    the cache still lists — its own `harvest_project_name` is the fallback.

    Projects of an excluded client are dropped. Exclusion is account-wide (our
    own company is a Harvest client), so it applies here exactly as it does to
    the Projects roster.

    `non_empty` drops projects that have nothing to show across the whole
    window — a grid row of dashes tells the reader only that the project
    exists. Which measure counts as "something" depends on what is being
    looked at, so the caller says:

        "revenue"  the project recognized nothing      → drop
        "hours"    the project logged nothing          → drop

    The revenue view asks for `"revenue"`; the hours and revenue-per-hour views
    both ask for `"hours"`, since a rate cell has no value without a
    denominator. A project with hours but no revenue is deliberately kept under
    `"hours"` — it reads $0/hr, which is real (unrecognized work) and is
    precisely what someone looking at rates wants to see.

    It is a **project**-level filter, not a row-level one: a project that
    qualifies keeps every entry it has, zeros included. Dropping the zero rows
    themselves would silently change the revenue-per-hour blend for everyone
    else on the screen.
    """
    # The exclusion goes in as an ordinary condition so the WHERE clause is
    # never empty and the optional filters need no special-casing.
    conditions: list[str] = [f"(p.harvest_id IS NULL OR {not_excluded_sql()})"]
    params: list[Any] = []
    # Remembered rather than recomputed, so the correlated subquery below can
    # reuse the same bindings without depending on the order these were added.
    from_ph = to_ph = None

    if date_from is not None:
        params.append(date_from)
        from_ph = f"${len(params)}"
        conditions.append(f"e.period_month >= {from_ph}")
    if date_to is not None:
        params.append(date_to)
        to_ph = f"${len(params)}"
        conditions.append(f"e.period_month <= {to_ph}")
    if harvest_project_id is not None:
        params.append(harvest_project_id)
        conditions.append(f"e.harvest_project_id = ${len(params)}")
    if client_ids:
        # An entry whose project has left the Harvest cache has no client to
        # match, so it drops out here. That is the honest answer: it cannot be
        # said to belong to any of the selected clients.
        params.append(client_ids)
        conditions.append(f"p.client_id = ANY(${len(params)})")

    if non_empty is not None:
        # Correlated on the project and bounded by the same window, so "empty"
        # means empty *for this period* rather than ever.
        window = ""
        if from_ph:
            window += f" AND z.period_month >= {from_ph}"
        if to_ph:
            window += f" AND z.period_month <= {to_ph}"
        has = (
            "z.recognized_amount <> 0"
            if non_empty == "revenue"
            else "coalesce(z.logged_hours, 0) <> 0"
        )
        conditions.append(
            f"""EXISTS (
                SELECT 1 FROM revenue_entries z
                WHERE z.harvest_project_id = e.harvest_project_id
                  AND {has}
                  AND {recognized_only_sql("z")}{window}
            )"""
        )

    rows = await pool.fetch(
        f"""
        WITH ledger AS (
            SELECT e.*,
                   sum(e.recognized_amount) OVER (
                       PARTITION BY e.harvest_project_id
                       ORDER BY e.period_month
                       ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                   ) AS cumulative_recognized
            FROM revenue_entries e
            WHERE {recognized_only_sql()}
        )
        SELECT e.id, e.period_month, e.harvest_project_id,
               e.harvest_project_name, e.revenue_type,
               e.recognized_amount, e.cumulative_recognized, e.computed_amount,
               e.logged_hours, e.scheduled_hours, e.percent_complete,
               e.contracted_fees, e.invoiced_to_date, e.notes,
               e.override_reason, e.overridden_by, e.overridden_at,
               p.client_id, p.client_name
        FROM ledger e
        LEFT JOIN harvest_projects p ON p.harvest_id = e.harvest_project_id
        WHERE {" AND ".join(conditions)}
        ORDER BY e.period_month DESC, e.harvest_project_name
        """,
        *params,
    )
    return [dict(r) for r in rows]


async def monthly_totals(
    pool: asyncpg.Pool,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    client_ids: list[int] | None = None,
) -> list[dict[str, Any]]:
    """Recognized revenue and hours per month, oldest first — the trend.

    Oldest first because it is read left to right as a chart, unlike the entry
    and run lists which are lookups and read newest first.

    Bounded by date rather than a trailing month count. A count can only ever
    express "the last N months", which is a reporting window; a calendar year
    or an arbitrary range is what someone actually asks for, and only dates can
    say either. Unbounded returns every month, which is what the Overview tab
    wants so it can offer the years the ledger actually covers.

    Hours ride along so the caller can compute revenue per billable hour from a
    period's own two numbers. That ratio is deliberately **not** computed here
    or stored anywhere: blending it (total revenue / total hours) and averaging
    it across months give different answers, and only the former is right — a
    light month must not weigh the same as a heavy one.

    Takes `client_ids` for the same reason `list_entries` does: the caller
    renders these totals as a chart beside a grid built from the entries, and a
    filter that narrowed one without the other would put two different books on
    the same screen.
    """
    conditions = [f"(p.harvest_id IS NULL OR {not_excluded_sql()})"]
    params: list[Any] = []

    if date_from is not None:
        params.append(date_from)
        conditions.append(f"e.period_month >= ${len(params)}")
    if date_to is not None:
        params.append(date_to)
        conditions.append(f"e.period_month <= ${len(params)}")
    if client_ids:
        params.append(client_ids)
        conditions.append(f"p.client_id = ANY(${len(params)})")

    rows = await pool.fetch(
        f"""
        SELECT e.period_month,
               sum(e.recognized_amount) AS recognized_amount,
               sum(e.logged_hours)      AS logged_hours,
               count(*)                 AS entry_count
        FROM revenue_entries e
        LEFT JOIN harvest_projects p ON p.harvest_id = e.harvest_project_id
        WHERE {recognized_only_sql()}
          AND {" AND ".join(conditions)}
        GROUP BY e.period_month
        ORDER BY e.period_month
        """,
        *params,
    )
    return [dict(r) for r in rows]


async def list_runs(
    pool: asyncpg.Pool, *, limit: int = 24
) -> list[dict[str, Any]]:
    """Run history, newest month first.

    Every status, including drafts and abandoned runs — this is the run log, not
    the ledger. The totals are of the run's own entries, so a draft reports what
    it currently proposes.
    """
    rows = await pool.fetch(
        """
        SELECT r.id, r.period_month, r.status, r.created_at, r.created_by,
               r.finalized_at, r.finalized_by, r.abandoned_at, r.abandoned_by,
               count(e.id)                             AS entry_count,
               coalesce(sum(e.recognized_amount), 0)   AS total_recognized
        FROM revenue_runs r
        LEFT JOIN revenue_entries e ON e.revenue_run_id = r.id
        GROUP BY r.id
        ORDER BY r.period_month DESC, r.created_at DESC
        LIMIT $1
        """,
        limit,
    )
    return [dict(r) for r in rows]


async def get_run(
    pool: asyncpg.Pool, run_id: UUID
) -> dict[str, Any] | None:
    """One run and its entries, whatever its status.

    `recognized_only_sql()` deliberately does not apply. This is what an
    operator reads before finalizing — the draft *is* the thing being looked
    at — and under ADR-0004 seeing this exact payload is what makes the
    subsequent click an authorization. Filtering it to finalized runs would
    make the review screen permanently empty.

    Entries are ordered by project name: the operator is scanning for a
    particular one, not reading a ranking.
    """
    run = await pool.fetchrow(
        """
        SELECT id, period_month, status, created_at, created_by,
               finalized_at, finalized_by, abandoned_at, abandoned_by
        FROM revenue_runs WHERE id = $1
        """,
        run_id,
    )
    if run is None:
        return None

    entries = await pool.fetch(
        f"""
        SELECT e.id, e.period_month, e.harvest_project_id,
               e.harvest_project_name, e.revenue_type,
               e.recognized_amount, e.computed_amount,
               e.logged_hours,
               -- The denominator of `percent_complete`, rebuilt so the row can
               -- be checked against itself. `logged_hours` is the period's own,
               -- while percent complete is a fraction of total effort, so a
               -- month showing 25 hours at 100% complete looks wrong until the
               -- 1,816 hours behind it are visible.
               --
               -- Must stay identical to `revenue_run._cumulative_hours`, which
               -- is what the planner actually divided by: this period's hours
               -- plus every recognized month before it. Drafts and abandoned
               -- runs are excluded there and here — a proposal is not history,
               -- and counting one would make the figure shift as other months
               -- are planned.
               coalesce(e.logged_hours, 0) + coalesce((
                   SELECT sum(h.logged_hours)
                   FROM revenue_entries h
                   WHERE h.harvest_project_id = e.harvest_project_id
                     AND h.period_month < e.period_month
                     AND {recognized_only_sql("h")}
               ), 0) AS cumulative_hours,
               e.scheduled_hours, e.percent_complete,
               e.contracted_fees, e.invoiced_to_date, e.notes,
               e.override_reason, e.overridden_by, e.overridden_at,
               p.client_name,
               -- Read live rather than snapshotted, and that is the point: the
               -- reviewer is being asked whether this project is finished
               -- *now*. An archived project only reaches a run because it still
               -- had a balance, and recognizing the rest of a contract assumes
               -- the work completed — which is usually true and is catastrophic
               -- when it is not, because a cancelled project looks identical to
               -- a finished one once its Forecast bookings end. Null when the
               -- project has left the snapshot entirely.
               p.is_active AS project_is_active
        FROM revenue_entries e
        LEFT JOIN harvest_projects p ON p.harvest_id = e.harvest_project_id
        WHERE e.revenue_run_id = $1
        ORDER BY e.harvest_project_name
        """,
        run_id,
    )

    return {
        **dict(run),
        "entries": [dict(e) for e in entries],
        # Decimal start value, so an empty run totals to Decimal("0.00") rather
        # than int 0 and the response model sees one type either way.
        "total_recognized": sum(
            (e["recognized_amount"] for e in entries), Decimal("0.00")
        ),
    }
