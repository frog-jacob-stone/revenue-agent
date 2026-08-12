import json
import logging
from typing import Any
from uuid import UUID

from app.agents.tools.base import Done, ToolContext, ToolDefinition, ToolReturn

logger = logging.getLogger(__name__)


def _parse(raw: str) -> dict[str, Any]:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Failed to parse LLM JSON response: %s", raw[:200])
        return {}


async def _rewrite_post(
    ctx: ToolContext,
    *,
    post_id: str,
    instruction: str,
    channel: str = "linkedin",
    **_: Any,
) -> ToolReturn:
    from app.agents.linkedin_agent import LinkedInAgent
    from app.agents.tools.content._creation_prompts import LINKEDIN_WRITER_SYSTEM_PROMPT
    from app.db import get_pool
    from app.integrations.llm import Attribution, dispatch
    from app.services import social_posts as svc

    pool = await get_pool()
    post = await svc.get_post(pool, UUID(post_id))
    if not post:
        return Done({"error": f"Post {post_id} not found"})

    user_msg = (
        f"Current post:\n\n{post.get('post_text', '')}\n\n"
        f"Idea context:\n"
        f"Title: {post.get('idea_title', '')}\n"
        f"Angle: {post.get('core_angle', '')}\n\n"
        f"Rewrite instruction: {instruction}\n"
        f"Channel: {channel}"
    )

    response = await dispatch(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": LINKEDIN_WRITER_SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        attribution=Attribution(
            agent_slug=LinkedInAgent.slug,
            purpose="rewrite_post",
        ),
        response_format={"type": "json_object"},
    )
    draft = _parse(response.text or "{}")

    post_text = draft.get("post_text") or post.get("post_text", "")

    updated = await svc.update_post(
        pool,
        UUID(post_id),
        post_text=post_text,
        status="draft",
    )

    return Done({
        "id": str(updated["id"]),
        "post_text": post_text,
        "status": updated["status"],
        "hook": draft.get("hook"),
        "cta": draft.get("cta"),
    })


REWRITE_POST = ToolDefinition(
    name="rewrite_post",
    description=(
        "Rewrite a post based on user instructions. Works on posts in any status. "
        "Resets status to 'draft'. User can publish directly after rewriting or "
        "ask for voice review first."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "post_id": {
                "type": "string",
                "description": "UUID of the post to rewrite.",
            },
            "instruction": {
                "type": "string",
                "description": (
                    "What to change. E.g. 'Make the hook more direct', "
                    "'Cut the last paragraph'."
                ),
            },
            "channel": {
                "type": "string",
                "enum": ["linkedin", "email", "proposal", "slack"],
                "description": "Channel context. Defaults to linkedin.",
            },
        },
        "required": ["post_id", "instruction"],
    },
    execute=_rewrite_post,
)
