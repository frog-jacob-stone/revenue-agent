from typing import Any
from uuid import UUID

from app.tools.base import ToolContext, ToolDefinition


async def _publish_post(
    ctx: ToolContext,
    *,
    post_id: str,
    **_: Any,
) -> dict[str, Any]:
    from app.db import get_pool
    from app.orchestrator import runner
    from app.orchestrator.graphs.content_publish import CONTENT_PUBLISH_KIND
    from app.services import social_posts as svc

    pool = await get_pool()
    post = await svc.get_post(pool, UUID(post_id))
    if not post:
        return {"error": f"Post {post_id} not found"}
    if not post.get("post_text"):
        return {"error": f"Post {post_id} has no text to publish"}

    if ctx.progress is not None:
        from app.services.audit_tail import forward_workflow_to_progress

        workflow_id, drive_task = await runner.start_in_background(
            CONTENT_PUBLISH_KIND,
            initial_state={"post_id": post_id},
            subject_type="social_post",
            subject_id=post_id,
        )
        await forward_workflow_to_progress(
            pool, workflow_id, CONTENT_PUBLISH_KIND, ctx.progress,
            drive_task=drive_task,
        )
    else:
        workflow_id = await runner.start(
            CONTENT_PUBLISH_KIND,
            initial_state={"post_id": post_id},
            subject_type="social_post",
            subject_id=post_id,
        )

    return {
        "post_id": post_id,
        "workflow_id": str(workflow_id),
        "message": "Post is now in your approval inbox. Approve it there to publish.",
    }


PUBLISH_POST = ToolDefinition(
    name="publish_post",
    description=(
        "Queue a post for publishing. Creates a workflow that puts the post in the "
        "approval inbox — the user must approve it there before anything is posted. "
        "Works on posts in any status that have text."
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
