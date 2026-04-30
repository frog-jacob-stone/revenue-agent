import json
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import Field, field_validator

from app.models.common import ORMBase


class Agent(ORMBase):
    # DB-backed columns
    id: UUID
    slug: str
    config: dict[str, Any] = Field(default_factory=dict)
    is_active: bool = True
    created_at: datetime | None = None
    updated_at: datetime | None = None
    # Enriched from registry by router (not stored in DB)
    name: str = ""
    description: str | None = None
    requires_approval: bool = True
    is_conversational: bool = False

    @field_validator("config", mode="before")
    @classmethod
    def _parse_config(cls, v: Any) -> Any:
        if isinstance(v, str):
            return json.loads(v)
        return v
