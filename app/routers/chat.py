"""Chat HTTP layer.

Single front-door pattern: there is exactly one conversational agent
(`chat_turn.FRONT_DOOR_SLUG`). The router doesn't take an agent slug —
session creation defaults at the DB layer; sending a message goes through
`chat_turn.start_turn`.
"""
import asyncio
import json
import logging
from uuid import UUID

from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import StreamingResponse

from app.db import get_pool
from app.models.chat import (
    ChatMessageResponse,
    ChatSendRequest,
    ChatSessionCreate,
    ChatSessionResponse,
)
from app.services import chat_sessions as sessions
from app.services.chat_turn import FRONT_DOOR_SLUG, start_turn

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/chat", tags=["chat"])


def _sse(event_type: str, data: dict) -> bytes:
    return f"event: {event_type}\ndata: {json.dumps(data, default=str)}\n\n".encode()


# ── Sessions ────────────────────────────────────────────────────────────────


@router.post("/sessions", response_model=ChatSessionResponse)
async def create_session(body: ChatSessionCreate | None = None):
    """Create a chat session. Body is optional — when omitted, the DB DEFAULT
    (`'chief-of-staff'`) is used for `agent_slug`. Callers may pass an explicit
    slug to opt out of the default."""
    pool = await get_pool()
    slug = body.agent_slug if body else None
    row = await sessions.create_session(pool, slug)
    return ChatSessionResponse.model_validate(row)


@router.get("/sessions", response_model=list[ChatSessionResponse])
async def list_sessions(agent_slug: str = FRONT_DOOR_SLUG):
    """List sessions for an agent. Defaults to the front-door slug."""
    pool = await get_pool()
    rows = await sessions.list_sessions(pool, agent_slug)
    return [ChatSessionResponse.model_validate(r) for r in rows]


@router.get("/sessions/{session_id}", response_model=ChatSessionResponse)
async def get_session(session_id: UUID):
    pool = await get_pool()
    row = await sessions.get_session(pool, session_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return ChatSessionResponse.model_validate(row)


@router.get(
    "/sessions/{session_id}/messages",
    response_model=list[ChatMessageResponse],
)
async def get_messages(session_id: UUID):
    pool = await get_pool()
    row = await sessions.get_session(pool, session_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Session not found")
    msgs = await sessions.get_messages(pool, session_id)
    return [ChatMessageResponse.model_validate(m) for m in msgs]


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_session(session_id: UUID):
    pool = await get_pool()
    deleted = await sessions.delete_session(pool, session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Session not found")
    return Response(status_code=204)


# ── Send a message ──────────────────────────────────────────────────────────


@router.post("/sessions/{session_id}/messages")
async def send_message(session_id: UUID, body: ChatSendRequest):
    """Send a user message and stream the assistant's response as SSE.

    The turn is detached into a background task — if the client disconnects,
    work continues and the final assistant message is persisted. SSE events:
    delta, tool_call_started, workflow_started, workflow_event,
    tool_call_completed, done, error.
    """
    pool = await get_pool()
    session = await sessions.get_session(pool, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if await sessions.has_streaming_message(pool, session_id):
        raise HTTPException(
            status_code=409,
            detail="A turn is already in progress for this session",
        )

    runtime = await start_turn(pool, session_id, body.content)
    queue = runtime.subscribe()
    if queue is None:
        # Race: task completed before we could subscribe. Stream nothing — the
        # client will re-fetch messages and see the final state.
        async def empty():
            yield _sse("done", {"type": "done", "answer": "", "tool_used": None})

        return StreamingResponse(
            empty(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    async def gen():
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    # Keepalive to defeat proxy idle timeouts.
                    yield b": ping\n\n"
                    continue
                if event is None:
                    return
                yield _sse(event["type"], event)
        finally:
            runtime.unsubscribe(queue)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
