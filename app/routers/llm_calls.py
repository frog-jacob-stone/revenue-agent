"""LLM call inspection API.

Read-only endpoints over the `llm_calls` table for the LangSmith-like UI at
`/llm-calls`. List returns summary fields; detail returns full request and
response JSONB; summary returns aggregates for dashboard cards.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.db import get_pool

router = APIRouter(prefix="/llm-calls", tags=["llm-calls"])


async def _db() -> asyncpg.Pool:
    return await get_pool()


class LlmCallSummary(BaseModel):
    id: int
    started_at: datetime
    latency_ms: int
    model: str
    agent_slug: str | None
    status: str
    streamed: bool
    purpose: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None


class LlmCallDetail(LlmCallSummary):
    ended_at: datetime
    provider: str
    workflow_id: UUID | None
    thread_id: UUID | None
    error: str | None
    request: dict[str, Any] | list[Any] | None
    response: dict[str, Any] | list[Any] | None


class ModelAgg(BaseModel):
    model: str
    calls: int
    tokens: int


class AgentAgg(BaseModel):
    agent_slug: str | None
    calls: int
    tokens: int


class LlmCallsSummaryResponse(BaseModel):
    total_calls: int
    total_prompt_tokens: int
    total_completion_tokens: int
    total_tokens: int
    avg_latency_ms: float
    error_rate: float
    by_model: list[ModelAgg]
    by_agent: list[AgentAgg]


def _parse_jsonb(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        import json
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


@router.get("", response_model=list[LlmCallSummary])
async def list_llm_calls(
    agent_slug: str | None = None,
    workflow_id: UUID | None = None,
    model: str | None = None,
    status: Literal["ok", "error"] | None = None,
    from_: datetime | None = Query(default=None, alias="from"),
    to: datetime | None = None,
    limit: int = Query(default=100, le=500),
    cursor: int | None = Query(default=None, description="Return ids strictly less than this value"),
    pool: asyncpg.Pool = Depends(_db),
):
    conditions: list[str] = []
    params: list[Any] = []

    if agent_slug:
        params.append(agent_slug)
        conditions.append(f"agent_slug = ${len(params)}")
    if workflow_id:
        params.append(workflow_id)
        conditions.append(f"workflow_id = ${len(params)}")
    if model:
        params.append(model)
        conditions.append(f"model = ${len(params)}")
    if status:
        params.append(status)
        conditions.append(f"status = ${len(params)}")
    if from_:
        params.append(from_)
        conditions.append(f"started_at >= ${len(params)}")
    if to:
        params.append(to)
        conditions.append(f"started_at <= ${len(params)}")
    if cursor:
        params.append(cursor)
        conditions.append(f"id < ${len(params)}")

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    params.append(limit)
    limit_idx = len(params)

    rows = await pool.fetch(
        f"""
        SELECT id, started_at, latency_ms, model, agent_slug, status, streamed,
               purpose, prompt_tokens, completion_tokens, total_tokens
        FROM llm_calls
        {where}
        ORDER BY id DESC
        LIMIT ${limit_idx}
        """,
        *params,
    )
    return [LlmCallSummary(**dict(r)) for r in rows]


@router.get("/summary", response_model=LlmCallsSummaryResponse)
async def llm_calls_summary(
    from_: datetime | None = Query(default=None, alias="from"),
    to: datetime | None = None,
    pool: asyncpg.Pool = Depends(_db),
):
    conditions: list[str] = []
    params: list[Any] = []
    if from_:
        params.append(from_)
        conditions.append(f"started_at >= ${len(params)}")
    if to:
        params.append(to)
        conditions.append(f"started_at <= ${len(params)}")
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    totals_row = await pool.fetchrow(
        f"""
        SELECT
            COUNT(*) AS total_calls,
            COALESCE(SUM(prompt_tokens), 0) AS total_prompt_tokens,
            COALESCE(SUM(completion_tokens), 0) AS total_completion_tokens,
            COALESCE(SUM(total_tokens), 0) AS total_tokens,
            COALESCE(AVG(latency_ms), 0)::float AS avg_latency_ms,
            COALESCE(
                SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END)::float
                    / NULLIF(COUNT(*), 0),
                0
            ) AS error_rate
        FROM llm_calls
        {where}
        """,
        *params,
    )

    model_rows = await pool.fetch(
        f"""
        SELECT model,
               COUNT(*)::int AS calls,
               COALESCE(SUM(total_tokens), 0)::int AS tokens
        FROM llm_calls
        {where}
        GROUP BY model
        ORDER BY tokens DESC, calls DESC
        """,
        *params,
    )

    agent_rows = await pool.fetch(
        f"""
        SELECT agent_slug,
               COUNT(*)::int AS calls,
               COALESCE(SUM(total_tokens), 0)::int AS tokens
        FROM llm_calls
        {where}
        GROUP BY agent_slug
        ORDER BY tokens DESC, calls DESC
        """,
        *params,
    )

    return LlmCallsSummaryResponse(
        total_calls=totals_row["total_calls"],
        total_prompt_tokens=totals_row["total_prompt_tokens"],
        total_completion_tokens=totals_row["total_completion_tokens"],
        total_tokens=totals_row["total_tokens"],
        avg_latency_ms=float(totals_row["avg_latency_ms"]),
        error_rate=float(totals_row["error_rate"]),
        by_model=[ModelAgg(**dict(r)) for r in model_rows],
        by_agent=[AgentAgg(**dict(r)) for r in agent_rows],
    )


@router.get("/{call_id}", response_model=LlmCallDetail)
async def get_llm_call(call_id: int, pool: asyncpg.Pool = Depends(_db)):
    row = await pool.fetchrow(
        """
        SELECT id, started_at, ended_at, latency_ms, provider, model,
               agent_slug, workflow_id, thread_id, purpose, status, error,
               streamed, request, response,
               prompt_tokens, completion_tokens, total_tokens
        FROM llm_calls
        WHERE id = $1
        """,
        call_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail="llm_call not found")
    data = dict(row)
    data["request"] = _parse_jsonb(data["request"])
    data["response"] = _parse_jsonb(data["response"])
    return LlmCallDetail(**data)
