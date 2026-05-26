"""HTTP-layer tests for the chat router.

Single front-door pattern: there is no `/{agent_slug}` path segment any more.
Sessions default to `chief-of-staff` at the DB layer.
"""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import patch

import pytest

from app.db import get_pool


async def _fake_stream(events: list[dict[str, Any]]):
    async def gen(_history):
        for e in events:
            await asyncio.sleep(0)
            yield e

    return gen


@pytest.mark.asyncio
async def test_create_session_with_no_body_uses_front_door_default(client):
    res = await client.post("/chat/sessions", json={})
    assert res.status_code == 200
    body = res.json()
    assert body["agent_slug"] == "chief-of-staff"
    assert body["title"] == "New chat"


@pytest.mark.asyncio
async def test_create_session_with_explicit_slug_honored(client):
    res = await client.post("/chat/sessions", json={"agent_slug": "chief-of-staff"})
    assert res.status_code == 200
    assert res.json()["agent_slug"] == "chief-of-staff"


@pytest.mark.asyncio
async def test_session_crud_roundtrip(client):
    create = await client.post("/chat/sessions", json={})
    assert create.status_code == 200
    sid = create.json()["id"]

    listed = await client.get("/chat/sessions")
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
async def test_post_message_persists_user_message_and_streams(client):
    create = await client.post("/chat/sessions", json={})
    sid = create.json()["id"]

    events = [
        {"type": "delta", "text": "Hello"},
        {"type": "delta", "text": ", world"},
        {"type": "done", "answer": "Hello, world", "tool_used": None},
    ]
    fake_gen = await _fake_stream(events)
    with patch("app.services.chat_turn._stream_llm_turn", new=fake_gen):
        res = await client.post(
            f"/chat/sessions/{sid}/messages",
            json={"content": "hi"},
        )
        assert res.status_code == 200
        body = res.text
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
async def test_post_message_rejects_when_streaming_row_exists(client):
    create = await client.post("/chat/sessions", json={})
    sid = create.json()["id"]

    # Seed a streaming row directly so the next POST should 409.
    pool = await get_pool()
    await pool.execute(
        """
        INSERT INTO chat_messages (session_id, role, content, status)
        VALUES ($1, 'assistant', '', 'streaming')
        """,
        create.json()["id"],
    )

    res = await client.post(
        f"/chat/sessions/{sid}/messages",
        json={"content": "second"},
    )
    assert res.status_code == 409


@pytest.mark.asyncio
async def test_post_message_unknown_session_returns_404(client):
    res = await client.post(
        "/chat/sessions/00000000-0000-0000-0000-000000000000/messages",
        json={"content": "hi"},
    )
    assert res.status_code == 404
