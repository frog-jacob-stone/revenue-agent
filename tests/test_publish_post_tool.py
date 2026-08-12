"""End-to-end tests for the inlined `publish_post` tool (ADR-0002, plan 17).

Replaces the deleted `test_content_publish_graph.py`. Tests the full
`publish_post tool → approval row → POST /approve → executor runs`
chain.

`httpx.AsyncClient(transport=ASGITransport(app))` runs FastAPI background
tasks to completion before the response returns, so no extra awaiting is
needed after `await client.post('/approvals/.../approve')`.
"""
from __future__ import annotations

import uuid
from uuid import UUID

import pytest
from httpx import AsyncClient

from app.db import get_pool
from app.agents.tools.base import AwaitingApproval, Blocked, ToolContext
from app.agents.tools.content.publish_post import _publish_post


async def _seed_ready_post(*, post_text: str = "Hello world.") -> UUID:
    pool = await get_pool()
    return await pool.fetchval(
        """
        INSERT INTO social_posts (topic, idea_title, core_angle, post_text, status)
        VALUES ($1, $2, $3, $4, 'ready')
        RETURNING id
        """,
        "test topic",
        "test idea",
        "test angle",
        post_text,
    )


def _ctx() -> ToolContext:
    return ToolContext(agent_id=uuid.UUID(int=0), agent_slug="chief-of-staff")


@pytest.mark.asyncio
async def test_publish_post_returns_awaiting_approval():
    """Tool reads the post and returns AwaitingApproval with the expected fields."""
    post_id = await _seed_ready_post(post_text="Original draft.")

    result = await _publish_post(_ctx(), post_id=str(post_id))

    assert isinstance(result, AwaitingApproval)
    assert result.executor == "post_to_linkedin"
    assert result.action_type == "post_to_linkedin"
    assert result.risk_level == "medium"
    assert result.summary == "test idea"
    assert result.payload["post_id"] == str(post_id)
    assert result.payload["post_text"] == "Original draft."
    assert result.payload["idea_title"] == "test idea"
    assert result.payload["status"] == "ready"


@pytest.mark.asyncio
async def test_missing_post_returns_blocked():
    """Tool returns Blocked, not AwaitingApproval, when the post does not exist."""
    fake_id = str(uuid.uuid4())
    result = await _publish_post(_ctx(), post_id=fake_id)

    assert isinstance(result, Blocked)
    assert fake_id in result.reason
    assert result.hint == {"post_id": fake_id}


@pytest.mark.asyncio
async def test_post_without_text_returns_blocked():
    """Tool returns Blocked when the post has no text to publish."""
    pool = await get_pool()
    post_id = await pool.fetchval(
        """
        INSERT INTO social_posts (topic, idea_title, core_angle, post_text, status)
        VALUES ('topic', 'title', 'angle', NULL, 'ready')
        RETURNING id
        """,
    )

    result = await _publish_post(_ctx(), post_id=str(post_id))

    assert isinstance(result, Blocked)
    assert "no text to publish" in result.reason
    assert result.hint == {"post_id": str(post_id), "status": "ready"}


@pytest.mark.asyncio
async def test_approve_runs_executor_and_publishes(
    client: AsyncClient, test_agent_slug
):
    """Happy path: tool → approval row → POST /approve → executor → status='published'."""
    from app.orchestrator.dispatch import dispatch_tool
    from app.agents.tools.content.publish_post import PUBLISH_POST

    post_id = await _seed_ready_post(post_text="Original draft.")
    pool = await get_pool()

    # Run through dispatch_tool so the approval row gets created the same way
    # the LLM-driven path would.
    result_dict = await dispatch_tool(
        PUBLISH_POST, _ctx(), {"post_id": str(post_id)}
    )
    assert result_dict["status"] == "awaiting_approval"
    approval_id = result_dict["approval_id"]

    # Exactly one pending approval, with the expected fields.
    appr = await pool.fetchrow(
        "SELECT * FROM approvals WHERE id = $1", UUID(approval_id)
    )
    assert appr is not None
    assert appr["status"] == "pending"
    assert appr["executor"] == "post_to_linkedin"
    assert appr["workflow_id"] is None  # tool-driven, no workflow
    assert appr["action_type"] == "post_to_linkedin"

    # Approve via HTTP — the router schedules the executor as a background task,
    # which runs to completion before the response returns under ASGITransport.
    resp = await client.post(
        f"/approvals/{approval_id}/approve",
        json={"approved_by": "tester"},
    )
    assert resp.status_code == 200, resp.text

    appr_after = await pool.fetchrow(
        "SELECT status FROM approvals WHERE id = $1", UUID(approval_id)
    )
    assert appr_after["status"] == "executed"

    post_after = await pool.fetchrow(
        "SELECT status, post_text FROM social_posts WHERE id = $1", post_id
    )
    assert post_after["status"] == "published"
    # Original text preserved (no executed_payload override).
    assert post_after["post_text"] == "Original draft."


@pytest.mark.asyncio
async def test_approve_with_edited_payload(client: AsyncClient, test_agent_slug):
    """Approving with executed_payload override uses the edited text."""
    from app.orchestrator.dispatch import dispatch_tool
    from app.agents.tools.content.publish_post import PUBLISH_POST

    post_id = await _seed_ready_post(post_text="Original.")
    pool = await get_pool()

    result_dict = await dispatch_tool(
        PUBLISH_POST, _ctx(), {"post_id": str(post_id)}
    )
    approval_id = result_dict["approval_id"]

    edited = {
        "post_id": str(post_id),
        "idea_title": "test idea",
        "post_text": "EDITED BY HUMAN",
        "status": "ready",
    }
    resp = await client.post(
        f"/approvals/{approval_id}/approve",
        json={"approved_by": "tester", "executed_payload": edited},
    )
    assert resp.status_code == 200, resp.text

    post_after = await pool.fetchrow(
        "SELECT status, post_text FROM social_posts WHERE id = $1", post_id
    )
    assert post_after["status"] == "published"
    assert post_after["post_text"] == "EDITED BY HUMAN"


@pytest.mark.asyncio
async def test_reject_leaves_post_unchanged(client: AsyncClient, test_agent_slug):
    """Rejecting the approval leaves the post at 'ready'; no executor runs."""
    from app.orchestrator.dispatch import dispatch_tool
    from app.agents.tools.content.publish_post import PUBLISH_POST

    post_id = await _seed_ready_post(post_text="Draft for rejection.")
    pool = await get_pool()

    result_dict = await dispatch_tool(
        PUBLISH_POST, _ctx(), {"post_id": str(post_id)}
    )
    approval_id = result_dict["approval_id"]

    resp = await client.post(
        f"/approvals/{approval_id}/reject",
        json={"rejected_by": "tester", "rejection_reason": "off voice"},
    )
    assert resp.status_code == 200, resp.text

    appr_after = await pool.fetchrow(
        "SELECT status FROM approvals WHERE id = $1", UUID(approval_id)
    )
    assert appr_after["status"] == "rejected"

    post_after = await pool.fetchrow(
        "SELECT status, post_text FROM social_posts WHERE id = $1", post_id
    )
    assert post_after["status"] == "ready"
    assert post_after["post_text"] == "Draft for rejection."
