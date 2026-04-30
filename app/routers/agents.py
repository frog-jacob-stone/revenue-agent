from typing import Any

import asyncpg
from fastapi import APIRouter, Depends, HTTPException

from app.agents.base import ConversationalAgent
from app.agents.registry import AGENTS_BY_SLUG
from app.db import get_pool
from app.models.agents import Agent
from app.tools import TOOLS

router = APIRouter(prefix="/agents", tags=["agents"])


async def _db() -> asyncpg.Pool:
    return await get_pool()


def _enrich(d: dict) -> dict:
    cls = AGENTS_BY_SLUG.get(d["slug"])
    if cls:
        d["name"] = cls.name
        d["description"] = cls.description
        d["requires_approval"] = cls.requires_approval
        d["is_conversational"] = issubclass(cls, ConversationalAgent)
    return d


@router.get("", response_model=list[Agent])
async def list_agents(pool: asyncpg.Pool = Depends(_db)):
    rows = await pool.fetch("SELECT * FROM agents ORDER BY slug")
    return [Agent.model_validate(_enrich(dict(r))) for r in rows]


@router.get("/{slug}", response_model=Agent)
async def get_agent(slug: str, pool: asyncpg.Pool = Depends(_db)):
    row = await pool.fetchrow("SELECT * FROM agents WHERE slug = $1", slug)
    if not row:
        raise HTTPException(status_code=404, detail=f"Agent '{slug}' not found")
    return Agent.model_validate(_enrich(dict(row)))


@router.patch("/{slug}/active", response_model=Agent)
async def set_agent_active(slug: str, is_active: bool, pool: asyncpg.Pool = Depends(_db)):
    row = await pool.fetchrow(
        "UPDATE agents SET is_active = $1 WHERE slug = $2 RETURNING *",
        is_active,
        slug,
    )
    if not row:
        raise HTTPException(status_code=404, detail=f"Agent '{slug}' not found")
    return Agent.model_validate(_enrich(dict(row)))


@router.get("/{slug}/tools")
async def get_agent_tools(slug: str) -> list[dict[str, Any]]:
    cls = AGENTS_BY_SLUG.get(slug)
    if not cls:
        raise HTTPException(status_code=404, detail=f"Agent '{slug}' not found")
    return [
        {"name": TOOLS[n].name, "description": TOOLS[n].description, "input_schema": TOOLS[n].input_schema}
        for n in cls.allowed_tools
        if n in TOOLS
    ]
