"""Content chains — content_creation and content_publish.

content_creation (prompt_chain_action):
  0. llm_step  — Interpret brief (ContentStrategyAgent prompt)
  1. llm_step  — Draft post (LinkedInWritingAgent prompt; creates social_posts row)
  2. critique  — Voice review (PersonalVoiceAgent prompt; max_attempts=3; retries draft)

  On voice pass  → social_posts status = 'ready'
  On budget exhausted → workflow → failed (status remains 'draft')

content_publish (supervised_automation):
  0. execution — Post to LinkedIn (stub); pauses in approval inbox before sending
                 On approve → social_posts status = 'published'
                 On reject  → status unchanged (post stays 'ready')

LLM calls use OpenAI (gpt-4o-mini).
"""
from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID

from app.models.workflows import WorkflowPattern
from app.orchestrator.chain import Chain, register_chain
from app.orchestrator.chains.utils import parse_json
from app.orchestrator.state import StepContext
from app.orchestrator.steps import (
    CritiqueStep,
    ExecutionStep,
    LLMStep,
)
from app.services import social_posts

logger = logging.getLogger(__name__)

CONTENT_CREATION_KIND = "content_creation"
CONTENT_PUBLISH_KIND = "content_publish"
CONTENT_AGENT_SLUG = "content-orchestrator"
PERSONAL_VOICE_SLUG = "personal-voice"

# Step indices for content_creation chain
STEP_INTERPRET = 0
STEP_DRAFT = 1
STEP_VOICE = 2

_MODEL = "gpt-4o-mini"


# ---------------------------------------------------------------------------
# LLM helper
# ---------------------------------------------------------------------------


async def _complete(system: str, user: str, *, max_tokens: int = 800) -> str:
    """Single OpenAI chat completion (gpt-4o-mini, JSON response format)."""
    from app.integrations.openai_client import get_client

    client = get_client()
    response = await client.chat.completions.create(
        model=_MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        response_format={"type": "json_object"},
        max_tokens=max_tokens,
    )
    return response.choices[0].message.content or "{}"


# ---------------------------------------------------------------------------
# content_creation step handlers
# ---------------------------------------------------------------------------


async def _interpret_brief(ctx: StepContext) -> dict[str, Any]:
    """LLM step 0: turn the user's brief into a structured idea."""
    from app.agents.content import ContentStrategyAgent

    brief = await ctx.trigger_payload_get("brief") or ""
    channel = await ctx.trigger_payload_get("channel") or "linkedin"
    instructions = await ctx.trigger_payload_get("instructions") or ""

    user_msg = f"Brief: {brief}\nChannel: {channel}"
    if instructions:
        user_msg += f"\nAdditional instructions: {instructions}"

    raw = await _complete(ContentStrategyAgent.system_prompt, user_msg)
    idea = parse_json(raw)

    if not idea.get("idea_title"):
        idea = {
            "idea_title": brief[:80] if brief else "Untitled",
            "core_angle": brief,
            "target_reader": "business professionals",
            "main_point": brief,
            "suggested_post_type": "opinion",
        }

    return idea


async def _draft_post(ctx: StepContext) -> dict[str, Any]:
    """LLM step 1: draft the post and write (or update) the social_posts row.

    On retry, critique_feedback is surfaced so the model can address specific issues.
    """
    from app.agents.content import LinkedInWritingAgent

    idea = (ctx.state.latest_for_step(STEP_INTERPRET).result or {}) if ctx.state.latest_for_step(STEP_INTERPRET) else {}
    channel = await ctx.trigger_payload_get("channel") or "linkedin"
    brief = await ctx.trigger_payload_get("brief") or ""

    user_msg = (
        f"Idea:\n{json.dumps(idea, indent=2)}\n\n"
        f"Brief: {brief}\n"
        f"Channel: {channel}"
    )

    if ctx.critique_feedback and ctx.attempt_number > 1:
        feedback = ctx.critique_feedback.get("feedback", "")
        issues = ctx.critique_feedback.get("issues", [])
        prior_action = ctx.state.latest_for_step(STEP_DRAFT)
        prior_text = (prior_action.result or {}).get("post_text", "") if prior_action else ""
        user_msg += (
            "\n\nPREVIOUS DRAFT WAS REJECTED BY VOICE REVIEW. Revise to address the feedback.\n"
            f"PRIOR DRAFT:\n{prior_text}\n"
            f"VOICE FEEDBACK: {feedback}\n"
            f"SPECIFIC ISSUES: {issues}\n"
        )

    raw = await _complete(LinkedInWritingAgent.system_prompt, user_msg, max_tokens=1000)
    draft = parse_json(raw)

    post_text = draft.get("post_text") or f"[Draft: {idea.get('idea_title', brief)}]"

    # Get or create the social_posts row. post_id is set in trigger_payload by
    # the create_post tool before the chain runs.
    post_id_str = await ctx.trigger_payload_get("post_id")
    post_id = UUID(post_id_str) if post_id_str else None

    if post_id:
        await social_posts.update_post_conn(
            ctx.conn,
            post_id,
            post_text=post_text,
            idea_title=idea.get("idea_title"),
            core_angle=idea.get("core_angle"),
            status="draft",
        )
    else:
        # Fallback: create row if not pre-created (shouldn't normally happen)
        topic = brief or idea.get("idea_title", "Untitled")
        row = await ctx.conn.fetchrow(
            """
            INSERT INTO social_posts (topic, idea_title, core_angle, post_text, status)
            VALUES ($1, $2, $3, $4, 'draft')
            RETURNING id
            """,
            topic,
            idea.get("idea_title"),
            idea.get("core_angle"),
            post_text,
        )
        post_id = row["id"]
        # Patch trigger_payload with the newly created post_id so step 2 can find it.
        await ctx.conn.execute(
            """
            UPDATE workflows
            SET trigger_payload = trigger_payload || $1::jsonb
            WHERE id = $2
            """,
            json.dumps({"post_id": str(post_id)}),
            ctx.workflow_id,
        )

    return {
        "post_id": str(post_id),
        "post_text": post_text,
        "hook": draft.get("hook"),
        "cta": draft.get("cta"),
        "estimated_strength_score": draft.get("estimated_strength_score"),
        "idea_title": idea.get("idea_title"),
        "core_angle": idea.get("core_angle"),
    }


async def _voice_review(ctx: StepContext) -> dict[str, Any]:
    """Critique step 2: evaluate the draft against the personal voice profile."""
    from app.agents.content import PersonalVoiceAgent

    draft_action = ctx.state.latest_for_step(STEP_DRAFT)
    draft_result = (draft_action.result or {}) if draft_action else {}
    post_text = draft_result.get("post_text", "")
    channel = await ctx.trigger_payload_get("channel") or "linkedin"

    raw = await _complete(
        PersonalVoiceAgent.get_system_prompt(channel),
        f"Post to review:\n\n{post_text}",
        max_tokens=600,
    )
    review = parse_json(raw)

    passed = bool(review.get("passed_voice_review", False))

    if passed:
        post_id_str = await ctx.trigger_payload_get("post_id")
        if post_id_str:
            revised = review.get("revised_post_text") or post_text
            await social_posts.update_post_conn(ctx.conn, UUID(post_id_str), post_text=revised, status="ready")

    return {
        "passed": passed,
        "score": float(review.get("voice_score", 0.0)),
        "feedback": f"Voice score {review.get('voice_score')}: " + "; ".join(review.get("issues_found") or []),
        "issues": review.get("issues_found") or [],
        "suggested_changes": review.get("suggested_changes") or [],
        "revised_post_text": review.get("revised_post_text") or post_text,
    }


# ---------------------------------------------------------------------------
# content_publish step handlers
# ---------------------------------------------------------------------------


async def _propose_linkedin_post(ctx: StepContext) -> dict[str, Any]:
    """Surface the post text for human review in the approval inbox."""
    post_id_str = await ctx.trigger_payload_get("post_id")
    if not post_id_str:
        return {"error": "No post_id in trigger_payload"}

    post = await social_posts.get_post_conn(ctx.conn, UUID(post_id_str))
    if not post:
        return {"error": f"Post {post_id_str} not found"}

    return {
        "post_id": str(post["id"]),
        "idea_title": post.get("idea_title"),
        "post_text": post.get("post_text"),
        "status": post.get("status"),
    }


async def _linkedin_post_stub(ctx: StepContext) -> dict[str, Any]:
    """Stub executor — logs what would be posted; marks post as published on approval."""
    payload = ctx.executed_payload or {}
    post_id_str = payload.get("post_id") or await ctx.trigger_payload_get("post_id")

    logger.info(
        "[linkedin-stub] would post: post_id=%r text=%r",
        post_id_str,
        (payload.get("post_text") or "")[:120],
    )

    if post_id_str:
        await social_posts.update_post_conn(ctx.conn, UUID(post_id_str), status="published")

    return {
        "stub": True,
        "post_id": post_id_str,
        "would_post_text": payload.get("post_text", "")[:200],
    }


# ---------------------------------------------------------------------------
# Chain definitions
# ---------------------------------------------------------------------------

CONTENT_CREATION_CHAIN = Chain(
    kind=CONTENT_CREATION_KIND,
    pattern=WorkflowPattern.prompt_chain_action,
    agent_slug=CONTENT_AGENT_SLUG,
    steps=(
        LLMStep("Interpret brief", _interpret_brief),
        LLMStep("Draft post", _draft_post),
        CritiqueStep(
            "Voice review",
            _voice_review,
            critiques_step_index=STEP_DRAFT,
            max_attempts=3,
            agent_slug=PERSONAL_VOICE_SLUG,
        ),
    ),
)

CONTENT_PUBLISH_CHAIN = Chain(
    kind=CONTENT_PUBLISH_KIND,
    pattern=WorkflowPattern.supervised_automation,
    agent_slug=CONTENT_AGENT_SLUG,
    steps=(
        ExecutionStep(
            "Post to LinkedIn",
            _linkedin_post_stub,
            propose_handler=_propose_linkedin_post,
            action_type="post_to_linkedin",
            risk_level="medium",
        ),
    ),
)


def register() -> None:
    from app.orchestrator.chain import has_chain

    if not has_chain(CONTENT_CREATION_KIND):
        register_chain(CONTENT_CREATION_CHAIN)
    if not has_chain(CONTENT_PUBLISH_KIND):
        register_chain(CONTENT_PUBLISH_CHAIN)
