"""Tests for the detached chat-turn runtime.

Verifies that:
  - the runtime persists a final assistant message even with no subscriber
  - subscribers can drop mid-stream without affecting completion
  - the activity tree is captured and written to chat_messages.activity
"""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import patch

import pytest

from app.db import get_pool
from app.services import chat_sessions as cs
from app.services.chat_runtime import detach_turn, get_active


async def _fake_stream(events: list[dict[str, Any]]):
    async def gen(_agent_slug, _history):
        for e in events:
            await asyncio.sleep(0)
            yield e

    return gen


@pytest.mark.asyncio
async def test_turn_completes_and_persists_without_subscriber():
    """No SSE subscriber attached: runtime still finishes and writes final state."""
    pool = await get_pool()
    session = await cs.create_session(pool, "content-orchestrator")
    turn_id, _ = await cs.append_user_message_and_prepare_turn(pool, session["id"], "hi")

    events = [
        {"type": "delta", "text": "Hello"},
        {"type": "delta", "text": ", world"},
        {"type": "done", "answer": "Hello, world", "tool_used": None},
    ]
    fake_gen = await _fake_stream(events)
    with patch("app.services.chat_runtime.agent_chat_stream", new=fake_gen):
        runtime = detach_turn(
            pool=pool,
            session_id=session["id"],
            turn_id=turn_id,
            agent_slug="content-orchestrator",
            history=[],
        )
        await runtime.task

    msgs = await cs.get_messages(pool, session["id"])
    assistant = msgs[1]
    assert assistant["status"] == "complete"
    assert assistant["content"] == "Hello, world"
    assert assistant["activity"] == []
    assert assistant["completed_at"] is not None
    assert get_active(turn_id) is None  # cleaned up after done


@pytest.mark.asyncio
async def test_turn_pushes_events_to_subscriber():
    pool = await get_pool()
    session = await cs.create_session(pool, "content-orchestrator")
    turn_id, _ = await cs.append_user_message_and_prepare_turn(pool, session["id"], "hi")

    events = [
        {"type": "delta", "text": "Hi"},
        {"type": "tool_call_started", "name": "create_post", "args": {}},
        {"type": "tool_call_completed", "name": "create_post", "ok": True, "result_summary": "{ok}"},
        {"type": "done", "answer": "Hi", "tool_used": "create_post"},
    ]
    fake_gen = await _fake_stream(events)
    with patch("app.services.chat_runtime.agent_chat_stream", new=fake_gen):
        runtime = detach_turn(
            pool=pool,
            session_id=session["id"],
            turn_id=turn_id,
            agent_slug="content-orchestrator",
            history=[],
        )
        queue = runtime.subscribe()
        assert queue is not None

        received: list[dict[str, Any]] = []
        while True:
            ev = await queue.get()
            if ev is None:
                break
            received.append(ev)

    types = [e["type"] for e in received]
    assert types == ["delta", "tool_call_started", "tool_call_completed", "done"]


@pytest.mark.asyncio
async def test_subscriber_drop_does_not_abort_turn():
    """Drop the subscriber mid-stream; runtime should still persist final state."""
    pool = await get_pool()
    session = await cs.create_session(pool, "content-orchestrator")
    turn_id, _ = await cs.append_user_message_and_prepare_turn(pool, session["id"], "hi")

    events = [
        {"type": "delta", "text": "A"},
        {"type": "delta", "text": "B"},
        {"type": "delta", "text": "C"},
        {"type": "done", "answer": "ABC", "tool_used": None},
    ]
    fake_gen = await _fake_stream(events)
    with patch("app.services.chat_runtime.agent_chat_stream", new=fake_gen):
        runtime = detach_turn(
            pool=pool,
            session_id=session["id"],
            turn_id=turn_id,
            agent_slug="content-orchestrator",
            history=[],
        )
        queue = runtime.subscribe()
        # Read one event then unsubscribe
        first = await queue.get()
        assert first["type"] == "delta"
        runtime.unsubscribe(queue)

        await runtime.task

    msgs = await cs.get_messages(pool, session["id"])
    assistant = msgs[1]
    assert assistant["status"] == "complete"
    assert assistant["content"] == "ABC"


@pytest.mark.asyncio
async def test_runtime_records_failure_on_exception():
    pool = await get_pool()
    session = await cs.create_session(pool, "content-orchestrator")
    turn_id, _ = await cs.append_user_message_and_prepare_turn(pool, session["id"], "hi")

    async def boom(_agent_slug, _history):
        raise RuntimeError("upstream timeout")
        yield  # pragma: no cover  (make this an async generator type)

    with patch("app.services.chat_runtime.agent_chat_stream", new=boom):
        runtime = detach_turn(
            pool=pool,
            session_id=session["id"],
            turn_id=turn_id,
            agent_slug="content-orchestrator",
            history=[],
        )
        await runtime.task

    msgs = await cs.get_messages(pool, session["id"])
    assistant = msgs[1]
    assert assistant["status"] == "failed"
    assert assistant["error"] and "upstream timeout" in assistant["error"]


@pytest.mark.asyncio
async def test_activity_is_persisted_with_tool_lifecycle():
    pool = await get_pool()
    session = await cs.create_session(pool, "content-orchestrator")
    turn_id, _ = await cs.append_user_message_and_prepare_turn(pool, session["id"], "hi")

    events = [
        {"type": "tool_call_started", "name": "create_post", "args": {}},
        {"type": "tool_call_completed", "name": "create_post", "ok": True, "result_summary": "{ok}"},
        {"type": "delta", "text": "Done."},
        {"type": "done", "answer": "Done.", "tool_used": "create_post"},
    ]
    fake_gen = await _fake_stream(events)
    with patch("app.services.chat_runtime.agent_chat_stream", new=fake_gen):
        runtime = detach_turn(
            pool=pool,
            session_id=session["id"],
            turn_id=turn_id,
            agent_slug="content-orchestrator",
            history=[],
        )
        await runtime.task

    msgs = await cs.get_messages(pool, session["id"])
    assistant = msgs[1]
    assert assistant["tool_used"] == "create_post"
    assert len(assistant["activity"]) == 1
    line = assistant["activity"][0]
    assert line["kind"] == "tool"
    assert line["status"] == "ok"
    assert line["label"] == "Calling create_post"
