"""Project delivery models."""
from __future__ import annotations

from datetime import date, datetime

from app.models.billing import SnapshotRefreshResponse
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
    # From Forecast: the last day a person is booked. Not from Harvest, and not
    # a commitment — it moves whenever the schedule does. Null when nobody is
    # scheduled (hosting, retainers) or Forecast has not been synced.
    projected_end_date: date | None
    is_active: bool
    # The page reads a cache. Surfacing when it was filled is what keeps stale
    # data from reading as live data.
    synced_at: datetime


class ForecastRefreshResponse(ORMBase):
    """What a Forecast refresh found."""

    projects: int
    with_schedule: int
    # Linked to Forecast but nobody booked — hosting and retainers, mostly.
    # Reported separately so "nothing scheduled" cannot be mistaken for a
    # failed sync.
    without_schedule: int
    # Rows dropped because the project is no longer linked to Forecast.
    pruned: int


class ProjectRefreshResponse(ORMBase):
    """One refresh, two sources.

    The tab reads Harvest (name, client, start, end) and Forecast (projected
    end), so refreshing only one of them would leave half the row stale while
    looking like the page had updated. Both are reported separately rather than
    reduced to a single "ok": Harvest can succeed while Forecast is
    unconfigured or down, and that is a partial result, not a failure.
    """

    harvest: SnapshotRefreshResponse
    # Null when Forecast did not run. `forecast_error` says why — the Harvest
    # half still committed, so this is not a 5xx.
    forecast: ForecastRefreshResponse | None = None
    forecast_error: str | None = None
