"""HTTP-layer tests for the chat router endpoints."""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import patch

import pytest

from app.db import get_pool


async def _fake_stream(events: list[dict[str, Any]]):
    async def gen(_agent_slug, _history):
        for e in events:
            await asyncio.sleep(0)
            yield e

    return gen


@pytest.mark.asyncio
async def test_create_session_unknown_agent_returns_404(client):
    res = await client.post("/chat/sessions", json={"agent_slug": "no-such-agent"})
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_session_crud_roundtrip(client):
    create = await client.post(
        "/chat/sessions", json={"agent_slug": "content-orchestrator"}
    )
    assert create.status_code == 200
    session = create.json()
    sid = session["id"]
    assert session["agent_slug"] == "content-orchestrator"
    assert session["title"] == "New chat"

    listed = await client.get("/chat/sessions?agent_slug=content-orchestrator")
    assert listed.status_code == 200
    assert any(s["id"] == sid for s in listed.json())

    got = await client.get(f"/chat/sessions/{sid}")
    assert got.status_code == 200
    assert got.json()["id"] == sid

    deleted = await client.delete(f"/chat/sessions/{sid}")
    assert deleted.status_code == 204
    after = await client.get(f"/chat/sessions/{sid}")
    assert after.status_code == 404


@pytest.mark.asyncio
async def test_post_chat_persists_user_message_and_streams(client):
    create = await client.post(
        "/chat/sessions", json={"agent_slug": "content-orchestrator"}
    )
    sid = create.json()["id"]

    events = [
        {"type": "delta", "text": "Hello"},
        {"type": "delta", "text": ", world"},
        {"type": "done", "answer": "Hello, world", "tool_used": None},
    ]
    fake_gen = await _fake_stream(events)
    with patch("app.services.chat_runtime.agent_chat_stream", new=fake_gen):
        res = await client.post(
            "/chat/content-orchestrator",
            json={"session_id": sid, "content": "hi"},
        )
        assert res.status_code == 200
        body = res.text
        # SSE frames for each event type appear in order
        assert "event: delta" in body
        assert "event: done" in body

    msgs = await client.get(f"/chat/sessions/{sid}/messages")
    assert msgs.status_code == 200
    messages = msgs.json()
    assert len(messages) == 2
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == "hi"
    assert messages[1]["role"] == "assistant"
    assert messages[1]["status"] == "complete"
    assert messages[1]["content"] == "Hello, world"


@pytest.mark.asyncio
async def test_post_chat_rejects_when_streaming_row_exists(client):
    create = await client.post(
        "/chat/sessions", json={"agent_slug": "content-orchestrator"}
    )
    sid = create.json()["id"]

    # Seed a streaming row directly so the next POST should 409.
    pool = await get_pool()
    from app.services import chat_sessions as cs
    await cs.append_user_message_and_prepare_turn(pool, create.json()["id"], "first")

    res = await client.post(
        "/chat/content-orchestrator",
        json={"session_id": sid, "content": "second"},
    )
    assert res.status_code == 409


@pytest.mark.asyncio
async def test_post_chat_rejects_session_for_different_agent(client):
    create = await client.post(
        "/chat/sessions", json={"agent_slug": "content-orchestrator"}
    )
    sid = create.json()["id"]
    res = await client.post(
        "/chat/revenue-recognition",
        json={"session_id": sid, "content": "hi"},
    )
    assert res.status_code == 400


@pytest.mark.asyncio
async def test_post_chat_unknown_session_returns_404(client):
    res = await client.post(
        "/chat/content-orchestrator",
        json={
            "session_id": "00000000-0000-0000-0000-000000000000",
            "content": "hi",
        },
    )
    assert res.status_code == 404
