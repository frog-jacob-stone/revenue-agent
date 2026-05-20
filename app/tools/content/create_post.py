from typing import Any

from app.tools.base import ToolContext, ToolDefinition


async def _create_post(
    ctx: ToolContext,
    *,
    brief: str,
    channel: str = "linkedin",
    instructions: str | None = None,
    **_: Any,
) -> dict[str, Any]:
    from app.db import get_pool
    from app.orchestrator.graphs.content_creation import CONTENT_CREATION_KIND
    from app.orchestrator.runner import runner
    from app.services import social_posts as svc

    pool = await get_pool()

    # Pre-create the social_posts row so post_id is available throughout the graph.
    post = await svc.save_post(pool, topic=brief)
    post_id = post["id"]

    initial_state: dict[str, Any] = {
        "brief": brief,
        "channel": channel,
        "post_id": str(post_id),
    }
    if instructions:
        initial_state["instructions"] = instructions

    if ctx.progress is not None:
        from app.services.audit_tail import forward_workflow_to_progress

        workflow_id, drive_task = await runner.start_in_background(
            CONTENT_CREATION_KIND,
            initial_state=initial_state,
            subject_type="social_post",
            subject_id=str(post_id),
        )
        await forward_workflow_to_progress(
            pool, workflow_id, CONTENT_CREATION_KIND, ctx.progress,
            drive_task=drive_task,
        )
    else:
        workflow_id = await runner.start(
            CONTENT_CREATION_KIND,
            initial_state=initial_state,
            subject_type="social_post",
            subject_id=str(post_id),
        )

    refreshed = await svc.get_post(pool, post_id)
    return {
        "post_id": str(post_id),
        "workflow_id": str(workflow_id),
        "status": (refreshed or post).get("status"),
        "idea_title": (refreshed or post).get("idea_title"),
        "post_text": (refreshed or post).get("post_text"),
    }


CREATE_POST = ToolDefinition(
    name="create_post",
    description=(
        "Draft a LinkedIn post from a brief. "
        "Runs the full content_creation chain: interprets the brief, writes a draft, "
        "and runs voice review. Returns when the post is 'ready' (passed review) or "
        "'needs_revision' (failed after max retries). "
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
