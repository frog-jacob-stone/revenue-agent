"""End-to-end tests for the content_publish v2 graph.

Three paths:
  - happy: pause → approve → resume → social_posts.status='published'
  - reject: pause → reject → workflow failed, post stays at 'ready'
  - edited payload: approve with executed_payload override → published with edited text
"""
from __future__ import annotations

import json
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient

from app.db import get_pool
from app.orchestrator_v2 import runner
from app.orchestrator_v2.graphs.content_publish import (
    ACTION_TYPE,
    CONTENT_AGENT_SLUG,
    CONTENT_PUBLISH_KIND,
    build_graph,
)


@pytest.fixture(autouse=True)
def _register_graph():
    """Register content_publish for the duration of one test, unregister after."""
    if not runner.is_registered(CONTENT_PUBLISH_KIND):
        runner.register(CONTENT_PUBLISH_KIND, build_graph)
    yield
    runner.unregister(CONTENT_PUBLISH_KIND)


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


async def test_happy_path_publishes_post(client: AsyncClient, test_agent_slug):
    post_id = await _seed_ready_post(post_text="Original draft.")

    wf_id = await runner.start(
        CONTENT_PUBLISH_KIND,
        initial_state={"post_id": str(post_id)},
        subject_type="social_post",
        subject_id=str(post_id),
    )

    pool = await get_pool()

    # Workflow paused at the approval gate.
    wf = await pool.fetchrow("SELECT status, kind FROM workflows WHERE id = $1", wf_id)
    assert wf["status"] == "awaiting_approval"
    assert wf["kind"] == CONTENT_PUBLISH_KIND

    # Exactly one approval row, with the v1-equivalent payload shape.
    appr = await pool.fetchrow(
        "SELECT * FROM approvals WHERE workflow_id = $1", wf_id
    )
    assert appr is not None
    assert appr["status"] == "pending"
    assert appr["action_type"] == ACTION_TYPE
    assert appr["risk_level"] == "medium"
    assert appr["agent_slug"] == CONTENT_AGENT_SLUG

    proposed = appr["proposed_payload"]
    if isinstance(proposed, str):
        proposed = json.loads(proposed)
    assert proposed["post_id"] == str(post_id)
    assert proposed["post_text"] == "Original draft."
    assert proposed["idea_title"] == "test idea"
    assert proposed["status"] == "ready"

    # Approve via HTTP (mirrors the inbox flow).
    resp = await client.post(
        f"/approvals/{appr['id']}/approve",
        json={"approved_by": "tester"},
    )
    assert resp.status_code == 200, resp.text

    # Drive resume explicitly (BackgroundTasks doesn't run in this test loop).
    await runner.resume(wf_id)

    # Workflow completed and post published.
    wf_after = await pool.fetchrow("SELECT status FROM workflows WHERE id = $1", wf_id)
    assert wf_after["status"] == "completed"

    post_after = await pool.fetchrow(
        "SELECT status, post_text FROM social_posts WHERE id = $1", post_id
    )
    assert post_after["status"] == "published"
    # Original text preserved (no executed_payload override).
    assert post_after["post_text"] == "Original draft."

    appr_after = await pool.fetchrow(
        "SELECT status FROM approvals WHERE id = $1", appr["id"]
    )
    assert appr_after["status"] == "executed"


async def test_reject_leaves_post_at_ready(client: AsyncClient, test_agent_slug):
    post_id = await _seed_ready_post(post_text="Draft for rejection.")

    wf_id = await runner.start(
        CONTENT_PUBLISH_KIND,
        initial_state={"post_id": str(post_id)},
    )

    pool = await get_pool()
    appr = await pool.fetchrow(
        "SELECT id FROM approvals WHERE workflow_id = $1", wf_id
    )

    resp = await client.post(
        f"/approvals/{appr['id']}/reject",
        json={"rejected_by": "tester", "rejection_reason": "off voice"},
    )
    assert resp.status_code == 200, resp.text

    await runner.resume(wf_id)

    wf_after = await pool.fetchrow("SELECT status FROM workflows WHERE id = $1", wf_id)
    assert wf_after["status"] == "failed"

    post_after = await pool.fetchrow(
        "SELECT status, post_text FROM social_posts WHERE id = $1", post_id
    )
    # Post stays at ready, text untouched — matches v1 contract.
    assert post_after["status"] == "ready"
    assert post_after["post_text"] == "Draft for rejection."


async def test_edited_payload_publishes_with_human_edits(
    client: AsyncClient, test_agent_slug,
):
    post_id = await _seed_ready_post(post_text="Original.")

    wf_id = await runner.start(
        CONTENT_PUBLISH_KIND,
        initial_state={"post_id": str(post_id)},
    )

    pool = await get_pool()
    appr = await pool.fetchrow(
        "SELECT id FROM approvals WHERE workflow_id = $1", wf_id
    )

    edited = {
        "post_id": str(post_id),
        "idea_title": "test idea",
        "post_text": "EDITED BY HUMAN",
        "status": "ready",
    }
    resp = await client.post(
        f"/approvals/{appr['id']}/approve",
        json={"approved_by": "tester", "executed_payload": edited},
    )
    assert resp.status_code == 200, resp.text

    await runner.resume(wf_id)

    post_after = await pool.fetchrow(
        "SELECT status, post_text FROM social_posts WHERE id = $1", post_id
    )
    assert post_after["status"] == "published"
    assert post_after["post_text"] == "EDITED BY HUMAN"
