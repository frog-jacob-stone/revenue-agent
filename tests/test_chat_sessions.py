"""DB-backed tests for `chat_sessions` — pure CRUD only.

Turn-lifecycle ops (append-user-message, finalize, load-history,
title-from-user-text) now live as private helpers in
`app.services.chat_turn` and are exercised through `tests/test_chat_turn.py`
via the public `start_turn` API.
"""
from __future__ import annotations

from uuid import UUID

import pytest

from app.db import get_pool
from app.services import chat_sessions as cs


@pytest.mark.asyncio
async def test_create_session_with_explicit_slug():
    pool = await get_pool()
    row = await cs.create_session(pool, "chief-of-staff")
    assert row["agent_slug"] == "chief-of-staff"
    assert row["title"] == "New chat"
    fetched = await cs.get_session(pool, row["id"])
    assert fetched is not None
    assert fetched["id"] == row["id"]


@pytest.mark.asyncio
async def test_create_session_with_no_slug_uses_db_default():
    """Migration 0019 set the default to 'revenue-ops'; migration 0023 renames it to 'chief-of-staff'."""
    pool = await get_pool()
    row = await cs.create_session(pool)
    assert row["agent_slug"] == "chief-of-staff"


@pytest.mark.asyncio
async def test_list_sessions_filters_by_agent():
    pool = await get_pool()
    a = await cs.create_session(pool, "chief-of-staff")
    b = await cs.create_session(pool, "legacy-something")
    ro = await cs.list_sessions(pool, "chief-of-staff")
    ro_ids = {r["id"] for r in ro}
    assert a["id"] in ro_ids
    assert b["id"] not in ro_ids


async def _insert_streaming_placeholder(pool, session_id: UUID) -> None:
    """Helper: directly write a placeholder assistant row in 'streaming' state.
    Lets these tests stay scoped to chat_sessions without pulling in chat_turn.
    """
    await pool.execute(
        """
        INSERT INTO chat_messages (session_id, role, content, status)
        VALUES ($1, 'assistant', '', 'streaming')
        """,
        session_id,
    )


@pytest.mark.asyncio
async def test_has_streaming_message_detects_in_flight_turns():
    pool = await get_pool()
    session = await cs.create_session(pool)
    assert await cs.has_streaming_message(pool, session["id"]) is False
    await _insert_streaming_placeholder(pool, session["id"])
    assert await cs.has_streaming_message(pool, session["id"]) is True


@pytest.mark.asyncio
async def test_get_messages_returns_in_insertion_order():
    pool = await get_pool()
    session = await cs.create_session(pool)
    await pool.execute(
        "INSERT INTO chat_messages (session_id, role, content, status) VALUES ($1, 'user', 'a', 'complete')",
        session["id"],
    )
    await pool.execute(
        "INSERT INTO chat_messages (session_id, role, content, status) VALUES ($1, 'assistant', 'b', 'complete')",
        session["id"],
    )
    msgs = await cs.get_messages(pool, session["id"])
    contents = [m["content"] for m in msgs]
    assert contents == ["a", "b"]


@pytest.mark.asyncio
async def test_delete_session_cascades_to_messages():
    pool = await get_pool()
    session = await cs.create_session(pool)
    await _insert_streaming_placeholder(pool, session["id"])
    deleted = await cs.delete_session(pool, session["id"])
    assert deleted is True
    msgs = await cs.get_messages(pool, session["id"])
    assert msgs == []


@pytest.mark.asyncio
async def test_delete_session_missing_returns_false():
    from uuid import uuid4
    pool = await get_pool()
    deleted = await cs.delete_session(pool, uuid4())
    assert deleted is False


@pytest.mark.asyncio
async def test_mark_orphaned_streaming_failed():
    pool = await get_pool()
    session = await cs.create_session(pool)
    await _insert_streaming_placeholder(pool, session["id"])
    count = await cs.mark_orphaned_streaming_failed(pool)
    assert count >= 1
    msgs = await cs.get_messages(pool, session["id"])
    # The streaming row should now be failed.
    assert any(m["status"] == "failed" and m["error"] == "process terminated" for m in msgs)
    # Idempotent: a second call should find nothing.
    again = await cs.mark_orphaned_streaming_failed(pool)
    assert again == 0
