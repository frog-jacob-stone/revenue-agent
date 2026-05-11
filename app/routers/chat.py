import json
import logging

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.services.chat import agent_chat_stream

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/chat", tags=["chat"])


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage]


def _sse(event_type: str, data: dict) -> bytes:
    return f"event: {event_type}\ndata: {json.dumps(data, default=str)}\n\n".encode()


@router.post("/{agent_slug}")
async def chat_with_agent(agent_slug: str, req: ChatRequest):
    """Stream a chat turn as Server-Sent Events.

    Event types: delta, tool_call_started, workflow_started, workflow_event,
    tool_call_completed, done, error.
    """
    async def gen():
        try:
            async for evt in agent_chat_stream(
                agent_slug, [m.model_dump() for m in req.messages]
            ):
                yield _sse(evt["type"], evt)
        except ValueError as exc:
            yield _sse("error", {"type": "error", "message": str(exc), "status": 404})
        except Exception as exc:
            logger.exception("Chat stream failed for agent %s", agent_slug)
            yield _sse("error", {"type": "error", "message": str(exc), "status": 500})

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
