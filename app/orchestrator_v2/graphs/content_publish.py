"""content_publish — first real workflow on the v2 LangGraph runner.

Two nodes, one approval gate:

    propose_post  →  [interrupt_before]  →  post_to_linkedin  →  END

`propose_post` reads the social_posts row identified by initial_state.post_id
and returns the payload under `_propose` so the runner writes an `approvals`
row. The graph then pauses at `post_to_linkedin`.

After human approval, the runner resumes the graph. `post_to_linkedin` reads
the (possibly edited) `executed_payload` from state, marks the post as
published, and the graph terminates.

Mirrors the v1 chain at `app/orchestrator/chains/content.py`. v1 is being
unregistered in this phase; the chain file stays until Phase 5 cleanup.
"""
from __future__ import annotations

import logging
from typing import Any, NotRequired
from uuid import UUID

from langgraph.graph import END, StateGraph

from app.db import get_pool
from app.orchestrator_v2.runner import GraphSpec
from app.orchestrator_v2.state import BaseGraphState
from app.services import social_posts

logger = logging.getLogger(__name__)


CONTENT_PUBLISH_KIND = "content_publish"
CONTENT_AGENT_SLUG = "content-orchestrator"
ACTION_TYPE = "post_to_linkedin"


class ContentPublishState(BaseGraphState, total=False):
    post_id: NotRequired[str]
    executed_payload: NotRequired[dict[str, Any]]
    result: NotRequired[dict[str, Any]]


async def propose_post(state: ContentPublishState) -> ContentPublishState:
    """Read the social_posts row and return a `_propose` for the approval row."""
    post_id_str = state.get("post_id")
    if not post_id_str:
        return {"_propose": {
            "action_type": ACTION_TYPE,
            "agent_slug": CONTENT_AGENT_SLUG,
            "risk_level": "medium",
            "summary": "Cannot publish: no post_id in initial state",
            "proposed_payload": {"error": "no post_id"},
        }}

    pool = await get_pool()
    async with pool.acquire() as conn:
        post = await social_posts.get_post_conn(conn, UUID(post_id_str))

    if not post:
        return {"_propose": {
            "action_type": ACTION_TYPE,
            "agent_slug": CONTENT_AGENT_SLUG,
            "risk_level": "medium",
            "summary": f"Cannot publish: post {post_id_str} not found",
            "proposed_payload": {"error": f"post {post_id_str} not found"},
        }}

    proposed_payload = {
        "post_id": str(post["id"]),
        "idea_title": post.get("idea_title"),
        "post_text": post.get("post_text"),
        "status": post.get("status"),
    }
    return {
        "_propose": {
            "action_type": ACTION_TYPE,
            "agent_slug": CONTENT_AGENT_SLUG,
            "risk_level": "medium",
            "summary": post.get("idea_title") or "LinkedIn post",
            "proposed_payload": proposed_payload,
        },
    }


async def post_to_linkedin(state: ContentPublishState) -> ContentPublishState:
    """Stub executor — logs the post, marks social_posts.status = 'published'."""
    payload = state.get("executed_payload") or {}
    post_id_str = payload.get("post_id") or state.get("post_id")
    post_text = payload.get("post_text") or ""

    logger.info(
        "[linkedin-stub v2] would post: post_id=%r text=%r",
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
        "result": {
            "stub": True,
            "post_id": post_id_str,
            "would_post_text": post_text[:200],
        },
    }


def build_graph() -> GraphSpec:
    g: StateGraph = StateGraph(ContentPublishState)
    g.add_node("propose_post", propose_post)
    g.add_node("post_to_linkedin", post_to_linkedin)
    g.set_entry_point("propose_post")
    g.add_edge("propose_post", "post_to_linkedin")
    g.add_edge("post_to_linkedin", END)
    return GraphSpec(graph=g, interrupt_before=("post_to_linkedin",))
