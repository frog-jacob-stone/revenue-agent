import asyncpg
from fastapi import APIRouter, Depends, HTTPException

from app.db import get_pool
from app.models.agents import Agent

router = APIRouter(prefix="/agents", tags=["agents"])


async def _db() -> asyncpg.Pool:
    return await get_pool()


@router.get("", response_model=list[Agent])
async def list_agents(pool: asyncpg.Pool = Depends(_db)):
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
