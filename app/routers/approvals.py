"""Approvals router — human-in-the-loop queue.

Tool-driven (ADR-0002): an approved row carries an `executor` name; the
named executor runs in a background task. The inbox UI is the only path to
approval action.
"""
from __future__ import annotations

import logging
from typing import Any, Optional
from uuid import UUID

import asyncpg
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException

from app.db import get_pool
from app.executors.base import ExecutorContext, ExecutorDefinition
from app.executors.registry import EXECUTORS_BY_NAME
from app.models.approvals import ApprovalApprove, ApprovalReject, ApprovalResponse
from app.services import approvals as approvals_service

logger = logging.getLogger(__name__)

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

    executor_name = updated["executor"]
    executor = EXECUTORS_BY_NAME.get(executor_name)
    if executor is None:
        await approvals_service.mark_failed(
            pool,
            approval_id,
            error=f"executor '{executor_name}' is not registered",
        )
        raise HTTPException(
            status_code=500,
            detail=f"Executor '{executor_name}' is not registered",
        )
    ctx = ExecutorContext(
        approval_id=approval_id,
        approved_by=approved_by,
        pool=pool,
    )
    payload = updated.get("executed_payload") or updated["proposed_payload"]
    background_tasks.add_task(_run_executor, executor, ctx, payload, approval_id)
    return _to_response(updated)


async def _run_executor(
    executor: ExecutorDefinition,
    ctx: ExecutorContext,
    payload: dict[str, Any],
    approval_id: UUID,
) -> None:
    """Background-task wrapper that invokes the executor and marks the approval."""
    try:
        result = await executor.execute(ctx, payload)
        await approvals_service.mark_executed(
            ctx.pool,
            approval_id,
            executed_payload=result if isinstance(result, dict) else None,
        )
    except Exception as exc:
        logger.exception("Executor %s failed for approval %s", executor.name, approval_id)
        await approvals_service.mark_failed(ctx.pool, approval_id, error=str(exc))


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
