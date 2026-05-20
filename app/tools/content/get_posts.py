from typing import Any

from app.tools.base import ToolContext, ToolDefinition


async def _get_posts(
    ctx: ToolContext,
    *,
    status: str | None = None,
    **_: Any,
) -> dict[str, Any]:
    from app.db import get_pool
    from app.services import social_posts as svc

    pool = await get_pool()

    if status:
        posts = await svc.get_posts_by_status(pool, status)
    else:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM social_posts WHERE status NOT IN ('rejected', 'published') "
                "ORDER BY created_at ASC"
            )
            posts = [dict(r) for r in rows]

    return {
        "count": len(posts),
        "posts": [
            {
                "id": str(p["id"]),
                "topic": p["topic"],
                "idea_title": p.get("idea_title"),
                "core_angle": p.get("core_angle"),
                "status": p["status"],
                "post_text": p.get("post_text"),
                "created_at": str(p["created_at"]),
            }
            for p in posts
        ],
    }


GET_POSTS = ToolDefinition(
    name="get_posts",
    description=(
        "Retrieve posts, optionally filtered by status. "
        "Valid statuses: draft, needs_revision, ready, rejected, published. "
        "Omit status to see all active (non-rejected, non-published) posts."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "status": {
                "type": "string",
                "description": "Filter by status. Omit to see all active posts.",
            },
        },
    },
    execute=_get_posts,
)
