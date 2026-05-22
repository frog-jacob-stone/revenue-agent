from typing import Any

from app.tools.base import Done, ToolContext, ToolDefinition, ToolReturn


async def _export_posts(
    ctx: ToolContext,
    **_: Any,
) -> ToolReturn:
    from app.db import get_pool

    pool = await get_pool()

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM social_posts WHERE status IN ('ready', 'published') "
            "ORDER BY created_at ASC"
        )
        posts = [dict(r) for r in rows]

    if not posts:
        return Done({"count": 0, "export": "No ready or published posts found."})

    lines: list[str] = []
    for i, post in enumerate(posts, 1):
        label = post.get("idea_title") or post["topic"]
        status = post.get("status", "")
        lines.append(f"--- Post {i}: {label} [{status}] ---")
        lines.append(post.get("post_text") or "(no text)")
        lines.append("")

    return Done({
        "count": len(posts),
        "export": "\n".join(lines).strip(),
        "post_ids": [str(p["id"]) for p in posts],
    })


EXPORT_POSTS = ToolDefinition(
    name="export_posts",
    description="Return all ready and published posts as clean copy/paste text.",
    input_schema={
        "type": "object",
        "properties": {},
    },
    execute=_export_posts,
)
