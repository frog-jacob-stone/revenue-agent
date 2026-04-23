import json
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import Field, field_validator

from app.models.common import ORMBase


class Agent(ORMBase):
    id: UUID
    slug: str
    name: str
    description: str | None = None
    requires_approval: bool = True
    approval_scope: list[str] = Field(default_factory=list)
    config: dict[str, Any] = Field(default_factory=dict)
    system_prompt: str | None = None
    is_active: bool = True
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @field_validator("config", mode="before")
    @classmethod
    def _parse_config(cls, v: Any) -> Any:
        if isinstance(v, str):
            return json.loads(v)
        return v
