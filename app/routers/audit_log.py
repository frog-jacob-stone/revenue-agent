from datetime import date, datetime
from typing import Any, Literal

import asyncpg
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.db import get_pool

router = APIRouter(prefix="/audit-log", tags=["audit-log"])


async def _db() -> asyncpg.Pool:
    return await get_pool()


class AuditLogEntry(BaseModel):
    """
    One `audit_log` row, flattened.

    `action_type` and `target` used to come from the v1 `actions` table, which
    migration 0014 dropped. Nothing records them now, so they are gone rather
    than returned as permanent nulls — `event_type` is the type, and `payload`
    is the detail.
    """

    id: int
    timestamp: datetime
    agent_slug: str | None
    event_type: str
    outcome: str
    reason: str | None
    payload: dict[str, Any]


def _derive_outcome(event_type: str) -> str:
    """
    Outcome comes from the event name alone.

    The v1 `actions` table used to carry a status column that drove this;
    migration 0014 dropped that table. `audit_log.event_type` is now the only
    signal, so the suffix is the whole story.
    """
    if event_type.endswith((".failed", ".error")):
        return "failed"
    if event_type.endswith(".rejected"):
        return "rejected"
    if event_type.endswith((".approved", ".completed", ".executed", ".succeeded")):
        return "success"
    return "pending"


@router.get("", response_model=list[AuditLogEntry])
async def list_audit_log(
    agent_slug: str | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
    outcome: Literal["success", "failed", "pending", "rejected"] | None = None,
    limit: int = Query(default=100, le=500),
    offset: int = 0,
    pool: asyncpg.Pool = Depends(_db),
):
    conditions: list[str] = []
    params: list = []

    if agent_slug:
        params.append(agent_slug)
        conditions.append(f"ag.slug = ${len(params)}")

    if from_date:
        params.append(from_date)
        conditions.append(f"al.occurred_at::date >= ${len(params)}")

    if to_date:
        params.append(to_date)
        conditions.append(f"al.occurred_at::date <= ${len(params)}")

    if outcome:
        params.append(outcome)
        outcome_idx = len(params)
        conditions.append(
            f"""CASE
                WHEN al.event_type LIKE '%.failed'
                  OR al.event_type LIKE '%.error' THEN 'failed'
                WHEN al.event_type LIKE '%.rejected' THEN 'rejected'
                WHEN al.event_type LIKE '%.approved'
                  OR al.event_type LIKE '%.completed'
                  OR al.event_type LIKE '%.executed'
                  OR al.event_type LIKE '%.succeeded' THEN 'success'
                ELSE 'pending'
            END = ${outcome_idx}"""
        )

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    params.append(limit)
    limit_idx = len(params)
    params.append(offset)
    offset_idx = len(params)

    rows = await pool.fetch(
        f"""
        SELECT
            al.id,
            al.occurred_at,
            al.event_type,
            al.actor,
            al.payload,
            ag.slug AS agent_slug
        FROM audit_log al
        LEFT JOIN agents ag ON al.agent_id = ag.id
        {where}
        ORDER BY al.occurred_at DESC
        LIMIT ${limit_idx} OFFSET ${offset_idx}
        """,
        *params,
    )

    return [
        AuditLogEntry(
            id=row["id"],
            timestamp=row["occurred_at"],
            agent_slug=row["agent_slug"],
            event_type=row["event_type"],
            outcome=_derive_outcome(row["event_type"]),
            reason=row["actor"],
            payload=dict(row["payload"]) if row["payload"] else {},
        )
        for row in rows
    ]
