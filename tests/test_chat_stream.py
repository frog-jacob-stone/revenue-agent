"""Unit tests for the streaming chat service.

These tests stub the OpenAI client and the orchestrator runner so we can
verify the SSE event sequence without making network calls or running a
real LangGraph.
"""
import asyncio
import json
import uuid
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.models.workflows import TraceEvent
from app.orchestrator import events as evt_const
from app.services.chat import agent_chat_stream


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

    async def create(self, **_kwargs):  # called as client.chat.completions.create(...)
        if not self._streams:
            raise RuntimeError("no more fake streams queued")
        return _async_iter(self._streams.pop(0))


# ── Tests ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stream_emits_deltas_and_done_for_text_only_response():
    """No tool call: stream produces delta events then done."""
    fake = _FakeOpenAIClient(
        streams=[
            [
                _chunk_text("Hello"),
                _chunk_text(", "),
                _chunk_text("world", finish="stop"),
            ]
        ]
    )

    with patch("app.services.chat.get_client", return_value=fake):
        out: list[dict[str, Any]] = []
        async for evt in agent_chat_stream("revenue-recognition", [{"role": "user", "content": "hi"}]):
            out.append(evt)

    types = [e["type"] for e in out]
    assert types == ["delta", "delta", "delta", "done"]
    assert "".join(e["text"] for e in out if e["type"] == "delta") == "Hello, world"
    final = out[-1]
    assert final["answer"] == "Hello, world"
    assert final["tool_used"] is None


@pytest.mark.asyncio
async def test_stream_emits_tool_lifecycle_and_workflow_events(monkeypatch):
    """A tool call that spawns a workflow produces the full event sequence,
    including workflow_started and workflow_event lines tailed from audit_log."""

    args_json = json.dumps({"date_recognized": "2026-05-01"})
    fake = _FakeOpenAIClient(
        streams=[
            # First LLM turn: emit a tool call
            [
                _chunk_tool_call(
                    idx=0, id="call_1", name="trigger_revenue_recognition",
                    arguments=None, finish=None,
                ),
                _chunk_tool_call(
                    idx=0, id=None, name=None, arguments=args_json, finish="tool_calls",
                ),
            ],
            # Second LLM turn: text response after seeing the tool result
            [_chunk_text("Triggered.", finish="stop")],
        ]
    )

    wf_id = uuid.uuid4()

    async def fake_runner_start_bg(*_args, **_kwargs):
        async def _noop():
            return None
        return wf_id, asyncio.create_task(_noop())

    # Synthetic audit events: one node, one sub-agent pair, one terminal.
    fake_events = [
        TraceEvent(
            id=1, event_type=evt_const.NODE_ENTERED, occurred_at=__import__("datetime").datetime.now(),
            actor="orchestrator", payload={"node": "compute_entries"},
        ),
        TraceEvent(
            id=2, event_type=evt_const.AGENT_INVOKED, occurred_at=__import__("datetime").datetime.now(),
            actor="orchestrator", payload={"agent_slug": "revenue-recognition"},
        ),
        TraceEvent(
            id=3, event_type=evt_const.AGENT_COMPLETED, occurred_at=__import__("datetime").datetime.now(),
            actor="orchestrator", payload={"agent_slug": "revenue-recognition", "total_tokens": 1234},
        ),
        TraceEvent(
            id=4, event_type=evt_const.NODE_EXITED, occurred_at=__import__("datetime").datetime.now(),
            actor="orchestrator", payload={"node": "compute_entries"},
        ),
        TraceEvent(
            id=5, event_type=evt_const.WORKFLOW_COMPLETED, occurred_at=__import__("datetime").datetime.now(),
            actor="orchestrator", payload={},
        ),
    ]

    async def fake_tail(_pool, _wf_id, **kwargs):
        include = kwargs.get("include_subagents", True)
        for ev in fake_events:
            if not include and ev.event_type in {
                evt_const.AGENT_INVOKED, evt_const.AGENT_COMPLETED, evt_const.AGENT_FAILED,
            }:
                continue
            yield ev

    with patch("app.services.chat.get_client", return_value=fake), \
         patch("app.orchestrator.runner.runner.start_in_background",
               new=AsyncMock(side_effect=fake_runner_start_bg)), \
         patch("app.services.audit_tail.tail_workflow_events", new=fake_tail), \
         patch("app.db.get_pool", new=AsyncMock(return_value=None)):
        out: list[dict[str, Any]] = []
        async for ev in agent_chat_stream(
            "revenue-recognition", [{"role": "user", "content": "run rev rec"}]
        ):
            out.append(ev)

    types = [e["type"] for e in out]

    # Sanity: tool call + workflow + at least one node + a done at the end
    assert types[0] == "tool_call_started"
    assert "workflow_started" in types
    assert types.count("workflow_event") >= 3  # node.entered, node.exited, workflow.completed (+ subagent rows by default)
    assert types[-1] == "done"

    # The sub-agent toggle is ON by default → we must see agent.invoked/completed
    workflow_event_types = [e.get("event_type") for e in out if e["type"] == "workflow_event"]
    assert evt_const.AGENT_INVOKED in workflow_event_types
    assert evt_const.AGENT_COMPLETED in workflow_event_types

    # tool_call_completed comes after workflow events, before done
    tool_completed_idx = types.index("tool_call_completed")
    last_workflow_idx = max(i for i, t in enumerate(types) if t == "workflow_event")
    assert last_workflow_idx < tool_completed_idx

    final = out[-1]
    assert final["answer"] == "Triggered."
    assert final["tool_used"] == "trigger_revenue_recognition"


@pytest.mark.asyncio
async def test_subagent_toggle_filters_agent_events(monkeypatch):
    """When chat_stream_show_subagents=False, agent.invoked/completed do not reach the stream."""

    from app.config import settings
    monkeypatch.setattr(settings, "chat_stream_show_subagents", False)

    fake = _FakeOpenAIClient(
        streams=[
            [
                _chunk_tool_call(
                    idx=0, id="call_1", name="trigger_revenue_recognition",
                    arguments="{}", finish="tool_calls",
                ),
            ],
            [_chunk_text("ok", finish="stop")],
        ]
    )

    async def fake_runner_start_bg(*_args, **_kwargs):
        async def _noop():
            return None
        return uuid.uuid4(), asyncio.create_task(_noop())

    fake_events = [
        TraceEvent(id=1, event_type=evt_const.NODE_ENTERED, occurred_at=__import__("datetime").datetime.now(),
                   actor="o", payload={"node": "compute_entries"}),
        TraceEvent(id=2, event_type=evt_const.AGENT_INVOKED, occurred_at=__import__("datetime").datetime.now(),
                   actor="o", payload={"agent_slug": "revenue-recognition"}),
        TraceEvent(id=3, event_type=evt_const.AGENT_COMPLETED, occurred_at=__import__("datetime").datetime.now(),
                   actor="o", payload={"agent_slug": "revenue-recognition"}),
        TraceEvent(id=4, event_type=evt_const.NODE_EXITED, occurred_at=__import__("datetime").datetime.now(),
                   actor="o", payload={"node": "compute_entries"}),
        TraceEvent(id=5, event_type=evt_const.WORKFLOW_COMPLETED, occurred_at=__import__("datetime").datetime.now(),
                   actor="o", payload={}),
    ]

    async def fake_tail(_pool, _wf_id, **kwargs):
        include = kwargs.get("include_subagents", True)
        for ev in fake_events:
            if not include and ev.event_type in {
                evt_const.AGENT_INVOKED, evt_const.AGENT_COMPLETED, evt_const.AGENT_FAILED,
            }:
                continue
            yield ev

    with patch("app.services.chat.get_client", return_value=fake), \
         patch("app.orchestrator.runner.runner.start_in_background",
               new=AsyncMock(side_effect=fake_runner_start_bg)), \
         patch("app.services.audit_tail.tail_workflow_events", new=fake_tail), \
         patch("app.db.get_pool", new=AsyncMock(return_value=None)):
        out: list[dict[str, Any]] = []
        async for ev in agent_chat_stream(
            "revenue-recognition", [{"role": "user", "content": "run rev rec"}]
        ):
            out.append(ev)

    workflow_event_types = [e.get("event_type") for e in out if e["type"] == "workflow_event"]
    assert evt_const.AGENT_INVOKED not in workflow_event_types
    assert evt_const.AGENT_COMPLETED not in workflow_event_types
    # Node lifecycle still streams
    assert evt_const.NODE_ENTERED in workflow_event_types
    assert evt_const.NODE_EXITED in workflow_event_types
