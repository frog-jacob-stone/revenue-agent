"""Projects router — delivery view of the engagement roster.

Read-only. Nothing here writes, to Harvest or to our own store, so there is no
approval chain and no audit event; the data is a projection of the Harvest
snapshot cache. Registered with router-wide auth in `app/main.py` like every
other router (Unbreakable Rule #2).
"""
from __future__ import annotations

import asyncpg
from fastapi import APIRouter, Depends, Query

from app.auth import AuthUser, get_current_user
from app.config import settings
from app.db import get_pool
from app.models.projects import ProjectRefreshResponse, ProjectSummary
from app.services import projects

router = APIRouter(prefix="/projects", tags=["projects"])


async def _db() -> asyncpg.Pool:
    return await get_pool()


@router.get("", response_model=list[ProjectSummary])
async def list_projects(
    archived: bool = Query(
        False,
        description=(
            "Return closed engagements instead of running ones. The two lists "
            "are disjoint — this swaps the result set, it does not extend it."
        ),
    ),
    pool: asyncpg.Pool = Depends(_db),
):
    """Billable Harvest projects, from the last snapshot refresh.

    Unpaginated: the roster is tens of projects, and the caller renders all of
    them in one table.
    """
    rows = await projects.list_projects(pool, archived=archived)
    return [ProjectSummary.model_validate(r) for r in rows]


@router.post("/refresh", response_model=ProjectRefreshResponse)
async def refresh_projects(
    pool: asyncpg.Pool = Depends(_db),
    user: AuthUser = Depends(get_current_user),
):
    """Re-read both sources behind this tab: Harvest, then Forecast.

    Read-only against both vendors, operator-initiated, and audited once per
    source. Nothing schedules this — the caches are only as fresh as the last
    time someone asked.

    A few seconds: the Harvest snapshot costs one request per billable active
    project for task assignments, which the rate limiter pipelines.
    """
    return ProjectRefreshResponse.model_validate(
        await projects.refresh_sources(
            pool, settings, actor=user.email or str(user.id)
        )
    )
