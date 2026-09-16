"""Revenue recognition router.

Reads plus the operator-initiated run. Registered with router-wide auth in
`app/main.py` like every other router (Unbreakable Rule #2).

**No approval rows here, by ADR-0004.** The writing endpoints qualify on all
three counts: the operator reads the exact entries via `GET /revenue/runs/{id}`
before finalizing, none of these are reachable from any agent (enforced by
`tests/test_no_agent_approval_tools.py`), and every transition writes
`audit_log`. Safer than the billing precedent it is modelled on — finalizing a
run writes nothing outside Postgres, so there is no vendor call to go wrong.

`GET /revenue/runs/{id}` deliberately serves drafts: it is the payload being
authorized, and filtering it to finalized runs would make the review screen
permanently empty.
"""
from __future__ import annotations

from datetime import date
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth import AuthUser, get_current_user
from app.config import settings
from app.db import get_pool
from app.models.revenue import (
    LedgerEntry,
    OverrideEntryRequest,
    PlanRunRequest,
    RevenueClient,
    RevenueConfigRequest,
    RevenueMonth,
    RevenueProjectConfig,
    RevenueRunDetail,
    RevenueRunSummary,
)
from app.services import revenue_config, revenue_ledger, revenue_run

router = APIRouter(prefix="/revenue", tags=["revenue"])


async def _db() -> asyncpg.Pool:
    return await get_pool()


def _actor(user: AuthUser) -> str:
    return user.email or str(user.id)


@router.get("/entries", response_model=list[LedgerEntry])
async def list_entries(
    date_from: date | None = Query(
        None, description="First period to include, as any date in that month."
    ),
    date_to: date | None = Query(
        None, description="Last period to include, as any date in that month."
    ),
    harvest_project_id: int | None = Query(
        None, description="Restrict to one project."
    ),
    client_ids: list[int] | None = Query(
        None,
        description=(
            "Restrict to these Harvest clients. Repeat the parameter to pass "
            "several. Omit for all."
        ),
    ),
    exclude_empty_projects: bool = Query(
        False,
        description=(
            "Drop projects that recognized nothing across the whole window. "
            "Project-level: a project that qualifies keeps every entry, zeros "
            "included."
        ),
    ),
    pool: asyncpg.Pool = Depends(_db),
):
    """Ledger rows, newest month first, each carrying its cumulative total.

    Only entries of finalized runs — a draft is a proposal, not history.
    Unpaginated: the caller renders a month-by-project grid and needs the whole
    range at once.

    The bounds are inclusive and month-granular. A caller passing a mid-month
    date gets that whole month, since periods are first-of-month and the
    comparison is against that.
    """
    rows = await revenue_ledger.list_entries(
        pool,
        date_from=_first_of_month(date_from),
        date_to=_first_of_month(date_to),
        harvest_project_id=harvest_project_id,
        client_ids=client_ids,
        exclude_empty_projects=exclude_empty_projects,
    )
    return [LedgerEntry.model_validate(r) for r in rows]


@router.get("/clients", response_model=list[RevenueClient])
async def list_clients(
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    pool: asyncpg.Pool = Depends(_db),
):
    """Clients with revenue in the window — the options for the client filter.

    Deliberately not derived from `/revenue/entries`: a faceted filter's
    options must come from the unfiltered set, or each selection would remove
    the others from the list and leave no way back.
    """
    rows = await revenue_ledger.list_clients(
        pool,
        date_from=_first_of_month(date_from),
        date_to=_first_of_month(date_to),
    )
    return [RevenueClient.model_validate(r) for r in rows]


@router.get("/summary", response_model=list[RevenueMonth])
async def revenue_summary(
    date_from: date | None = Query(
        None, description="First period to include, as any date in that month."
    ),
    date_to: date | None = Query(
        None, description="Last period to include, as any date in that month."
    ),
    client_ids: list[int] | None = Query(
        None, description="Restrict to these Harvest clients. Omit for all."
    ),
    pool: asyncpg.Pool = Depends(_db),
):
    """Recognized revenue and hours per month, oldest first — the trend.

    Oldest first because it is read left to right as a chart, unlike the entry
    and run lists, which are lookups and read newest first.

    Bounds match `/revenue/entries`: inclusive and month-granular. Unbounded
    returns every month the ledger covers. `client_ids` matches too, so a
    caller can narrow the chart and the grid beside it to the same book.
    """
    rows = await revenue_ledger.monthly_totals(
        pool,
        date_from=_first_of_month(date_from),
        date_to=_first_of_month(date_to),
        client_ids=client_ids,
    )
    return [RevenueMonth.model_validate(r) for r in rows]


@router.get("/runs", response_model=list[RevenueRunSummary])
async def list_runs(
    limit: int = Query(24, ge=1, le=200),
    pool: asyncpg.Pool = Depends(_db),
):
    """Run history, newest month first. Every status, including drafts."""
    rows = await revenue_ledger.list_runs(pool, limit=limit)
    return [RevenueRunSummary.model_validate(r) for r in rows]


@router.get("/runs/{run_id}", response_model=RevenueRunDetail)
async def get_run(run_id: UUID, pool: asyncpg.Pool = Depends(_db)):
    """One run and its entries, whatever its status."""
    run = await revenue_ledger.get_run(pool, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Revenue run not found.")
    return RevenueRunDetail.model_validate(run)


# ── Per-project configuration ───────────────────────────────────────────────
#
# Human-only (ADR-0004). Never in any agent's `allowed_tools`.


@router.get("/config", response_model=list[RevenueProjectConfig])
async def list_config(pool: asyncpg.Pool = Depends(_db)):
    """Every in-scope project, with its configuration if it has one.

    Unconfigured first: they are the work. A null `revenue_type` is the marker.
    """
    rows = await revenue_config.list_projects(pool)
    return [RevenueProjectConfig.model_validate(r) for r in rows]


@router.patch("/config/{harvest_project_id}", response_model=RevenueProjectConfig)
async def set_config(
    harvest_project_id: int,
    body: RevenueConfigRequest,
    pool: asyncpg.Pool = Depends(_db),
    user: AuthUser = Depends(get_current_user),
):
    """Configure a project. Idempotent: creates the configuration or replaces
    it, because the caller is a form being saved and "has this been configured
    before" is not a distinction the person filling it in is making.

    PATCH rather than PUT despite being a full replace. Every other update in
    this API is a PATCH, `PUT` is in none of them, and it is absent from the
    CORS `allow_methods` in `app/main.py` — adding it globally for one endpoint
    buys a verb and costs a wider preflight surface.
    `tests/test_cors_allows_every_route_method.py` catches this.
    """
    try:
        row = await revenue_config.set_config(
            pool,
            harvest_project_id,
            revenue_type=body.revenue_type,
            contracted_fees=body.contracted_fees,
            notes=body.notes,
            actor=_actor(user),
        )
    except revenue_config.RevenueConfigError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return RevenueProjectConfig.model_validate(row)


@router.delete("/config/{harvest_project_id}", status_code=204)
async def remove_config(
    harvest_project_id: int,
    pool: asyncpg.Pool = Depends(_db),
    user: AuthUser = Depends(get_current_user),
):
    """Unconfigure a project. Past entries are untouched — a month that was
    recognized stays recognized; this only stops future runs including it."""
    removed = await revenue_config.remove_config(
        pool, harvest_project_id, actor=_actor(user)
    )
    if not removed:
        raise HTTPException(status_code=404, detail="Project is not configured.")


# ── The run ─────────────────────────────────────────────────────────────────


@router.post("/runs", response_model=RevenueRunDetail, status_code=201)
async def plan_run(
    body: PlanRunRequest,
    pool: asyncpg.Pool = Depends(_db),
    user: AuthUser = Depends(get_current_user),
):
    """Draft a month. Read-only against Harvest and Forecast; writes only our
    own tables, and nothing counts until it is finalized.

    409 with the project list when configuration is missing — the UI links
    straight to the setup screen rather than making the operator hunt.
    """
    try:
        return RevenueRunDetail.model_validate(
            await revenue_run.plan_run(
                pool, settings, period_month=body.period_month, actor=_actor(user)
            )
        )
    except revenue_run.RevenueConfigMissing as exc:
        raise HTTPException(
            status_code=409,
            detail={"message": str(exc), "unconfigured_projects": exc.projects},
        ) from exc
    except revenue_run.RevenueRunConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except revenue_run.RevenueRunError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.patch("/runs/{run_id}/entries/{entry_id}")
async def override_entry(
    run_id: UUID,
    entry_id: UUID,
    body: OverrideEntryRequest,
    pool: asyncpg.Pool = Depends(_db),
    user: AuthUser = Depends(get_current_user),
):
    """Replace a computed figure with a human's. Draft runs only.

    `computed_amount` is never touched, so the row always carries both what the
    system said and what was booked.
    """
    try:
        return await revenue_run.override_entry(
            pool,
            run_id,
            entry_id,
            recognized_amount=body.recognized_amount,
            override_reason=body.override_reason,
            actor=_actor(user),
        )
    except revenue_run.RevenueRunConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except revenue_run.RevenueRunError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/runs/{run_id}/finalize", response_model=RevenueRunDetail)
async def finalize_run(
    run_id: UUID,
    pool: asyncpg.Pool = Depends(_db),
    user: AuthUser = Depends(get_current_user),
):
    """Make a draft the ledger.

    The authorizing click. The operator has just read this exact run via
    `GET /revenue/runs/{id}`, which is what lets this skip an approval row
    (ADR-0004). Nothing outside Postgres is written.
    """
    try:
        return RevenueRunDetail.model_validate(
            await revenue_run.finalize_run(pool, run_id, actor=_actor(user))
        )
    except revenue_run.RevenueRunConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except revenue_run.RevenueRunError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/runs/{run_id}/abandon", response_model=RevenueRunDetail)
async def abandon_run(
    run_id: UUID,
    pool: asyncpg.Pool = Depends(_db),
    user: AuthUser = Depends(get_current_user),
):
    """Discard a draft, freeing its month to be planned again. The row and its
    entries are kept — what was proposed and thrown away is worth seeing."""
    try:
        return RevenueRunDetail.model_validate(
            await revenue_run.abandon_run(pool, run_id, actor=_actor(user))
        )
    except revenue_run.RevenueRunConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except revenue_run.RevenueRunError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _first_of_month(value: date | None) -> date | None:
    """Periods are first-of-month by CHECK, so a bound has to be too.

    Done here rather than in the service because it is a courtesy to HTTP
    callers — "some date in March" meaning March — and the service's contract
    is the column's: a period is a first-of-month date.
    """
    return value.replace(day=1) if value is not None else None
