"""publish_post — propose publishing a drafted post (ADR-0002, plan 17).

Reads the social_posts row and returns one of:
  - `Blocked` if `post_id` is missing, the post is missing, or the post
    has no text to publish.
  - `AwaitingApproval(executor="post_to_linkedin", ...)` otherwise.

The actual write is performed by the `post_to_linkedin` executor after
a human approves the proposal in the inbox.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from app.tools.base import (
    AwaitingApproval,
    Blocked,
    ToolContext,
    ToolDefinition,
    ToolReturn,
)


async def _publish_post(
    ctx: ToolContext,
    *,
    post_id: str,
    **_: Any,
) -> ToolReturn:
    from app.db import get_pool
    from app.services import social_posts as svc

    if not post_id:
        return Blocked(reason="post_id is required")

    pool = await get_pool()
    post = await svc.get_post(pool, UUID(post_id))
    if not post:
        return Blocked(
            reason=f"Post {post_id} not found",
            hint={"post_id": post_id},
        )
    if not post.get("post_text"):
        return Blocked(
            reason=f"Post {post_id} has no text to publish",
            hint={"post_id": post_id, "status": post.get("status")},
        )

    return AwaitingApproval(
        executor="post_to_linkedin",
        payload={
            "post_id": str(post["id"]),
            "idea_title": post.get("idea_title"),
            "post_text": post.get("post_text"),
            "status": post.get("status"),
        },
        summary=post.get("idea_title") or "LinkedIn post",
        action_type="post_to_linkedin",
        risk_level="medium",
    )


PUBLISH_POST = ToolDefinition(
    name="publish_post",
    description=(
        "Queue a post for publishing. Reads the post, then proposes the "
        "publish for human approval; nothing is sent until the user approves "
        "it in the inbox. Returns awaiting_approval with the approval id, or "
        "blocked if the post is missing or has no text."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "post_id": {
                "type": "string",
                "description": "UUID of the post to publish.",
            },
        },
        "required": ["post_id"],
    },
    execute=_publish_post,
)
