"""Live-tail audit_log rows for a single workflow.

Polls audit_log at a configurable interval, yielding each new row as a
TraceEvent. Terminates when a terminal workflow event is observed
(workflow.completed / workflow.failed) or when the caller cancels.

Used by the chat SSE stream to forward orchestrator events to the UI
while a tool-spawned workflow is running.
"""

import asyncio
from typing import AsyncIterator
from uuid import UUID

import asyncpg

from app.models.workflows import TraceEvent
from app.orchestrator import events
from app.tools.base import ProgressEmitter

_TERMINAL = {events.WORKFLOW_COMPLETED, events.WORKFLOW_FAILED}
_SUBAGENT_EVENTS = {events.AGENT_INVOKED, events.AGENT_COMPLETED, events.AGENT_FAILED}


async def tail_workflow_events(
    pool: asyncpg.Pool,
    workflow_id: UUID,
    *,
    interval: float = 0.3,
    include_subagents: bool = True,
    timeout: float = 600.0,
) -> AsyncIterator[TraceEvent]:
    """Yield audit_log rows for workflow_id as they appear.

    Stops on a terminal event (yielded before stopping) or after `timeout`
    seconds with no terminal event seen. Callers can cancel the iterator
    at any time.
    """
    last_id: int = 0
    deadline = asyncio.get_event_loop().time() + timeout

    while True:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, event_type, occurred_at, actor, payload
                FROM audit_log
                WHERE workflow_id = $1 AND id > $2
                ORDER BY id
                """,
                workflow_id,
                last_id,
            )

        terminal = False
        for row in rows:
            last_id = row["id"]
            event_type = row["event_type"]
            if not include_subagents and event_type in _SUBAGENT_EVENTS:
                if event_type in _TERMINAL:
                    terminal = True
                continue
            yield TraceEvent.model_validate(dict(row))
            if event_type in _TERMINAL:
                terminal = True

        if terminal:
            return
        if asyncio.get_event_loop().time() >= deadline:
            return
        await asyncio.sleep(interval)


async def forward_workflow_to_progress(
    pool: asyncpg.Pool,
    workflow_id: UUID,
    kind: str,
    progress: ProgressEmitter,
    *,
    drive_task: asyncio.Task | None = None,
) -> None:
    """Emit a workflow_started + workflow_event stream to a ProgressEmitter.

    Reads `chat_stream_show_subagents` from settings to decide whether
    agent.invoked/completed events are forwarded.

    If `drive_task` is provided (the asyncio.Task driving the graph), it
    is awaited after the tail terminates so callers know the workflow has
    fully finished — any exception from the graph propagates.
    """
    from app.config import settings

    progress.emit({
        "type": "workflow_started",
        "workflow_id": str(workflow_id),
        "kind": kind,
    })
    async for ev in tail_workflow_events(
        pool,
        workflow_id,
        include_subagents=settings.chat_stream_show_subagents,
    ):
        progress.emit({
            "type": "workflow_event",
            "workflow_id": str(workflow_id),
            "event_type": ev.event_type,
            "actor": ev.actor,
            "payload": ev.payload,
        })

    if drive_task is not None:
        await drive_task
