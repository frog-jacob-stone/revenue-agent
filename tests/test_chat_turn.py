"""End-to-end tests for the single-front-door chat-turn module.

Replaces the prior `test_chat_stream.py` (loop only) and `test_chat_runtime.py`
(detached runtime) — both surfaces are now exercised through `chat_turn.py`.

Two layers of testing:
  1. `_stream_llm_turn` — drive the OpenAI tool-call loop with a fake client.
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
from app.models.workflows import TraceEvent
from app.orchestrator import events as evt_const
from app.services import chat_sessions as cs
from app.services.chat_turn import (
    FRONT_DOOR_SLUG,
    _stream_llm_turn,
    get_active,
    start_turn,
)


# ── Fake OpenAI streaming chunks ────────────────────────────────────────────


class _FakeFunctionDelta:
    def __init__(self, name: str | None = None, arguments: str | None = None) -> None:
        self.name = name
        self.arguments = arguments


class _FakeToolCallDelta:
    def __init__(self, index: int, id: str | None = None, function: _FakeFunctionDelta | None = None) -> None:
        self.index = index
        self.id = id
        self.function = function


class _FakeDelta:
    def __init__(
        self,
        content: str | None = None,
        tool_calls: list[_FakeToolCallDelta] | None = None,
    ) -> None:
        self.content = content
        self.tool_calls = tool_calls


class _FakeChoice:
    def __init__(self, delta: _FakeDelta, finish_reason: str | None = None) -> None:
        self.delta = delta
        self.finish_reason = finish_reason


class _FakeChunk:
    def __init__(self, choices: list[_FakeChoice]) -> None:
        self.choices = choices


def _chunk_text(text: str, finish: str | None = None) -> _FakeChunk:
    return _FakeChunk([_FakeChoice(_FakeDelta(content=text), finish_reason=finish)])


def _chunk_tool_call(
    *, idx: int, id: str | None, name: str | None, arguments: str | None, finish: str | None
) -> _FakeChunk:
    tc = _FakeToolCallDelta(
        index=idx, id=id, function=_FakeFunctionDelta(name=name, arguments=arguments)
    )
    return _FakeChunk([_FakeChoice(_FakeDelta(tool_calls=[tc]), finish_reason=finish)])


async def _async_iter(chunks: list[_FakeChunk]):
    for c in chunks:
        yield c


class _FakeOpenAIClient:
    """Replays a queue of pre-built streams, one per `create()` call."""

    def __init__(self, streams: list[list[_FakeChunk]]) -> None:
        self._streams = list(streams)

        class _Chat:
            def __init__(_self_inner) -> None:
                _self_inner.completions = self  # type: ignore[assignment]

        self.chat = _Chat()

    async def create(self, **_kwargs):
        if not self._streams:
            raise RuntimeError("no more fake streams queued")
        return _async_iter(self._streams.pop(0))


# ── _stream_llm_turn (loop) ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stream_emits_deltas_and_done_for_text_only_response():
    fake = _FakeOpenAIClient(
        streams=[
            [
                _chunk_text("Hello"),
                _chunk_text(", "),
                _chunk_text("world", finish="stop"),
            ]
        ]
    )

    with patch("app.services.chat_turn.get_client", return_value=fake), \
         patch("app.services.chat_turn.fire_and_forget_write", new=lambda **_: None):
        out: list[dict[str, Any]] = []
        async for evt in _stream_llm_turn([{"role": "user", "content": "hi"}]):
            out.append(evt)

    types = [e["type"] for e in out]
    assert types == ["delta", "delta", "delta", "done"]
    assert "".join(e["text"] for e in out if e["type"] == "delta") == "Hello, world"
    final = out[-1]
    assert final["answer"] == "Hello, world"
    assert final["tool_used"] is None


@pytest.mark.asyncio
async def test_stream_emits_tool_lifecycle_and_workflow_events():
    """A tool call that spawns a workflow produces the full event sequence,
    including workflow_started + workflow_event lines tailed from audit_log."""

    args_json = json.dumps({"date_recognized": "2026-05-01"})
    fake = _FakeOpenAIClient(
        streams=[
            [
                _chunk_tool_call(
                    idx=0, id="call_1", name="trigger_revenue_recognition",
                    arguments=None, finish=None,
                ),
                _chunk_tool_call(
                    idx=0, id=None, name=None, arguments=args_json, finish="tool_calls",
                ),
            ],
            [_chunk_text("Triggered.", finish="stop")],
        ]
    )

    wf_id = uuid.uuid4()

    async def fake_runner_start_bg(*_args, **_kwargs):
        async def _noop():
            return None
        return wf_id, asyncio.create_task(_noop())

    import datetime as _dt
    fake_events = [
        TraceEvent(id=1, event_type=evt_const.NODE_ENTERED, occurred_at=_dt.datetime.now(),
                   actor="orchestrator", payload={"node": "compute_entries"}),
        TraceEvent(id=2, event_type=evt_const.AGENT_INVOKED, occurred_at=_dt.datetime.now(),
                   actor="orchestrator", payload={"agent_slug": "revenue-recognition"}),
        TraceEvent(id=3, event_type=evt_const.AGENT_COMPLETED, occurred_at=_dt.datetime.now(),
                   actor="orchestrator", payload={"agent_slug": "revenue-recognition", "total_tokens": 1234}),
        TraceEvent(id=4, event_type=evt_const.NODE_EXITED, occurred_at=_dt.datetime.now(),
                   actor="orchestrator", payload={"node": "compute_entries"}),
        TraceEvent(id=5, event_type=evt_const.WORKFLOW_COMPLETED, occurred_at=_dt.datetime.now(),
                   actor="orchestrator", payload={}),
    ]

    async def fake_tail(_pool, _wf_id, **kwargs):
        include = kwargs.get("include_subagents", True)
        for ev in fake_events:
            if not include and ev.event_type in {
                evt_const.AGENT_INVOKED, evt_const.AGENT_COMPLETED, evt_const.AGENT_FAILED,
            }:
                continue
            yield ev

    with patch("app.services.chat_turn.get_client", return_value=fake), \
         patch("app.services.chat_turn.fire_and_forget_write", new=lambda **_: None), \
         patch("app.orchestrator.runner.runner.start_in_background",
               new=AsyncMock(side_effect=fake_runner_start_bg)), \
         patch("app.services.audit_tail.tail_workflow_events", new=fake_tail), \
         patch("app.db.get_pool", new=AsyncMock(return_value=None)):
        out: list[dict[str, Any]] = []
        async for ev in _stream_llm_turn([{"role": "user", "content": "run rev rec"}]):
            out.append(ev)

    types = [e["type"] for e in out]
    assert types[0] == "tool_call_started"
    assert "workflow_started" in types
    assert types.count("workflow_event") >= 3
    assert types[-1] == "done"

    final = out[-1]
    assert final["answer"] == "Triggered."
    assert final["tool_used"] == "trigger_revenue_recognition"


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
