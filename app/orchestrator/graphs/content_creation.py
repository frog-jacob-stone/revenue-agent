"""content_creation — drafts a social post with a voice-review critique loop.

Three host-defined nodes plus the critique loop attached by
`add_critique_loop` (which adds `voice_critique` and the shared
`failed_terminal` node):

    [entry] → interpret_brief → draft_post
                                    │
            ┌───────────────────────┘
            ▼
       add_critique_loop(...)
       (voice 3× → fail loops back to draft_post)
            │ pass
            ▼
           END

The voice review writes `social_posts.status = 'ready'` on pass (inside
`run_voice_review`). On terminal failure the row stays at `status='draft'`.

All LLM calls go through the dispatcher in `app.integrations.llm`.
"""
from __future__ import annotations

import json
import logging
from typing import Any, NotRequired
from uuid import UUID

from langgraph.graph import END, StateGraph

from app.agents.content_orchestrator_agent import ContentOrchestratorAgent
from app.db import get_pool
from app.integrations.llm import Attribution, dispatch
from app.lib.json_utils import parse_json
from app.orchestrator.critique_loop import Critic, add_critique_loop
from app.orchestrator.graphs._content_creation_prompts import (
    CONTENT_STRATEGY_SYSTEM_PROMPT,
    LINKEDIN_WRITER_SYSTEM_PROMPT,
    build_personal_voice_system_prompt,
)
from app.orchestrator.runner import GraphSpec
from app.orchestrator.state import BaseGraphState
from app.services import social_posts

logger = logging.getLogger(__name__)


CONTENT_CREATION_KIND = "content_creation"

# This graph is content-orchestrator's work. Every LLM call inside attributes
# to ContentOrchestratorAgent.slug; the sub-step (strategy vs draft vs voice
# review) is captured by the `purpose` field on each dispatch.
OWNING_AGENT = ContentOrchestratorAgent

DEFAULT_VOICE_MAX_ATTEMPTS = 3


def _wf_uuid(state: "ContentCreationState") -> UUID | None:
    wf_id = state.get("workflow_id")
    return UUID(wf_id) if wf_id else None


def _attribution(state: "ContentCreationState", purpose: str) -> Attribution:
    """Build an Attribution for a dispatch from this graph.

    `agent_slug` comes from the workflow's owning agent (seeded by the runner
    from the GraphSpec default or an invoker override); `purpose` discriminates
    the sub-step.
    """
    return Attribution(
        agent_slug=state.get("_owning_agent_slug"),
        purpose=purpose,
        workflow_id=_wf_uuid(state),
    )


# ── State ────────────────────────────────────────────────────────────────────


class ContentCreationState(BaseGraphState, total=False):
    # From initial_state / trigger
    brief: NotRequired[str]
    channel: NotRequired[str]
    instructions: NotRequired[str]
    post_id: NotRequired[str]

    # Built by interpret_brief
    idea: NotRequired[dict[str, Any]]

    # Critique state — written by the critique_loop helper.
    voice_attempts: NotRequired[int]
    voice_max_attempts: NotRequired[int]
    last_voice_critique: NotRequired[dict[str, Any]]

    # Shared slot — set by the voice critic on fail; cleared by draft_post.
    last_critique_feedback: NotRequired[dict[str, Any] | None]

    # Set by the critique_loop helper on the exhausting attempt.
    failure_reason: NotRequired[str]

    # Final
    result: NotRequired[dict[str, Any]]


# ── Nodes ────────────────────────────────────────────────────────────────────


async def interpret_brief(state: ContentCreationState) -> ContentCreationState:
    """LLM call: turn the user's brief into a structured idea object."""
    brief = state.get("brief") or ""
    channel = state.get("channel") or "linkedin"
    instructions = state.get("instructions") or ""

    user_msg = f"Brief: {brief}\nChannel: {channel}"
    if instructions:
        user_msg += f"\nAdditional instructions: {instructions}"

    response = await dispatch(
        model=ContentOrchestratorAgent.model,
        messages=[
            {"role": "system", "content": CONTENT_STRATEGY_SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        attribution=_attribution(state, "content_creation.interpret_brief"),
        response_format={"type": "json_object"},
    )
    idea = parse_json(response.text or "{}")

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
    the model can address specific issues. Writes/updates the social_posts row.
    Clears `last_critique_feedback` after consumption."""
    idea = state.get("idea") or {}
    channel = state.get("channel") or "linkedin"
    brief = state.get("brief") or ""

    user_msg = (
        f"Idea:\n{json.dumps(idea, indent=2)}\n\n"
        f"Brief: {brief}\n"
        f"Channel: {channel}"
    )

    last_feedback = state.get("last_critique_feedback") or {}
    if last_feedback:
        feedback = last_feedback.get("feedback", "")
        issues = last_feedback.get("issues", [])
        # The prior post_text lives in the DB; surface it for the revision prompt.
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

    response = await dispatch(
        model=ContentOrchestratorAgent.model,
        messages=[
            {"role": "system", "content": LINKEDIN_WRITER_SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        attribution=_attribution(state, "content_creation.draft_post"),
        response_format={"type": "json_object"},
        max_tokens=1000,
    )
    draft = parse_json(response.text or "{}")

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

    return {"post_id": post_id_str, "last_critique_feedback": None}


# ── Critic body (host-owned; helper wraps it with counter + slot logic) ──────


async def run_voice_review(state: ContentCreationState) -> dict[str, Any]:
    """LLM call: evaluate the latest draft against the personal voice profile.

    Side effect on pass: writes `revised_post_text` to social_posts and flips
    status to 'ready'. Returns the critique dict; the wrapped node owns the
    counter/slot bookkeeping.
    """
    post_id_str = state.get("post_id")
    channel = state.get("channel") or "linkedin"

    post_text = ""
    if post_id_str:
        pool = await get_pool()
        async with pool.acquire() as conn:
            post = await social_posts.get_post_conn(conn, UUID(post_id_str))
            post_text = (post or {}).get("post_text", "") or ""

    response = await dispatch(
        model=ContentOrchestratorAgent.model,
        messages=[
            {"role": "system", "content": build_personal_voice_system_prompt(channel)},
            {"role": "user", "content": f"Post to review:\n\n{post_text}"},
        ],
        attribution=_attribution(state, "content_creation.voice_review"),
        response_format={"type": "json_object"},
        max_tokens=600,
    )
    review = parse_json(response.text or "{}")

    passed = bool(review.get("passed_voice_review", False))
    revised_text = review.get("revised_post_text") or post_text

    if passed and post_id_str:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await social_posts.update_post_conn(
                conn, UUID(post_id_str), post_text=revised_text, status="ready"
            )

    return {
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


# ── Graph factory ────────────────────────────────────────────────────────────


def build_graph() -> GraphSpec:
    g: StateGraph = StateGraph(ContentCreationState)

    g.add_node("interpret_brief", interpret_brief)
    g.add_node("draft_post", draft_post)

    g.set_entry_point("interpret_brief")
    g.add_edge("interpret_brief", "draft_post")

    add_critique_loop(
        g,
        draft_node="draft_post",
        critics=[Critic("voice", run_voice_review, DEFAULT_VOICE_MAX_ATTEMPTS)],
        pass_target=END,
    )

    return GraphSpec(graph=g, owning_agent=OWNING_AGENT)
