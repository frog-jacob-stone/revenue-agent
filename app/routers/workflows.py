from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException

from app.db import get_pool
from app.models.actions import ActionCreate, ActionResponse
from app.models.workflows import WorkflowCreate, WorkflowResponse
from app.services import audit

router = APIRouter(prefix="/workflows", tags=["workflows"])


async def _db() -> asyncpg.Pool:
    return await get_pool()


def _row_to_action(row: asyncpg.Record) -> ActionResponse:
    return ActionResponse.model_validate(dict(row))


def _row_to_workflow(row: asyncpg.Record, actions: list[asyncpg.Record] | None = None) -> WorkflowResponse:
    d = dict(row)
    d["actions"] = [dict(a) for a in (actions or [])]
    return WorkflowResponse.model_validate(d)


@router.post("", response_model=WorkflowResponse, status_code=201)
async def create_workflow(body: WorkflowCreate, pool: asyncpg.Pool = Depends(_db)):
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                INSERT INTO workflows
                    (kind, status, trigger_source, trigger_payload,
                     subject_type, subject_id, subject_ref, initiated_by, metadata)
                VALUES ($1, 'running', $2, $3, $4, $5, $6, $7, $8)
                RETURNING *
                """,
                body.kind,
                body.trigger_source,
                body.trigger_payload,
                body.subject_type,
                body.subject_id,
                body.subject_ref,
                body.initiated_by,
                body.metadata or {},
            )
            await audit.write_audit_event(
                conn,
                "workflow.started",
                workflow_id=row["id"],
                actor=body.initiated_by,
                payload={"kind": body.kind, "trigger_source": body.trigger_source},
            )
    return _row_to_workflow(row)


@router.get("", response_model=list[WorkflowResponse])
async def list_workflows(
    status: str | None = None,
    kind: str | None = None,
    pool: asyncpg.Pool = Depends(_db),
):
    conditions: list[str] = []
    params: list = []

    if status:
        params.append(status)
        conditions.append(f"status::text = ${len(params)}")
    if kind:
        params.append(kind)
        conditions.append(f"kind = ${len(params)}")

    where = "WHERE " + " AND ".join(conditions) if conditions else ""
    rows = await pool.fetch(
        f"SELECT * FROM workflows {where} ORDER BY started_at DESC",
        *params,
    )
    return [_row_to_workflow(r) for r in rows]


@router.get("/{workflow_id}", response_model=WorkflowResponse)
async def get_workflow(workflow_id: UUID, pool: asyncpg.Pool = Depends(_db)):
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM workflows WHERE id = $1", workflow_id)
        if not row:
            raise HTTPException(status_code=404, detail="Workflow not found")
        actions = await conn.fetch(
            "SELECT * FROM actions WHERE workflow_id = $1 ORDER BY sequence",
            workflow_id,
        )
    return _row_to_workflow(row, actions)


@router.post("/{workflow_id}/actions", response_model=ActionResponse, status_code=201)
async def propose_action(
    workflow_id: UUID,
    body: ActionCreate,
    pool: asyncpg.Pool = Depends(_db),
):
    async with pool.acquire() as conn:
        async with conn.transaction():
            workflow = await conn.fetchrow("SELECT id FROM workflows WHERE id = $1", workflow_id)
            if not workflow:
                raise HTTPException(status_code=404, detail="Workflow not found")

            next_seq = await conn.fetchval(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM actions WHERE workflow_id = $1",
                workflow_id,
            )

            row = await conn.fetchrow(
                """
                INSERT INTO actions
                    (workflow_id, agent_id, sequence, action_type, status,
                     summary, proposed_payload, reasoning, risk_level)
                VALUES ($1, $2, $3, $4, 'proposed', $5, $6, $7, $8)
                RETURNING *
                """,
                workflow_id,
                body.agent_id,
                next_seq,
                body.action_type.value,
                body.summary,
                body.proposed_payload,
                body.reasoning,
                body.risk_level.value if body.risk_level else None,
            )
            await audit.write_audit_event(
                conn,
                "action.proposed",
                agent_id=body.agent_id,
                workflow_id=workflow_id,
                action_id=row["id"],
                actor=f"system:{body.agent_id}",
                payload={"action_type": body.action_type.value, "summary": body.summary},
            )
    return _row_to_action(row)
