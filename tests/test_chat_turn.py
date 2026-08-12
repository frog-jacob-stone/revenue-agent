"""End-to-end tests for the single-front-door chat-turn module.

Replaces the prior `test_chat_stream.py` (loop only) and `test_chat_runtime.py`
(detached runtime) — both surfaces are now exercised through `chat_turn.py`.

Two layers of testing:
  1. `_stream_llm_turn` — drive the LLM tool-call loop with a fake provider.
  2. `start_turn` — drive the full detached runtime + persistence.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.db import get_pool
from app.integrations.llm import LlmResponse, StreamDelta, ToolCall, use_provider
from app.orchestrator import events as evt_const
from app.services import chat_sessions as cs
from app.services.chat_turn import (
    FRONT_DOOR_SLUG,
    _stream_llm_turn,
    get_active,
    start_turn,
)
from tests.fakes.llm import FakeProvider


# ── _stream_llm_turn (loop) ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stream_emits_deltas_and_done_for_text_only_response():
    provider = FakeProvider(
        streams=[
            [
                StreamDelta(text="Hello"),
                StreamDelta(text=", "),
                StreamDelta(text="world"),
                LlmResponse(text="Hello, world", finish_reason="stop"),
            ]
        ]
    )

    with use_provider(provider):
        out: list[dict[str, Any]] = []
        async for evt in _stream_llm_turn([{"role": "user", "content": "hi"}]):
            out.append(evt)

    types = [e["type"] for e in out]
    assert types == ["delta", "delta", "delta", "done"]
    assert "".join(e["text"] for e in out if e["type"] == "delta") == "Hello, world"
    final = out[-1]
    assert final["answer"] == "Hello, world"
    assert final["tool_used"] is None


# test_stream_emits_tool_lifecycle_and_workflow_events deleted in plan 18 —
# no production tool spawns a workflow anymore (rev_rec was the last). Phase 4
# removes the workflow-forwarding mechanism entirely.


# ── start_turn + TurnRuntime (detached + persistence) ───────────────────────


async def _fake_stream_gen(events: list[dict[str, Any]]):
    async def gen(_history):
        for e in events:
            await asyncio.sleep(0)
            yield e
    return gen


@pytest.mark.asyncio
async def test_start_turn_persists_without_subscriber():
    pool = await get_pool()
    session = await cs.create_session(pool, FRONT_DOOR_SLUG)

    events = [
        {"type": "delta", "text": "Hello"},
        {"type": "delta", "text": ", world"},
        {"type": "done", "answer": "Hello, world", "tool_used": None},
    ]
    fake_gen = await _fake_stream_gen(events)
    with patch("app.services.chat_turn._stream_llm_turn", new=fake_gen):
        runtime = await start_turn(pool, session["id"], "hi")
        await runtime.task

    msgs = await cs.get_messages(pool, session["id"])
    # msgs[0] = user, msgs[1] = assistant placeholder → finalized
    assert msgs[0]["role"] == "user"
    assert msgs[0]["content"] == "hi"
    assistant = msgs[1]
    assert assistant["role"] == "assistant"
    assert assistant["status"] == "complete"
    assert assistant["content"] == "Hello, world"
    assert assistant["completed_at"] is not None
    assert get_active(runtime.turn_id) is None


@pytest.mark.asyncio
async def test_start_turn_pushes_events_to_subscriber():
    pool = await get_pool()
    session = await cs.create_session(pool, FRONT_DOOR_SLUG)

    events = [
        {"type": "delta", "text": "Hi"},
        {"type": "tool_call_started", "name": "create_post", "args": {}},
        {"type": "tool_call_completed", "name": "create_post", "ok": True, "result_summary": "{ok}"},
        {"type": "done", "answer": "Hi", "tool_used": "create_post"},
    ]
    fake_gen = await _fake_stream_gen(events)
    with patch("app.services.chat_turn._stream_llm_turn", new=fake_gen):
        runtime = await start_turn(pool, session["id"], "hi")
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
    pool = await get_pool()
    session = await cs.create_session(pool, FRONT_DOOR_SLUG)

    events = [
        {"type": "delta", "text": "A"},
        {"type": "delta", "text": "B"},
        {"type": "delta", "text": "C"},
        {"type": "done", "answer": "ABC", "tool_used": None},
    ]
    fake_gen = await _fake_stream_gen(events)
    with patch("app.services.chat_turn._stream_llm_turn", new=fake_gen):
        runtime = await start_turn(pool, session["id"], "hi")
        queue = runtime.subscribe()
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
    session = await cs.create_session(pool, FRONT_DOOR_SLUG)

    async def boom(_history):
        raise RuntimeError("upstream timeout")
        yield  # pragma: no cover

    with patch("app.services.chat_turn._stream_llm_turn", new=boom):
        runtime = await start_turn(pool, session["id"], "hi")
        await runtime.task

    msgs = await cs.get_messages(pool, session["id"])
    assistant = msgs[1]
    assert assistant["status"] == "failed"
    assert assistant["error"] and "upstream timeout" in assistant["error"]


@pytest.mark.asyncio
async def test_activity_is_persisted_with_tool_lifecycle():
    pool = await get_pool()
    session = await cs.create_session(pool, FRONT_DOOR_SLUG)

    events = [
        {"type": "tool_call_started", "name": "create_post", "args": {}},
        {"type": "tool_call_completed", "name": "create_post", "ok": True, "result_summary": "{ok}"},
        {"type": "delta", "text": "Done."},
        {"type": "done", "answer": "Done.", "tool_used": "create_post"},
    ]
    fake_gen = await _fake_stream_gen(events)
    with patch("app.services.chat_turn._stream_llm_turn", new=fake_gen):
        runtime = await start_turn(pool, session["id"], "hi")
        await runtime.task

    msgs = await cs.get_messages(pool, session["id"])
    assistant = msgs[1]
    assert assistant["tool_used"] == "create_post"
    assert len(assistant["activity"]) == 1
    line = assistant["activity"][0]
    assert line["kind"] == "tool"
    assert line["status"] == "ok"
    assert line["label"] == "Calling create_post"


@pytest.mark.asyncio
async def test_chat_turn_writes_started_and_completed_audit_events():
    """A successful turn emits CHAT_TURN_STARTED + CHAT_TURN_COMPLETED with
    matching turn_id in the payload."""
    pool = await get_pool()
    session = await cs.create_session(pool, FRONT_DOOR_SLUG)

    events_list = [
        {"type": "delta", "text": "ok"},
        {"type": "done", "answer": "ok", "tool_used": None},
    ]
    fake_gen = await _fake_stream_gen(events_list)
    with patch("app.services.chat_turn._stream_llm_turn", new=fake_gen):
        runtime = await start_turn(pool, session["id"], "hi")
        await runtime.task

    rows = await pool.fetch(
        "SELECT event_type, payload FROM audit_log WHERE event_type LIKE 'chat.turn.%' "
        "ORDER BY id"
    )
    types = [r["event_type"] for r in rows]
    assert types == [evt_const.CHAT_TURN_STARTED, evt_const.CHAT_TURN_COMPLETED]
    assert rows[0]["payload"]["turn_id"] == str(runtime.turn_id)
    assert rows[1]["payload"]["turn_id"] == str(runtime.turn_id)
    assert rows[1]["payload"]["status"] == "complete"


@pytest.mark.asyncio
async def test_chat_turn_writes_failed_audit_event_on_error():
    """A turn that raises emits CHAT_TURN_FAILED with the error in the payload."""
    pool = await get_pool()
    session = await cs.create_session(pool, FRONT_DOOR_SLUG)

    async def boom(_history):
        raise RuntimeError("upstream timeout")
        yield  # pragma: no cover

    with patch("app.services.chat_turn._stream_llm_turn", new=boom):
        runtime = await start_turn(pool, session["id"], "hi")
        await runtime.task

    rows = await pool.fetch(
        "SELECT event_type, payload FROM audit_log WHERE event_type LIKE 'chat.turn.%' "
        "ORDER BY id"
    )
    types = [r["event_type"] for r in rows]
    assert types == [evt_const.CHAT_TURN_STARTED, evt_const.CHAT_TURN_FAILED]
    assert rows[1]["payload"]["status"] == "failed"
    assert "upstream timeout" in rows[1]["payload"]["error"]


@pytest.mark.asyncio
async def test_first_user_message_sets_session_title():
    pool = await get_pool()
    session = await cs.create_session(pool, FRONT_DOOR_SLUG)
    assert session["title"] == "New chat"  # placeholder

    events = [{"type": "done", "answer": "ok", "tool_used": None}]
    fake_gen = await _fake_stream_gen(events)
    with patch("app.services.chat_turn._stream_llm_turn", new=fake_gen):
        runtime = await start_turn(pool, session["id"], "What was top revenue last month?")
        await runtime.task

    refreshed = await cs.get_session(pool, session["id"])
    assert refreshed["title"] == "What was top revenue last month?"
