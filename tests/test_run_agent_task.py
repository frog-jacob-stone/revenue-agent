"""Tests for run_agent_task — the ReAct loop primitive.

All tests use the test DB. Loop-behaviour tests mock `_agent_class_for_slug`
and `_agent_id_for_slug` to avoid depending on agent-seed state; audit-event
tests use the real BDR slug which is always seeded.
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.agents.base import Agent
from app.agents.tools.base import ToolContext, ToolDefinition
from app.integrations.llm import LlmResponse, ToolCall, use_provider
from app.orchestrator import NodeContext, events
from app.orchestrator.agent_invoke import run_agent_task
from tests.fakes.llm import FakeProvider

# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_tool(name: str, return_value: Any) -> ToolDefinition:
    async def _execute(ctx: ToolContext, **_: Any) -> Any:
        return return_value

    return ToolDefinition(
        name=name,
        description=f"Test tool: {name}",
        input_schema={"type": "object", "properties": {}, "required": []},
        execute=_execute,
    )


def _fake_agent_cls(slug: str, tools: tuple[ToolDefinition, ...]) -> type[Agent]:
    return type(
        "_FakeAgent",
        (Agent,),
        {
            "slug": slug,
            "name": "Fake",
            "description": "",
            "model": "gpt-4o-mini",
            "get_system_prompt": lambda self: "You are a test agent.",
            "allowed_tools": tools,
        },
    )


def _tool_call(name: str, args: dict | None = None) -> LlmResponse:
    return LlmResponse(
        text="",
        tool_calls=[ToolCall(id="call-1", name=name, arguments=json.dumps(args or {}))],
        finish_reason="tool_calls",
    )


def _final(text: str) -> LlmResponse:
    return LlmResponse(text=text, finish_reason="stop")


# ── Core loop behaviour ───────────────────────────────────────────────────────


async def test_single_tool_call_then_final_answer():
    """Tool call on first turn, final answer on second — returns final text."""
    tool = _make_tool("my_tool", {"result": "enriched-data"})
    agent_cls = _fake_agent_cls("bdr", (tool,))
    provider = FakeProvider(completions=[_tool_call("my_tool"), _final("Draft done.")])

    with patch(
        "app.orchestrator.agent_invoke._agent_class_for_slug",
        return_value=agent_cls,
    ), patch(
        "app.orchestrator.agent_invoke._agent_id_for_slug",
        new=AsyncMock(return_value=None),
    ), use_provider(provider):
        result = await run_agent_task("bdr", "Draft a reply.", ctx=None)

    assert result["text"] == "Draft done."
    # Two LLM calls: one that requested a tool, one that produced the answer.
    assert len(provider.requests) == 2
    # Second request must include a "tool" role message.
    second_msgs = provider.requests[1]["messages"]
    assert any(m["role"] == "tool" for m in second_msgs)


async def test_tool_result_passed_to_next_turn():
    """The tool's return value must appear in the messages of the next LLM call."""
    captured: list[list[dict]] = []

    async def _capture(ctx: ToolContext, **_: Any) -> Any:
        from app.agents.tools.base import Done
        return Done({"key": "value-from-tool"})

    tool = ToolDefinition(
        name="capture_tool",
        description="capture",
        input_schema={"type": "object", "properties": {}, "required": []},
        execute=_capture,
    )
    agent_cls = _fake_agent_cls("bdr", (tool,))

    def _respond(request: dict) -> LlmResponse:
        captured.append(request["messages"])
        if any(m.get("role") == "tool" for m in request["messages"]):
            return _final("Done.")
        return _tool_call("capture_tool")

    provider = FakeProvider(respond=_respond)

    with patch(
        "app.orchestrator.agent_invoke._agent_class_for_slug",
        return_value=agent_cls,
    ), patch(
        "app.orchestrator.agent_invoke._agent_id_for_slug",
        new=AsyncMock(return_value=None),
    ), use_provider(provider):
        await run_agent_task("bdr", "Go.", ctx=None)

    tool_msg = next(m for m in captured[1] if m.get("role") == "tool")
    assert json.loads(tool_msg["content"]) == {"key": "value-from-tool"}


async def test_no_tools_falls_back_to_single_turn():
    """Agents with no allowed_tools get a single-turn invoke_agent call."""
    agent_cls = _fake_agent_cls("bdr", ())
    provider = FakeProvider(completions=[_final("Single-turn answer.")])

    with patch(
        "app.orchestrator.agent_invoke._agent_class_for_slug",
        return_value=agent_cls,
    ), patch(
        "app.orchestrator.agent_invoke._agent_id_for_slug",
        new=AsyncMock(return_value=None),
    ), use_provider(provider):
        result = await run_agent_task("bdr", "Explain this.", ctx=None)

    assert result["text"] == "Single-turn answer."
    assert len(provider.requests) == 1


async def test_max_iterations_guard_raises():
    """A model that never stops calling tools hits the limit and raises."""
    tool = _make_tool("loop_tool", {"ok": True})
    agent_cls = _fake_agent_cls("bdr", (tool,))
    provider = FakeProvider(respond=lambda _: _tool_call("loop_tool"))

    with patch(
        "app.orchestrator.agent_invoke._agent_class_for_slug",
        return_value=agent_cls,
    ), patch(
        "app.orchestrator.agent_invoke._agent_id_for_slug",
        new=AsyncMock(return_value=None),
    ), use_provider(provider):
        with pytest.raises(RuntimeError, match="exceeded maximum iterations"):
            await run_agent_task("bdr", "Loop.", ctx=None, max_iterations=3)

    assert len(provider.requests) == 3


# ── Audit events (requires test DB) ──────────────────────────────────────────


async def test_emits_invoked_and_completed_on_success():
    from uuid import uuid4

    from app.db import get_pool

    pool = await get_pool()
    # Post-0022 the workflows table is gone. The workflow_id column on
    # audit_log is a plain UUID kept for historical lookups; pass a fresh
    # UUID so we can filter audit_log rows by it.
    wf_id = uuid4()
    provider = FakeProvider(completions=[_final("Draft email.")])

    with use_provider(provider):
        await run_agent_task("bdr", "Draft a reply.", NodeContext(workflow_id=wf_id))

    rows = await pool.fetch(
        "SELECT event_type FROM audit_log WHERE workflow_id = $1 ORDER BY occurred_at, id",
        wf_id,
    )
    event_types = [r["event_type"] for r in rows]
    assert events.AGENT_INVOKED in event_types
    assert events.AGENT_COMPLETED in event_types
    assert events.AGENT_FAILED not in event_types


async def test_emits_agent_failed_on_exception():
    from uuid import uuid4

    from app.db import get_pool

    pool = await get_pool()
    wf_id = uuid4()
    provider = FakeProvider(completions=[RuntimeError("llm exploded")])

    with use_provider(provider):
        with pytest.raises(RuntimeError, match="llm exploded"):
            await run_agent_task("bdr", "Fail.", NodeContext(workflow_id=wf_id))

    rows = await pool.fetch(
        "SELECT event_type FROM audit_log WHERE workflow_id = $1 ORDER BY occurred_at, id",
        wf_id,
    )
    event_types = [r["event_type"] for r in rows]
    assert events.AGENT_FAILED in event_types
    assert events.AGENT_COMPLETED not in event_types
