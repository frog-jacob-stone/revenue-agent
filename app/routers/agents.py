from typing import Any

import asyncpg
from fastapi import APIRouter, Depends, HTTPException

from app.agents.registry import AGENTS_BY_SLUG
from app.db import get_pool
from app.models.agents import AgentRead

router = APIRouter(prefix="/agents", tags=["agents"])


async def _db() -> asyncpg.Pool:
    return await get_pool()


def _enrich(d: dict) -> dict:
    cls = AGENTS_BY_SLUG.get(d["slug"])
    if cls:
        d["name"] = cls.name
        d["description"] = cls.description
        d["requires_approval"] = cls.requires_approval
    return d


@router.get("", response_model=list[AgentRead])
async def list_agents(pool: asyncpg.Pool = Depends(_db)):
    rows = await pool.fetch("SELECT * FROM agents ORDER BY slug")
    return [AgentRead.model_validate(_enrich(dict(r))) for r in rows]


@router.get("/{slug}", response_model=AgentRead)
async def get_agent(slug: str, pool: asyncpg.Pool = Depends(_db)):
    row = await pool.fetchrow("SELECT * FROM agents WHERE slug = $1", slug)
    if not row:
        raise HTTPException(status_code=404, detail=f"Agent '{slug}' not found")
    return AgentRead.model_validate(_enrich(dict(row)))


@router.patch("/{slug}/active", response_model=AgentRead)
async def set_agent_active(slug: str, is_active: bool, pool: asyncpg.Pool = Depends(_db)):
    row = await pool.fetchrow(
        "UPDATE agents SET is_active = $1 WHERE slug = $2 RETURNING *",
        is_active,
        slug,
    )
    if not row:
        raise HTTPException(status_code=404, detail=f"Agent '{slug}' not found")
    return AgentRead.model_validate(_enrich(dict(row)))


@router.get("/{slug}/tools")
async def get_agent_tools(slug: str) -> list[dict[str, Any]]:
    cls = AGENTS_BY_SLUG.get(slug)
    if not cls:
        raise HTTPException(status_code=404, detail=f"Agent '{slug}' not found")
    return [
        {"name": t.name, "description": t.description, "input_schema": t.input_schema}
        for t in cls.allowed_tools
    ]
