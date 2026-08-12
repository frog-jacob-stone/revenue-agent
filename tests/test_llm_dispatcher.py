"""Tests for the LLM dispatcher in isolation.

Uses `FakeProvider` scoped via `use_provider()` — no real OpenAI traffic.
Asserts on `llm_calls` rows after each dispatch.
"""
from __future__ import annotations

import pytest

from app.db import get_pool
from app.integrations.llm import (
    Attribution,
    LlmResponse,
    StreamDelta,
    ToolCall,
    dispatch,
    dispatch_stream,
    use_provider,
)
from tests.fakes.llm import FakeProvider


def _attribution(**overrides) -> Attribution:
    defaults = dict(agent_slug="test-agent", purpose="test.dispatch")
    defaults.update(overrides)
    return Attribution(**defaults)


async def _last_llm_call_row(purpose: str) -> dict:
    pool = await get_pool()
    row = await pool.fetchrow(
        "SELECT * FROM llm_calls WHERE purpose = $1 ORDER BY started_at DESC LIMIT 1",
        purpose,
    )
    assert row is not None, f"No llm_calls row found for purpose={purpose}"
    return dict(row)


# ── Non-streaming ───────────────────────────────────────────────────────────


async def test_dispatch_success_writes_one_row():
    fake = FakeProvider(
        completions=[
            LlmResponse(
                text="hello",
                tool_calls=[],
                finish_reason="stop",
                prompt_tokens=3,
                completion_tokens=2,
            )
        ]
    )

    with use_provider(fake):
        result = await dispatch(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": "hi"}],
            attribution=_attribution(purpose="test.success"),
        )


    assert result.text == "hello"
    assert result.finish_reason == "stop"
    row = await _last_llm_call_row("test.success")
    assert row["agent_slug"] == "test-agent"
    assert row["provider"] == "fake"
    assert row["status"] == "ok"
    assert row["streamed"] is False
    assert row["prompt_tokens"] == 3
    assert row["completion_tokens"] == 2
    assert row["total_tokens"] == 5


async def test_dispatch_error_writes_one_row_then_raises():
    fake = FakeProvider(completions=[RuntimeError("network ded")])

    with use_provider(fake):
        with pytest.raises(RuntimeError, match="network ded"):
            await dispatch(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": "hi"}],
                attribution=_attribution(purpose="test.error"),
            )


    row = await _last_llm_call_row("test.error")
    assert row["status"] == "error"
    assert "network ded" in (row["error"] or "")
    assert row["streamed"] is False


# ── Streaming ───────────────────────────────────────────────────────────────


async def test_dispatch_stream_yields_deltas_then_terminal_response():
    terminal = LlmResponse(
        text="hi there",
        tool_calls=[ToolCall(id="tc1", name="lookup", arguments='{"q":"x"}')],
        finish_reason="stop",
        prompt_tokens=5,
        completion_tokens=4,
    )
    fake = FakeProvider(
        streams=[
            [
                StreamDelta(text="hi "),
                StreamDelta(text="there"),
                StreamDelta(
                    tool_call_index=0,
                    tool_call_id="tc1",
                    tool_call_name_delta="lookup",
                ),
                terminal,
            ]
        ]
    )

    seen: list = []
    with use_provider(fake):
        async for evt in dispatch_stream(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": "hi"}],
            attribution=_attribution(purpose="test.stream"),
        ):
            seen.append(evt)


    # Three deltas, then terminal LlmResponse.
    assert len(seen) == 4
    assert all(isinstance(e, StreamDelta) for e in seen[:3])
    assert isinstance(seen[-1], LlmResponse)
    assert seen[-1].finish_reason == "stop"

    row = await _last_llm_call_row("test.stream")
    assert row["streamed"] is True
    assert row["status"] == "ok"
    assert row["prompt_tokens"] == 5


async def test_dispatch_stream_error_writes_row_then_raises():
    fake = FakeProvider(streams=[RuntimeError("stream blew up")])

    with use_provider(fake):
        with pytest.raises(RuntimeError, match="stream blew up"):
            async for _ in dispatch_stream(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": "hi"}],
                attribution=_attribution(purpose="test.stream_error"),
            ):
                pass


    row = await _last_llm_call_row("test.stream_error")
    assert row["status"] == "error"
    assert row["streamed"] is True
    assert "stream blew up" in (row["error"] or "")


# ── use_provider scoping ────────────────────────────────────────────────────


async def test_use_provider_nests_correctly():
    outer = FakeProvider(
        completions=[LlmResponse(text="outer", finish_reason="stop")]
    )
    inner = FakeProvider(
        completions=[LlmResponse(text="inner", finish_reason="stop")]
    )

    with use_provider(outer):
        with use_provider(inner):
            r1 = await dispatch(
                model="m",
                messages=[{"role": "user", "content": "x"}],
                attribution=_attribution(purpose="test.nest_inner"),
            )
        r2 = await dispatch(
            model="m",
            messages=[{"role": "user", "content": "x"}],
            attribution=_attribution(purpose="test.nest_outer"),
        )


    assert r1.text == "inner"
    assert r2.text == "outer"
    assert len(outer.requests) == 1
    assert len(inner.requests) == 1


# ── Attribution required ────────────────────────────────────────────────────


async def test_attribution_is_required():
    with pytest.raises(TypeError):
        await dispatch(  # type: ignore[call-arg]
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": "hi"}],
        )
