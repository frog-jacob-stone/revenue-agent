import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services.chat import agent_chat

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/chat", tags=["chat"])


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage]


class ChatResponse(BaseModel):
    answer: str
    tool_used: str | None = None


@router.post("/{agent_slug}", response_model=ChatResponse)
async def chat_with_agent(agent_slug: str, req: ChatRequest):
    try:
        result = await agent_chat(agent_slug, [m.model_dump() for m in req.messages])
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        logger.exception("Chat failed for agent %s", agent_slug)
        raise HTTPException(status_code=500, detail=str(exc))
    return ChatResponse(**result)
