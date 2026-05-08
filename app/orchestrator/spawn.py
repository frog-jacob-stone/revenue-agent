"""Sub-workflow spawning primitive.

Lets a node start a child workflow with a parent_workflow_id linkage. The
child runs the same way as a top-level workflow (via the runner) — it just
has a parent edge in `workflows` and emits SUBWORKFLOW_SPAWNED on the
parent's audit trail. Used for supervisor → specialist patterns and any
case where one workflow needs to spin up a nested workflow.
"""
from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from app.db import get_pool
from app.orchestrator import events
from app.orchestrator.runner import runner
from app.services import audit

logger = logging.getLogger(__name__)


async def spawn_workflow(
    kind: str,
    initial_state: dict[str, Any],
    *,
    parent_workflow_id: UUID,
    initiated_by: str = "system",
    trigger_source: str = "subworkflow",
) -> UUID:
    """Spawn a child workflow and link it to the parent.

    Returns the child workflow_id immediately. The child is driven by the
    runner up to its first interrupt or completion in this same task — the
    caller awaits.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        await audit.write_audit_event(
            conn,
            events.SUBWORKFLOW_SPAWNED,
            workflow_id=parent_workflow_id,
            actor=f"orchestrator:{kind}",
            payload={
                "child_kind": kind,
                "parent_workflow_id": str(parent_workflow_id),
            },
        )
    child_id = await runner.start(
        kind,
        initial_state=initial_state,
        initiated_by=initiated_by,
        trigger_source=trigger_source,
        parent_workflow_id=parent_workflow_id,
    )
    logger.info(
        "spawn_workflow: kind=%s parent=%s child=%s",
        kind, parent_workflow_id, child_id,
    )
    return child_id
