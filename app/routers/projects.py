"""Projects router — delivery view of the engagement roster.

Read-only. Nothing here writes, to Harvest or to our own store, so there is no
approval chain and no audit event; the data is a projection of the Harvest
snapshot cache. Registered with router-wide auth in `app/main.py` like every
other router (Unbreakable Rule #2).
"""
from __future__ import annotations

import asyncpg
from fastapi import APIRouter, Depends, Query

from app.db import get_pool
from app.models.projects import ProjectSummary
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
