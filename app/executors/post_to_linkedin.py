"""post_to_linkedin executor (ADR-0002, plan 17).

Stub LinkedIn poster. Runs after a human approves a `publish_post`
proposal. Logs the would-be post and marks the social_posts row as
published. Accepts a human-edited `post_text` from the inbox if present.
"""
from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from app.db import get_pool
from app.executors.base import ExecutorContext, ExecutorDefinition
from app.services import social_posts

logger = logging.getLogger(__name__)


async def _post_to_linkedin(
    ctx: ExecutorContext, payload: dict[str, Any]
) -> dict[str, Any]:
    post_id_str = payload.get("post_id")
    post_text = payload.get("post_text") or ""

    logger.info(
        "[linkedin-stub] would post: post_id=%r text=%r",
        post_id_str,
        post_text[:120],
    )

    if post_id_str:
        update_fields: dict[str, Any] = {"status": "published"}
        if post_text:
            update_fields["post_text"] = post_text
        pool = await get_pool()
        async with pool.acquire() as conn:
            await social_posts.update_post_conn(
                conn, UUID(post_id_str), **update_fields
            )

    return {
        "stub": True,
        "post_id": post_id_str,
        "would_post_text": post_text[:200],
    }


POST_TO_LINKEDIN = ExecutorDefinition(
    name="post_to_linkedin",
    description="Publish a drafted post to LinkedIn after human approval.",
    execute=_post_to_linkedin,
)
