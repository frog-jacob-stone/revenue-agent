"""End-to-end tests for the content_creation v2 graph.

Stubs `call_openai` so the three OpenAI-backed agents (ContentStrategy,
LinkedInWriting, PersonalVoice) can run without network. The fake
dispatches by system prompt — each role has a distinct phrase.

Three scenarios:
  - happy: voice passes immediately → social_posts.status='ready'
  - voice_loop: voice fails once, then passes on retry
  - voice_exhausted: voice fails 3 times → failed_terminal, post stays 'draft'

The graph has no interrupt gate; the workflow runs to completion (or
failed_terminal) synchronously inside `runner.start`.
"""
from __future__ import annotations

import json
from unittest.mock import patch
from uuid import UUID

import pytest

from app.db import get_pool
from app.orchestrator import runner
from app.orchestrator.graphs.content_creation import (
    CONTENT_CREATION_KIND,
    build_graph,
)


@pytest.fixture(autouse=True)
def _register_graph():
    if not runner.is_registered(CONTENT_CREATION_KIND):
        runner.register(CONTENT_CREATION_KIND, build_graph)
    yield
    runner.unregister(CONTENT_CREATION_KIND)


# ── Fake LLM dispatcher ──────────────────────────────────────────────────────


def _make_fake_call(*, voice_results: list[bool]):
    """Build a fake call_openai. `voice_results` is FIFO-popped on each
    voice_review call. interpret_brief and draft_post return canned shapes."""
    voice = list(voice_results)

    async def fake(system: str, user: str, *, model: str, max_tokens: int = 800) -> str:
        if "personal writing coach" in system:
            passed = voice.pop(0) if voice else False
            return json.dumps({
                "passed_voice_review": passed,
                "voice_score": 0.9 if passed else 0.4,
                "issues_found": [] if passed else ["too generic"],
                "suggested_changes": [] if passed else ["add a concrete example"],
                "revised_post_text": "Polished post text." if passed else None,
            })
        if "content strategist" in system:
            return json.dumps({
                "idea_title": "AI agents in revenue ops",
                "core_angle": "operational infrastructure, not a chatbot",
                "target_reader": "CROs",
                "main_point": "treat agents like new hires",
                "suggested_post_type": "opinion",
            })
        if "LinkedIn ghostwriter" in system:
            return json.dumps({"post_text": "Draft post body."})
        return "{}"

    return fake


async def _post_status(post_id: UUID) -> str | None:
    pool = await get_pool()
    return await pool.fetchval(
        "SELECT status FROM social_posts WHERE id = $1", post_id
    )


# ── Scenarios ────────────────────────────────────────────────────────────────


async def test_happy_path_voice_passes_first_try():
    """Voice review passes on first try → workflow completes; post status=ready
    with the revised_post_text written by voice_review."""
    fake = _make_fake_call(voice_results=[True])
    with patch("app.orchestrator.graphs.content_creation.call_openai", side_effect=fake):
        wf_id = await runner.start(
            CONTENT_CREATION_KIND,
            initial_state={"brief": "talk about AI agents", "channel": "linkedin"},
        )

    pool = await get_pool()
    wf = await pool.fetchrow("SELECT status FROM workflows WHERE id = $1", wf_id)
    assert wf["status"] == "completed"

    # The graph creates a fresh social_posts row; pick it up by topic.
    post_row = await pool.fetchrow(
        "SELECT id, status, post_text FROM social_posts WHERE topic = $1",
        "talk about AI agents",
    )
    assert post_row is not None
    assert post_row["status"] == "ready"
    # Voice review pushed `revised_post_text` on pass.
    assert post_row["post_text"] == "Polished post text."


async def test_voice_loop_passes_after_one_retry():
    """Voice fails once with budget remaining → loops back to draft_post → voice
    passes on second review → post status=ready. Two voice attempts total."""
    fake = _make_fake_call(voice_results=[False, True])
    with patch("app.orchestrator.graphs.content_creation.call_openai", side_effect=fake):
        wf_id = await runner.start(
            CONTENT_CREATION_KIND,
            initial_state={"brief": "second-try post", "channel": "linkedin"},
        )

    pool = await get_pool()
    wf = await pool.fetchrow("SELECT status FROM workflows WHERE id = $1", wf_id)
    assert wf["status"] == "completed"

    post_row = await pool.fetchrow(
        "SELECT status FROM social_posts WHERE topic = $1", "second-try post"
    )
    assert post_row["status"] == "ready"


async def test_voice_budget_exhausted_terminates():
    """Voice fails 3 times (default max) → failed_terminal → workflow completes
    but post stays at status='draft' (matches v1 behavior)."""
    fake = _make_fake_call(voice_results=[False, False, False])
    with patch("app.orchestrator.graphs.content_creation.call_openai", side_effect=fake):
        wf_id = await runner.start(
            CONTENT_CREATION_KIND,
            initial_state={"brief": "doomed post", "channel": "linkedin"},
        )

    pool = await get_pool()
    wf = await pool.fetchrow("SELECT status FROM workflows WHERE id = $1", wf_id)
    # The graph terminates cleanly even on failed_terminal — workflow status=completed.
    assert wf["status"] == "completed"

    post_row = await pool.fetchrow(
        "SELECT status FROM social_posts WHERE topic = $1", "doomed post"
    )
    assert post_row["status"] == "draft"
