"""Approvals router — v2 (LangGraph) human-in-the-loop queue.

Mirrors the surface of `app/routers/actions.py` so the inbox UI migration in
Phase 1 is a near-rename. The actions router is left untouched until Phase 5.
"""
from __future__ import annotations

from typing import Optional
from uuid import UUID

import asyncpg
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException

from app.db import get_pool
from app.models.approvals import ApprovalApprove, ApprovalReject, ApprovalResponse
from app.orchestrator_v2 import runner as v2_runner_singleton
from app.services import approvals as approvals_service

router = APIRouter(prefix="/approvals", tags=["approvals"])


async def _db() -> asyncpg.Pool:
    return await get_pool()


def _to_response(row: dict) -> ApprovalResponse:
    return ApprovalResponse.model_validate(row)


@router.get("", response_model=list[ApprovalResponse])
async def list_approvals(
    status: str = "pending",
    agent_slug: str | None = None,
    action_type: str | None = None,
    pool: asyncpg.Pool = Depends(_db),
):
    rows = await approvals_service.list_(
        pool,
        status=None if status == "all" else status,
        agent_slug=agent_slug,
        action_type=action_type,
    )
    return [_to_response(r) for r in rows]


@router.get("/{approval_id}", response_model=ApprovalResponse)
async def get_approval(approval_id: UUID, pool: asyncpg.Pool = Depends(_db)):
    row = await approvals_service.get(pool, approval_id)
    if not row:
        raise HTTPException(status_code=404, detail="Approval not found")
    return _to_response(row)


@router.post("/{approval_id}/approve", response_model=ApprovalResponse)
async def approve_approval(
    approval_id: UUID,
    background_tasks: BackgroundTasks,
    body: Optional[ApprovalApprove] = None,
    pool: asyncpg.Pool = Depends(_db),
):
    approved_by = body.approved_by if body else "system"
    executed_payload = body.executed_payload if body else None
    try:
        updated = await approvals_service.approve(
            pool, approval_id, approved_by, executed_payload
        )
    except approvals_service.ApprovalStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    # Drive the graph forward off-request so this response returns immediately.
    background_tasks.add_task(v2_runner_singleton.resume, updated["workflow_id"])
    return _to_response(updated)


@router.post("/{approval_id}/reject", response_model=ApprovalResponse)
async def reject_approval(
    approval_id: UUID,
    body: ApprovalReject,
    pool: asyncpg.Pool = Depends(_db),
):
    try:
        updated = await approvals_service.reject(
            pool, approval_id, body.rejected_by, body.rejection_reason
        )
    except approvals_service.ApprovalStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return _to_response(updated)
