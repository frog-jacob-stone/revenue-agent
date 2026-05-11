from datetime import datetime
from typing import Any
from uuid import UUID

from app.models.common import ORMBase


class ChatSessionCreate(ORMBase):
    agent_slug: str


class ChatSessionResponse(ORMBase):
    id: UUID
    agent_slug: str
    title: str
    created_at: datetime
    updated_at: datetime
    last_message_at: datetime | None = None


class ChatMessageResponse(ORMBase):
    id: int
    session_id: UUID
    turn_id: UUID | None = None
    role: str
    content: str
    activity: list[dict[str, Any]]
    status: str
    tool_used: str | None = None
    error: str | None = None
    created_at: datetime
    completed_at: datetime | None = None


class ChatSendRequest(ORMBase):
    session_id: UUID
    content: str
