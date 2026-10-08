"""The monthly revenue recognition run.

Operator-initiated throughout (ADR-0004): a human plans a month, reads the
entries, adjusts what needs judgement, and finalizes. No agent can reach any of
this, and `tests/test_no_agent_approval_tools.py` keeps it that way.

Safer than the billing run it is modelled on (`POST /billing/draws/{id}/invoice`)
in one important way: **finalizing writes nothing outside Postgres.** There is
no vendor call at the end, so no in-flight state, no unknown outcome, and
nothing to reconcile against Harvest afterwards. A month that goes wrong is
abandoned and planned again.

    plan_run       refresh the Harvest snapshot, then draft one entry per
                   in-scope project. Reads Harvest and Forecast; writes only
                   our own tables.
    override_entry a human replaces a computed figure. Draft only.
    finalize_run   the entries become the ledger.
    abandon_run    discard, freeing the month to be planned again.
    delete_run     remove an abandoned run for good. Abandoned only.

THE SUBTRACTION

Every *computed* recognition method produces a cumulative figure — percent
complete against the whole contract, or Harvest's invoiced-to-date. The period
amount is that minus everything already recognized:

    computed_amount = cumulative_target - prior_recognized(project, month)

Which is why correcting a closed month is not a restatement: the correction
changes the prior sum, so the next open month absorbs it automatically.

A retainer is not computed — `calc_revenue` says so by returning
`needs_decision` — and the subtraction does not apply to it. Its placeholder
zero is written through as the period amount. Differencing it instead reads
"nothing computed" as "nothing earned to date" and books the difference as a
clawback of the project's entire history, which is what the live September
draft did to two retainers before this was fixed.
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
from app.services.billing import harvest_snapshot
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


async def _recognizable_projects(conn: Any) -> list[dict[str, Any]]:
    """Billable, not an excluded client's, configured — **archived included**.

    Joined to config rather than checked afterwards, so the planner reads one
    row per project with everything it needs.

    Deliberately not `IN_SCOPE_SQL`: that carries `p.is_active`, which is read
    now and would decide a month that has already closed. A fixed-fee project
    reaches 100% complete exactly when its Forecast bookings end — which is the
    same month somebody archives it — so filtering on `is_active` removed
    projects from the run precisely when their final true-up fell due. D&A
    SOW #7 was archived at 99.80% recognized and stranded the last $110.

    Including an archived project here does not mean it gets a row. The caller
    writes one only if the project is active, or it has something left to say —
    see `plan_run`. `is_active` comes back so it can decide, and so the review
    screen can mark the row.
    """
    rows = await conn.fetch(
        f"""
        SELECT p.harvest_id AS harvest_project_id,
               p.name       AS harvest_project_name,
               p.is_active,
               c.revenue_type, c.contracted_fees
        FROM harvest_projects p
        JOIN revenue_project_config c ON c.harvest_project_id = p.harvest_id
        WHERE {revenue_config.RECOGNIZABLE_SQL}
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
    refresh: bool = True,
) -> dict[str, Any]:
    """Draft a month. Read-only against Harvest and Forecast.

    The whole plan is one transaction: a half-written run with entries for some
    projects and not others would look finalizable and be wrong.

    Raises `RevenueConfigMissing` if any in-scope project is unconfigured, and
    `RevenueRunConflict` if a live run already owns the month — the latter from
    the partial unique index rather than a read-then-write check, so two people
    planning at once cannot both succeed.

    `refresh=False` plans against the snapshot as it stands. For tests, and for
    replanning against a known cache state — not for a real month.
    """
    period_month = period_month.replace(day=1)

    # The roster first, before anything is read or decided.
    #
    # Scope is "billable, active, not an excluded client's" — evaluated against
    # `harvest_projects`, which is a cache that nothing but an operator pressing
    # a button used to refresh. Plan a month against a three-week-old snapshot
    # and every project created since is not merely unconfigured but *absent*:
    # the config gate below cannot flag a row that does not exist, so the run
    # comes back clean and short. That is what happened to the live September
    # close, which silently omitted three real projects.
    #
    # Billing's planner has refreshed since 0024 (`billing/planner.py:443`).
    # This one never did, and the asymmetry had no reason behind it.
    #
    # Failure is not caught. Nothing has been written yet, so there is no run to
    # lose, and planning a month against a roster you *know* you failed to
    # refresh is the bug this exists to prevent — better a visible error than a
    # quietly incomplete month.
    if refresh:
        await harvest_snapshot.refresh_snapshot(pool, cfg, actor=actor)

    # Outside the transaction: these are slow vendor reads, and holding a
    # transaction open across them would keep a connection pinned for the
    # duration for no benefit.
    hours, scheduled, invoiced = await _gather_harvest_and_forecast(cfg, period_month)

    async with pool.acquire() as conn:
        async with conn.transaction():
            missing = await revenue_config.unconfigured_projects(conn)
            if missing:
                raise RevenueConfigMissing(missing)

            projects = await _recognizable_projects(conn)
            if not projects:
                raise RevenueRunError(
                    "No billable projects are in scope for revenue "
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
            written = 0
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

                # The subtraction only applies to a cumulative figure. A
                # retainer's is a placeholder for an amount a human types, and
                # differencing it against the ledger turns "nothing computed"
                # into "reverse everything ever recognized" — which is exactly
                # what the live September draft did to two retainers, at
                # -$19,841.25 and -$84,751.25. An undecided entry is written at
                # zero, which is also what `finalize_run` looks for.
                if result.needs_decision:
                    computed = Decimal("0.00")
                else:
                    prior = await revenue_ledger.prior_recognized(
                        conn, pid, period_month
                    )
                    computed = _dec(round(result.amount - prior, 2)) or Decimal("0.00")

                # An archived project earns a row only while it still has
                # something to say. It is here at all because archiving usually
                # means *completing*, and completion is when a fixed-fee
                # project's last true-up falls due — but once that is
                # recognized the delta is zero, and the project drops out again
                # of its own accord. No flag to set and nothing to clean up:
                # the balance going to zero is what ends it.
                #
                # Active projects are unconditional, as before. A zero there is
                # a real statement — this project earned nothing this month —
                # and the grid is read expecting a row per engagement.
                if (
                    not project["is_active"]
                    and computed == 0
                    and not hours.get(pid, 0.0)
                ):
                    continue

                total += computed
                written += 1

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

            # Every candidate was an archived project with nothing left to
            # recognize. A draft with no entries would be worse than an error:
            # it occupies the month's one live-run slot, reports a $0 total that
            # reads as a finding rather than an absence, and can be finalized
            # into a month that says nothing happened. Raised inside the
            # transaction, so the run row rolls back with it.
            if written == 0:
                raise RevenueRunError(
                    f"Nothing to recognize for {period_month:%B %Y}. Every "
                    "configured project is archived in Harvest with no "
                    "remaining balance and no hours logged in the period."
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


async def delete_run(
    pool: asyncpg.Pool, run_id: UUID, *, actor: str = "system"
) -> dict[str, Any]:
    """Remove an abandoned run and its entries for good.

    **Abandoned only.** A draft is live — it owns its month's one run slot, and
    deleting it instead of abandoning it would skip the state transition that
    frees the month on the record. A recognized run is the ledger: every
    cumulative figure in the system is a sum over its entries, so deleting one
    silently restates history, which is the thing this subsystem exists to make
    impossible. Both are refused rather than cascaded.

    Abandoning keeps the row deliberately — what was proposed and thrown away is
    worth being able to look at (`abandon_run`). That reasoning holds for a
    draft somebody reconsidered; it does not hold for the runs a defect
    produced, which accumulate as noise in the one list the operator uses to
    find real months. Keeping them is a default, not an invariant, so this makes
    the default overridable by a human who can see what they are removing.

    Entries go with it via `revenue_entries.revenue_run_id ... on delete
    cascade`. They are already invisible to every reader — `recognized_only_sql`
    excludes an abandoned run — so nothing that has been reported on changes.

    The audit row is written *before* the delete and carries what the run was,
    not just its id: the id is about to stop resolving, and a line saying a
    thing you can no longer look up was deleted is not a record of anything.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            run = await conn.fetchrow(
                "SELECT period_month, status, created_at, created_by "
                "FROM revenue_runs WHERE id = $1 FOR UPDATE",
                run_id,
            )
            if run is None:
                raise RevenueRunError("Revenue run not found.")
            if run["status"] == "recognized":
                raise RevenueRunConflict(
                    "A finalized month cannot be deleted. Every cumulative "
                    "figure is a sum over its entries, so removing it would "
                    "restate history rather than correct it."
                )
            if run["status"] == "draft":
                raise RevenueRunConflict(
                    "A draft is live and owns this month. Abandon it first, "
                    "then delete it."
                )

            summary = await conn.fetchrow(
                """
                SELECT count(*)                            AS entry_count,
                       coalesce(sum(recognized_amount), 0) AS total_recognized
                FROM revenue_entries WHERE revenue_run_id = $1
                """,
                run_id,
            )

            await audit.write_audit_event(
                conn,
                events.REVENUE_RUN_DELETED,
                actor=actor,
                payload={
                    "revenue_run_id": str(run_id),
                    "period_month": run["period_month"].isoformat(),
                    "planned_at": run["created_at"].isoformat(),
                    "planned_by": run["created_by"],
                    "entry_count": summary["entry_count"],
                    "total_proposed": str(summary["total_recognized"]),
                },
            )
            await conn.execute("DELETE FROM revenue_runs WHERE id = $1", run_id)

    return {
        "id": run_id,
        "period_month": run["period_month"],
        "entry_count": summary["entry_count"],
    }
