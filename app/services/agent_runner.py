import logging
from typing import Any
from uuid import UUID

from app.agents.base import BaseAgent
from app.agents.revenue_recognition import RevenueRecognitionAgent
from app.db import get_pool
from app.services import audit

logger = logging.getLogger(__name__)

AGENT_CLASSES: dict[str, type[BaseAgent]] = {
    "revenue-recognition": RevenueRecognitionAgent,
}


async def run_agent(
    slug: str,
    initiated_by: str = "system",
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    pool = await get_pool()
    context = context or {}

    async with pool.acquire() as conn:
        agent_row = await conn.fetchrow(
            "SELECT * FROM agents WHERE slug = $1 AND is_active",
            slug,
        )
        if not agent_row:
            raise ValueError(f"Agent '{slug}' not found or inactive")

        agent_id: UUID = agent_row["id"]
        agent_config: dict = agent_row["config"] or {}

        async with conn.transaction():
            workflow_row = await conn.fetchrow(
                """
                INSERT INTO workflows
                    (kind, status, trigger_source, trigger_payload, initiated_by)
                VALUES ($1, 'running', 'manual', $2, $3)
                RETURNING *
                """,
                slug,
                context,
                initiated_by,
            )
            workflow_id: UUID = workflow_row["id"]
            await audit.write_audit_event(
                conn,
                "workflow.started",
                agent_id=agent_id,
                workflow_id=workflow_id,
                actor=initiated_by,
                payload={"slug": slug, "trigger_source": "manual"},
            )

    agent_cls = AGENT_CLASSES.get(slug)
    if not agent_cls:
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "UPDATE workflows SET status = 'failed', error = $1, completed_at = now() WHERE id = $2",
                    f"No implementation for agent '{slug}'",
                    workflow_id,
                )
        raise NotImplementedError(f"No implementation for agent '{slug}'")

    agent = agent_cls(agent_id=agent_id, config=agent_config)

    try:
        proposals = await agent.run(workflow_id=workflow_id, context=context)
    except Exception as exc:
        logger.exception("agent %s run failed", slug)
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "UPDATE workflows SET status = 'failed', error = $1, completed_at = now() WHERE id = $2",
                    str(exc),
                    workflow_id,
                )
                await audit.write_audit_event(
                    conn,
                    "workflow.failed",
                    agent_id=agent_id,
                    workflow_id=workflow_id,
                    actor="system",
                    payload={"error": str(exc)},
                )
        raise

    async with pool.acquire() as conn:
        async with conn.transaction():
            for seq, proposal in enumerate(proposals, start=1):
                # Cancel any stale proposed actions of the same type + period
                # so re-triggering doesn't stack up duplicates in the inbox.
                if proposal["action_type"] == "configure_rev_rec_projects":
                    date_rec = (proposal.get("proposed_payload") or {}).get("date_recognized")
                    if date_rec:
                        await conn.execute(
                            """
                            UPDATE actions
                            SET status = 'rejected',
                                rejection_reason = 'Superseded by newer run'
                            WHERE agent_id = $1
                              AND action_type = 'configure_rev_rec_projects'
                              AND status = 'proposed'
                              AND proposed_payload->>'date_recognized' = $2
                            """,
                            agent_id,
                            date_rec,
                        )

                action_row = await conn.fetchrow(
                    """
                    INSERT INTO actions
                        (workflow_id, agent_id, sequence, action_type, status,
                         summary, proposed_payload, reasoning, risk_level)
                    VALUES ($1, $2, $3, $4, 'proposed', $5, $6, $7, $8)
                    RETURNING id
                    """,
                    workflow_id,
                    agent_id,
                    seq,
                    proposal["action_type"],
                    proposal["summary"],
                    proposal["proposed_payload"],
                    proposal.get("reasoning"),
                    proposal.get("risk_level", "low"),
                )
                await audit.write_audit_event(
                    conn,
                    "action.proposed",
                    agent_id=agent_id,
                    workflow_id=workflow_id,
                    action_id=action_row["id"],
                    actor=f"system:{slug}",
                    payload={
                        "action_type": proposal["action_type"],
                        "summary": proposal["summary"],
                    },
                )

            await conn.execute(
                "UPDATE workflows SET status = 'awaiting_approval', completed_at = now() WHERE id = $1",
                workflow_id,
            )
            await audit.write_audit_event(
                conn,
                "workflow.awaiting_approval",
                agent_id=agent_id,
                workflow_id=workflow_id,
                actor="system",
                payload={"proposals": len(proposals)},
            )

    logger.info(
        "agent %s completed: %d proposals for workflow %s",
        slug,
        len(proposals),
        workflow_id,
    )
    return {"workflow_id": str(workflow_id), "proposals": len(proposals)}
