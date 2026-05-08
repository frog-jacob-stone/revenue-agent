"""Agent-to-agent message log.

Backed by the `agent_messages` table (migration 0013). Every cross-agent turn
made via `ask_agent` (or directly from a graph node) writes one row here.
Threads are correlated by `thread_id` (the sender generates a fresh UUID for
the first message of a new conversation; subsequent turns reuse it).
`workflow_id` is set when the call originated from a graph node, NULL when it
originated from chat.

Service-layer audit policy: these functions do NOT call `write_audit_event`.
The table is the audit. If we later want per-turn audit visibility for the
graph trace, add an `AGENT_MESSAGE_SENT` event in `app.orchestrator_v2.events`
and a one-line write here.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import asyncpg


async def send_message(
    pool_or_conn: asyncpg.Pool | asyncpg.Connection,
    *,
    from_agent_slug: str,
    to_agent_slug: str,
    content: str,
    thread_id: UUID | None = None,
    workflow_id: UUID | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Insert one agent_messages row. Generates a fresh thread_id if none given.

    Accepts either an asyncpg pool or an open connection so callers inside an
    existing transaction can chain writes (mirrors the `social_posts` pattern).
    """
    thread_uuid = thread_id or uuid4()
    sql = """
        INSERT INTO agent_messages
            (thread_id, workflow_id, from_agent_slug, to_agent_slug, content, metadata)
        VALUES ($1, $2, $3, $4, $5, $6)
        RETURNING *
        """
    args = (
        thread_uuid,
        workflow_id,
        from_agent_slug,
        to_agent_slug,
        content,
        metadata or {},
    )

    if isinstance(pool_or_conn, asyncpg.Connection):
        row = await pool_or_conn.fetchrow(sql, *args)
    else:
        async with pool_or_conn.acquire() as conn:
            row = await conn.fetchrow(sql, *args)
    return dict(row)


async def read_thread(
    pool: asyncpg.Pool, thread_id: UUID
) -> list[dict[str, Any]]:
    """All messages in this thread, oldest first."""
    rows = await pool.fetch(
        """
        SELECT * FROM agent_messages
        WHERE thread_id = $1
        ORDER BY created_at ASC, id ASC
        """,
        thread_id,
    )
    return [dict(r) for r in rows]


async def get_messages_for_workflow(
    pool: asyncpg.Pool, workflow_id: UUID
) -> list[dict[str, Any]]:
    """All messages tied to a workflow, oldest first. Returns [] if none."""
    rows = await pool.fetch(
        """
        SELECT * FROM agent_messages
        WHERE workflow_id = $1
        ORDER BY created_at ASC, id ASC
        """,
        workflow_id,
    )
    return [dict(r) for r in rows]
