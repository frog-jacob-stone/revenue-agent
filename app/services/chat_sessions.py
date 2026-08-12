"""DB layer for chat sessions and messages — pure CRUD.

Turn-lifecycle DB ops (append-and-prepare, finalize, history-load) live in
`app/services/chat_turn.py` as private helpers — they're not reusable DB
helpers, they're the first/last steps of one chat turn that happen to write
SQL. Distinct from `agent_messages`, which is the agent-to-agent log.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

import asyncpg


# ── Sessions ────────────────────────────────────────────────────────────────


async def create_session(
    pool: asyncpg.Pool,
    agent_slug: str | None = None,
) -> dict[str, Any]:
    """Create a chat session. When `agent_slug` is None, the DB DEFAULT
    (migration 0023: 'chief-of-staff') is used."""
    if agent_slug is None:
        row = await pool.fetchrow(
            """
            INSERT INTO chat_sessions DEFAULT VALUES
            RETURNING *
            """,
        )
    else:
        row = await pool.fetchrow(
            """
            INSERT INTO chat_sessions (agent_slug)
            VALUES ($1)
            RETURNING *
            """,
            agent_slug,
        )
    return dict(row)


async def list_sessions(pool: asyncpg.Pool, agent_slug: str) -> list[dict[str, Any]]:
    rows = await pool.fetch(
        """
        SELECT * FROM chat_sessions
        WHERE agent_slug = $1
        ORDER BY last_message_at DESC NULLS LAST, created_at DESC
        """,
        agent_slug,
    )
    return [dict(r) for r in rows]


async def get_session(pool: asyncpg.Pool, session_id: UUID) -> dict[str, Any] | None:
    row = await pool.fetchrow(
        "SELECT * FROM chat_sessions WHERE id = $1",
        session_id,
    )
    return dict(row) if row else None


async def delete_session(pool: asyncpg.Pool, session_id: UUID) -> bool:
    """Hard delete; cascades to chat_messages. Returns True if a row was deleted."""
    status = await pool.execute(
        "DELETE FROM chat_sessions WHERE id = $1",
        session_id,
    )
    return status.endswith(" 1")


# ── Messages ────────────────────────────────────────────────────────────────


async def get_messages(pool: asyncpg.Pool, session_id: UUID) -> list[dict[str, Any]]:
    rows = await pool.fetch(
        """
        SELECT * FROM chat_messages
        WHERE session_id = $1
        ORDER BY id ASC
        """,
        session_id,
    )
    return [dict(r) for r in rows]


async def has_streaming_message(pool: asyncpg.Pool, session_id: UUID) -> bool:
    row = await pool.fetchrow(
        """
        SELECT 1 FROM chat_messages
        WHERE session_id = $1 AND status = 'streaming'
        LIMIT 1
        """,
        session_id,
    )
    return row is not None


# ── Startup cleanup ─────────────────────────────────────────────────────────


async def mark_orphaned_streaming_failed(pool: asyncpg.Pool) -> int:
    """On app startup: any chat_messages row with status='streaming' is from a
    previous process — the upstream LLM stream is gone. Mark it failed.
    Returns the number of rows updated."""
    status = await pool.execute(
        """
        UPDATE chat_messages
        SET status = 'failed',
            error = COALESCE(error, 'process terminated'),
            completed_at = now()
        WHERE status = 'streaming'
        """,
    )
    parts = status.split()
    try:
        return int(parts[-1])
    except (ValueError, IndexError):
        return 0
