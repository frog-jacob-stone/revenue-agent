"""content_creation — drafts a social post with a voice-review critique loop.

Four nodes, no interrupt gate, one critique loop:

    [entry] → interpret_brief → draft_post → voice_review
                                    ▲           │
                                    │           ▼
                                    │   ┌───────┼─────────┐
                                    │ pass   fail+      fail+
                                    │  │    budget    exhausted
                                    │  ▼      │           │
                                    │ END    (loop)       ▼
                                    └────────┘     failed_terminal → END

The voice review writes `social_posts.status = 'ready'` on pass; on terminal
failure the row stays at `status='draft'`.

All three node LLM calls go through the instrumented `call_openai_chat`
wrapper, with `with_llm_context` setting the workflow + purpose so each row
in `llm_calls` is attributable.
"""
from __future__ import annotations

import json
import logging
from typing import Any, NotRequired
from uuid import UUID

from langgraph.graph import END, StateGraph

from app.agents.content import (
    ContentStrategyAgent,
    LinkedInWritingAgent,
    PersonalVoiceAgent,
)
from app.db import get_pool
from app.integrations.openai_client import call_openai_chat
from app.lib.json_utils import parse_json
from app.orchestrator.runner import GraphSpec
from app.orchestrator.state import BaseGraphState
from app.services import social_posts
from app.services.llm_logging import with_llm_context

logger = logging.getLogger(__name__)


CONTENT_CREATION_KIND = "content_creation"
CONTENT_AGENT_SLUG = "content-orchestrator"

DEFAULT_VOICE_MAX_ATTEMPTS = 3


def _wf_uuid(state: "ContentCreationState") -> UUID | None:
    wf_id = state.get("workflow_id")
    return UUID(wf_id) if wf_id else None


# ── State ────────────────────────────────────────────────────────────────────


class ContentCreationState(BaseGraphState, total=False):
    # From initial_state / trigger
    brief: NotRequired[str]
    channel: NotRequired[str]
    instructions: NotRequired[str]
    post_id: NotRequired[str]

    # Built by interpret_brief
    idea: NotRequired[dict[str, Any]]

    # Critique state
    voice_attempts: NotRequired[int]
    voice_max_attempts: NotRequired[int]
    last_voice_review: NotRequired[dict[str, Any]]

    # Final
    result: NotRequired[dict[str, Any]]
    failure_reason: NotRequired[str]


# ── Nodes ────────────────────────────────────────────────────────────────────


async def interpret_brief(state: ContentCreationState) -> ContentCreationState:
    """LLM call: turn the user's brief into a structured idea object."""
    brief = state.get("brief") or ""
    channel = state.get("channel") or "linkedin"
    instructions = state.get("instructions") or ""

    user_msg = f"Brief: {brief}\nChannel: {channel}"
    if instructions:
        user_msg += f"\nAdditional instructions: {instructions}"

    with with_llm_context(
        agent_slug=ContentStrategyAgent.slug,
        workflow_id=_wf_uuid(state),
        purpose="interpret_brief",
    ):
        completion = await call_openai_chat(
            model=ContentStrategyAgent.model,
            messages=[
                {"role": "system", "content": ContentStrategyAgent.system_prompt},
                {"role": "user", "content": user_msg},
            ],
            response_format={"type": "json_object"},
        )
    raw = completion.choices[0].message.content or "{}"
    idea = parse_json(raw)

    if not idea.get("idea_title"):
        idea = {
            "idea_title": brief[:80] if brief else "Untitled",
            "core_angle": brief,
            "target_reader": "business professionals",
            "main_point": brief,
            "suggested_post_type": "opinion",
        }

    return {"idea": idea}


async def draft_post(state: ContentCreationState) -> ContentCreationState:
    """LLM call: draft the post; on retry surface the prior voice feedback so
    the model can address specific issues. Writes/updates the social_posts row."""
    idea = state.get("idea") or {}
    channel = state.get("channel") or "linkedin"
    brief = state.get("brief") or ""

    user_msg = (
        f"Idea:\n{json.dumps(idea, indent=2)}\n\n"
        f"Brief: {brief}\n"
        f"Channel: {channel}"
    )

    last_review = state.get("last_voice_review") or {}
    if last_review and not last_review.get("passed"):
        feedback = last_review.get("feedback", "")
        issues = last_review.get("issues", [])
        # We don't have the prior post_text in state directly, so read it
        # from the social_posts row.
        post_id_str = state.get("post_id")
        prior_text = ""
        if post_id_str:
            pool = await get_pool()
            async with pool.acquire() as conn:
                prior = await social_posts.get_post_conn(conn, UUID(post_id_str))
                prior_text = (prior or {}).get("post_text", "") or ""
        user_msg += (
            "\n\nPREVIOUS DRAFT WAS REJECTED BY VOICE REVIEW. Revise to address the feedback.\n"
            f"PRIOR DRAFT:\n{prior_text}\n"
            f"VOICE FEEDBACK: {feedback}\n"
            f"SPECIFIC ISSUES: {issues}\n"
        )

    with with_llm_context(
        agent_slug=LinkedInWritingAgent.slug,
        workflow_id=_wf_uuid(state),
        purpose="draft_post",
    ):
        completion = await call_openai_chat(
            model=LinkedInWritingAgent.model,
            messages=[
                {"role": "system", "content": LinkedInWritingAgent.system_prompt},
                {"role": "user", "content": user_msg},
            ],
            response_format={"type": "json_object"},
            max_tokens=1000,
        )
    raw = completion.choices[0].message.content or "{}"
    draft = parse_json(raw)

    post_text = draft.get("post_text") or f"[Draft: {idea.get('idea_title', brief)}]"

    # Update or create the social_posts row.
    post_id_str = state.get("post_id")
    pool = await get_pool()
    async with pool.acquire() as conn:
        if post_id_str:
            await social_posts.update_post_conn(
                conn,
                UUID(post_id_str),
                post_text=post_text,
                idea_title=idea.get("idea_title"),
                core_angle=idea.get("core_angle"),
                status="draft",
            )
        else:
            topic = brief or idea.get("idea_title", "Untitled")
            new_id = await social_posts.create_post_conn(
                conn,
                topic=topic,
                idea_title=idea.get("idea_title"),
                core_angle=idea.get("core_angle"),
                post_text=post_text,
                status="draft",
            )
            post_id_str = str(new_id)

    return {"post_id": post_id_str}


async def voice_review(state: ContentCreationState) -> ContentCreationState:
    """LLM call: evaluate the latest draft against the personal voice profile.
    Increments `voice_attempts` whether or not the review passes."""
    post_id_str = state.get("post_id")
    channel = state.get("channel") or "linkedin"
    attempt = state.get("voice_attempts", 0) + 1

    post_text = ""
    if post_id_str:
        pool = await get_pool()
        async with pool.acquire() as conn:
            post = await social_posts.get_post_conn(conn, UUID(post_id_str))
            post_text = (post or {}).get("post_text", "") or ""

    with with_llm_context(
        agent_slug=PersonalVoiceAgent.slug,
        workflow_id=_wf_uuid(state),
        purpose="voice_review",
    ):
        completion = await call_openai_chat(
            model=PersonalVoiceAgent.model,
            messages=[
                {"role": "system", "content": PersonalVoiceAgent.get_system_prompt(channel)},
                {"role": "user", "content": f"Post to review:\n\n{post_text}"},
            ],
            response_format={"type": "json_object"},
            max_tokens=600,
        )
    raw = completion.choices[0].message.content or "{}"
    review = parse_json(raw)

    passed = bool(review.get("passed_voice_review", False))
    revised_text = review.get("revised_post_text") or post_text

    if passed and post_id_str:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await social_posts.update_post_conn(
                conn, UUID(post_id_str), post_text=revised_text, status="ready"
            )

    last_voice_review = {
        "passed": passed,
        "score": float(review.get("voice_score", 0.0)),
        "feedback": (
            f"Voice score {review.get('voice_score')}: "
            + "; ".join(review.get("issues_found") or [])
        ),
        "issues": review.get("issues_found") or [],
        "suggested_changes": review.get("suggested_changes") or [],
        "revised_post_text": revised_text,
    }

    return {
        "voice_attempts": attempt,
        "last_voice_review": last_voice_review,
    }


async def failed_terminal(state: ContentCreationState) -> ContentCreationState:
    """Terminal failure node: voice review budget exhausted.

    The social_posts row is left at `status='draft'`. A future enhancement
    could move it to a `needs_revision` status once the inbox UI surfaces
    that.
    """
    last = state.get("last_voice_review") or {}
    return {
        "result": {
            "outcome": "failed",
            "reason": "voice review budget exhausted",
            "last_score": last.get("score"),
        },
    }


# ── Routing ──────────────────────────────────────────────────────────────────


def route_after_voice_review(state: ContentCreationState) -> str:
    """pass → END; fail with budget → loop back to draft_post; budget exhausted → terminal."""
    last = state.get("last_voice_review") or {}
    if last.get("passed"):
        return END
    attempts = state.get("voice_attempts", 0)
    max_attempts = state.get("voice_max_attempts", DEFAULT_VOICE_MAX_ATTEMPTS)
    if attempts >= max_attempts:
        return "failed_terminal"
    return "draft_post"


# ── Graph factory ────────────────────────────────────────────────────────────


def build_graph() -> GraphSpec:
    g: StateGraph = StateGraph(ContentCreationState)

    g.add_node("interpret_brief", interpret_brief)
    g.add_node("draft_post", draft_post)
    g.add_node("voice_review", voice_review)
    g.add_node("failed_terminal", failed_terminal)

    g.set_entry_point("interpret_brief")
    g.add_edge("interpret_brief", "draft_post")
    g.add_edge("draft_post", "voice_review")
    g.add_conditional_edges(
        "voice_review",
        route_after_voice_review,
        {
            "draft_post": "draft_post",
            "failed_terminal": "failed_terminal",
            END: END,
        },
    )
    g.add_edge("failed_terminal", END)

    return GraphSpec(graph=g)
