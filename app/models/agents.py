from datetime import datetime
from uuid import UUID

from app.models.common import ORMBase


class AgentRead(ORMBase):
    # DB-backed columns
    id: UUID
    slug: str
    is_active: bool = True
    created_at: datetime | None = None
    updated_at: datetime | None = None
    # Enriched from registry by router (not stored in DB)
    name: str = ""
    description: str | None = None
    requires_approval: bool = True
