from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field

from app.db import get_pool
from app.models.common import ORMBase
from app.models.workflows import (
    TraceEvent,
    WorkflowCreate,
    WorkflowResponse,
    WorkflowTraceResponse,
)
from app.orchestrator import runner as v2_runner
from app.orchestrator.graphs.outreach import OUTREACH_KIND
from app.services import audit

router = APIRouter(prefix="/workflows", tags=["workflows"])


async def _db() -> asyncpg.Pool:
    return await get_pool()


def _row_to_workflow(row: asyncpg.Record) -> WorkflowResponse:
    return WorkflowResponse.model_validate(dict(row))


class OutreachTrigger(ORMBase):
    hubspot_contact_id: str
    initiated_by: str = "system"
    notes: dict | None = Field(default=None, description="Optional context to seed the chain")


class OutreachTriggerResponse(ORMBase):
    workflow_id: UUID
    kind: str
    status: str = "running"


@router.post("/outreach", response_model=OutreachTriggerResponse, status_code=202)
async def trigger_outreach(body: OutreachTrigger):
    """Kick off an Outreach graph for a HubSpot contact.

    Drives the graph forward until it pauses at the `gmail_send` approval gate
    (or completes, on terminal failure). Clients poll `/workflows/{id}/trace`
    (or watch the inbox) to see progress.
    """
    workflow_id = await v2_runner.start(
        OUTREACH_KIND,
        initial_state={
            "hubspot_contact_id": body.hubspot_contact_id,
            "notes": body.notes or {},
        },
        initiated_by=body.initiated_by,
        trigger_source="manual",
        subject_type="contact",
        subject_id=body.hubspot_contact_id,
    )
    return OutreachTriggerResponse(workflow_id=workflow_id, kind=OUTREACH_KIND)


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
    row = await pool.fetchrow("SELECT * FROM workflows WHERE id = $1", workflow_id)
    if not row:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return _row_to_workflow(row)


@router.get("/{workflow_id}/trace", response_model=WorkflowTraceResponse)
async def get_workflow_trace(workflow_id: UUID, pool: asyncpg.Pool = Depends(_db)):
    """Return every audit_log event for the workflow in time order."""
    async with pool.acquire() as conn:
        wf = await conn.fetchrow(
            "SELECT id, kind, status FROM workflows WHERE id = $1",
            workflow_id,
        )
        if not wf:
            raise HTTPException(status_code=404, detail="Workflow not found")

        event_rows = await conn.fetch(
            """
            SELECT id, event_type, occurred_at, actor, payload
            FROM audit_log
            WHERE workflow_id = $1
            ORDER BY occurred_at, id
            """,
            workflow_id,
        )
    events = [TraceEvent.model_validate(dict(r)) for r in event_rows]
    return WorkflowTraceResponse(
        workflow_id=wf["id"],
        kind=wf["kind"],
        status=wf["status"],
        events=events,
    )
