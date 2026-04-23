from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import Field

from app.models.common import ORMBase


class MemoryKind(str, Enum):
    fact = "fact"
    summary = "summary"
    embedding = "embedding"
    preference = "preference"


class MemoryCreate(ORMBase):
    agent_id: UUID | None = None
    kind: MemoryKind
    scope: str | None = None
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    expires_at: datetime | None = None


class MemoryResponse(ORMBase):
    id: UUID
    agent_id: UUID | None = None
    kind: MemoryKind
    scope: str | None = None
    content: str
    source_workflow_id: UUID | None = None
    source_action_id: UUID | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    expires_at: datetime | None = None
    created_at: datetime
