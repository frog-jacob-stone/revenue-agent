import logging
from uuid import UUID

import asyncpg
from fastapi import HTTPException

from app.services import audit, execution

logger = logging.getLogger(__name__)


async def approve_action(
    pool: asyncpg.Pool,
    action_id: UUID,
    approved_by: str = "system",
    executed_payload: dict | None = None,
) -> tuple[dict, bool]:
    """Approve an action. Returns (action_row, needs_orchestrator_resume).

    If the workflow has a `pattern` set, the action is left in 'approved' status
    and the caller is expected to dispatch `orchestrator.resume(workflow_id)` —
    the orchestrator owns execution for chained workflows.

    Otherwise the legacy path runs inline: mark executing, dispatch to
    `execution.execute()`, mark completed.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            action = await conn.fetchrow(
                "SELECT * FROM actions WHERE id = $1 FOR UPDATE",
                action_id,
            )
            if not action:
                raise HTTPException(status_code=404, detail="Action not found")
            if action["status"] != "proposed":
                raise HTTPException(
                    status_code=409,
                    detail=f"Action is '{action['status']}', expected 'proposed'",
                )

            action_dict = dict(action)

            workflow_pattern = await conn.fetchval(
                "SELECT pattern FROM workflows WHERE id = $1",
                action_dict["workflow_id"],
            )

            await conn.execute(
                """
                UPDATE actions
                SET status = 'approved',
                    approved_by = $1,
                    approved_at = now(),
                    executed_payload = $2
                WHERE id = $3
                """,
                approved_by,
                executed_payload,
                action_id,
            )
            await audit.write_audit_event(
                conn,
                "action.approved",
                agent_id=action_dict["agent_id"],
                workflow_id=action_dict["workflow_id"],
                action_id=action_id,
                actor=approved_by,
                payload={"approved_by": approved_by},
            )

            if workflow_pattern:
                # Orchestrated workflow — return now; caller schedules resume.
                approved = await conn.fetchrow(
                    "SELECT * FROM actions WHERE id = $1", action_id
                )
                return dict(approved), True

            await conn.execute(
                "UPDATE actions SET status = 'executing' WHERE id = $1",
                action_id,
            )

            try:
                result = await execution.execute(conn, action_dict)
            except NotImplementedError as exc:
                await conn.execute(
                    "UPDATE actions SET status = 'failed', error = $1 WHERE id = $2",
                    str(exc),
                    action_id,
                )
                await audit.write_audit_event(
                    conn,
                    "action.failed",
                    agent_id=action_dict["agent_id"],
                    workflow_id=action_dict["workflow_id"],
                    action_id=action_id,
                    actor="system",
                    payload={"error": str(exc)},
                )
                raise HTTPException(status_code=501, detail=str(exc))

            updated = await conn.fetchrow(
                """
                UPDATE actions
                SET status = 'completed',
                    result = $1,
                    executed_payload = $2,
                    executed_at = now()
                WHERE id = $3
                RETURNING *
                """,
                result,
                executed_payload if executed_payload is not None else action_dict["proposed_payload"],
                action_id,
            )
            await audit.write_audit_event(
                conn,
                "action.completed",
                agent_id=action_dict["agent_id"],
                workflow_id=action_dict["workflow_id"],
                action_id=action_id,
                actor="system",
                payload={"result": result},
            )

            return dict(updated), False


async def reject_action(pool: asyncpg.Pool, action_id: UUID, reason: str) -> dict:
    async with pool.acquire() as conn:
        async with conn.transaction():
            action = await conn.fetchrow(
                "SELECT * FROM actions WHERE id = $1 FOR UPDATE",
                action_id,
            )
            if not action:
                raise HTTPException(status_code=404, detail="Action not found")
            if action["status"] != "proposed":
                raise HTTPException(
                    status_code=409,
                    detail=f"Action is '{action['status']}', expected 'proposed'",
                )

            updated = await conn.fetchrow(
                """
                UPDATE actions
                SET status = 'rejected', rejection_reason = $1
                WHERE id = $2
                RETURNING *
                """,
                reason,
                action_id,
            )
            await audit.write_audit_event(
                conn,
                "action.rejected",
                agent_id=action["agent_id"],
                workflow_id=action["workflow_id"],
                action_id=action_id,
                actor="system",
                payload={"rejection_reason": reason},
            )

            # Orchestrated workflows: a rejected checkpoint cancels the chain.
            workflow_pattern = await conn.fetchval(
                "SELECT pattern FROM workflows WHERE id = $1",
                action["workflow_id"],
            )
            if workflow_pattern:
                await conn.execute(
                    """
                    UPDATE workflows
                    SET status = 'cancelled', completed_at = now(), error = $1
                    WHERE id = $2
                    """,
                    f"checkpoint rejected: {reason}",
                    action["workflow_id"],
                )
                await audit.write_audit_event(
                    conn,
                    "workflow.cancelled",
                    workflow_id=action["workflow_id"],
                    actor="system",
                    payload={"rejection_reason": reason, "action_id": str(action_id)},
                )

            return dict(updated)
