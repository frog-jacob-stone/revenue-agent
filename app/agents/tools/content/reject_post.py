from typing import Any
from uuid import UUID

from app.agents.tools.base import Done, ToolContext, ToolDefinition, ToolReturn


async def _reject_post(
    ctx: ToolContext,
    *,
    post_id: str,
    **_: Any,
) -> ToolReturn:
    from app.db import get_pool
    from app.services import social_posts as svc

    pool = await get_pool()
    updated = await svc.update_post_status(pool, UUID(post_id), "rejected")
    return Done({"id": str(updated["id"]), "status": updated["status"]})


REJECT_POST = ToolDefinition(
    name="reject_post",
    description="Reject a post by ID. Sets status to 'rejected'.",
    input_schema={
        "type": "object",
        "properties": {
            "post_id": {
                "type": "string",
                "description": "UUID of the post to reject.",
            },
        },
        "required": ["post_id"],
    },
    execute=_reject_post,
)
