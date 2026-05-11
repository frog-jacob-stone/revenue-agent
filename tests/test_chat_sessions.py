"""DB-backed tests for the chat_sessions service."""
from __future__ import annotations

import pytest

from app.db import get_pool
from app.services import chat_sessions as cs


# ── Pure title helper ──────────────────────────────────────────────────────


def test_title_from_user_text_empty_returns_default():
    assert cs.title_from_user_text("") == "New chat"
    assert cs.title_from_user_text("   ") == "New chat"


def test_title_from_user_text_collapses_whitespace():
    assert cs.title_from_user_text("hello\n\n  world") == "hello world"


def test_title_from_user_text_under_max_kept_as_is():
    assert cs.title_from_user_text("draft a linkedin post about pricing") == (
        "draft a linkedin post about pricing"
    )


def test_title_from_user_text_truncates_long_input():
    long = "x" * 100
    out = cs.title_from_user_text(long, max_len=20)
    assert len(out) == 20
    assert out.endswith("…")


# ── DB CRUD ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_and_get_session():
    pool = await get_pool()
    row = await cs.create_session(pool, "content-orchestrator")
    assert row["agent_slug"] == "content-orchestrator"
    assert row["title"] == "New chat"
    fetched = await cs.get_session(pool, row["id"])
    assert fetched is not None
    assert fetched["id"] == row["id"]


@pytest.mark.asyncio
async def test_list_sessions_filters_by_agent():
    pool = await get_pool()
    a = await cs.create_session(pool, "content-orchestrator")
    b = await cs.create_session(pool, "revenue-recognition")
    co = await cs.list_sessions(pool, "content-orchestrator")
    co_ids = {r["id"] for r in co}
    assert a["id"] in co_ids
    assert b["id"] not in co_ids


@pytest.mark.asyncio
async def test_append_user_message_sets_title_on_first_turn():
    pool = await get_pool()
    session = await cs.create_session(pool, "content-orchestrator")
    turn_id, placeholder_id = await cs.append_user_message_and_prepare_turn(
        pool, session["id"], "draft a linkedin post about pricing"
    )
    assert turn_id is not None
    assert placeholder_id > 0

    refreshed = await cs.get_session(pool, session["id"])
    assert refreshed is not None
    assert refreshed["title"] == "draft a linkedin post about pricing"
    assert refreshed["last_message_at"] is not None

    msgs = await cs.get_messages(pool, session["id"])
    assert len(msgs) == 2
    assert msgs[0]["role"] == "user"
    assert msgs[1]["role"] == "assistant"
    assert msgs[1]["status"] == "streaming"
    assert msgs[1]["content"] == ""


@pytest.mark.asyncio
async def test_append_user_message_does_not_overwrite_title_on_subsequent_turns():
    pool = await get_pool()
    session = await cs.create_session(pool, "content-orchestrator")
    await cs.append_user_message_and_prepare_turn(pool, session["id"], "first question")
    # Simulate first turn completing so the streaming row goes complete.
    await pool.execute(
        "UPDATE chat_messages SET status='complete', content='ok' WHERE session_id=$1 AND status='streaming'",
        session["id"],
    )
    await cs.append_user_message_and_prepare_turn(pool, session["id"], "second question")
    refreshed = await cs.get_session(pool, session["id"])
    assert refreshed["title"] == "first question"


@pytest.mark.asyncio
async def test_has_streaming_message_detects_in_flight_turns():
    pool = await get_pool()
    session = await cs.create_session(pool, "content-orchestrator")
    assert await cs.has_streaming_message(pool, session["id"]) is False
    await cs.append_user_message_and_prepare_turn(pool, session["id"], "hi")
    assert await cs.has_streaming_message(pool, session["id"]) is True


@pytest.mark.asyncio
async def test_delete_session_cascades_to_messages():
    pool = await get_pool()
    session = await cs.create_session(pool, "content-orchestrator")
    await cs.append_user_message_and_prepare_turn(pool, session["id"], "hi")
    deleted = await cs.delete_session(pool, session["id"])
    assert deleted is True
    msgs = await cs.get_messages(pool, session["id"])
    assert msgs == []


@pytest.mark.asyncio
async def test_finalize_assistant_message_writes_content_and_activity():
    pool = await get_pool()
    session = await cs.create_session(pool, "content-orchestrator")
    turn_id, _ = await cs.append_user_message_and_prepare_turn(pool, session["id"], "hi")
    activity = [{"id": "tl-1", "kind": "tool", "parentId": None, "label": "Calling x", "status": "ok"}]
    await cs.finalize_assistant_message(
        pool,
        turn_id=turn_id,
        content="hello world",
        activity=activity,
        status="complete",
        tool_used=None,
        error=None,
    )
    msgs = await cs.get_messages(pool, session["id"])
    assistant = msgs[1]
    assert assistant["status"] == "complete"
    assert assistant["content"] == "hello world"
    assert assistant["activity"] == activity
    assert assistant["completed_at"] is not None


@pytest.mark.asyncio
async def test_mark_orphaned_streaming_failed():
    pool = await get_pool()
    session = await cs.create_session(pool, "content-orchestrator")
    await cs.append_user_message_and_prepare_turn(pool, session["id"], "hi")
    count = await cs.mark_orphaned_streaming_failed(pool)
    assert count >= 1
    msgs = await cs.get_messages(pool, session["id"])
    assistant = msgs[1]
    assert assistant["status"] == "failed"
    assert assistant["error"] == "process terminated"
    # idempotent: a second call should find nothing
    again = await cs.mark_orphaned_streaming_failed(pool)
    assert again == 0


@pytest.mark.asyncio
async def test_load_history_for_llm_skips_streaming_rows_and_orders_oldest_first():
    pool = await get_pool()
    session = await cs.create_session(pool, "content-orchestrator")
    turn_id_1, _ = await cs.append_user_message_and_prepare_turn(pool, session["id"], "first q")
    await cs.finalize_assistant_message(
        pool,
        turn_id=turn_id_1,
        content="first answer",
        activity=[],
        status="complete",
        tool_used=None,
        error=None,
    )
    # Second turn still streaming
    await cs.append_user_message_and_prepare_turn(pool, session["id"], "second q")
    history = await cs.load_history_for_llm(pool, session["id"])
    # Should include both user messages + the first assistant answer; skip the
    # streaming placeholder.
    roles = [m["role"] for m in history]
    contents = [m["content"] for m in history]
    assert roles == ["user", "assistant", "user"]
    assert contents == ["first q", "first answer", "second q"]
