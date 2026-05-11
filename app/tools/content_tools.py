import json
import logging
from typing import Any
from uuid import UUID

from app.tools.base import ToolContext, ToolDefinition

logger = logging.getLogger(__name__)

_MODEL = "gpt-4o-mini"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _llm(system: str, user: str) -> str:
    """Single OpenAI call. Returns the text content of the first choice."""
    from app.config import settings
    from app.integrations.openai_client import get_client

    if not settings.openai_api_key:
        return "{}"

    client = get_client()
    response = await client.chat.completions.create(
        model=_MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        response_format={"type": "json_object"},
    )
    return response.choices[0].message.content or "{}"


def _parse(raw: str) -> dict[str, Any]:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Failed to parse LLM JSON response: %s", raw[:200])
        return {}


# ---------------------------------------------------------------------------
# Tool: create_post
# Triggers the content_creation chain. The chain handles strategy → draft → voice.
# ---------------------------------------------------------------------------


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

    # Return current post state so the orchestrator agent can report back.
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


# ---------------------------------------------------------------------------
# Tool: get_posts
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Tool: rewrite_post
# ---------------------------------------------------------------------------


async def _rewrite_post(
    ctx: ToolContext,
    *,
    post_id: str,
    instruction: str,
    channel: str = "linkedin",
    **_: Any,
) -> dict[str, Any]:
    from app.agents.content import LinkedInWritingAgent
    from app.db import get_pool
    from app.services import social_posts as svc

    pool = await get_pool()
    post = await svc.get_post(pool, UUID(post_id))
    if not post:
        return {"error": f"Post {post_id} not found"}

    user_msg = (
        f"Current post:\n\n{post.get('post_text', '')}\n\n"
        f"Idea context:\n"
        f"Title: {post.get('idea_title', '')}\n"
        f"Angle: {post.get('core_angle', '')}\n\n"
        f"Rewrite instruction: {instruction}\n"
        f"Channel: {channel}"
    )

    raw = await _llm(LinkedInWritingAgent.system_prompt, user_msg)
    draft = _parse(raw)

    post_text = draft.get("post_text") or post.get("post_text", "")

    updated = await svc.update_post(
        pool,
        UUID(post_id),
        post_text=post_text,
        status="draft",
    )

    return {
        "id": str(updated["id"]),
        "post_text": post_text,
        "status": updated["status"],
        "hook": draft.get("hook"),
        "cta": draft.get("cta"),
    }


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
                "description": "What to change. E.g. 'Make the hook more direct', 'Cut the last paragraph'.",
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


# ---------------------------------------------------------------------------
# Tool: reject_post
# ---------------------------------------------------------------------------


async def _reject_post(
    ctx: ToolContext,
    *,
    post_id: str,
    **_: Any,
) -> dict[str, Any]:
    from app.db import get_pool
    from app.services import social_posts as svc

    pool = await get_pool()
    updated = await svc.update_post_status(pool, UUID(post_id), "rejected")
    return {"id": str(updated["id"]), "status": updated["status"]}


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


# ---------------------------------------------------------------------------
# Tool: publish_post
# Triggers the content_publish chain — puts the post in the approval inbox.
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Tool: export_posts
# ---------------------------------------------------------------------------


async def _export_posts(
    ctx: ToolContext,
    **_: Any,
) -> dict[str, Any]:
    from app.db import get_pool
    from app.services import social_posts as svc

    pool = await get_pool()

    # Export ready + published posts
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM social_posts WHERE status IN ('ready', 'published') "
            "ORDER BY created_at ASC"
        )
        posts = [dict(r) for r in rows]

    if not posts:
        return {"count": 0, "export": "No ready or published posts found."}

    lines: list[str] = []
    for i, post in enumerate(posts, 1):
        label = post.get("idea_title") or post["topic"]
        status = post.get("status", "")
        lines.append(f"--- Post {i}: {label} [{status}] ---")
        lines.append(post.get("post_text") or "(no text)")
        lines.append("")

    return {
        "count": len(posts),
        "export": "\n".join(lines).strip(),
        "post_ids": [str(p["id"]) for p in posts],
    }


EXPORT_POSTS = ToolDefinition(
    name="export_posts",
    description="Return all ready and published posts as clean copy/paste text.",
    input_schema={
        "type": "object",
        "properties": {},
    },
    execute=_export_posts,
)


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------

ALL_CONTENT_TOOLS: list[ToolDefinition] = [
    CREATE_POST,
    GET_POSTS,
    REWRITE_POST,
    REJECT_POST,
    PUBLISH_POST,
    EXPORT_POSTS,
]
