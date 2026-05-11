"""DB layer for human-to-agent chat sessions and messages.

Mirrors `app/services/approvals.py` style: asyncpg pool, plain dict returns,
transactions for state changes. Distinct from `agent_messages` (migration 0013),
which is the agent-to-agent log.

A session is "in flight" when it has any `chat_messages` row with
status='streaming'. The detached `TurnRuntime` in `chat_runtime.py` writes the
placeholder row, runs the OpenAI loop in a background task, and updates the
row on completion.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import asyncpg


# ── Sessions ────────────────────────────────────────────────────────────────


async def create_session(pool: asyncpg.Pool, agent_slug: str) -> dict[str, Any]:
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


# ── Turn lifecycle ──────────────────────────────────────────────────────────


def title_from_user_text(text: str, max_len: int = 60) -> str:
    """Single-line truncated title for sidebar display."""
    s = " ".join(text.strip().split())
    if not s:
        return "New chat"
    if len(s) <= max_len:
        return s
    return s[: max_len - 1].rstrip() + "…"


async def append_user_message_and_prepare_turn(
    pool: asyncpg.Pool,
    session_id: UUID,
    content: str,
) -> tuple[UUID, int]:
    """In one transaction:
      1. Insert the user message.
      2. If this is the session's first message, set the title.
      3. Mint a turn_id.
      4. Insert the placeholder assistant message (status='streaming').
      5. Bump session.last_message_at, updated_at.
    Returns (turn_id, placeholder_assistant_message_id).
    """
    turn_id = uuid4()
    async with pool.acquire() as conn:
        async with conn.transaction():
            existing = await conn.fetchval(
                "SELECT count(*) FROM chat_messages WHERE session_id = $1",
                session_id,
            )
            await conn.execute(
                """
                INSERT INTO chat_messages (session_id, role, content, status)
                VALUES ($1, 'user', $2, 'complete')
                """,
                session_id,
                content,
            )
            if existing == 0:
                await conn.execute(
                    "UPDATE chat_sessions SET title = $2 WHERE id = $1",
                    session_id,
                    title_from_user_text(content),
                )
            placeholder_id = await conn.fetchval(
                """
                INSERT INTO chat_messages
                    (session_id, turn_id, role, content, status)
                VALUES ($1, $2, 'assistant', '', 'streaming')
                RETURNING id
                """,
                session_id,
                turn_id,
            )
            await conn.execute(
                """
                UPDATE chat_sessions
                SET last_message_at = now(), updated_at = now()
                WHERE id = $1
                """,
                session_id,
            )
    return turn_id, placeholder_id


async def finalize_assistant_message(
    pool: asyncpg.Pool,
    *,
    turn_id: UUID,
    content: str,
    activity: list[dict[str, Any]],
    status: str,
    tool_used: str | None,
    error: str | None,
) -> None:
    """Mark the placeholder assistant row as complete/failed and update the
    session's last_message_at / updated_at in one transaction."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                UPDATE chat_messages
                SET content = $2,
                    activity = $3,
                    status = $4,
                    tool_used = $5,
                    error = $6,
                    completed_at = now()
                WHERE turn_id = $1 AND role = 'assistant'
                RETURNING session_id
                """,
                turn_id,
                content,
                activity,
                status,
                tool_used,
                error,
            )
            if row is None:
                return
            await conn.execute(
                """
                UPDATE chat_sessions
                SET last_message_at = now(), updated_at = now()
                WHERE id = $1
                """,
                row["session_id"],
            )


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


async def load_history_for_llm(
    pool: asyncpg.Pool,
    session_id: UUID,
    limit: int = 30,
) -> list[dict[str, str]]:
    """Return the last `limit` complete messages as {role, content} dicts,
    oldest first, for feeding back to the LLM. Skips streaming/failed rows."""
    rows = await pool.fetch(
        """
        SELECT role, content FROM chat_messages
        WHERE session_id = $1
          AND status = 'complete'
          AND content <> ''
        ORDER BY id DESC
        LIMIT $2
        """,
        session_id,
        limit,
    )
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]
