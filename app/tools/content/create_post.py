"""create_post — drafts a social post inline (ADR-0002, plan 16).

Three sequential steps inside a single tool call:
  1. interpret_brief  → structured idea object
  2. draft_post       → post text (looped with voice_review)
  3. voice_review     → critique; on pass writes status='ready' and exits

Voice budget exhaustion flips the social_posts row to `needs_revision`
so the inbox / UI reflects the state. Each step emits ProgressEmitter
events so the chat stream can render a live activity tree.

The Attribution agent_slug for every LLM call inside is the
ContentOrchestratorAgent's slug; the per-step `purpose` discriminates.
"""
from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID

from app.agents.content_orchestrator_agent import ContentOrchestratorAgent
from app.db import get_pool
from app.integrations.llm import Attribution, dispatch
from app.lib.json_utils import parse_json
from app.tools.base import (
    Done,
    ProgressEmitter,
    ToolContext,
    ToolDefinition,
    ToolReturn,
)
from app.tools.content._creation_prompts import (
    CONTENT_STRATEGY_SYSTEM_PROMPT,
    LINKEDIN_WRITER_SYSTEM_PROMPT,
    build_personal_voice_system_prompt,
)

# `social_posts` (via `app.services.audit` -> `app.orchestrator.events`) triggers
# the orchestrator package init, which transitively imports agents that re-export
# tools — creating a circular import at module-load time. Imported lazily inside
# `_create_post` to break the cycle.

logger = logging.getLogger(__name__)

DEFAULT_VOICE_MAX_ATTEMPTS = 3
TOOL_NAME = "create_post"


def _emit(progress: ProgressEmitter | None, event: dict[str, Any]) -> None:
    if progress is not None:
        progress.emit(event)


async def _interpret_brief(
    brief: str, channel: str, instructions: str
) -> dict[str, Any]:
    user_msg = f"Brief: {brief}\nChannel: {channel}"
    if instructions:
        user_msg += f"\nAdditional instructions: {instructions}"
    response = await dispatch(
        model=ContentOrchestratorAgent.model,
        messages=[
            {"role": "system", "content": CONTENT_STRATEGY_SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        attribution=Attribution(
            agent_slug=ContentOrchestratorAgent.slug,
            purpose="content_creation.interpret_brief",
        ),
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
    return idea


async def _draft_post(
    *,
    brief: str,
    channel: str,
    idea: dict[str, Any],
    prior_text: str | None,
    last_feedback: dict[str, Any] | None,
) -> str:
    user_msg = (
        f"Idea:\n{json.dumps(idea, indent=2)}\n\n"
        f"Brief: {brief}\nChannel: {channel}"
    )
    if last_feedback and prior_text is not None:
        user_msg += (
            "\n\nPREVIOUS DRAFT WAS REJECTED BY VOICE REVIEW. Revise to address the feedback.\n"
            f"PRIOR DRAFT:\n{prior_text}\n"
            f"VOICE FEEDBACK: {last_feedback.get('feedback', '')}\n"
            f"SPECIFIC ISSUES: {last_feedback.get('issues', [])}\n"
        )
    response = await dispatch(
        model=ContentOrchestratorAgent.model,
        messages=[
            {"role": "system", "content": LINKEDIN_WRITER_SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        attribution=Attribution(
            agent_slug=ContentOrchestratorAgent.slug,
            purpose="content_creation.draft_post",
        ),
        response_format={"type": "json_object"},
        max_tokens=1000,
    )
    draft = parse_json(response.text or "{}")
    return draft.get("post_text") or f"[Draft: {idea.get('idea_title', brief)}]"


async def _run_voice_review(*, post_text: str, channel: str) -> dict[str, Any]:
    response = await dispatch(
        model=ContentOrchestratorAgent.model,
        messages=[
            {"role": "system", "content": build_personal_voice_system_prompt(channel)},
            {"role": "user", "content": f"Post to review:\n\n{post_text}"},
        ],
        attribution=Attribution(
            agent_slug=ContentOrchestratorAgent.slug,
            purpose="content_creation.voice_review",
        ),
        response_format={"type": "json_object"},
        max_tokens=600,
    )
    review = parse_json(response.text or "{}")
    return {
        "passed": bool(review.get("passed_voice_review", False)),
        "score": float(review.get("voice_score", 0.0)),
        "feedback": (
            f"Voice score {review.get('voice_score')}: "
            + "; ".join(review.get("issues_found") or [])
        ),
        "issues": review.get("issues_found") or [],
        "suggested_changes": review.get("suggested_changes") or [],
        "revised_post_text": review.get("revised_post_text") or post_text,
    }


async def _create_post(
    ctx: ToolContext,
    *,
    brief: str,
    channel: str = "linkedin",
    instructions: str | None = None,
    voice_max_attempts: int = DEFAULT_VOICE_MAX_ATTEMPTS,
    **_: Any,
) -> ToolReturn:
    from app.services import social_posts as svc

    pool = await get_pool()
    progress = ctx.progress

    # Pre-create the social_posts row so post_id is available for streaming UI.
    post = await svc.save_post(pool, topic=brief)
    post_id: UUID = post["id"]

    # Step 1: interpret brief
    _emit(progress, {"type": "tool_step_started", "tool": TOOL_NAME, "step": "interpret_brief"})
    idea = await _interpret_brief(brief, channel, instructions or "")
    _emit(progress, {"type": "tool_step_completed", "tool": TOOL_NAME, "step": "interpret_brief"})

    last_feedback: dict[str, Any] | None = None
    prior_text: str | None = None

    for attempt in range(1, voice_max_attempts + 1):
        # Step 2: draft post
        _emit(progress, {
            "type": "tool_step_started", "tool": TOOL_NAME,
            "step": "draft_post", "attempt": attempt,
        })
        post_text = await _draft_post(
            brief=brief, channel=channel, idea=idea,
            prior_text=prior_text, last_feedback=last_feedback,
        )
        async with pool.acquire() as conn:
            await svc.update_post_conn(
                conn, post_id,
                post_text=post_text,
                idea_title=idea.get("idea_title"),
                core_angle=idea.get("core_angle"),
                status="draft",
            )
        _emit(progress, {
            "type": "tool_step_completed", "tool": TOOL_NAME,
            "step": "draft_post", "attempt": attempt,
        })

        # Step 3: voice review
        _emit(progress, {
            "type": "tool_step_started", "tool": TOOL_NAME,
            "step": "voice_review", "attempt": attempt,
        })
        critique = await _run_voice_review(post_text=post_text, channel=channel)
        if critique["passed"]:
            final_text = critique["revised_post_text"]
            async with pool.acquire() as conn:
                await svc.update_post_conn(
                    conn, post_id, post_text=final_text, status="ready"
                )
            _emit(progress, {
                "type": "tool_step_completed", "tool": TOOL_NAME,
                "step": "voice_review", "attempt": attempt, "passed": True,
            })
            break

        last_feedback = critique
        prior_text = post_text
        _emit(progress, {
            "type": "tool_step_completed", "tool": TOOL_NAME,
            "step": "voice_review", "attempt": attempt, "passed": False,
        })
    else:
        # Budget exhausted: flip status to needs_revision so the UI reflects it.
        async with pool.acquire() as conn:
            await svc.update_post_conn(conn, post_id, status="needs_revision")

    refreshed = await svc.get_post(pool, post_id)
    return Done({
        "post_id": str(post_id),
        "status": (refreshed or post).get("status"),
        "idea_title": (refreshed or post).get("idea_title"),
        "post_text": (refreshed or post).get("post_text"),
    })


CREATE_POST = ToolDefinition(
    name="create_post",
    description=(
        "Draft a LinkedIn post from a brief. "
        "Runs the full content creation flow: interprets the brief, writes a draft, "
        "and runs voice review (up to 3 attempts). Returns when the post is 'ready' "
        "(passed review) or 'needs_revision' (failed after max retries). "
        "Call once per post; call concurrently for multiple posts."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "brief": {
                "type": "string",
                "description": "What the post should be about. Can be vague or detailed.",
            },
            "channel": {
                "type": "string",
                "enum": ["linkedin", "email", "proposal", "slack"],
                "description": "Target channel. Defaults to linkedin.",
            },
            "instructions": {
                "type": "string",
                "description": "Optional extra guidance for the writing agent.",
            },
        },
        "required": ["brief"],
    },
    execute=_create_post,
)
