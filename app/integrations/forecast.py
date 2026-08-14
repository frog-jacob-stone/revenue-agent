import logging
from datetime import date, timedelta
from typing import Any

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)

_BASE = "https://api.forecastapp.com"

# `/assignments` requires a bounded window and rejects a long one with a 422.
# Six years is accepted and eight is not, so five leaves room without probing
# for the exact ceiling on every call.
#
# The window is wide in both directions on purpose. It reads with *overlap*
# semantics — a one-day window still returns assignments that span it — so the
# future reach is what catches long bookings, and the backward reach is what
# keeps a project whose schedule has already run out from silently reporting no
# forecast at all.
_LOOKBACK_YEARS = 2
_LOOKAHEAD_YEARS = 3

# Matches app/integrations/harvest.py — same token, same vendor, same reasons.
_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


def _headers(cfg: Settings) -> dict[str, str]:
    # Forecast uses the same Personal Access Token as Harvest
    return {
        "Authorization": f"Bearer {cfg.harvest_token.get_secret_value()}",
        "Forecast-Account-Id": cfg.forecast_account_id,
    }


async def _get_projects(cfg: Settings) -> list[dict[str, Any]]:
    """Return all Forecast projects (includes harvest_id mapping)."""
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.get(f"{_BASE}/projects", headers=_headers(cfg))
        resp.raise_for_status()
        return resp.json().get("projects", [])


async def _get_future_scheduled_hours_raw(
    cfg: Settings, from_date: str
) -> dict[int, float]:
    """Return aggregate scheduled hours keyed by Forecast project ID."""
    url = f"{_BASE}/aggregate/future_scheduled_hours/{from_date}"
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.get(url, headers=_headers(cfg))
        resp.raise_for_status()
        data = resp.json()

    allocations = data.get("future_scheduled_hours", [])
    totals: dict[int, float] = {}
    for entry in allocations:
        pid = entry["project_id"]
        totals[pid] = totals.get(pid, 0.0) + float(entry.get("allocation", 0))
    return totals


async def get_scheduled_hours_by_harvest_id(
    cfg: Settings, from_date: str
) -> dict[int, float]:
    """
    Return scheduled future hours keyed by Harvest project ID.
    Joins Forecast project list (which has harvest_id) with the aggregated hours.
    """
    import asyncio

    projects, hours_by_forecast_id = await asyncio.gather(
        _get_projects(cfg),
        _get_future_scheduled_hours_raw(cfg, from_date),
    )

    result: dict[int, float] = {}
    for proj in projects:
        harvest_id = proj.get("harvest_id")
        if harvest_id is None:
            continue
        forecast_id = proj["id"]
        if forecast_id in hours_by_forecast_id:
            result[int(harvest_id)] = hours_by_forecast_id[forecast_id]
    return result


async def _get_assignments(
    cfg: Settings, start_date: str, end_date: str
) -> list[dict[str, Any]]:
    """Assignments overlapping the window. One request, no pagination."""
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.get(
            f"{_BASE}/assignments",
            headers=_headers(cfg),
            params={"start_date": start_date, "end_date": end_date},
        )
        resp.raise_for_status()
        return resp.json().get("assignments", [])


async def get_last_scheduled_by_harvest_id(
    cfg: Settings, *, today: date | None = None
) -> dict[int, dict[str, Any]]:
    """The last day a person is booked on each project, keyed by Harvest id.

    This is the delivery forecast: not when the project was planned to end, but
    when the schedule actually runs out.

    Counts only assignments with a `person_id`. Forecast also allows
    *placeholder* assignments — unfilled roles held open on the plan — and those
    are capacity rather than someone. Including them would push a handful of
    projects later on the strength of a booking with no name against it.

    Returns `{harvest_id: {"forecast_project_id", "last_scheduled_on",
    "assignment_count"}}`. A project present with `last_scheduled_on = None` has
    been looked at and genuinely has nobody booked — hosting and support
    retainers, mostly — which is a different fact from being absent entirely.
    """
    import asyncio

    today = today or date.today()
    start = today.replace(year=today.year - _LOOKBACK_YEARS)
    end = today.replace(year=today.year + _LOOKAHEAD_YEARS)

    projects, assignments = await asyncio.gather(
        _get_projects(cfg),
        _get_assignments(cfg, start.isoformat(), end.isoformat()),
    )

    # Forecast projects without a `harvest_id` are Forecast-only (internal
    # planning, prospects) and have nothing to attach to on our side.
    by_forecast_id = {
        p["id"]: int(p["harvest_id"]) for p in projects if p.get("harvest_id")
    }

    result: dict[int, dict[str, Any]] = {
        harvest_id: {
            "forecast_project_id": forecast_id,
            "last_scheduled_on": None,
            "assignment_count": 0,
        }
        for forecast_id, harvest_id in by_forecast_id.items()
    }

    for a in assignments:
        if not a.get("person_id"):
            continue
        harvest_id = by_forecast_id.get(a.get("project_id"))
        if harvest_id is None:
            continue
        end_date = a.get("end_date")
        if not end_date:
            continue
        row = result[harvest_id]
        row["assignment_count"] += 1
        if row["last_scheduled_on"] is None or end_date > row["last_scheduled_on"]:
            row["last_scheduled_on"] = end_date

    return result
