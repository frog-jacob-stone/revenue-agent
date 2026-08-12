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
    id: int
    timestamp: datetime
    agent_slug: str | None
    event_type: str
    action_type: str | None
    target: str | None
    outcome: str
    reason: str | None
    payload: dict[str, Any]


def _derive_outcome(action_status: str | None, event_type: str) -> str:
    if action_status == "proposed":
        return "pending"
    if action_status in ("approved", "completed"):
        return "success"
    if action_status == "rejected":
        return "rejected"
    if action_status == "failed":
        return "failed"
    if event_type.endswith(".failed"):
        return "failed"
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
                WHEN ac.status = 'proposed' THEN 'pending'
                WHEN ac.status IN ('approved', 'completed') THEN 'success'
                WHEN ac.status = 'rejected' THEN 'rejected'
                WHEN ac.status = 'failed' THEN 'failed'
                WHEN ac.status IS NULL AND al.event_type LIKE '%.failed' THEN 'failed'
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
            ag.slug AS agent_slug,
            ac.action_type,
            ac.summary AS action_summary,
            ac.status AS action_status
        FROM audit_log al
        LEFT JOIN agents ag ON al.agent_id = ag.id
        LEFT JOIN actions ac ON al.action_id = ac.id
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
            action_type=row["action_type"],
            target=row["action_summary"],
            outcome=_derive_outcome(row["action_status"], row["event_type"]),
            reason=row["actor"],
            payload=dict(row["payload"]) if row["payload"] else {},
        )
        for row in rows
    ]
