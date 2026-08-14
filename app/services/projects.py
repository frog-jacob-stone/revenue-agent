"""Project delivery reads.

Reads the Harvest snapshot cache (`harvest_projects`) from the delivery side
rather than the billing side. `app/services/billing/catalog.py` also lists
projects, but for billing configuration — it joins billing-group assignment and
takes an `exclude_group_id` so the group edit form doesn't report a group as
conflicting with itself. Same table, different question.

Read-only, so nothing here writes `audit_log`.
"""
from __future__ import annotations

import logging
from typing import Any

import asyncpg

from app.config import Settings
from app.services import forecast_snapshot
from app.services.billing import harvest_snapshot
from app.services.client_exclusions import not_excluded_sql

logger = logging.getLogger(__name__)

# Active reads alphabetically — you are scanning for a name you already have in
# mind. Archived reads newest-closed-first, because the useful archived lookup
# is almost always something that ended recently. Nulls sort last either way
# rather than sorting as the empty string.
_ORDER = {
    False: "name",
    True: "ends_on DESC NULLS LAST, name",
}


async def list_projects(
    pool: asyncpg.Pool, *, archived: bool = False
) -> list[dict[str, Any]]:
    """Billable Harvest projects, either running or closed.

    The two lists are disjoint and never merged: a closed engagement is a
    lookup, not something you scan past to see what is running.

    Two exclusions, and they catch different things:

    - Non-billable projects. Internal time buckets — PTO, overhead, sales — are
      Harvest projects but not engagements.
    - Projects of an excluded client (`excluded_harvest_clients`). Our own
      company is a Harvest client, and some of its internal work *is* flagged
      billable, so `is_billable` alone does not catch it.

    `projected_end_date` is the last day a person is booked in Forecast, LEFT
    JOINed so a project with no Forecast link still lists. Null there means one
    of two things the table renders identically and honestly: nobody is
    scheduled (hosting, retainers), or Forecast has never been synced.
    """
    rows = await pool.fetch(
        f"""
        SELECT p.harvest_id, p.name, p.client_name, p.starts_on, p.ends_on,
               p.is_active, p.synced_at,
               f.last_scheduled_on AS projected_end_date
        FROM harvest_projects p
        LEFT JOIN forecast_project_schedule f
               ON f.harvest_project_id = p.harvest_id
        WHERE p.is_billable
          AND p.is_active = $1
          AND {not_excluded_sql()}
        ORDER BY {_ORDER[archived]}
        """,
        not archived,
    )
    return [dict(r) for r in rows]


async def refresh_sources(
    pool: asyncpg.Pool, cfg: Settings, *, actor: str = "system"
) -> dict[str, Any]:
    """Re-read both sources behind the Projects tab. Read-only against both.

    Harvest first, then Forecast: the roster should be current before a
    forecast is attached to it, and a project created in Harvest today is
    otherwise invisible no matter what Forecast says about it.

    Sequential rather than concurrent. Harvest dominates the wall clock — one
    request per billable active project for task assignments — so overlapping
    Forecast's two requests would save a couple of seconds in exchange for a
    partial-failure story that is harder to report honestly.

    A Forecast failure does not fail the call. Harvest has already committed by
    then, and a 5xx would tell the operator nothing happened when half of it
    did. The error is returned instead, for the caller to surface.

    (`harvest_snapshot` lives under `services/billing/` for historical reasons.
    It is an integration sync, not billing logic — there is no billing concern
    imported here.)
    """
    harvest = await harvest_snapshot.refresh_snapshot(pool, cfg, actor=actor)

    forecast: dict[str, Any] | None = None
    forecast_error: str | None = None
    try:
        forecast = await forecast_snapshot.refresh_forecast_schedule(
            pool, cfg, actor=actor
        )
    except forecast_snapshot.ForecastNotConfigured as exc:
        forecast_error = str(exc)
    except Exception as exc:  # noqa: BLE001 — reported, not swallowed
        logger.exception("forecast refresh failed during project refresh")
        forecast_error = f"Forecast refresh failed: {exc}"

    return {
        "harvest": harvest,
        "forecast": forecast,
        "forecast_error": forecast_error,
    }
