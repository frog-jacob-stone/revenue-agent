from typing import Optional
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException

from app.db import get_pool
from app.models.actions import ActionApprove, ActionReject, ActionResponse
from app.services import approval

router = APIRouter(prefix="/actions", tags=["actions"])


async def _db() -> asyncpg.Pool:
    return await get_pool()


def _row_to_action(row: asyncpg.Record) -> ActionResponse:
    return ActionResponse.model_validate(dict(row))


@router.get("", response_model=list[ActionResponse])
async def list_actions(
    status: str = "proposed",
    pool: asyncpg.Pool = Depends(_db),
):
    if status == "all":
        rows = await pool.fetch("SELECT * FROM actions ORDER BY created_at DESC")
    else:
        rows = await pool.fetch(
            "SELECT * FROM actions WHERE status::text = $1 ORDER BY created_at DESC",
            status,
        )
    return [_row_to_action(r) for r in rows]


@router.get("/{action_id}", response_model=ActionResponse)
async def get_action(action_id: UUID, pool: asyncpg.Pool = Depends(_db)):
    row = await pool.fetchrow("SELECT * FROM actions WHERE id = $1", action_id)
    if not row:
        raise HTTPException(status_code=404, detail="Action not found")
    return _row_to_action(row)


@router.post("/{action_id}/approve", response_model=ActionResponse)
async def approve_action(
    action_id: UUID,
    body: Optional[ActionApprove] = None,
    pool: asyncpg.Pool = Depends(_db),
):
    approved_by = body.approved_by if body else "system"
    executed_payload = body.executed_payload if body else None
    updated = await approval.approve_action(pool, action_id, approved_by, executed_payload)
    return ActionResponse.model_validate(updated)


@router.post("/{action_id}/reject", response_model=ActionResponse)
async def reject_action(
    action_id: UUID,
    body: ActionReject,
    pool: asyncpg.Pool = Depends(_db),
):
    updated = await approval.reject_action(pool, action_id, body.rejection_reason)
    return ActionResponse.model_validate(updated)
