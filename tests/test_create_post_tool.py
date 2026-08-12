"""End-to-end tests for the inlined `create_post` tool (ADR-0002, plan 16).

Replaces the deleted `test_content_creation_graph.py`. Dispatches LLM
responses by system-prompt content (each of the three prompts has a
distinguishing phrase: "content strategist", "LinkedIn ghostwriter",
"personal writing coach").

Three scenarios:
  - happy: voice passes on attempt 1 → social_posts.status='ready'
  - retry: voice fails on attempt 1, passes on attempt 2
  - exhausted: voice fails all 3 attempts → status='needs_revision'
"""
from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

from app.db import get_pool
from app.integrations.llm import LlmResponse, use_provider
from app.services import social_posts as svc
from app.agents.tools.base import Done, ProgressEmitter, ToolContext
from app.agents.tools.content.create_post import _create_post
from tests.fakes.llm import FakeProvider


_IDEA_JSON = json.dumps({
    "idea_title": "AI agents fail in sales when they over-automate",
    "core_angle": "Automation without judgement is a trap",
    "target_reader": "Revenue leaders",
    "main_point": "Keep the human in the loop on writes",
    "suggested_post_type": "opinion",
})


def _draft_response(text: str) -> LlmResponse:
    return LlmResponse(
        text=json.dumps({
            "post_text": text,
            "hook": "Direct opener",
            "cta": "Worth a chat?",
            "estimated_strength_score": 7.5,
            "notes": "",
        }),
        finish_reason="stop",
    )


def _voice_response(*, passed: bool, revised_text: str) -> LlmResponse:
    return LlmResponse(
        text=json.dumps({
            "passed_voice_review": passed,
            "voice_score": 8.5 if passed else 4.0,
            "issues_found": [] if passed else ["too generic"],
            "suggested_changes": [] if passed else ["be more specific"],
            "revised_post_text": revised_text,
        }),
        finish_reason="stop",
    )


def _make_provider(*, voice_results: list[bool]) -> FakeProvider:
    """`voice_results` is FIFO-popped on each voice_review call. interpret_brief
    and draft_post return canned shapes."""
    voice = list(voice_results)

    def respond(request: dict[str, Any]) -> LlmResponse:
        messages = request.get("messages", [])
        system = next(
            (m.get("content", "") for m in messages if m.get("role") == "system"), ""
        )
        if "content strategist" in system:
            return LlmResponse(text=_IDEA_JSON, finish_reason="stop")
        if "LinkedIn ghostwriter" in system:
            # Differentiate first draft from retry by checking for the rejection marker.
            user_msg = next(
                (m.get("content", "") for m in messages if m.get("role") == "user"), ""
            )
            if "PREVIOUS DRAFT WAS REJECTED" in user_msg:
                return _draft_response("Revised post text addressing feedback.")
            return _draft_response("Initial post text.")
        if "personal writing coach" in system:
            passed = voice.pop(0) if voice else False
            # On pass, return a slightly revised version to verify it lands in DB.
            text = (
                "Polished post text after voice pass."
                if passed
                else "Initial post text."
            )
            return _voice_response(passed=passed, revised_text=text)
        raise AssertionError(f"Unexpected system prompt: {system[:60]!r}")

    return FakeProvider(respond=respond)


async def _drain_progress(emitter: ProgressEmitter) -> list[dict[str, Any]]:
    """Drain whatever's currently in the emitter's queue (non-blocking)."""
    events: list[dict[str, Any]] = []
    while not emitter._queue.empty():  # noqa: SLF001 — internal but stable
        evt = emitter._queue.get_nowait()
        if evt is None:
            break
        events.append(evt)
    return events


def _ctx_with_progress() -> tuple[ToolContext, ProgressEmitter]:
    progress = ProgressEmitter()
    ctx = ToolContext(
        agent_id=uuid.UUID(int=0),
        agent_slug="chief-of-staff",
        progress=progress,
    )
    return ctx, progress


@pytest.mark.asyncio
async def test_voice_passes_first_attempt():
    provider = _make_provider(voice_results=[True])
    ctx, progress = _ctx_with_progress()

    with use_provider(provider):
        result = await _create_post(ctx, brief="why AI agents fail in sales")

    assert isinstance(result, Done)
    payload = result.payload
    assert payload["status"] == "ready"
    assert payload["post_text"] == "Polished post text after voice pass."
    assert payload["idea_title"] == "AI agents fail in sales when they over-automate"

    # DB reflects the final state
    pool = await get_pool()
    row = await svc.get_post(pool, uuid.UUID(payload["post_id"]))
    assert row is not None
    assert row["status"] == "ready"
    assert row["post_text"] == "Polished post text after voice pass."

    # Progress events: one started/completed per step, no retries.
    events = await _drain_progress(progress)
    steps_started = [e for e in events if e["type"] == "tool_step_started"]
    steps_completed = [e for e in events if e["type"] == "tool_step_completed"]
    assert [e["step"] for e in steps_started] == ["interpret_brief", "draft_post", "voice_review"]
    assert [e["step"] for e in steps_completed] == ["interpret_brief", "draft_post", "voice_review"]
    voice_completed = next(e for e in steps_completed if e["step"] == "voice_review")
    assert voice_completed["passed"] is True
    assert voice_completed["attempt"] == 1


@pytest.mark.asyncio
async def test_voice_retries_then_passes():
    provider = _make_provider(voice_results=[False, True])
    ctx, progress = _ctx_with_progress()

    with use_provider(provider):
        result = await _create_post(ctx, brief="why AI agents fail in sales")

    assert isinstance(result, Done)
    payload = result.payload
    assert payload["status"] == "ready"

    # Three LLM steps + one extra draft + one extra voice review = 5 calls total
    assert len(provider.requests) == 5

    # Second draft request must carry the prior feedback
    drafts = [
        r for r in provider.requests
        if any(
            "LinkedIn ghostwriter" in (m.get("content") or "")
            for m in r.get("messages", [])
            if m.get("role") == "system"
        )
    ]
    assert len(drafts) == 2
    retry_user_msg = next(
        m["content"] for m in drafts[1]["messages"] if m.get("role") == "user"
    )
    assert "PREVIOUS DRAFT WAS REJECTED" in retry_user_msg
    assert "too generic" in retry_user_msg

    # Progress events: voice_review fires twice with different attempts
    events = await _drain_progress(progress)
    voice_completed = [
        e for e in events
        if e["type"] == "tool_step_completed" and e["step"] == "voice_review"
    ]
    assert len(voice_completed) == 2
    assert voice_completed[0] == {
        "type": "tool_step_completed", "tool": "create_post",
        "step": "voice_review", "attempt": 1, "passed": False,
    }
    assert voice_completed[1] == {
        "type": "tool_step_completed", "tool": "create_post",
        "step": "voice_review", "attempt": 2, "passed": True,
    }


@pytest.mark.asyncio
async def test_voice_budget_exhausted_sets_needs_revision():
    provider = _make_provider(voice_results=[False, False, False])
    ctx, progress = _ctx_with_progress()

    with use_provider(provider):
        result = await _create_post(ctx, brief="generic post about something")

    assert isinstance(result, Done)
    payload = result.payload
    assert payload["status"] == "needs_revision"

    # DB reflects needs_revision
    pool = await get_pool()
    row = await svc.get_post(pool, uuid.UUID(payload["post_id"]))
    assert row is not None
    assert row["status"] == "needs_revision"

    # All three voice_review attempts fired with passed=False
    events = await _drain_progress(progress)
    voice_completed = [
        e for e in events
        if e["type"] == "tool_step_completed" and e["step"] == "voice_review"
    ]
    assert len(voice_completed) == 3
    assert all(e["passed"] is False for e in voice_completed)
    assert [e["attempt"] for e in voice_completed] == [1, 2, 3]
