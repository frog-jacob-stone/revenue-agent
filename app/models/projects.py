"""Project delivery models."""
from __future__ import annotations

from datetime import date, datetime

from app.models.common import ORMBase


class ProjectSummary(ORMBase):
    """One engagement as the Projects tab shows it.

    Everything here comes from the Harvest snapshot cache. Both dates are
    nullable because Harvest treats them as optional — and `ends_on` is
    editable in Harvest, so it is the current end date rather than a
    contractual commitment. A committed end arrives with contracts.
    """

    harvest_id: int
    name: str
    client_name: str | None
    starts_on: date | None
    ends_on: date | None
    is_active: bool
    # The page reads a cache. Surfacing when it was filled is what keeps stale
    # data from reading as live data.
    synced_at: datetime
