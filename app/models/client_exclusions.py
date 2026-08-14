"""Client exclusion models."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.models.common import ORMBase


class ExcludedClient(ORMBase):
    """A Harvest client this system treats as not-a-client."""

    harvest_client_id: int
    # Null when the snapshot cache has no row for this id — the client was
    # deleted in Harvest, or a resync has not run. The exclusion still stands.
    client_name: str | None
    reason: str | None
    project_count: int
    excluded_at: datetime
    excluded_by: str


class ExcludeClientRequest(BaseModel):
    harvest_client_id: int
    reason: str | None = Field(
        None,
        max_length=500,
        description=(
            "Why this client is not a client. Optional, but it is what makes "
            "the row interpretable later."
        ),
    )
