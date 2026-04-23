from typing import Any

import asyncpg
from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field

from app.db import get_pool
from app.models.agents import Agent
from app.models.common import ORMBase
from app.services import agent_runner

router = APIRouter(prefix="/agents", tags=["agents"])


async def _db() -> asyncpg.Pool:
    return await get_pool()


class TriggerRequest(ORMBase):
    initiated_by: str = "system"
    context: dict[str, Any] = Field(default_factory=dict)


@router.get("", response_model=list[Agent])
async def list_agents(pool: asyncpg.Pool = Depends(_db)):
    print("Fetching agents from database...")
    rows = await pool.fetch("SELECT * FROM agents ORDER BY name")
    return [Agent.model_validate(dict(r)) for r in rows]


@router.get("/{slug}", response_model=Agent)
async def get_agent(slug: str, pool: asyncpg.Pool = Depends(_db)):
    row = await pool.fetchrow("SELECT * FROM agents WHERE slug = $1", slug)
    if not row:
        raise HTTPException(status_code=404, detail=f"Agent '{slug}' not found")
    return Agent.model_validate(dict(row))


@router.patch("/{slug}/active", response_model=Agent)
async def set_agent_active(slug: str, is_active: bool, pool: asyncpg.Pool = Depends(_db)):
    row = await pool.fetchrow(
        "UPDATE agents SET is_active = $1 WHERE slug = $2 RETURNING *",
        is_active,
        slug,
    )
    if not row:
        raise HTTPException(status_code=404, detail=f"Agent '{slug}' not found")
    return Agent.model_validate(dict(row))


@router.post("/{slug}/trigger", status_code=202)
async def trigger_agent(slug: str, body: TriggerRequest):
    try:
        result = await agent_runner.run_agent(
            slug=slug,
            initiated_by=body.initiated_by,
            context=body.context,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except NotImplementedError as exc:
        raise HTTPException(status_code=501, detail=str(exc))
    return result
