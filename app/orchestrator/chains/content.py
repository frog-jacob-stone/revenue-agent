"""Content chains — content_creation and content_publish.

content_creation (prompt_chain_action):
  0. llm_step  — Interpret brief (ContentStrategyAgent prompt)
  1. llm_step  — Draft post (LinkedInWritingAgent prompt; creates social_posts row)
  2. critique  — Voice review (PersonalVoiceAgent prompt; max_attempts=3; retries draft)

  On voice pass  → social_posts status = 'ready'
  On budget exhausted → social_posts status = 'needs_revision'; workflow → failed

content_publish (supervised_automation):
  0. execution — Post to LinkedIn (stub); pauses in approval inbox before sending
                 On approve → social_posts status = 'published'
                 On reject  → status unchanged (post stays 'ready')

LLM calls use OpenAI (gpt-4o-mini). Stubs returned when OPENAI_API_KEY is not set
so the chain runs end-to-end in dev without credentials.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any
from uuid import UUID

from app.config import settings
from app.models.workflows import WorkflowPattern
from app.orchestrator.chain import Chain, register_chain
from app.orchestrator.state import StepContext
from app.orchestrator.steps import (
    CritiqueStep,
    ExecutionStep,
    LLMStep,
)

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
    """Single OpenAI chat completion. Returns stub text when key is unset."""
    if not settings.openai_api_key:
        logger.warning("[content-chain] OPENAI_API_KEY unset — returning stub LLM output.")
        return _stub_response(system)

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


def _parse_json(raw: str) -> dict[str, Any]:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.MULTILINE)
    try:
        return json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        logger.warning("[content-chain] JSON parse failed: %s", raw[:200])
        return {}


def _stub_response(system: str) -> str:
    """Dev-mode stubs keyed on unique system prompt markers."""
    if "voice profile" in system.lower():
        return json.dumps({
            "voice_score": 8.5,
            "passed_voice_review": True,
            "issues_found": [],
            "suggested_changes": [],
            "revised_post_text": "Most companies skip the boring stuff when adopting AI. That's why it fails.\n\nStart with one process. One team. One workflow that costs you time every week.\n\nAutomate that. Learn from it. Then move to the next.\n\nAI works when it's boring. It fails when it's a strategy.",
        })
    if "content strategist" in system.lower():
        return json.dumps({
            "idea_title": "Start with one boring process",
            "core_angle": "AI adoption fails when companies try to do too much at once — start with one repetitive workflow",
            "target_reader": "Operations and technology leaders at mid-market companies",
            "main_point": "The companies that get ROI from AI pick the most boring, repetitive task first",
            "suggested_post_type": "opinion",
        })
    # LinkedIn writer stub
    return json.dumps({
        "post_text": (
            "Most companies skip the boring stuff when adopting AI. That's why it fails.\n\n"
            "Start with one process. One team. One workflow that costs you time every week.\n\n"
            "Automate that. Learn from it. Then move to the next.\n\n"
            "AI works when it's boring. It fails when it's a strategy."
        ),
        "hook": "Most companies skip the boring stuff when adopting AI.",
        "cta": "AI works when it's boring. It fails when it's a strategy.",
        "estimated_strength_score": 7.8,
        "notes": "Stub draft",
    })


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _trigger_payload_get(ctx: StepContext, key: str) -> Any:
    row = await ctx.conn.fetchrow(
        "SELECT trigger_payload FROM workflows WHERE id = $1",
        ctx.workflow_id,
    )
    payload = (row["trigger_payload"] if row else None) or {}
    return payload.get(key)


async def _get_post(ctx: StepContext, post_id: UUID) -> dict[str, Any] | None:
    row = await ctx.conn.fetchrow("SELECT * FROM social_posts WHERE id = $1", post_id)
    return dict(row) if row else None


async def _update_post(ctx: StepContext, post_id: UUID, **fields: Any) -> None:
    if not fields:
        return
    set_clauses = ", ".join(f"{col} = ${i + 2}" for i, col in enumerate(fields))
    await ctx.conn.execute(
        f"UPDATE social_posts SET {set_clauses} WHERE id = $1",
        post_id,
        *fields.values(),
    )


# ---------------------------------------------------------------------------
# content_creation step handlers
# ---------------------------------------------------------------------------


async def _interpret_brief(ctx: StepContext) -> dict[str, Any]:
    """LLM step 0: turn the user's brief into a structured idea."""
    from app.agents.content import ContentStrategyAgent

    brief = await _trigger_payload_get(ctx, "brief") or ""
    channel = await _trigger_payload_get(ctx, "channel") or "linkedin"
    instructions = await _trigger_payload_get(ctx, "instructions") or ""

    user_msg = f"Brief: {brief}\nChannel: {channel}"
    if instructions:
        user_msg += f"\nAdditional instructions: {instructions}"

    raw = await _complete(ContentStrategyAgent.system_prompt, user_msg)
    idea = _parse_json(raw)

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
    channel = await _trigger_payload_get(ctx, "channel") or "linkedin"
    brief = await _trigger_payload_get(ctx, "brief") or ""

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
    draft = _parse_json(raw)

    post_text = draft.get("post_text") or f"[Draft: {idea.get('idea_title', brief)}]"

    # Get or create the social_posts row. post_id is set in trigger_payload by
    # the create_post tool before the chain runs.
    post_id_str = await _trigger_payload_get(ctx, "post_id")
    post_id = UUID(post_id_str) if post_id_str else None

    if post_id:
        await _update_post(
            ctx,
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
    channel = await _trigger_payload_get(ctx, "channel") or "linkedin"

    raw = await _complete(
        PersonalVoiceAgent.get_system_prompt(channel),
        f"Post to review:\n\n{post_text}",
        max_tokens=600,
    )
    review = _parse_json(raw)

    passed = bool(review.get("passed_voice_review", False))

    # On pass: update status to 'ready' with the (possibly revised) text
    if passed:
        post_id_str = await _trigger_payload_get(ctx, "post_id")
        if post_id_str:
            revised = review.get("revised_post_text") or post_text
            await _update_post(ctx, UUID(post_id_str), post_text=revised, status="ready")

    return {
        "passed": passed,
        "score": float(review.get("voice_score", 0.0)),
        "feedback": f"Voice score {review.get('voice_score')}: " + "; ".join(review.get("issues_found") or []),
        "issues": review.get("issues_found") or [],
        "suggested_changes": review.get("suggested_changes") or [],
        "revised_post_text": review.get("revised_post_text") or post_text,
    }


async def _on_voice_budget_exhausted(ctx: StepContext) -> None:
    """Called by the orchestrator when the critique loop hits max_attempts and fails.
    Updates social_posts status to 'needs_revision' so the user can see it in chat.
    """
    post_id_str = await _trigger_payload_get(ctx, "post_id")
    if post_id_str:
        await _update_post(ctx, UUID(post_id_str), status="needs_revision")


# ---------------------------------------------------------------------------
# content_publish step handlers
# ---------------------------------------------------------------------------


async def _propose_linkedin_post(ctx: StepContext) -> dict[str, Any]:
    """Surface the post text for human review in the approval inbox."""
    post_id_str = await _trigger_payload_get(ctx, "post_id")
    if not post_id_str:
        return {"error": "No post_id in trigger_payload"}

    post = await _get_post(ctx, UUID(post_id_str))
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
    post_id_str = payload.get("post_id") or await _trigger_payload_get(ctx, "post_id")

    logger.info(
        "[linkedin-stub] would post: post_id=%r text=%r",
        post_id_str,
        (payload.get("post_text") or "")[:120],
    )

    if post_id_str:
        await _update_post(ctx, UUID(post_id_str), status="published")

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
