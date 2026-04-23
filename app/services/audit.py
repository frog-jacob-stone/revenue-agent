import logging
from typing import Any
from uuid import UUID

import asyncpg

logger = logging.getLogger(__name__)


async def write_audit_event(
    conn: asyncpg.Connection,
    event_type: str,
    *,
    agent_id: UUID | None = None,
    workflow_id: UUID | None = None,
    action_id: UUID | None = None,
    actor: str | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    await conn.execute(
        """
        INSERT INTO audit_log (event_type, agent_id, workflow_id, action_id, actor, payload)
        VALUES ($1, $2, $3, $4, $5, $6)
        """,
        event_type,
        agent_id,
        workflow_id,
        action_id,
        actor,
        payload or {},
    )
    logger.debug("audit event written: %s", event_type)
