"""DB layer for the `approvals` table.

`approvals` is the human-in-the-loop queue for v2 (LangGraph) workflows. It
replaces the role of `actions WHERE status='proposed'` in v1. Phase 0 of the
multi-agent rearchitecture (.agent/plans/3.langgraph-multi-agent-rearchitecture.md).

Every state-changing function writes an audit event using the canonical
constants in app/orchestrator_v2/events.py.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

import asyncpg

from app.orchestrator_v2 import events
from app.services import audit


# ── Create ──────────────────────────────────────────────────────────────────────


async def create_pending_conn(
    conn: asyncpg.Connection,
    *,
    workflow_id: UUID,
    node_name: str,
    agent_slug: str,
    action_type: str,
    proposed_payload: dict[str, Any],
    summary: str | None = None,
    reasoning: str | None = None,
    risk_level: str | None = None,
    assigned_to: str | None = None,
) -> dict[str, Any]:
    """Insert a `pending` approval and emit APPROVAL_REQUESTED."""
    row = await conn.fetchrow(
        """
        INSERT INTO approvals
            (workflow_id, node_name, agent_slug, action_type, status,
             risk_level, summary, reasoning, proposed_payload, assigned_to)
        VALUES ($1, $2, $3, $4, 'pending', $5, $6, $7, $8, $9)
        RETURNING *
        """,
        workflow_id,
        node_name,
        agent_slug,
        action_type,
        risk_level,
        summary,
        reasoning,
        proposed_payload,
        assigned_to,
    )
    await audit.write_audit_event(
        conn,
        events.APPROVAL_REQUESTED,
        workflow_id=workflow_id,
        actor=f"orchestrator_v2:{node_name}",
        payload={
            "approval_id": str(row["id"]),
            "node_name": node_name,
            "action_type": action_type,
            "agent_slug": agent_slug,
        },
    )
    return dict(row)


# ── Read ────────────────────────────────────────────────────────────────────────


async def get(pool: asyncpg.Pool, approval_id: UUID) -> dict[str, Any] | None:
    row = await pool.fetchrow("SELECT * FROM approvals WHERE id = $1", approval_id)
    return dict(row) if row else None


async def list_(
    pool: asyncpg.Pool,
    *,
    status: str | None = "pending",
    agent_slug: str | None = None,
    action_type: str | None = None,
) -> list[dict[str, Any]]:
    conditions: list[str] = []
    params: list[Any] = []
    if status is not None:
        params.append(status)
        conditions.append(f"status = ${len(params)}")
    if agent_slug is not None:
        params.append(agent_slug)
        conditions.append(f"agent_slug = ${len(params)}")
    if action_type is not None:
        params.append(action_type)
        conditions.append(f"action_type = ${len(params)}")
    where = "WHERE " + " AND ".join(conditions) if conditions else ""
    rows = await pool.fetch(
        f"SELECT * FROM approvals {where} ORDER BY created_at DESC",
        *params,
    )
    return [dict(r) for r in rows]


# ── Lifecycle transitions ───────────────────────────────────────────────────────


class ApprovalStateError(Exception):
    """Raised when an approval transition isn't valid (e.g., approving a rejected row)."""


async def approve(
    pool: asyncpg.Pool,
    approval_id: UUID,
    approved_by: str,
    executed_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """pending → approved. Returns the updated row."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            current = await conn.fetchrow(
                "SELECT * FROM approvals WHERE id = $1 FOR UPDATE",
                approval_id,
            )
            if current is None:
                raise ApprovalStateError(f"approval {approval_id} not found")
            if current["status"] != "pending":
                raise ApprovalStateError(
                    f"approval {approval_id} is {current['status']}, expected pending"
                )

            row = await conn.fetchrow(
                """
                UPDATE approvals
                SET status = 'approved',
                    approved_by = $2,
                    approved_at = now(),
                    executed_payload = COALESCE($3, executed_payload)
                WHERE id = $1
                RETURNING *
                """,
                approval_id,
                approved_by,
                executed_payload,
            )
            await audit.write_audit_event(
                conn,
                events.APPROVAL_GRANTED,
                workflow_id=row["workflow_id"],
                actor=approved_by,
                payload={"approval_id": str(approval_id)},
            )
    return dict(row)


async def reject(
    pool: asyncpg.Pool,
    approval_id: UUID,
    rejected_by: str,
    rejection_reason: str,
) -> dict[str, Any]:
    """pending → rejected."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            current = await conn.fetchrow(
                "SELECT * FROM approvals WHERE id = $1 FOR UPDATE",
                approval_id,
            )
            if current is None:
                raise ApprovalStateError(f"approval {approval_id} not found")
            if current["status"] != "pending":
                raise ApprovalStateError(
                    f"approval {approval_id} is {current['status']}, expected pending"
                )

            row = await conn.fetchrow(
                """
                UPDATE approvals
                SET status = 'rejected',
                    rejected_by = $2,
                    rejection_reason = $3
                WHERE id = $1
                RETURNING *
                """,
                approval_id,
                rejected_by,
                rejection_reason,
            )
            await audit.write_audit_event(
                conn,
                events.APPROVAL_REJECTED,
                workflow_id=row["workflow_id"],
                actor=rejected_by,
                payload={"approval_id": str(approval_id), "reason": rejection_reason},
            )
    return dict(row)


async def mark_executed(
    pool: asyncpg.Pool,
    approval_id: UUID,
    executed_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """approved → executed. Called by the runner after the gated node completes."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                UPDATE approvals
                SET status = 'executed',
                    executed_at = now(),
                    executed_payload = COALESCE($2, executed_payload)
                WHERE id = $1
                RETURNING *
                """,
                approval_id,
                executed_payload,
            )
            if row is None:
                raise ApprovalStateError(f"approval {approval_id} not found")
            await audit.write_audit_event(
                conn,
                events.APPROVAL_EXECUTED,
                workflow_id=row["workflow_id"],
                actor="orchestrator_v2",
                payload={"approval_id": str(approval_id)},
            )
    return dict(row)


async def mark_failed(
    pool: asyncpg.Pool,
    approval_id: UUID,
    error: str,
) -> dict[str, Any]:
    """Any → failed. Used when the gated node raises after approval."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                UPDATE approvals
                SET status = 'failed',
                    error = $2
                WHERE id = $1
                RETURNING *
                """,
                approval_id,
                error,
            )
            if row is None:
                raise ApprovalStateError(f"approval {approval_id} not found")
            await audit.write_audit_event(
                conn,
                events.APPROVAL_FAILED,
                workflow_id=row["workflow_id"],
                actor="orchestrator_v2",
                payload={"approval_id": str(approval_id), "error": error},
            )
    return dict(row)
