"""The monthly revenue recognition run.

Operator-initiated throughout (ADR-0004): a human plans a month, reads the
entries, adjusts what needs judgement, and finalizes. No agent can reach any of
this, and `tests/test_no_agent_approval_tools.py` keeps it that way.

Safer than the billing run it is modelled on (`POST /billing/draws/{id}/invoice`)
in one important way: **finalizing writes nothing outside Postgres.** There is
no vendor call at the end, so no in-flight state, no unknown outcome, and
nothing to reconcile against Harvest afterwards. A month that goes wrong is
abandoned and planned again.

    plan_run       draft + one entry per in-scope project. Reads Harvest and
                   Forecast; writes only our own tables.
    override_entry a human replaces a computed figure. Draft only.
    finalize_run   the entries become the ledger.
    abandon_run    discard, freeing the month to be planned again.

THE SUBTRACTION

Every recognition method computes a *cumulative* figure — percent complete
against the whole contract, or Harvest's invoiced-to-date. The period amount is
that minus everything already recognized:

    computed_amount = cumulative_target - prior_recognized(project, month)

Which is why correcting a closed month is not a restatement: the correction
changes the prior sum, so the next open month absorbs it automatically.
"""
from __future__ import annotations

import asyncio
from calendar import monthrange
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

import asyncpg

from app.config import Settings
from app.integrations import forecast, harvest
from app.orchestrator import events
from app.services import audit, revenue_config, revenue_ledger
from app.services.revenue import calc_revenue


class RevenueRunError(Exception):
    """The run cannot proceed. Raised before anything is written."""


class RevenueConfigMissing(RevenueRunError):
    """In-scope projects have no usable configuration.

    Carries the list so the caller can name them. The same "fix it and re-run"
    shape the Airtable flow had, except the fixing now happens on a screen in
    this system rather than in a spreadsheet.
    """

    def __init__(self, projects: list[dict[str, Any]]):
        self.projects = projects
        names = ", ".join(p["harvest_project_name"] for p in projects[:5])
        more = f" and {len(projects) - 5} more" if len(projects) > 5 else ""
        super().__init__(
            f"{len(projects)} project(s) need revenue configuration before a "
            f"month can be planned: {names}{more}."
        )


class RevenueRunConflict(RevenueRunError):
    """A live run already owns this month, or the run is in the wrong state."""


def _period_bounds(period_month: date) -> tuple[date, date]:
    """First and last day of the month. Harvest and Forecast want dates, and
    the ledger's period is a month."""
    last = monthrange(period_month.year, period_month.month)[1]
    return period_month, period_month.replace(day=last)


def _next_month(period_month: date) -> date:
    return (
        period_month.replace(year=period_month.year + 1, month=1)
        if period_month.month == 12
        else period_month.replace(month=period_month.month + 1)
    )


def _dec(value: float | None, places: str = "0.01") -> Decimal | None:
    """Float from a computation -> the numeric(x,y) the column holds.

    Via `str()` so the Decimal is the number as computed rather than the
    float's binary approximation of it.
    """
    return None if value is None else Decimal(str(value)).quantize(Decimal(places))


async def _gather_harvest_and_forecast(
    cfg: Settings, period_month: date
) -> tuple[dict[int, float], dict[int, float], dict[int, dict[str, Any]]]:
    """Period hours, forward-looking scheduled hours, and invoiced-to-date.

    Three reads, no writes. Hours come from one account-wide sweep rather than a
    request per project: Harvest has no bulk per-project filter, so asking
    individually costs one paginated query each against a bucket that allows 100
    per 15 seconds.

    The sweep is bounded to the period, so its sum *is* the period quantity —
    unlike `harvest.get_time_entries`, which sums to a date and would need a
    second call and a subtraction to say the same thing.
    """
    period_start, period_end = _period_bounds(period_month)

    entries, scheduled, invoiced = await asyncio.gather(
        harvest.list_time_entries_all(
            cfg, from_=period_start.isoformat(), to=period_end.isoformat()
        ),
        # Scheduled hours are forward-looking as of the period end, so the
        # window opens the day after it.
        forecast.get_scheduled_hours_by_harvest_id(
            cfg, _next_month(period_month).isoformat()
        ),
        harvest.get_invoice_totals_by_project(cfg, period_end.isoformat()),
    )

    hours_by_project: dict[int, float] = {}
    for entry in entries:
        pid = int((entry.get("project") or {}).get("id") or 0)
        if pid:
            hours_by_project[pid] = round(
                hours_by_project.get(pid, 0.0) + float(entry.get("hours") or 0), 4
            )

    return hours_by_project, {int(k): float(v) for k, v in scheduled.items()}, invoiced


async def _in_scope_projects(conn: Any) -> list[dict[str, Any]]:
    """Billable, active, not an excluded client's, and configured.

    Joined to config rather than checked afterwards, so the planner reads one
    row per project with everything it needs. The gate above has already
    guaranteed every in-scope project has a row here.
    """
    rows = await conn.fetch(
        f"""
        SELECT p.harvest_id AS harvest_project_id,
               p.name       AS harvest_project_name,
               c.revenue_type, c.contracted_fees
        FROM harvest_projects p
        JOIN revenue_project_config c ON c.harvest_project_id = p.harvest_id
        WHERE {revenue_config.IN_SCOPE_SQL}
        ORDER BY p.name
        """
    )
    return [dict(r) for r in rows]


async def plan_run(
    pool: asyncpg.Pool,
    cfg: Settings,
    *,
    period_month: date,
    actor: str = "system",
) -> dict[str, Any]:
    """Draft a month. Read-only against Harvest and Forecast.

    The whole plan is one transaction: a half-written run with entries for some
    projects and not others would look finalizable and be wrong.

    Raises `RevenueConfigMissing` if any in-scope project is unconfigured, and
    `RevenueRunConflict` if a live run already owns the month — the latter from
    the partial unique index rather than a read-then-write check, so two people
    planning at once cannot both succeed.
    """
    period_month = period_month.replace(day=1)

    # Outside the transaction: these are slow vendor reads, and holding a
    # transaction open across them would keep a connection pinned for the
    # duration for no benefit.
    hours, scheduled, invoiced = await _gather_harvest_and_forecast(cfg, period_month)

    async with pool.acquire() as conn:
        async with conn.transaction():
            missing = await revenue_config.unconfigured_projects(conn)
            if missing:
                raise RevenueConfigMissing(missing)

            projects = await _in_scope_projects(conn)
            if not projects:
                raise RevenueRunError(
                    "No billable, active projects are in scope for revenue "
                    "recognition. Check the Harvest snapshot is current and "
                    "that the clients you expect are not excluded."
                )

            try:
                run_id = await conn.fetchval(
                    "INSERT INTO revenue_runs (period_month, status, created_by) "
                    "VALUES ($1, 'draft', $2) RETURNING id",
                    period_month, actor,
                )
            except asyncpg.UniqueViolationError as exc:
                raise RevenueRunConflict(
                    f"{period_month:%B %Y} already has a run. Abandon it before "
                    "planning the month again."
                ) from exc

            total = Decimal("0.00")
            for project in projects:
                pid = project["harvest_project_id"]
                invoice = invoiced.get(pid, {})
                contracted = project["contracted_fees"]

                # Percent complete is a fraction of *total* effort, so it needs
                # the running total — while the column stores the period's own.
                cumulative_hours = await _cumulative_hours(
                    conn, pid, period_month, hours
                )
                result = calc_revenue(
                    revenue_type=project["revenue_type"],
                    hours_logged=cumulative_hours,
                    forecast_hours=scheduled.get(pid, 0.0),
                    contracted_fees=None if contracted is None else float(contracted),
                    invoiced_to_date=float(invoice.get("total_amount", 0.0) or 0),
                    billable_expenses=float(invoice.get("billable_expenses", 0.0) or 0),
                )

                prior = await revenue_ledger.prior_recognized(conn, pid, period_month)
                computed = _dec(round(result.amount - prior, 2)) or Decimal("0.00")
                total += computed

                await conn.execute(
                    """
                    INSERT INTO revenue_entries (
                        revenue_run_id, period_month, harvest_project_id,
                        harvest_project_name, revenue_type, recognized_amount,
                        computed_amount, logged_hours, scheduled_hours,
                        percent_complete, contracted_fees, invoiced_to_date, notes
                    ) VALUES ($1, $2, $3, $4, $5::revenue_type, $6, $6,
                              $7, $8, $9, $10, $11, $12)
                    """,
                    run_id, period_month, pid, project["harvest_project_name"],
                    project["revenue_type"],
                    computed,
                    _dec(hours.get(pid, 0.0)),
                    _dec(scheduled.get(pid, 0.0)),
                    _dec(result.percent_complete, "0.0001"),
                    contracted,
                    _dec(float(invoice.get("total_amount", 0.0) or 0)),
                    result.notes or None,
                )

            await audit.write_audit_event(
                conn,
                events.REVENUE_RUN_PLANNED,
                actor=actor,
                # Counts and the total, not the entries — those are rows in
                # `revenue_entries` and stay readable there.
                payload={
                    "revenue_run_id": str(run_id),
                    "period_month": period_month.isoformat(),
                    "projects": len(projects),
                    "computed_total": str(total),
                },
            )

    return await revenue_ledger.get_run(pool, run_id)


async def _cumulative_hours(
    conn: Any,
    harvest_project_id: int,
    period_month: date,
    period_hours: dict[int, float],
) -> float:
    """Hours logged from project inception through the end of this period.

    Percent complete is a fraction of *total* effort, so it needs the running
    total, while `revenue_entries.logged_hours` stores the period's own. Rather
    than a second Harvest sweep, the total is the ledger's summed history plus
    this period's — which is exactly what the stored figures mean, and stays
    correct if an earlier month was ever corrected.
    """
    prior = await conn.fetchval(
        f"""
        SELECT coalesce(sum(e.logged_hours), 0)
        FROM revenue_entries e
        WHERE e.harvest_project_id = $1
          AND e.period_month < $2
          AND {revenue_ledger.recognized_only_sql()}
        """,
        harvest_project_id,
        period_month,
    )
    return float(prior or 0) + period_hours.get(harvest_project_id, 0.0)


async def override_entry(
    pool: asyncpg.Pool,
    run_id: UUID,
    entry_id: UUID,
    *,
    recognized_amount: Decimal,
    override_reason: str,
    actor: str = "system",
) -> dict[str, Any]:
    """Replace a computed figure with a human's.

    Draft only. A finalized month is history, and the way to change it is to
    recognize the difference in the next open month — which happens by itself,
    since every period is computed against the sum of the ones before it.

    `computed_amount` is never touched. The pair is the whole point: what the
    system said, what we booked, and who changed it, on one row.

    A reason is required. This is the only record of why a figure is not what
    the arithmetic produced, and "because I said so" six months later is not an
    answer anyone can audit.
    """
    if not (override_reason or "").strip():
        raise RevenueRunError(
            "An override needs a reason — it is the only record of why this "
            "figure is not what was computed."
        )

    async with pool.acquire() as conn:
        async with conn.transaction():
            status = await conn.fetchval(
                "SELECT status FROM revenue_runs WHERE id = $1 FOR UPDATE", run_id
            )
            if status is None:
                raise RevenueRunError("Revenue run not found.")
            if status != "draft":
                raise RevenueRunConflict(
                    f"This run is {status}, not a draft. A finalized month is "
                    "history; recognize the difference in the next open month."
                )

            row = await conn.fetchrow(
                """
                UPDATE revenue_entries
                   SET recognized_amount = $3,
                       override_reason   = $4,
                       overridden_by     = $5,
                       overridden_at     = now()
                 WHERE id = $1 AND revenue_run_id = $2
                RETURNING id, harvest_project_id, harvest_project_name,
                          recognized_amount, computed_amount, override_reason
                """,
                entry_id, run_id, recognized_amount, override_reason.strip(), actor,
            )
            if row is None:
                raise RevenueRunError("Entry not found on this run.")

            await audit.write_audit_event(
                conn,
                events.REVENUE_ENTRY_OVERRIDDEN,
                actor=actor,
                payload={
                    "revenue_run_id": str(run_id),
                    "revenue_entry_id": str(entry_id),
                    "harvest_project_id": row["harvest_project_id"],
                    "harvest_project_name": row["harvest_project_name"],
                    # Both figures. "What did it say and what did we book" is
                    # one question, and it should be answerable from one row.
                    "computed_amount": str(row["computed_amount"]),
                    "recognized_amount": str(row["recognized_amount"]),
                    "reason": row["override_reason"],
                },
            )
    return dict(row)


async def finalize_run(
    pool: asyncpg.Pool, run_id: UUID, *, actor: str = "system"
) -> dict[str, Any]:
    """Make a draft the ledger.

    Refuses while any retainer still sits at zero with no override. A retainer
    computes to zero by design — the amount is a judgement call — so a zero
    there means "nobody has decided yet", not "nothing was earned". Letting it
    through would silently under-recognize a month, and nothing downstream would
    ever flag it.

    Every other zero is allowed through: a project with no activity legitimately
    recognizes nothing.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            status = await conn.fetchval(
                "SELECT status FROM revenue_runs WHERE id = $1 FOR UPDATE", run_id
            )
            if status is None:
                raise RevenueRunError("Revenue run not found.")
            if status != "draft":
                raise RevenueRunConflict(f"This run is already {status}.")

            undecided = await conn.fetch(
                """
                SELECT harvest_project_name
                FROM revenue_entries
                WHERE revenue_run_id = $1
                  AND revenue_type = 'retainer'
                  AND recognized_amount = 0
                  AND overridden_at IS NULL
                ORDER BY harvest_project_name
                """,
                run_id,
            )
            if undecided:
                names = ", ".join(r["harvest_project_name"] for r in undecided)
                raise RevenueRunConflict(
                    f"{len(undecided)} retainer(s) still need an amount: {names}. "
                    "Retainers are not computed — enter what was earned, or "
                    "override to zero with a reason if nothing was."
                )

            row = await conn.fetchrow(
                """
                UPDATE revenue_runs
                   SET status = 'recognized', finalized_at = now(), finalized_by = $2
                 WHERE id = $1
                RETURNING period_month
                """,
                run_id, actor,
            )
            booked = await conn.fetchval(
                "SELECT coalesce(sum(recognized_amount), 0) FROM revenue_entries "
                "WHERE revenue_run_id = $1",
                run_id,
            )
            await audit.write_audit_event(
                conn,
                events.REVENUE_RUN_FINALIZED,
                actor=actor,
                payload={
                    "revenue_run_id": str(run_id),
                    "period_month": row["period_month"].isoformat(),
                    # What was booked, which differs from what was computed if
                    # anything was overridden.
                    "total_recognized": str(booked),
                },
            )

    return await revenue_ledger.get_run(pool, run_id)


async def abandon_run(
    pool: asyncpg.Pool, run_id: UUID, *, actor: str = "system"
) -> dict[str, Any]:
    """Discard a draft, freeing its month to be planned again.

    The row and its entries are kept rather than deleted — what was proposed and
    thrown away is worth being able to look at — and the partial unique index
    excludes abandoned runs precisely so this works.

    A finalized run cannot be abandoned. That would silently remove a month from
    every historical total, which is a restatement, not a correction.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            status = await conn.fetchval(
                "SELECT status FROM revenue_runs WHERE id = $1 FOR UPDATE", run_id
            )
            if status is None:
                raise RevenueRunError("Revenue run not found.")
            if status == "recognized":
                raise RevenueRunConflict(
                    "A finalized month cannot be abandoned — that would remove "
                    "it from every total. Correct it in the next open month "
                    "instead, which the running sum does automatically."
                )
            if status == "abandoned":
                raise RevenueRunConflict("This run is already abandoned.")

            row = await conn.fetchrow(
                """
                UPDATE revenue_runs
                   SET status = 'abandoned', abandoned_at = now(), abandoned_by = $2
                 WHERE id = $1
                RETURNING period_month
                """,
                run_id, actor,
            )
            await audit.write_audit_event(
                conn,
                events.REVENUE_RUN_ABANDONED,
                actor=actor,
                payload={
                    "revenue_run_id": str(run_id),
                    "period_month": row["period_month"].isoformat(),
                },
            )

    return await revenue_ledger.get_run(pool, run_id)
