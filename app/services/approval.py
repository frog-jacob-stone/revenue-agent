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
) -> dict:
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

            await conn.execute(
                """
                UPDATE actions
                SET status = 'approved', approved_by = $1, approved_at = now()
                WHERE id = $2
                """,
                approved_by,
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

            await conn.execute(
                "UPDATE actions SET status = 'executing' WHERE id = $1",
                action_id,
            )

            result = await execution.execute(conn, action_dict)

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

            return dict(updated)


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

            return dict(updated)
